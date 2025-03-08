from collections import defaultdict, Counter
from flask import current_app
from datetime import datetime
from models import PreviousSchedule, db, Employee
import random

class Scheduler:
    def __init__(self, config):
        self.config = config
        # Order of days remains constant
        self.week_days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        self.week_working_days = config.get("WEEK_WORKING_DAYS", 7)

    def generate_schedule(self, employees, previous_week_off_days=None):
        # Build a schedule dict with a list per day.
        schedule = {day: [] for day in self.week_days}
        off_days = self.assign_weekly_off_days(employees, previous_week_off_days)

        for day in self.week_days:
            # For a 6-day workweek, Sunday is closed.
            if self.week_working_days == 6 and day == "Sunday":
                for emp in employees:
                    schedule[day].append({"employee": emp.name, "shift": "Store Closed"})
                continue

            # Only include employees not off on the day.
            available_employees = [emp for emp in employees if day not in off_days[emp.name]]

            # First add off-day entries.
            for emp in employees:
                if day in off_days[emp.name]:
                    schedule[day].append({
                        "employee": emp.name,
                        "shift": "Assigned Day Off",
                        "source": off_days[emp.name][day]
                    })

            self.assign_shifts_for_day(day, available_employees, schedule)

        # Enforce minimum staffing only on working days.
        self.enforce_min_staff(schedule, employees, off_days)
        return schedule

    def assign_weekly_off_days(self, employees, previous_week_off_days=None):
        off_days = defaultdict(dict)
        days_off_counter = Counter()

        for emp in employees:
            # Record manual off days first.
            manual_off = set(emp.manual_days_off or [])
            for day in manual_off:
                off_days[emp.name][day] = "manual"
                days_off_counter[day] += 1

            # For a 6-day workweek, force Sunday off if not already manually set.
            if self.week_working_days == 6 and "Sunday" not in off_days[emp.name]:
                off_days[emp.name]["Sunday"] = "manual"
                days_off_counter["Sunday"] += 1

            # Calculate the required off days.
            # Base requirement: 2 off days for 8-hour employees, 1 for 6-hour employees.
            base_required = 2 if emp.shift_type == "8-hour" else 1
            # Now, add the number of manual off days to lower the contract hours limit.
            required_off_days = base_required + len(manual_off)

            # Employee’s explicitly preferred off days.
            explicit_preferred = set(emp.preferred_day_off or [])
            # Exclude days for which the employee has a shift request.
            shift_request_days = set(emp.shift_requests.keys()) if emp.shift_requests else set()
            available_preferred = explicit_preferred - shift_request_days

            # While not enough off days are assigned, add additional off days.
            while len(off_days[emp.name]) < required_off_days:
                potential_days = available_preferred - set(off_days[emp.name].keys())
                if not potential_days:
                    potential_days = (set(self.week_days) - shift_request_days) - set(off_days[emp.name].keys())
                if not potential_days:
                    potential_days = set(self.week_days) - set(off_days[emp.name].keys())
                # Choose the day with the fewest off assignments.
                least_populated_days = sorted(potential_days, key=lambda d: days_off_counter[d])
                day_to_add = least_populated_days[0]
                source_type = 'preferred' if day_to_add in explicit_preferred else 'dynamic'
                off_days[emp.name][day_to_add] = source_type
                days_off_counter[day_to_add] += 1

        return off_days

    def assign_shifts_for_day(self, day, available_employees, schedule):
        morning_shift, evening_shift = [], []

        # First honor explicit shift requests.
        for emp in available_employees:
            request = (emp.shift_requests or {}).get(day)
            if request == "Morning":
                morning_shift.append((emp, True))  # mark as preferred
            elif request == "Evening":
                evening_shift.append((emp, True))

        # For remaining employees, assign shifts to balance staffing.
        remaining_employees = [emp for emp in available_employees if emp not in [t[0] for t in morning_shift + evening_shift]]
        random.shuffle(remaining_employees)
        while remaining_employees:
            emp = remaining_employees.pop()
            if len(morning_shift) <= len(evening_shift):
                morning_shift.append((emp, False))
            else:
                evening_shift.append((emp, False))

        # Create schedule entries.
        for emp, preferred in morning_shift:
            entry = {
                "employee": emp.name,
                "shift": self.get_shift_label(emp.shift_type, True),
                "shift_type": emp.shift_type  # include shift type for flipping
            }
            if preferred:
                entry["source"] = "preferred_shift"
            schedule[day].append(entry)
        for emp, preferred in evening_shift:
            entry = {
                "employee": emp.name,
                "shift": self.get_shift_label(emp.shift_type, False),
                "shift_type": emp.shift_type  # include shift type for flipping
            }
            if preferred:
                entry["source"] = "preferred_shift"
            schedule[day].append(entry)

    def enforce_min_staff(self, schedule, employees, off_days):
        # Only enforce staffing on working days.
        for day in self.week_days:
            if self.week_working_days == 6 and day == "Sunday":
                continue

            min_staff = self.config.get("MIN_STAFF_PER_SHIFT_DAY", {}).get(day, {'morning': 3, 'evening': 3})
            attempts = 0
            max_attempts = self.config.get("MAX_REBALANCE_ATTEMPTS", 10)
            while attempts < max_attempts:
                # Recalculate counts.
                morning_count = len([s for s in schedule[day] if "Morning" in s["shift"]])
                evening_count = len([s for s in schedule[day] if "Evening" in s["shift"]])
                if morning_count >= min_staff['morning'] and evening_count >= min_staff['evening']:
                    break

                # First, attempt to flip dynamic shifts.
                self.flip_dynamic_shifts(day, schedule, employees)
                # Recalculate counts.
                morning_count = len([s for s in schedule[day] if "Morning" in s["shift"]])
                evening_count = len([s for s in schedule[day] if "Evening" in s["shift"]])
                if morning_count >= min_staff['morning'] and evening_count >= min_staff['evening']:
                    break

                # Then, try rebalancing off days (which now flips dynamic off days as well).
                if not self.rebalance_days_off(schedule, off_days, employees, day):
                    break

                # And try flipping again after rebalancing.
                self.flip_dynamic_shifts(day, schedule, employees)
                attempts += 1

    def rebalance_days_off(self, schedule, off_days, employees, day):
        # Create a counter for how many employees have each off day.
        days_off_counter = Counter(day for offs in off_days.values() for day in offs)
        lock_preferred = self.config.get("LOCK_PREFERRED_OVERRIDES", True)
        min_staff = self.config.get("MIN_STAFF_PER_SHIFT_DAY", {}).get(day, {'morning': 3, 'evening': 3})

        shifts = schedule[day]
        morning_count = len([s for s in shifts if "Morning" in s["shift"]])
        evening_count = len([s for s in shifts if "Evening" in s["shift"]])
        total_shortage = max(0, min_staff["morning"] - morning_count) + max(0, min_staff["evening"] - evening_count)

        # Step 0: Reassign off day for any employee with an explicit shift request for this day.
        conflict_candidates = [emp for emp in employees if day in off_days[emp.name] and (emp.shift_requests and day in emp.shift_requests)]
        if conflict_candidates:
            for emp in conflict_candidates:
                potential_days = (set(self.week_days) - set(emp.shift_requests.keys())) - set(off_days[emp.name].keys())
                if not potential_days:
                    potential_days = set(self.week_days) - set(off_days[emp.name].keys())
                if potential_days:
                    new_day_off = sorted(potential_days, key=lambda d: days_off_counter[d])[0]
                    off_days[emp.name][new_day_off] = off_days[emp.name].pop(day)
                    days_off_counter[new_day_off] += 1
                    days_off_counter[day] -= 1
                    schedule[day] = [entry for entry in schedule[day]
                                     if not (entry["employee"] == emp.name and entry["shift"] == "Assigned Day Off")]
                    current_morning = len([s for s in schedule[day] if "Morning" in s["shift"]])
                    current_evening = len([s for s in schedule[day] if "Evening" in s["shift"]])
                    new_shift = self.get_shift_label(emp.shift_type, True) if current_morning <= current_evening else self.get_shift_label(emp.shift_type, False)
                    schedule[day].append({"employee": emp.name, "shift": new_shift, "shift_type": emp.shift_type})
                    return True

        # Step 1: Flip dynamic off days.
        dynamic_candidates = [emp for emp in employees if day in off_days[emp.name] and off_days[emp.name][day] == 'dynamic']
        if dynamic_candidates:
            for emp in dynamic_candidates:
                # Check working limit before flipping.
                if self.get_working_shifts_count(emp, schedule) < self.get_allowed_shifts(emp):
                    off_days[emp.name].pop(day)
                    schedule[day] = [entry for entry in schedule[day]
                                     if not (entry["employee"] == emp.name and entry["shift"] == "Assigned Day Off")]
                    current_morning = len([s for s in schedule[day] if "Morning" in s["shift"]])
                    current_evening = len([s for s in schedule[day] if "Evening" in s["shift"]])
                    new_shift = self.get_shift_label(emp.shift_type, True) if current_morning <= current_evening else self.get_shift_label(emp.shift_type, False)
                    schedule[day].append({"employee": emp.name, "shift": new_shift, "shift_type": emp.shift_type})
                    return True

        # Step 2: Preferred override if allowed and staffing shortage persists.
        if not lock_preferred and total_shortage > 0:
            preferred_candidates = [emp for emp in employees if day in off_days[emp.name] and off_days[emp.name][day] == 'preferred']
            if preferred_candidates:
                preferred_candidates.sort(key=lambda emp: sum(1 for d, src in off_days[emp.name].items() if src == 'preferred'))
                for emp in preferred_candidates:
                    potential_days = sorted(
                        set(self.week_days) - set(off_days[emp.name].keys()) - set(emp.manual_days_off or []),
                        key=lambda d: days_off_counter[d]
                    )
                    if potential_days:
                        new_day_off = potential_days[0]
                        off_days[emp.name][new_day_off] = off_days[emp.name].pop(day)
                        days_off_counter[new_day_off] += 1
                        days_off_counter[day] -= 1
                        schedule[day] = [entry for entry in schedule[day]
                                         if not (entry["employee"] == emp.name and entry["shift"] == "Assigned Day Off")]
                        current_morning = len([s for s in schedule[day] if "Morning" in s["shift"]])
                        current_evening = len([s for s in schedule[day] if "Evening" in s["shift"]])
                        new_shift = self.get_shift_label(emp.shift_type, True) if current_morning <= current_evening else self.get_shift_label(emp.shift_type, False)
                        schedule[day].append({"employee": emp.name, "shift": new_shift, "shift_type": emp.shift_type})
                        return True

        return False

    def flip_dynamic_shifts(self, day, schedule, employees):
        # Get current entries for the day.
        morning_entries = [entry for entry in schedule[day] if "Morning" in entry["shift"]]
        evening_entries = [entry for entry in schedule[day] if "Evening" in entry["shift"]]
        min_staff = self.config.get("MIN_STAFF_PER_SHIFT_DAY", {}).get(day, {'morning': 0, 'evening': 0})
        
        # First, if evening is understaffed, try flipping dynamic candidates from morning to evening.
        shortage_evening = min_staff['evening'] - len(evening_entries)
        if shortage_evening > 0:
            dynamic_morning = [entry for entry in morning_entries if entry.get("source") != "preferred_shift"]
            for candidate in dynamic_morning:
                if (len(morning_entries) - 1) >= min_staff['morning']:
                    schedule[day].remove(candidate)
                    candidate["shift"] = self.get_shift_label(candidate["shift_type"], is_morning=False)
                    candidate["source"] = "flipped_dynamic"
                    schedule[day].append(candidate)
                    shortage_evening -= 1
                    morning_entries = [entry for entry in schedule[day] if "Morning" in entry["shift"]]
                    if shortage_evening <= 0:
                        break
        
        # Update lists after potential flips.
        morning_entries = [entry for entry in schedule[day] if "Morning" in entry["shift"]]
        evening_entries = [entry for entry in schedule[day] if "Evening" in entry["shift"]]
        
        # Then, if morning is understaffed, try flipping dynamic candidates from evening to morning.
        shortage_morning = min_staff['morning'] - len(morning_entries)
        if shortage_morning > 0:
            dynamic_evening = [entry for entry in evening_entries if entry.get("source") != "preferred_shift"]
            for candidate in dynamic_evening:
                if (len(evening_entries) - 1) >= min_staff['evening']:
                    schedule[day].remove(candidate)
                    candidate["shift"] = self.get_shift_label(candidate["shift_type"], is_morning=True)
                    candidate["source"] = "flipped_dynamic"
                    schedule[day].append(candidate)
                    shortage_morning -= 1
                    evening_entries = [entry for entry in schedule[day] if "Evening" in entry["shift"]]
                    if shortage_morning <= 0:
                        break

    def get_shift_label(self, shift_type, is_morning):
        if shift_type == "8-hour":
            return "Morning (08:30–16:30)" if is_morning else "Evening (13:30–21:30)"
        return "Morning (09:00–15:00)" if is_morning else "Evening (15:00–21:00)"

    def get_allowed_shifts(self, emp):
        # For 8-hour employees: max shifts = 5 - (# manual off days)
        # For 6-hour employees: max shifts = 6 - (# manual off days)
        manual_off_count = len(emp.manual_days_off or [])
        if emp.shift_type == "8-hour":
            return 5 - manual_off_count
        return 6 - manual_off_count

    def get_working_shifts_count(self, emp, schedule):
        count = 0
        for day in self.week_days:
            for entry in schedule[day]:
                if entry["employee"] == emp.name and entry["shift"] not in ("Assigned Day Off", "Store Closed"):
                    count += 1
        return count

def create_schedule():
    config = current_app.config
    employees = Employee.query.all()
    last_week_schedule = PreviousSchedule.query.order_by(PreviousSchedule.date.desc()).first()
    previous_week_off_days = defaultdict(set)

    if last_week_schedule:
        for day, shifts in last_week_schedule.data.items():
            for shift in shifts:
                if shift['shift'] == 'Assigned Day Off':
                    previous_week_off_days[shift['employee']].add(day)

    scheduler = Scheduler(config)
    schedule = scheduler.generate_schedule(employees, previous_week_off_days)

    new_schedule_record = PreviousSchedule(
        date=datetime.utcnow(),
        data=schedule
    )
    db.session.add(new_schedule_record)
    db.session.commit()
    return schedule
