from flask import Flask, render_template, request, redirect, url_for, flash, send_file
from flask_sqlalchemy import SQLAlchemy
import enum
from collections import defaultdict
import pandas as pd
import random

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///store_scheduler.db'
app.config['SECRET_KEY'] = 'your-secret-key'
# Set workweek length: 6 for a closed Sunday, 7 for open all week.
app.config['WEEK_WORKING_DAYS'] = 7

db = SQLAlchemy(app)

# Optional: Enum for weekdays
class WeekdayEnum(enum.Enum):
    Monday = 'Monday'
    Tuesday = 'Tuesday'
    Wednesday = 'Wednesday'
    Thursday = 'Thursday'
    Friday = 'Friday'
    Saturday = 'Saturday'
    Sunday = 'Sunday'

class Employee(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    shift_type = db.Column(db.String(10), nullable=False)  # "8-hour" or "6-hour"
    # Now allowing multiple preferred days off (as a list)
    preferred_day_off = db.Column(db.JSON, nullable=True, default=list)
    # manual_days_off stored as a JSON list, e.g. ["Monday", "Wednesday"]
    manual_days_off = db.Column(db.JSON, nullable=True, default=list)
    # shift_requests stored as JSON dictionary, e.g. {"Monday": "Morning", "Friday": "Evening"}
    shift_requests = db.Column(db.JSON, nullable=True, default=dict)

def generate_schedule():
    """
    Build the weekly schedule under these rules:
      - No employee works more than 3 consecutive days with the same shift.
      - Weekly limits:
            * 8‑hour employees work at most 5 days (40 hours).
            * 6‑hour employees work at most 6 days (36 hours).
      - The store requires at least 3 employees on the morning shift and 3 on the evening shift each day.
      - If an employee submits a valid shift request for a day (and isn’t off), that request is used.
      - Off-day assignment:
            • For 6‑hour employees in a 7‑day week: if no off is chosen, default off = ["Sunday"].
            • For 8‑hour employees:
                 - In a 6‑day week: they work 5 days; Sunday is forced off.
                 - In a 7‑day week: they must have 2 off days. If they haven’t chosen 2 preferred off days, additional off days are assigned dynamically.
      - In the displayed schedule, an off day is marked as "Preferred Day Off" if explicitly chosen,
        or "Assigned Day Off" if set automatically.
    """
    employees = Employee.query.all()

    # Pre-calculate default off days for 8‑hour employees.
    default_off = {}
    if app.config.get('WEEK_WORKING_DAYS', 6) == 6:
        candidate_days = [d for d in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]]
        for emp in employees:
            if emp.shift_type == '8-hour':
                explicit = []
                if emp.preferred_day_off:
                    explicit.extend(emp.preferred_day_off)
                if emp.manual_days_off:
                    explicit.extend(emp.manual_days_off)
                effective = explicit.copy()
                # Force Sunday off in a 6-day workweek.
                if "Sunday" not in effective:
                    effective.append("Sunday")
                default_off[emp.name] = effective
    else:
        candidate_days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        for emp in employees:
            if emp.shift_type == '8-hour':
                explicit = []
                if emp.preferred_day_off:
                    explicit.extend(emp.preferred_day_off)
                if emp.manual_days_off:
                    explicit.extend(emp.manual_days_off)
                default_off[emp.name] = explicit.copy()
                # If fewer than 2 off days are specified, assign additional off days dynamically.
                while len(default_off[emp.name]) < 2:
                    for d in candidate_days:
                        if d not in default_off[emp.name]:
                            default_off[emp.name].append(d)
                            break

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

    # Process 8‑hour employees.
    employees_8 = [e for e in employees if e.shift_type == '8-hour']
    for day in days:
        working_8 = []
        for emp in employees_8:
            # Build list of explicitly chosen off days.
            chosen_off = []
            if emp.preferred_day_off:
                chosen_off.extend(emp.preferred_day_off)
            if emp.manual_days_off:
                chosen_off.extend(emp.manual_days_off)
            # For a 6-day week, force Sunday off; for a 7-day week, if none chosen, use defaults.
            if app.config.get('WEEK_WORKING_DAYS', 6) == 6:
                if "Sunday" not in chosen_off:
                    chosen_off.append("Sunday")
            elif not chosen_off and emp.name in default_off:
                chosen_off = default_off[emp.name]
            if day in chosen_off:
                label = "Preferred Day Off" if day in (emp.preferred_day_off if emp.preferred_day_off else []) else "Assigned Day Off"
                schedule[day].append({'employee': emp.name, 'shift': label})
            else:
                working_8.append(emp)
        working_8.sort(key=lambda x: x.name)
        n8 = len(working_8)
        for i, emp in enumerate(working_8):
            req = None
            if emp.shift_requests and day in emp.shift_requests:
                req = emp.shift_requests[day]
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
            if history.count(candidate) >= 5:
                if history.count(alternate) < 5:
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
                chosen_off = []
                if emp.preferred_day_off:
                    chosen_off.extend(emp.preferred_day_off)
                if emp.manual_days_off:
                    chosen_off.extend(emp.manual_days_off)
                if not chosen_off and emp.name in default_off:
                    chosen_off = default_off[emp.name]
                if day in chosen_off:
                    label = "Preferred Day Off" if day in (emp.preferred_day_off if emp.preferred_day_off else []) else "Assigned Day Off"
                    schedule[day].append({'employee': emp.name, 'shift': label})
                else:
                    working_6.append(emp)
            working_6.sort(key=lambda x: x.name)
            n6 = len(working_6)
            for i, emp in enumerate(working_6):
                req = None
                if emp.shift_requests and day in emp.shift_requests:
                    req = emp.shift_requests[day]
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
        # Post-Processing: Enforce minimum staffing.
        working_assignments = [a for a in schedule[day] if "Morning" in a['shift'] or "Evening" in a['shift']]
        morning_count = sum(1 for a in working_assignments if "Morning" in a['shift'])
        evening_count = sum(1 for a in working_assignments if "Evening" in a['shift'])
        if len(working_assignments) >= 6:
            while morning_count < 3:
                for assignment in schedule[day]:
                    if assignment['shift'] in ["Preferred Day Off", "Assigned Day Off"]:
                        emp_obj = next((e for e in employees if e.name == assignment['employee']), None)
                        if emp_obj and not (emp_obj.preferred_day_off or (emp_obj.manual_days_off and day in emp_obj.manual_days_off)):
                            if emp_obj.shift_type == '8-hour':
                                new_shift = 'Morning (08:30–16:30)'
                            else:
                                new_shift = 'Morning (09:00–15:00)'
                            assignment['shift'] = new_shift
                            employee_history.setdefault(emp_obj.name, []).append(new_shift)
                            morning_count += 1
                            break
                else:
                    break
            while evening_count < 3:
                for assignment in schedule[day]:
                    if assignment['shift'] in ["Preferred Day Off", "Assigned Day Off"]:
                        emp_obj = next((e for e in employees if e.name == assignment['employee']), None)
                        if emp_obj and not (emp_obj.preferred_day_off or (emp_obj.manual_days_off and day in emp_obj.manual_days_off)):
                            if emp_obj.shift_type == '8-hour':
                                new_shift = 'Evening (13:30–21:30)'
                            else:
                                new_shift = 'Evening (15:00–21:00)'
                            assignment['shift'] = new_shift
                            employee_history.setdefault(emp_obj.name, []).append(new_shift)
                            evening_count += 1
                            break
                else:
                    break

    return schedule

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        name = request.form['name']
        shift_type = request.form['shift_type']
        # Now preferred_day_off is expected as a list; use getlist.
        preferred_day_off = request.form.getlist('preferred_day_off')
        preferred_day_off = preferred_day_off if preferred_day_off else []
        manual_days_off = request.form.getlist('manual_days_off')
        manual_days_off = manual_days_off if manual_days_off else []
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
        # For editing, get list for preferred_day_off.
        preferred_day_off = request.form.getlist('preferred_day_off')
        emp.preferred_day_off = preferred_day_off if preferred_day_off else []
        manual_days_off = request.form.getlist('manual_days_off')
        emp.manual_days_off = manual_days_off if manual_days_off else []
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
        if workweek in ['6', '7']:
            app.config['WEEK_WORKING_DAYS'] = int(workweek)
            flash(f'Workweek updated to {workweek} days.')
        else:
            flash('Invalid selection.')
        return redirect(url_for('settings'))
    return render_template('settings.html', workweek=app.config.get('WEEK_WORKING_DAYS'))

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
            if emp not in employee_schedule:
                employee_schedule[emp] = {}
            employee_schedule[emp][day] = shift
    employees = sorted(employee_schedule.keys())
    return render_template('schedule.html', employee_schedule=employee_schedule, days=days, employees=employees)

@app.route('/download')
def download():
    """Download the schedule as a CSV file."""
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
