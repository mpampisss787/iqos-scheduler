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
# Workweek length: set to 6 (store closed on Sunday) or 7 (full week)
app.config['WEEK_WORKING_DAYS'] = 6
# Default overall minimum (fallback) for each shift if not set per day
app.config['MIN_STAFF_PER_SHIFT'] = 3
# New: Minimum staff per shift per day. Each day has separate minimums for morning and evening.
app.config['MIN_STAFF_PER_SHIFT_DAY'] = {
    "Monday": {"morning": 3, "evening": 3},
    "Tuesday": {"morning": 3, "evening": 3},
    "Wednesday": {"morning": 3, "evening": 3},
    "Thursday": {"morning": 3, "evening": 3},
    "Friday": {"morning": 3, "evening": 3},
    "Saturday": {"morning": 3, "evening": 3},
    "Sunday": {"morning": 0, "evening": 0}  # Typically not used for a 6-day week
}

db = SQLAlchemy(app)

# --- Custom JSON Types ---
class SafeJSONList(TypeDecorator):
    impl = TEXT
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return json.dumps([])
        return json.dumps(value)

    def process_result_value(self, value, dialect):
        if value is None or value.strip() == "":
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
        if value is None or value.strip() == "":
            return {}
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return {}

# --- Enum for weekdays ---
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
    # preferred_day_off: chosen by the employee for dynamic off‐day assignment.
    # For 8‑hour employees in a 7‑day week they may choose up to 2 off days.
    preferred_day_off = db.Column(SafeJSONList, nullable=True, default=list)
    # manual_days_off: hard off days (e.g. hospitalization) set by the manager.
    manual_days_off = db.Column(SafeJSONList, nullable=True, default=list)
    shift_requests = db.Column(SafeJSONDict, nullable=True, default=dict)

