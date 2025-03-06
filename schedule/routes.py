from flask import Blueprint, render_template, send_file, current_app
import pandas as pd

from ..models import Employee
from ..scheduler import generate_schedule

schedule_bp = Blueprint('schedule', __name__, template_folder='templates')

# Cache for the generated schedule so that all routes use the same version.
SCHEDULE_CACHE = None

@schedule_bp.route('/')
def schedule_view():
    global SCHEDULE_CACHE
    final_schedule = generate_schedule()  # uses DB + config from current_app
    SCHEDULE_CACHE = final_schedule      # Cache the generated schedule
    days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    employee_schedule = {}
    # Build a dictionary: employee -> day -> shift
    for d in days:
        for entry in final_schedule[d]:
            emp = entry["employee"]
            shift = entry["shift"]
            # If the shift was explicitly requested, mark it with a star.
            if entry.get("explicit") and ("Morning" in shift or "Evening" in shift):
                shift += " ★"
            if emp not in employee_schedule:
                employee_schedule[emp] = {}
            employee_schedule[emp][d] = shift
    employees = sorted(employee_schedule.keys())
    return render_template(
        'schedule.html',
        employees=employees,
        days=days,
        employee_schedule=employee_schedule
    )

@schedule_bp.route('/download_csv')
def download_csv():
    global SCHEDULE_CACHE
    final_schedule = SCHEDULE_CACHE if SCHEDULE_CACHE is not None else generate_schedule()
    rows = []
    for day, items in final_schedule.items():
        for obj in items:
            rows.append({
                "Day": day,
                "Employee": obj["employee"],
                "Shift": obj["shift"]
            })
    df = pd.DataFrame(rows)
    csv_file = "schedule.csv"
    df.to_csv(csv_file, index=False)
    return send_file(csv_file, as_attachment=True)

@schedule_bp.route('/download_txt')
def download_txt():
    global SCHEDULE_CACHE
    final_schedule = SCHEDULE_CACHE if SCHEDULE_CACHE is not None else generate_schedule()
    all_employees = sorted([emp.name for emp in Employee.query.all()], key=lambda x: x.strip())
    config = current_app.config
    week_working_days = config.get("WEEK_WORKING_DAYS", 7)
    fallback_min_staff = config.get("MIN_STAFF_PER_SHIFT", 3)
    min_staff_day = config.get("MIN_STAFF_PER_SHIFT_DAY", {})
    txt_file = "schedule.txt"
    with open(txt_file, "w", encoding="utf-8") as f:
        f.write("=== DEBUG CONFIG SETTINGS ===\n")
        f.write(f"WEEK_WORKING_DAYS: {week_working_days}\n")
        f.write(f"Default MIN_STAFF_PER_SHIFT (fallback): {fallback_min_staff}\n")
        f.write("MIN_STAFF_PER_SHIFT_DAY:\n")
        for d in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]:
            staff_conf = min_staff_day.get(d, {})
            m = staff_conf.get("morning", fallback_min_staff)
            e = staff_conf.get("evening", fallback_min_staff)
            f.write(f"  {d}: morning={m}, evening={e}\n")
        f.write("\n")
        for day in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]:
            assignments = final_schedule.get(day, [])
            staff_conf = min_staff_day.get(day, {})
            day_morn = staff_conf.get("morning", fallback_min_staff)
            day_even = staff_conf.get("evening", fallback_min_staff)
            f.write(f"=== {day} (MinStaff: morning={day_morn}, evening={day_even}) ===\n")
            day_shifts = {a["employee"]: a["shift"] for a in assignments}
            for emp in all_employees:
                shift = day_shifts.get(emp, "Not Scheduled")
                employee = Employee.query.filter_by(name=emp).first()
                shift_type = employee.shift_type if employee else "Unknown"
                f.write(f"{emp} ({shift_type}): {shift}\n")
            morn_count = sum("Morning" in s for s in day_shifts.values())
            eve_count = sum("Evening" in s for s in day_shifts.values())
            if morn_count < day_morn:
                f.write(f"⚠️ WARNING: Morning understaffed ({morn_count}/{day_morn})\n")
            if eve_count < day_even:
                f.write(f"⚠️ WARNING: Evening understaffed ({eve_count}/{day_even})\n")
            f.write("\n")
    return send_file(txt_file, as_attachment=True)
