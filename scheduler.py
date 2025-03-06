import random
from collections import defaultdict

from flask import current_app
from .models import Employee

class Scheduler:
    """
    Encapsulates scheduling logic:
      1) Off-day assignment for 8-hour vs 6-hour employees.
      2) Shift assignment (respecting requests, contract limits, and consecutive shift rules).
      3) Minimum staffing enforcement.
      4) Post-processing for 7-day week.
    """

    def __init__(self, config):
        self.config = config
        # New settings for preferred overrides:
        self.lock_preferred_overrides = config.get("LOCK_PREFERRED_OVERRIDES", True)
        self.preferred_override_threshold = config.get("PREFERRED_OVERRIDE_THRESHOLD", 2)
        # Ensure the max consecutive shifts rule is set to a low value for employee well-being.
        # If the configuration value is greater than 2, force it to 2.
        if config.get('max_consecutive', 3) > 2:
            config['max_consecutive'] = 2
        if config.get("WEEK_WORKING_DAYS", 7) == 6:
            config.setdefault("MIN_STAFF_PER_SHIFT_DAY", {})["Sunday"] = {"morning": 0, "evening": 0}

    def generate_schedule(self, employees):
        days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        schedule = {d: [] for d in days}

        contract_limit = {}
        for emp in employees:
            manual_off_count = len(emp.manual_days_off or [])
            if emp.shift_type == "8-hour":
                contract_limit[emp.name] = 5 - manual_off_count
            else:
                contract_limit[emp.name] = (6 if self.config.get("WEEK_WORKING_DAYS", 7) == 7 else 6) - manual_off_count

        default_off = self._compute_default_off(employees)
        employee_history = defaultdict(list)

        self._assign_shifts_8h(schedule, days, employees, contract_limit, default_off, employee_history)
        self._assign_shifts_6h(schedule, days, employees, contract_limit, default_off, employee_history)
        self._enforce_min_staff(schedule, days, employees, contract_limit, employee_history)

        if self.config.get('WEEK_WORKING_DAYS', 6) == 7:
            self._post_process_8h_7day(schedule, days, employees, contract_limit)

        return schedule

    def _compute_default_off(self, employees):
        default_off = {}
        days_6 = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
        days_7 = days_6 + ["Sunday"]
        week_days = self.config.get("WEEK_WORKING_DAYS", 7)

        for emp in employees:
            off_list = set(emp.preferred_day_off or []).union(set(emp.manual_days_off or []))
            if emp.shift_type == "8-hour":
                if week_days == 6:
                    if "Sunday" not in off_list:
                        off_list.add("Sunday")
                    if len(off_list) < 2:
                        request_days = set(emp.shift_requests.keys() if emp.shift_requests else [])
                        candidates = [d for d in days_6 if d not in off_list and d not in request_days]
                        random.shuffle(candidates)
                        if candidates:
                            off_list.add(candidates[0])
                else:
                    if len(off_list) < 2:
                        candidates = [d for d in days_7 if d not in off_list]
                        random.shuffle(candidates)
                        if candidates:
                            off_list.add(candidates[0])
            else:
                if week_days == 7 and not off_list:
                    off_list = {"Sunday"}
            default_off[emp.name] = list(off_list)
        return default_off

    def _assign_shifts_8h(self, schedule, days, employees, contract_limit, default_off, history):
        e8 = [e for e in employees if e.shift_type == "8-hour"]
        week_days = self.config.get("WEEK_WORKING_DAYS", 7)
        if week_days == 6:
            for emp in e8:
                schedule["Sunday"].append({
                    "employee": emp.name,
                    "shift": "Store Closed",
                    "explicit": False,
                    "source": "manual"
                })
            days_to_process = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
        else:
            days_to_process = days

        for day in days_to_process:
            working_8 = []
            for emp in e8:
                union_off = set(emp.preferred_day_off or []).union(emp.manual_days_off or [])
                union_off = union_off.union(default_off.get(emp.name, []))
                if day in union_off:
                    source = "manual" if day in (emp.manual_days_off or []) else "preferred"
                    label = "Manual Day Off" if source == "manual" else "Preferred Day Off"
                    schedule[day].append({
                        "employee": emp.name,
                        "shift": label,
                        "explicit": False,
                        "source": source
                    })
                else:
                    working_8.append(emp)
            if week_days == 7:
                random.shuffle(working_8)
            else:
                working_8.sort(key=lambda x: x.name)
            self._assign_for_group(schedule, day, working_8, contract_limit, history, shift_type="8-hour")

    def _assign_shifts_6h(self, schedule, days, employees, contract_limit, default_off, history):
        e6 = [e for e in employees if e.shift_type == "6-hour"]
        week_days = self.config.get("WEEK_WORKING_DAYS", 7)
        if week_days == 6:
            for emp in e6:
                schedule["Sunday"].append({
                    "employee": emp.name,
                    "shift": "Store Closed",
                    "explicit": False,
                    "source": "manual"
                })
            relevant_days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
        else:
            relevant_days = days

        for day in relevant_days:
            working_6 = []
            for emp in e6:
                union_off = set(emp.preferred_day_off or []).union(emp.manual_days_off or [])
                union_off = union_off.union(default_off.get(emp.name, []))
                if day in union_off:
                    source = "manual" if day in (emp.manual_days_off or []) else "preferred"
                    label = "Manual Day Off" if source == "manual" else "Preferred Day Off"
                    schedule[day].append({
                        "employee": emp.name,
                        "shift": label,
                        "explicit": False,
                        "source": source
                    })
                else:
                    working_6.append(emp)
            if week_days == 7:
                random.shuffle(working_6)
            else:
                working_6.sort(key=lambda x: x.name)
            self._assign_for_group(schedule, day, working_6, contract_limit, history, shift_type="6-hour")

    def _assign_for_group(self, schedule, day, group, contract_limit, history, shift_type):
        """
        Dynamically assign shifts for a group of employees.
        If an employee has an explicit request, it is honored.
        Otherwise, the assignment is balanced evenly between morning and evening.
        """
        req_morn = self.config["MIN_STAFF_PER_SHIFT_DAY"].get(day, {}).get("morning", 3)
        req_eve = self.config["MIN_STAFF_PER_SHIFT_DAY"].get(day, {}).get("evening", 3)
        dynamic_morn = sum(1 for a in schedule[day] if a.get("source") == "dynamic" and "Morning" in a["shift"])
        dynamic_eve = sum(1 for a in schedule[day] if a.get("source") == "dynamic" and "Evening" in a["shift"])
        for idx, emp in enumerate(group):
            name = emp.name
            used_shifts = sum(("Morning" in s or "Evening" in s) for s in history[name])
            if used_shifts >= contract_limit[name]:
                schedule[day].append({
                    "employee": name,
                    "shift": "Assigned Day Off",
                    "explicit": False,
                    "source": "dynamic"
                })
                history[name].append("Assigned Day Off")
                continue

            requested = (emp.shift_requests or {}).get(day, "No Request")
            if requested == "Morning":
                candidate, alternate = self._morning_evening(shift_type, morning=True)
                explicit = True
            elif requested == "Evening":
                candidate, alternate = self._morning_evening(shift_type, morning=False)
                explicit = True
            else:
                if dynamic_morn < dynamic_eve:
                    candidate, alternate = self._morning_evening(shift_type, morning=True)
                elif dynamic_eve < dynamic_morn:
                    candidate, alternate = self._morning_evening(shift_type, morning=False)
                else:
                    candidate, alternate = random.choice([
                        self._morning_evening(shift_type, morning=True),
                        self._morning_evening(shift_type, morning=False)
                    ])
                explicit = False

            # ***** Adjustment: Do not override explicit (preferred) requests via consecutive rule *****
            max_consec = self.config.get('max_consecutive', 3)
            if not explicit and max_consec > 0 and len(history[name]) >= max_consec:
                last_n = history[name][-max_consec:]
                if all(s == candidate for s in last_n):
                    candidate, alternate = alternate, candidate

            used_cand = history[name].count(candidate)
            if used_cand >= contract_limit[name]:
                used_alt = history[name].count(alternate)
                if used_alt < contract_limit[name]:
                    candidate = alternate
                else:
                    candidate = "Assigned Day Off"
                    explicit = False

            source = "preferred" if explicit else "dynamic"
            history[name].append(candidate)
            schedule[day].append({
                "employee": name,
                "shift": candidate,
                "explicit": explicit,
                "source": source
            })

    def _morning_evening(self, shift_type, morning=True):
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
        def is_evening_8h(s):
            return "Evening (13:30–21:30)" in s
        def is_evening_6h(s):
            return "Evening (15:00–21:00)" in s
        fallback = self.config.get("MIN_STAFF_PER_SHIFT", 3)
        min_staff_map = self.config.get("MIN_STAFF_PER_SHIFT_DAY", {})
        days = sorted(days, key=lambda x: (x != 'Saturday', x))
        for d in days:
            conf = min_staff_map.get(d, {})
            req_morn = conf.get("morning", fallback)
            req_eve = conf.get("evening", fallback)
            day_assignments = schedule[d]
            morn_count = sum("Morning" in a["shift"] for a in day_assignments)
            eve_count = sum((1 if is_evening_8h(a["shift"]) else 0.5) for a in day_assignments)
            if d == 'Saturday':
                needed_morn = max(req_morn - morn_count, 0)
                needed_eve = max(req_eve - eve_count, 0)
                if needed_morn > 0 or needed_eve > 0:
                    print(f'⚠ Enforcing staffing on Saturday: morning={needed_morn}, evening={needed_eve}')
            morn_count = self._flip_shifts_if_needed(
                schedule, d, employees, contract_limit, history,
                needed=req_morn - morn_count,
                from_shift="Evening", to_shift="Morning"
            )
            eve_count = sum((1 if is_evening_8h(a["shift"]) else 0.5) for a in schedule[d])
            eve_count = self._flip_shifts_if_needed(
                schedule, d, employees, contract_limit, history,
                needed=req_eve - eve_count,
                from_shift="Morning", to_shift="Evening"
            )
            morn_count = self._fill_shortage_off(
                schedule, d, employees, contract_limit, history,
                needed=req_morn - morn_count,
                fill_shift="morning"
            )
            eve_count = self._fill_shortage_off(
                schedule, d, employees, contract_limit, history,
                needed=req_eve - eve_count,
                fill_shift="evening"
            )

    def _flip_shifts_if_needed(self, schedule, day, employees, contract_limit, history,
                               needed, from_shift, to_shift):
        if needed <= 0:
            return 0
        def is_work_shift(s):
            return ("Morning" in s or "Evening" in s)
        day_assignments = schedule[day]
        to_shift_count = 0
        while needed > 0:
            flipped = False
            for rec in day_assignments:
                if rec.get("source") == "manual":
                    continue
                if rec.get("source") == "preferred":
                    if self.lock_preferred_overrides:
                        continue
                    else:
                        if needed < self.preferred_override_threshold:
                            continue
                old_shift = rec["shift"]
                if from_shift in old_shift:
                    emp_name = rec["employee"]
                    emp_obj = next((e for e in employees if e.name == emp_name), None)
                    if not emp_obj:
                        continue
                    used_so_far = sum(is_work_shift(x) for x in history[emp_name])
                    if used_so_far >= contract_limit[emp_name]:
                        continue
                    if emp_obj.shift_type == "8-hour":
                        new_s = "Morning (08:30–16:30)" if to_shift == "Morning" else "Evening (13:30–21:30)"
                    else:
                        new_s = "Morning (09:00–15:00)" if to_shift == "Morning" else "Evening (15:00–21:00)"
                    rec["shift"] = new_s
                    rec["source"] = "dynamic"
                    flipped = True
                    to_shift_count += 1
                    needed -= 1
                    break
            if not flipped:
                break
        return to_shift_count

    def _fill_shortage_off(self, schedule, day, employees, contract_limit, history,
                           needed, fill_shift):
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
            if rec["shift"] == "Assigned Day Off":
                emp_name = rec["employee"]
                used_shifts = sum(is_work_shift(x) for x in history[emp_name])
                if used_shifts < contract_limit[emp_name]:
                    emp_obj = next((e for e in employees if e.name == emp_name), None)
                    if emp_obj:
                        new_s = choose_shift(emp_obj.shift_type, shift_is_morning)
                        rec["shift"] = new_s
                        for i in reversed(range(len(history[emp_name]))):
                            if history[emp_name][i] == "Assigned Day Off":
                                history[emp_name][i] = new_s
                                break
                        needed -= 1
                        current_count += 1
        return current_count

    def _post_process_8h_7day(self, schedule, days, employees, contract_limit):
        fallback = self.config.get("MIN_STAFF_PER_SHIFT", 3)
        min_staff_map = self.config.get("MIN_STAFF_PER_SHIFT_DAY", {})
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
                removed = False
                for i, (d, rec) in enumerate(assigned_shifts):
                    shift_label = rec["shift"]
                    day_staff_conf = min_staff_map.get(d, {})
                    if "Morning" in shift_label:
                        req = day_staff_conf.get("morning", fallback)
                        current = sum("Morning" in a["shift"] for a in schedule[d])
                    elif "Evening" in shift_label:
                        req = day_staff_conf.get("evening", fallback)
                        current = sum("Evening" in a["shift"] for a in schedule[d])
                    else:
                        continue
                    if current - 1 >= req:
                        rec["shift"] = "Assigned Day Off"
                        assigned_shifts.pop(i)
                        removed = True
                        break
                if not removed:
                    break