def generate_schedule():
    """
    Generate the weekly schedule using these rules:
      - 8‑hour employees work 5 days in a 7‑day cycle.
        In a 6‑day workweek (store closed on Sunday), the schedule is built on a 7‑day cycle:
          • Sunday is forced off.
          • If the employee’s preferred_day_off (ignoring manual off days) contains fewer than 1 extra off day,
            one extra off day is added dynamically so that they have 2 off days total.
      - 6‑hour employees work up to 6 days.
      - The store requires that on each day there are at least a specified minimum number of employees on
        the morning and evening shifts as per MIN_STAFF_PER_SHIFT_DAY.
      - Valid shift requests (Morning/Evening) are honored if the employee isn’t off.
      - Off-day assignment:
            • For 6‑hour employees in a 7‑day week: if no off is chosen, default off = ["Sunday"].
            • For 8‑hour employees:
                 - In a 6‑day week: force Sunday off; then, if the preferred_day_off has fewer than 1 extra day,
                   add one extra from Monday–Saturday.
                 - In a 7‑day week: ensure at least 2 off days.
      - Manual off days are applied as “Manual Day Off” and reduce the contracted workdays.
    """
    employees = Employee.query.all()

    # Pre-calculate default off days for 8‑hour employees.
    default_off = {}
    if app.config.get('WEEK_WORKING_DAYS', 6) == 6:
        candidate_days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
        for emp in employees:
            if emp.shift_type == '8-hour':
                preferred = emp.preferred_day_off or []
                off = preferred.copy()
                if "Sunday" not in off:
                    off.append("Sunday")
                # If no extra preferred off day chosen, add one extra from candidate_days.
                if len(preferred) < 1:
                    for d in candidate_days:
                        if d not in off:
                            off.append(d)
                            break
                default_off[emp.name] = off
    else:
        candidate_days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        for emp in employees:
            if emp.shift_type == '8-hour':
                preferred = emp.preferred_day_off or []
                off = preferred.copy()
                while len(off) < 2:
                    for d in candidate_days:
                        if d not in off:
                            off.append(d)
                            break
                default_off[emp.name] = off

    # For 6‑hour employees.
    for emp in employees:
        if emp.shift_type == '6-hour':
            if not (emp.preferred_day_off or (emp.manual_days_off and len(emp.manual_days_off) > 0)):
                if app.config.get('WEEK_WORKING_DAYS', 6) == 7:
                    default_off[emp.name] = ["Sunday"]
                else:
                    default_off[emp.name] = []

    days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
    schedule = {day: [] for day in days}
    employee_history = {}
    # Save fallback minimum staff
    fallback_min_staff = app.config.get('MIN_STAFF_PER_SHIFT', 3)
    day_min = app.config.get('MIN_STAFF_PER_SHIFT_DAY', {})

    # For 8‑hour employees, contracted workdays = 5 (minus any manual off days)
    def get_contract_workdays(emp):
        base = 5
        manual = len(emp.manual_days_off or [])
        return base - manual

    # Process 8‑hour employees.
    employees_8 = [e for e in employees if e.shift_type == '8-hour']
    for day in days:
        working_8 = []
        for emp in employees_8:
            chosen_off = emp.preferred_day_off or []
            if app.config.get('WEEK_WORKING_DAYS', 6) == 6:
                if "Sunday" not in chosen_off:
                    chosen_off.append("Sunday")
                if len(emp.preferred_day_off or []) < 1:
                    chosen_off = default_off.get(emp.name, chosen_off)
            elif not chosen_off and emp.name in default_off:
                chosen_off = default_off[emp.name]
            if emp.manual_days_off:
                chosen_off = list(set(chosen_off) | set(emp.manual_days_off))
            if day in chosen_off:
                label = "Manual Day Off" if emp.manual_days_off and day in emp.manual_days_off else "Preferred Day Off"
                schedule[day].append({'employee': emp.name, 'shift': label})
            else:
                working_8.append(emp)
        working_8.sort(key=lambda x: x.name)
        n8 = len(working_8)
        contract = {emp.name: get_contract_workdays(emp) for emp in working_8}
        for i, emp in enumerate(working_8):
            req = (emp.shift_requests or {}).get(day)
            if req in ["Morning", "Evening"]:
                if req == "Morning":
                    candidate = 'Morning (08:30–16:30)'
                    alternate = 'Evening (13:30–21:30)'
                else:
                    candidate = 'Evening (13:30–21:30)'
                    alternate = 'Morning (08:30–16:30)'
            else:
                if i < n8 / 2:
                    candidate = 'Morning (08:30–16:30)'
                    alternate = 'Evening (13:30–21:30)'
                else:
                    candidate = 'Evening (13:30–21:30)'
                    alternate = 'Morning (08:30–16:30)'
            history = employee_history.get(emp.name, [])
            if len(history) >= 3 and history[-3:] == [candidate] * 3:
                candidate = alternate
            if history.count(candidate) >= contract.get(emp.name, 5):
                if history.count(alternate) < contract.get(emp.name, 5):
                    candidate = alternate
                else:
                    candidate = candidate if history.count(candidate) <= history.count(alternate) else alternate
            schedule[day].append({'employee': emp.name, 'shift': candidate})
            employee_history.setdefault(emp.name, []).append(candidate)

    # Process 6‑hour employees.
    employees_6 = [e for e in employees if e.shift_type == '6-hour']
    if app.config.get('WEEK_WORKING_DAYS', 6) == 7:
        working_days_for_6 = days
    else:
        working_days_for_6 = [d for d in days if d != 'Sunday']
    for day in days:
        if day in working_days_for_6:
            working_6 = []
            for emp in employees_6:
                chosen_off = emp.preferred_day_off or []
                if not chosen_off and emp.name in default_off:
                    chosen_off = default_off[emp.name]
                if emp.manual_days_off:
                    chosen_off = list(set(chosen_off) | set(emp.manual_days_off))
                if day in chosen_off:
                    label = "Manual Day Off" if emp.manual_days_off and day in emp.manual_days_off else "Preferred Day Off"
                    schedule[day].append({'employee': emp.name, 'shift': label})
                else:
                    working_6.append(emp)
            working_6.sort(key=lambda x: x.name)
            n6 = len(working_6)
            for i, emp in enumerate(working_6):
                req = (emp.shift_requests or {}).get(day)
                if req in ["Morning", "Evening"]:
                    if req == "Morning":
                        candidate = 'Morning (09:00–15:00)'
                        alternate = 'Evening (15:00–21:00)'
                    else:
                        candidate = 'Evening (15:00–21:00)'
                        alternate = 'Morning (09:00–15:00)'
                else:
                    if i < n6 / 2:
                        candidate = 'Morning (09:00–15:00)'
                        alternate = 'Evening (15:00–21:00)'
                    else:
                        candidate = 'Evening (15:00–21:00)'
                        alternate = 'Morning (09:00–15:00)'
                history = employee_history.get(emp.name, [])
                if len(history) >= 3 and history[-3:] == [candidate] * 3:
                    candidate = alternate
                if history.count(candidate) >= 6:
                    if history.count(alternate) < 6:
                        candidate = alternate
                    else:
                        candidate = candidate if history.count(candidate) <= history.count(alternate) else alternate
                schedule[day].append({'employee': emp.name, 'shift': candidate})
                employee_history.setdefault(emp.name, []).append(candidate)
                
        # For 6‑hour employees in a 6‑day week, mark Sunday as "Store Closed"
        if app.config.get('WEEK_WORKING_DAYS', 6) == 6:
            for emp in [e for e in employees if e.shift_type == '6-hour']:
                if not any(a['employee'] == emp.name for a in schedule["Sunday"]):
                    schedule["Sunday"].append({'employee': emp.name, 'shift': "Store Closed"})

        # Minimum Staffing Enforcement per day:
        # For each day, get the per-day minimums.
        min_morning = day_min.get(day, {}).get('morning', fallback_min_staff)
        min_evening = day_min.get(day, {}).get('evening', fallback_min_staff)
        morning_count = sum(1 for a in schedule[day] if "Morning" in a['shift'])
        evening_count = sum(1 for a in schedule[day] if "Evening" in a['shift'])
        while morning_count < min_morning:
            for assignment in schedule[day]:
                if assignment['shift'] in ["Preferred Day Off", "Assigned Day Off"]:
                    emp_obj = next((e for e in employees if e.name == assignment['employee']), None)
                    if emp_obj and not (emp_obj.manual_days_off and day in emp_obj.manual_days_off):
                        if emp_obj.shift_type == '8-hour':
                            new_shift = 'Morning (08:30–16:30)'
                        else:
                            new_shift = 'Morning (09:00–15:00)'
                        assignment['shift'] = new_shift
                        morning_count += 1
                        break
            else:
                break
        while evening_count < min_evening:
            for assignment in schedule[day]:
                if assignment['shift'] in ["Preferred Day Off", "Assigned Day Off"]:
                    emp_obj = next((e for e in employees if e.name == assignment['employee']), None)
                    if emp_obj and not (emp_obj.manual_days_off and day in emp_obj.manual_days_off):
                        if emp_obj.shift_type == '8-hour':
                            new_shift = 'Evening (13:30–21:30)'
                        else:
                            new_shift = 'Evening (15:00–21:00)'
                        assignment['shift'] = new_shift
                        evening_count += 1
                        break
            else:
                break

    # Post-Processing for 8‑hour employees in a 6‑day week: Ensure each gets at least 2 off days.
    if app.config.get('WEEK_WORKING_DAYS', 6) == 6:
        for emp in employees_8:
            off_count = 0
            for day in days:
                for a in schedule[day]:
                    if a['employee'] == emp.name and a['shift'] in ["Manual Day Off", "Preferred Day Off", "Assigned Day Off"]:
                        off_count += 1
            if off_count < 2:
                needed = 2 - off_count
                for day in days:
                    if day == "Sunday":
                        continue
                    for a in schedule[day]:
                        if a['employee'] == emp.name and a['shift'] not in ["Manual Day Off", "Preferred Day Off", "Assigned Day Off"]:
                            a['shift'] = "Assigned Day Off"
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
            manual_days_off=manual_days_off,
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
            flash_msg = 'Invalid workweek selection.'
        flash(flash_msg)
        return redirect(url_for('settings'))
    return render_template('settings.html', 
                           workweek=app.config.get('WEEK_WORKING_DAYS'),
                           min_staff_day=app.config.get('MIN_STAFF_PER_SHIFT_DAY'))

@app.route('/schedule')
def schedule_view():
    schedule = generate_schedule()
    days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
    employee_schedule = {}
    for day in days:
        for assignment in schedule[day]:
            emp = assignment['employee']
            shift = assignment['shift']
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

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(debug=True)
