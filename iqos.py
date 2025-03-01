from flask import Flask, render_template, request, redirect, url_for, flash, send_file
from flask_sqlalchemy import SQLAlchemy
import enum
from collections import defaultdict
import pandas as pd
import random
import json
from sqlalchemy.types import TypeDecorator, TEXT

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///store_scheduler.db'
app.config['SECRET_KEY'] = 'your-secret-key'
# Workweek length: set to 6 (store closed on Sunday) or 7 (all week open)
app.config['WEEK_WORKING_DAYS'] = 6
# Minimum staffing per shift per day (if not overridden via settings)
app.config['MIN_STAFF_PER_SHIFT'] = 3
app.config['MIN_STAFF_PER_SHIFT_DAY'] = {
    "Monday": {"morning": 3, "evening": 3},
    "Tuesday": {"morning": 3, "evening": 3},
    "Wednesday": {"morning": 3, "evening": 3},
    "Thursday": {"morning": 3, "evening": 3},
    "Friday": {"morning": 3, "evening": 3},
    "Saturday": {"morning": 3, "evening": 3},
    "Sunday": {"morning": 0, "evening": 0}  # In a 6-day week, Sunday is off.
}

db = SQLAlchemy(app)

# --- Custom JSON Types for lists/dicts ---
class SafeJSONList(TypeDecorator):
    impl = TEXT
    cache_ok = True
    def process_bind_param(self, value, dialect):
        if value is None:
            return json.dumps([])
        return json.dumps(value)
    def process_result_value(self, value, dialect):
        if not value or value.strip() == "":
            return []
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return []

class SafeJSONDict(TypeDecorator):
    impl = TEXT
    cache_ok = True
    def process_bind_param(self, value, dialect):
        if value is None:
            return json.dumps({})
        return json.dumps(value)
    def process_result_value(self, value, dialect):
        if not value or value.strip() == "":
            return {}
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return {}

# --- Enum for weekdays (for reference) ---
class WeekdayEnum(enum.Enum):
    Monday = 'Monday'
    Tuesday = 'Tuesday'
    Wednesday = 'Wednesday'
    Thursday = 'Thursday'
    Friday = 'Friday'
    Saturday = 'Saturday'
    Sunday = 'Sunday'