def is_valid_schedule(schedule, config):
    """
    Checks that for each day, the number of Morning and Evening assignments
    meets the minimum staffing requirements.
    """
    fallback = config.get("MIN_STAFF_PER_SHIFT", 3)
    min_staff = config.get("MIN_STAFF_PER_SHIFT_DAY", {})
    for day, assignments in schedule.items():
        req_morn = min_staff.get(day, {}).get("morning", fallback)
        req_eve = min_staff.get(day, {}).get("evening", fallback)
        morning_count = sum(1 for a in assignments if "Morning" in a["shift"])
        evening_count = sum(1 for a in assignments if "Evening" in a["shift"])
        if req_morn > 0 and morning_count < req_morn:
            return False
        if req_eve > 0 and evening_count < req_eve:
            return False
    return True

def generate_schedule():
    """
    Convenience function used by the schedule routes.
    It retrieves config from current_app, loads employees,
    and generates a valid schedule by re-running the generator if needed.
    """
    from flask import current_app
    from .models import Employee
    config = current_app.config
    all_emps = Employee.query.all()
    scheduler_obj = Scheduler(config)
    max_attempts = config.get("MAX_SCHEDULE_ATTEMPTS", 100)
    for attempt in range(max_attempts):
        sched = scheduler_obj.generate_schedule(all_emps)
        if is_valid_schedule(sched, config):
            print(f"Valid schedule found on attempt {attempt+1}")
            return sched
    print(f"Returning last schedule after {max_attempts} attempts.")
    return sched
