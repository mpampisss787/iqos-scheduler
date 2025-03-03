# scheduler.py

import random
from collections import defaultdict

from flask import current_app
from .models import Employee

class Scheduler:
    """
    Encapsulates all the scheduling logic:
      1) Off-day assignment for 8-hour vs 6-hour employees
      2) Shift assignment (respecting requests, contract limits, and consecutive shift rules)
      3) Minimum staffing enforcement
      4) Post-processing if 7-day week (ensuring no 8-hour employee goes over limit)
    """

    def __init__(self, config):
        # config is typically current_app.config
        self.config = config

    def generate_schedule(self, employees):
        """
        Generate a dictionary like:
            {
              "Monday": [
                {"employee": "Alice", "shift": "Morning (08:30–16:30)", "explicit": False},
                {"employee": "Bob",   "shift": "Preferred Day Off",      "explicit": False},
                ...
              ],
              "Tuesday": [...],
              ...
              "Sunday":  [...]
            }
        """
        days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        schedule = {d: [] for d in days}

        # Build each employee's contract limit
        contract_limit = {}
        for emp in employees:
            manual_off_count = len(emp.manual_days_off or [])
            if emp.shift_type == "8-hour":
                # In 7-day mode, 8-hour employees can work max 6 days (only Sunday off)
                # In 6-day mode, they work 6 days (Sunday closed)
                contract_limit[emp.name] = (6 if self.config.get("WEEK_WORKING_DAYS", 7) == 7 else 6) - manual_off_count
            else:
                # 6-hour employees can work max 6 days (or less if manual offs)
                contract_limit[emp.name] = (6 if self.config.get("WEEK_WORKING_DAYS", 7) == 7 else 6) - manual_off_count

        # Compute default "off" days for each employee
        default_off = self._compute_default_off(employees)

        # We'll keep a record of each day's assignments
        employee_history = defaultdict(list)

        # 1) Assign shifts for 8-hour employees
        self._assign_shifts_8h(schedule, days, employees, contract_limit, default_off, employee_history)

        # 2) Assign shifts for 6-hour employees
        self._assign_shifts_6h(schedule, days, employees, contract_limit, default_off, employee_history)

        # 3) Enforce minimum staffing
        self._enforce_min_staff(schedule, days, employees, contract_limit, employee_history)

        # 4) If 7-day week, ensure no 8-hour employee goes over limit
        if self.config.get('WEEK_WORKING_DAYS', 6) == 7:
            self._post_process_8h_7day(schedule, days, employees, contract_limit)

        return schedule

    def _compute_default_off(self, employees):
        """
        For each employee, determine any 'auto' or 'fallback' off days, depending on store config.
          - For 8-hour employees in a 6-day week, force Sunday plus one extra day if none chosen.
          - For 8-hour employees in a 7-day week, force only Sunday off.
          - For 6-hour employees in 7-day: if no off is chosen, default to Sunday.
        """
        default_off = {}
        days_6 = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
        days_7 = days_6 + ["Sunday"]

        for emp in employees:
            # Merge any already-chosen off days (preferred or manual)
            off_list = set(emp.preferred_day_off or []).union(set(emp.manual_days_off or []))

            if emp.shift_type == "8-hour":
                if self.config.get("WEEK_WORKING_DAYS", 6) == 6:
                    # 6-day scenario: force Sunday off plus one extra if none preferred
                    if "Sunday" not in off_list:
                        off_list.add("Sunday")
                    # When no preferred off is set, choose an extra day,
                    # but avoid any day for which there is an explicit shift request.
                    if len(emp.preferred_day_off or []) < 1:
                        request_days = set(emp.shift_requests.keys() if emp.shift_requests else [])
                        candidates = [d for d in days_6 if d not in off_list and d not in request_days]
                        random.shuffle(candidates)
                        if candidates:
                            off_list.add(candidates[0])
                else:
                    # 7-day scenario: force only Sunday off (ignore extra random off)
                    off_list.add("Sunday")
            else:
                # 6-hour employees
                if self.config.get("WEEK_WORKING_DAYS", 7) == 7:
                    # If no off chosen at all, default to Sunday
                    if not off_list:
                        off_list = {"Sunday"}
            default_off[emp.name] = list(off_list)

        return default_off

    def _assign_shifts_8h(self, schedule, days, employees, contract_limit, default_off, history):
        # Filter employees = 8-hour
        e8 = [e for e in employees if e.shift_type == "8-hour"]

        # For 6-day schedule, mark Sunday as Store Closed for 8-hour employees and only process Mon–Sat.
        if self.config.get("WEEK_WORKING_DAYS", 7) == 6:
            for emp in e8:
                schedule["Sunday"].append({"employee": emp.name, "shift": "Store Closed", "explicit": False})
            days_to_process = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
        else:
            days_to_process = days

        for day in days_to_process:
            working_8 = []
            for emp in e8:
                union_off = set(emp.preferred_day_off or []).union(emp.manual_days_off or [])
                # Also incorporate any "default_off"
                union_off = union_off.union(default_off.get(emp.name, []))
                if day in union_off:
                    label = "Manual Day Off" if day in (emp.manual_days_off or []) else "Preferred Day Off"
                    schedule[day].append({"employee": emp.name, "shift": label, "explicit": False})
                else:
                    working_8.append(emp)
            working_8.sort(key=lambda x: x.name)
            self._assign_for_group(schedule, day, working_8, contract_limit, history, shift_type="8-hour")

    def _assign_shifts_6h(self, schedule, days, employees, contract_limit, default_off, history):
        # Filter employees = 6-hour
        e6 = [e for e in employees if e.shift_type == "6-hour"]

        # For 6-day schedule, mark Sunday as Store Closed for all 6-hour employees and process Mon–Sat.
        if self.config.get("WEEK_WORKING_DAYS", 7) == 6:
            for emp in e6:
                schedule["Sunday"].append({"employee": emp.name, "shift": "Store Closed", "explicit": False})
            relevant_days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
        else:
            relevant_days = days

        for day in relevant_days:
            working_6 = []
            for emp in e6:
                union_off = set(emp.preferred_day_off or []).union(emp.manual_days_off or [])
                union_off = union_off.union(default_off.get(emp.name, []))
                if day in union_off:
                    label = "Manual Day Off" if day in (emp.manual_days_off or []) else "Preferred Day Off"
                    schedule[day].append({"employee": emp.name, "shift": label, "explicit": False})
                else:
                    working_6.append(emp)
            working_6.sort(key=lambda x: x.name)
            self._assign_for_group(schedule, day, working_6, contract_limit, history, shift_type="6-hour")

    def _assign_for_group(self, schedule, day, group, contract_limit, history, shift_type):
        """
        Place each employee in "Morning" or "Evening" (or force day off if their contract is exhausted)
        while considering explicit requests and the three-consecutive rule.
        """
        half = len(group) // 2

        for idx, emp in enumerate(group):
            name = emp.name
            used_shifts = sum(("Morning" in s or "Evening" in s) for s in history[name])
            if used_shifts >= contract_limit[name]:
                schedule[day].append({"employee": name, "shift": "Assigned Day Off", "explicit": False})
                history[name].append("Assigned Day Off")
                continue

            # Check explicit shift requests.
            requested = (emp.shift_requests or {}).get(day, "No Request")
            explicit = False
            if requested == "Morning":
                candidate, alternate = self._morning_evening(shift_type, morning=True)
                explicit = True
            elif requested == "Evening":
                candidate, alternate = self._morning_evening(shift_type, morning=False)
                explicit = True
            else:
                if idx < half:
                    candidate, alternate = self._morning_evening(shift_type, morning=True)
                else:
                    candidate, alternate = self._morning_evening(shift_type, morning=False)

            # Check 3-consecutive shift rule.
            if len(history[name]) >= 3:
                last3 = history[name][-3:]
                if all(s == candidate for s in last3):
                    candidate, alternate = alternate, candidate

            # Check contract usage for the candidate shift.
            used_cand = history[name].count(candidate)
            if used_cand >= contract_limit[name]:
                used_alt = history[name].count(alternate)
                if used_alt < contract_limit[name]:
                    candidate = alternate
                else:
                    candidate = "Assigned Day Off"
                    explicit = False

            history[name].append(candidate)
            schedule[day].append({"employee": name, "shift": candidate, "explicit": explicit})

    def _morning_evening(self, shift_type, morning=True):
        """Return (candidate, alternate) shift labels based on shift_type and preference."""
        if shift_type == "8-hour":
            if morning:
                return ("Morning (08:30–16:30)", "Evening (13:30–21:30)")
            else:
                return ("Evening (13:30–21:30)", "Morning (08:30–16:30)")
        else:
            if morning:
                return ("Morning (09:00–15:00)", "Evening (15:00–21:00)")
            else:
                return ("Evening (15:00–21:00)", "Morning (09:00–15:00)")

    def _enforce_min_staff(self, schedule, days, employees, contract_limit, history):
        def is_work_shift(s): 
            return ("Morning" in s or "Evening" in s)

        fallback = self.config.get("MIN_STAFF_PER_SHIFT", 3)
        min_staff_map = self.config.get("MIN_STAFF_PER_SHIFT_DAY", {})

        days = sorted(days, key=lambda x: (x != 'Saturday', x))  # Prioritize Saturday

        for d in days:
            conf = min_staff_map.get(d, {})
            req_morn = conf.get("morning", fallback)
            req_eve  = conf.get("evening", fallback)

            day_assignments = schedule[d]
            morning_count = sum("Morning" in a["shift"] for a in day_assignments)
            evening_count = sum("Evening" in a["shift"] for a in day_assignments)

            # PHASE A: Try flipping from one shift to the other.
            if d == 'Saturday':
                needed_morn = max(req_morn - morning_count, 0)
                needed_eve = max(req_eve - evening_count, 0)
                if needed_morn > 0 or needed_eve > 0:
                    print(f'⚠ Enforcing staffing on Saturday: morning={needed_morn}, evening={needed_eve}')
            morning_count = self._flip_shifts_if_needed(
                schedule, d, employees, contract_limit, history,
                needed=req_morn - morning_count,
                from_shift="Evening", to_shift="Morning"
            )

            day_assignments = schedule[d]
            morning_count = sum("Morning" in a["shift"] for a in day_assignments)
            evening_count = sum("Evening" in a["shift"] for a in day_assignments)

            evening_count = self._flip_shifts_if_needed(
                schedule, d, employees, contract_limit, history,
                needed=req_eve - evening_count,
                from_shift="Morning", to_shift="Evening"
            )

            # PHASE B: If still short, try overriding off assignments (but never Manual Day Off).
            day_assignments = schedule[d]
            morning_count = sum("Morning" in a["shift"] for a in day_assignments)
            evening_count = sum("Evening" in a["shift"] for a in day_assignments)

            morning_count = self._fill_shortage_off(
                schedule, d, employees, contract_limit, history,
                needed=req_morn - morning_count,
                fill_shift="morning"
            )
            evening_count = self._fill_shortage_off(
                schedule, d, employees, contract_limit, history,
                needed=req_eve - evening_count,
                fill_shift="evening"
            )

    def _flip_shifts_if_needed(self, schedule, day, employees, contract_limit, history,
                                 needed, from_shift, to_shift):
        """
        If needed > 0, try flipping employees from one shift to the other,
        ensuring contract limits and manual offs are not violated.
        IMPORTANT: This function now skips any assignment that was made explicitly.
        Return the new count of the target shift.
        """
        if needed <= 0:
            return sum(to_shift in a["shift"] for a in schedule[day])

        def is_work_shift(s):
            return ("Morning" in s or "Evening" in s)

        day_assignments = schedule[day]
        to_shift_count = sum(to_shift in a["shift"] for a in day_assignments)

        while needed > 0:
            flipped = False
            for rec in day_assignments:
                # Do not flip if the assignment was made explicitly.
                if rec.get("explicit", False):
                    continue
                old_shift = rec["shift"]
                if from_shift in old_shift:
                    emp_name = rec["employee"]
                    used_so_far = sum(is_work_shift(x) for x in history[emp_name])
                    if used_so_far < contract_limit[emp_name]:
                        emp_obj = next((e for e in employees if e.name == emp_name), None)
                        if emp_obj is None:
                            continue
                        if emp_obj.shift_type == "8-hour":
                            new_s = "Morning (08:30–16:30)" if to_shift == "Morning" else "Evening (13:30–21:30)"
                        else:
                            new_s = "Morning (09:00–15:00)" if to_shift == "Morning" else "Evening (15:00–21:00)"
                        rec["shift"] = new_s
                        for i in reversed(range(len(history[emp_name]))):
                            if old_shift in history[emp_name][i]:
                                history[emp_name][i] = new_s
                                break
                        to_shift_count += 1
                        flipped = True
                        needed -= 1
                        break
            if not flipped:
                break

        return to_shift_count

    def _fill_shortage_off(self, schedule, day, employees, contract_limit, history,
                           needed, fill_shift):
        """
        Attempt to reassign employees from "Preferred Day Off" or "Assigned Day Off"
        (never "Manual Day Off") to the needed shift if they have contract room.
        """
        if fill_shift not in ("morning", "evening"):
            return 0

        def is_work_shift(s): 
            return ("Morning" in s or "Evening" in s)

        def choose_shift(shift_type, morning=True):
            if shift_type == "8-hour":
                return "Morning (08:30–16:30)" if morning else "Evening (13:30–21:30)"
            else:
                return "Morning (09:00–15:00)" if morning else "Evening (15:00–21:00)"

        day_assignments = schedule[day]
        current_count = sum(fill_shift.capitalize() in a["shift"] for a in day_assignments)
        if needed <= 0:
            return current_count

        shift_is_morning = (fill_shift == "morning")

        for rec in day_assignments:
            if needed <= 0:
                break
            if rec["shift"] in ("Preferred Day Off", "Assigned Day Off"):
                emp_name = rec["employee"]
                used_shifts = sum(is_work_shift(x) for x in history[emp_name])
                if used_shifts < contract_limit[emp_name]:
                    emp_obj = next((e for e in employees if e.name == emp_name), None)
                    if emp_obj:
                        new_s = choose_shift(emp_obj.shift_type, shift_is_morning)
                        rec["shift"] = new_s
                        history[emp_name].append(new_s)
                        needed -= 1
                        current_count += 1

        return current_count

    def _post_process_8h_7day(self, schedule, days, employees, contract_limit):
        """
        For 7-day weeks, if an 8-hour employee ended up with more working shifts than allowed,
        remove the excess assignments from the end.
        """
        for emp in employees:
            if emp.shift_type != "8-hour":
                continue
            assigned_shifts = []
            for d in days:
                for rec in schedule[d]:
                    if rec["employee"] == emp.name and ("Morning" in rec["shift"] or "Evening" in rec["shift"]):
                        assigned_shifts.append((d, rec))
            limit = contract_limit.get(emp.name, 6)
            while len(assigned_shifts) > limit:
                d, rec = assigned_shifts.pop()
                rec["shift"] = "Assigned Day Off"


def generate_schedule():
    """
    Convenience function to be used by the schedule routes or elsewhere.
    It retrieves config from current_app, loads employees, and generates the schedule.
    """
    from flask import current_app
    from .models import Employee

    config = current_app.config
    all_emps = Employee.query.all()
    scheduler_obj = Scheduler(config)
    return scheduler_obj.generate_schedule(all_emps)