# --- Employee Model ---
class Employee(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    shift_type = db.Column(db.String(10), nullable=False)  # "8-hour" or "6-hour"
    preferred_day_off = db.Column(SafeJSONList, nullable=True, default=list)
    manual_days_off = db.Column(SafeJSONList, nullable=True, default=list)
    shift_requests = db.Column(SafeJSONDict, nullable=True, default=dict)

def generate_schedule():
    """
    Generates the weekly schedule.
    
    Rules (applied in order of priority):
      1. Minimum staffing enforcement: Each day must have at least the configured number
         of employees on the morning and evening shifts.
      2. Off-day assignment for 8‑hour employees:
           - In a 6‑day workweek, Sunday is forced off; plus, if the employee hasn't chosen at least one off day,
             an extra off is assigned (from Monday–Saturday).
           - In a 7‑day workweek, the employee must have 2 off days; if not provided, extra off days are added dynamically.
         For 6‑hour employees in a 7‑day week, if no off is chosen, default off is Sunday.
         Manual off days (set by the manager) are “hard” and never overridden.
      3. Working shift assignment:
           - If an explicit shift request (Morning/Evening) exists for a day, that is used.
           - Otherwise, employees are split (alphabetically) into two groups.
      4. Finally, if after assignments a day’s working (non‑off) count for morning or evening is below the minimum,
         assignments that are off (but not manual off or store closed) are overridden to working shifts.
    """
    employees = Employee.query.all()

    # Calculate default off days for 8‑hour employees.
    default_off = {}
    if app.config.get('WEEK_WORKING_DAYS', 6) == 6:
        candidate_days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
        for emp in employees:
            if emp.shift_type == "8-hour":
                explicit = emp.preferred_day_off or []
                off = explicit.copy()
                if "Sunday" not in off:
                    off.append("Sunday")
                # If no preferred off provided, add one extra from candidate days
                if len(explicit) < 1:
                    extra = [d for d in candidate_days if d not in off]
                    if extra:
                        off.append(random.choice(extra))
                default_off[emp.name] = off
    else:
        candidate_days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        for emp in employees:
            if emp.shift_type == "8-hour":
                explicit = emp.preferred_day_off or []
                off = explicit.copy()
                while len(off) < 2:
                    for d in candidate_days:
                        if d not in off:
                            off.append(d)
                            break
                default_off[emp.name] = off

    # For 6‑hour employees: if no off provided, default off = ["Sunday"] in a 7‑day week.
    for emp in employees:
        if emp.shift_type == "6-hour":
            if not (emp.preferred_day_off or (emp.manual_days_off or [])):
                if app.config.get('WEEK_WORKING_DAYS', 6) == 7:
                    default_off[emp.name] = ["Sunday"]
                else:
                    default_off[emp.name] = []

    days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    schedule = {day: [] for day in days}
    employee_history = {}

    # --- Process 8‑hour employees ---
    employees_8 = [e for e in employees if e.shift_type == "8-hour"]
    for day in days:
        working_8 = []
        for emp in employees_8:
            # Compute effective off days: union of preferred and manual off days
            chosen_off = (emp.preferred_day_off or []) + (emp.manual_days_off or [])
            if app.config.get('WEEK_WORKING_DAYS', 6) == 6:
                if "Sunday" not in chosen_off:
                    chosen_off.append("Sunday")
                if len(emp.preferred_day_off or []) < 1:
                    chosen_off = default_off.get(emp.name, chosen_off)
            elif not chosen_off and emp.name in default_off:
                chosen_off = default_off[emp.name]
            if day in chosen_off:
                # Mark off day as "Manual Day Off" if it's in manual_days_off; otherwise "Preferred Day Off"
                label = "Manual Day Off" if (emp.manual_days_off or []) and (day in emp.manual_days_off) else "Preferred Day Off"
                schedule[day].append({"employee": emp.name, "shift": label, "explicit": False})
            else:
                working_8.append(emp)
        working_8.sort(key=lambda x: x.name)
        n8 = len(working_8)
        # For 8‑hour employees, they should work (contract) 5 days minus manual off days.
        def contract_limit(emp):
            return 5 - len(emp.manual_days_off or [])
        contract = {emp.name: contract_limit(emp) for emp in working_8}
        for i, emp in enumerate(working_8):
            req = (emp.shift_requests or {}).get(day)
            if req in ["Morning", "Evening"]:
                if req == "Morning":
                    candidate = "Morning (08:30–16:30)"
                    alternate = "Evening (13:30–21:30)"
                else:
                    candidate = "Evening (13:30–21:30)"
                    alternate = "Morning (08:30–16:30)"
                explicit_flag = True
            else:
                explicit_flag = False
                if i < n8 / 2:
                    candidate = "Morning (08:30–16:30)"
                    alternate = "Evening (13:30–21:30)"
                else:
                    candidate = "Evening (13:30–21:30)"
                    alternate = "Morning (08:30–16:30)"
                # Avoid more than 3 consecutive same shifts:
                if len(employee_history.get(emp.name, [])) >= 3 and employee_history[emp.name][-3:] == [candidate] * 3:
                    candidate = alternate
                # Respect contract limit (5 days minus manual off)
                if employee_history.get(emp.name, []).count(candidate) >= contract.get(emp.name, 5):
                    if employee_history.get(emp.name, []).count(alternate) < contract.get(emp.name, 5):
                        candidate = alternate
            schedule[day].append({"employee": emp.name, "shift": candidate, "explicit": explicit_flag})
            employee_history.setdefault(emp.name, []).append(candidate)

    # --- Process 6‑hour employees ---
    employees_6 = [e for e in employees if e.shift_type == "6-hour"]
    working_days_for_6 = days if app.config.get('WEEK_WORKING_DAYS', 6) == 7 else [d for d in days if d != "Sunday"]
    for day in days:
        if day in working_days_for_6:
            working_6 = []
            for emp in employees_6:
                chosen_off = (emp.preferred_day_off or []) + (emp.manual_days_off or [])
                if not chosen_off and emp.name in default_off:
                    chosen_off = default_off[emp.name]
                if day in chosen_off:
                    label = "Manual Day Off" if (emp.manual_days_off or []) and (day in emp.manual_days_off) else "Preferred Day Off"
                    schedule[day].append({"employee": emp.name, "shift": label, "explicit": False})
                else:
                    working_6.append(emp)
            working_6.sort(key=lambda x: x.name)
            n6 = len(working_6)
            for i, emp in enumerate(working_6):
                req = (emp.shift_requests or {}).get(day)
                if req in ["Morning", "Evening"]:
                    if req == "Morning":
                        candidate = "Morning (09:00–15:00)"
                        alternate = "Evening (15:00–21:00)"
                    else:
                        candidate = "Evening (15:00–21:00)"
                        alternate = "Morning (09:00–15:00)"
                    explicit_flag = True
                else:
                    explicit_flag = False
                    if i < n6 / 2:
                        candidate = "Morning (09:00–15:00)"
                        alternate = "Evening (15:00–21:00)"
                    else:
                        candidate = "Evening (15:00–21:00)"
                        alternate = "Morning (09:00–15:00)"
                    if len(employee_history.get(emp.name, [])) >= 3 and employee_history[emp.name][-3:] == [candidate] * 3:
                        candidate = alternate
                    if employee_history.get(emp.name, []).count(candidate) >= 6:
                        if employee_history.get(emp.name, []).count(alternate) < 6:
                            candidate = alternate
                        else:
                            candidate = candidate if employee_history.get(emp.name, []).count(candidate) <= employee_history.get(emp.name, []).count(alternate) else alternate
                schedule[day].append({"employee": emp.name, "shift": candidate, "explicit": explicit_flag})
                employee_history.setdefault(emp.name, []).append(candidate)
        # For 6‑hour employees in a 6‑day week, mark Sunday as "Store Closed"
        if app.config.get('WEEK_WORKING_DAYS', 6) == 6:
            for emp in employees:
                if emp.shift_type == "6-hour" and not any(a["employee"] == emp.name for a in schedule["Sunday"]):
                    schedule["Sunday"].append({"employee": emp.name, "shift": "Store Closed", "explicit": False})

        # --- Minimum Staffing Enforcement per day ---
        min_morning = day_min.get(day, {}).get("morning", fallback_min_staff)
        min_evening = day_min.get(day, {}).get("evening", fallback_min_staff)
        working_assignments = [a for a in schedule[day] if ("Morning" in a["shift"] or "Evening" in a["shift"])]
        morning_count = sum(1 for a in working_assignments if "Morning" in a["shift"])
        evening_count = sum(1 for a in working_assignments if "Evening" in a["shift"])
        # Enforce minimum staffing for morning shifts:
        while morning_count < min_morning:
            for assignment in schedule[day]:
                if "Morning" not in assignment["shift"] and assignment["shift"] not in ["Manual Day Off", "Store Closed", "Preferred Day Off", "Assigned Day Off"]:
                    emp_obj = next((e for e in employees if e.name == assignment["employee"]), None)
                    if emp_obj:
                        if emp_obj.shift_type == "8-hour":
                            assignment["shift"] = "Morning (08:30–16:30)"
                        else:
                            assignment["shift"] = "Morning (09:00–15:00)"
                        morning_count += 1
                        break
            else:
                break
        # Enforce minimum staffing for evening shifts:
        while evening_count < min_evening:
            for assignment in schedule[day]:
                if "Evening" not in assignment["shift"] and assignment["shift"] not in ["Manual Day Off", "Store Closed", "Preferred Day Off", "Assigned Day Off"]:
                    emp_obj = next((e for e in employees if e.name == assignment["employee"]), None)
                    if emp_obj:
                        if emp_obj.shift_type == "8-hour":
                            assignment["shift"] = "Evening (13:30–21:30)"
                        else:
                            assignment["shift"] = "Evening (15:00–21:00)"
                        evening_count += 1
                        break
            else:
                break

    # Post-Processing for 8‑hour employees in a 6‑day week: Ensure each gets 2 off days.
    if app.config.get('WEEK_WORKING_DAYS', 6) == 6:
        for emp in [e for e in employees if e.shift_type == "8-hour"]:
            off_count = 0
            for day in days:
                for a in schedule[day]:
                    if a["employee"] == emp.name and a["shift"] in ["Manual Day Off", "Preferred Day Off", "Assigned Day Off"]:
                        off_count += 1
            if off_count < 2:
                needed = 2 - off_count
                for day in days:
                    if day == "Sunday":
                        continue
                    for a in schedule[day]:
                        if a["employee"] == emp.name and a["shift"] not in ["Manual Day Off", "Preferred Day Off", "Assigned Day Off"]:
                            a["shift"] = "Assigned Day Off"
                            needed -= 1
                            if needed == 0:
                                break
                    if needed == 0:
                        break

    return schedule

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        name = request.form['name']
        shift_type = request.form['shift_type']
        # Now preferred_day_off is stored as a list.
        preferred_day_off = request.form.getlist('preferred_day_off') or []
        manual_days_off = request.form.getlist('manual_days_off') or []
        days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
        shift_requests = {}
        for day in days:
            req = request.form.get("shift_request_" + day)
            if req and req != "No Request":
                shift_requests[day] = req
        new_emp = Employee(
            name=name,
            shift_type=shift_type,
            preferred_day_off=preferred_day_off,
            manual_days_off=manual_days_off if manual_days_off else [],
            shift_requests=shift_requests
        )
        db.session.add(new_emp)
        db.session.commit()
        flash('Employee added successfully!')
        return redirect(url_for('index'))
    
    employees = Employee.query.all()
    return render_template('index.html', employees=employees)

@app.route('/edit/<int:employee_id>', methods=['GET', 'POST'])
def edit_employee(employee_id):
    emp = Employee.query.get(employee_id)
    if not emp:
        flash("Employee not found.")
        return redirect(url_for('index'))
    
    if request.method == 'POST':
        emp.name = request.form['name']
        emp.shift_type = request.form['shift_type']
        preferred_day_off = request.form.getlist('preferred_day_off') or []
        emp.preferred_day_off = preferred_day_off
        manual_days_off = request.form.getlist('manual_days_off') or []
        emp.manual_days_off = manual_days_off
        days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
        shift_requests = {}
        for day in days:
            req = request.form.get("shift_request_" + day)
            if req and req != "No Request":
                shift_requests[day] = req
        emp.shift_requests = shift_requests
        db.session.commit()
        flash('Employee updated successfully!')
        return redirect(url_for('index'))
    
    return render_template("edit.html", employee=emp)

@app.route('/delete/<int:employee_id>', methods=['POST'])
def delete_employee(employee_id):
    emp = Employee.query.get(employee_id)
    if emp:
        db.session.delete(emp)
        db.session.commit()
        flash(f'Employee {emp.name} deleted successfully!')
    else:
        flash('Employee not found.')
    return redirect(url_for('index'))

@app.route('/settings', methods=['GET', 'POST'])
def settings():
    if request.method == 'POST':
        workweek = request.form.get('workweek')
        # Update per-day minimum staffing from form values.
        min_staff_day = {}
        days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
        for day in days:
            try:
                min_morning = int(request.form.get(f"min_morning_{day}", "3"))
                min_evening = int(request.form.get(f"min_evening_{day}", "3"))
            except ValueError:
                min_morning, min_evening = 3, 3
            min_staff_day[day] = {"morning": min_morning, "evening": min_evening}
        app.config['MIN_STAFF_PER_SHIFT_DAY'] = min_staff_day

        if workweek in ['6', '7']:
            app.config['WEEK_WORKING_DAYS'] = int(workweek)
            flash_msg = f'Workweek updated to {workweek} days.'
        else:
            flash_msg = 'Invalid selection.'
        flash(flash_msg)
        return redirect(url_for('settings'))
    return render_template('settings.html', 
                           workweek=app.config.get('WEEK_WORKING_DAYS'),
                           min_staff_day=app.config.get('MIN_STAFF_PER_SHIFT_DAY'))

@app.route('/schedule')
def schedule_view():
    schedule = generate_schedule()
    # Transform day-based schedule into an employee-based landscape view.
    days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
    employee_schedule = {}
    for day in days:
        for assignment in schedule[day]:
            emp = assignment['employee']
            shift = assignment['shift']
            # Append a star if the shift was explicitly requested.
            if assignment.get("explicit") and ("Morning" in shift or "Evening" in shift):
                shift += " ★"
            if emp not in employee_schedule:
                employee_schedule[emp] = {}
            employee_schedule[emp][day] = shift
    employees = sorted(employee_schedule.keys())
    return render_template('schedule.html', employee_schedule=employee_schedule, days=days, employees=employees)

@app.route('/download')
def download():
    schedule = generate_schedule()
    file_path = "schedule.csv"
    schedule_output = []
    for day, assignments in schedule.items():
        for assign in assignments:
            schedule_output.append({"Day": day, "Employee": assign["employee"], "Shift": assign["shift"]})
    df = pd.DataFrame(schedule_output)
    df.to_csv(file_path, index=False)
    return send_file(file_path, as_attachment=True)

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)
