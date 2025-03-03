# schedule/routes.py

from flask import Blueprint, render_template, send_file, current_app
import pandas as pd

from ..models import Employee
from ..scheduler import generate_schedule

schedule_bp = Blueprint('schedule', __name__, template_folder='templates')


@schedule_bp.route('/')
def schedule_view():
    """
    Renders the weekly schedule table.
    """
    final_schedule = generate_schedule()  # uses the DB + config from current_app
    days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    employee_schedule = {}

    # Build a dictionary: employee -> day -> shift
    for d in days:
        for entry in final_schedule[d]:
            emp = entry["employee"]
            shift = entry["shift"]
            # If shift was explicitly requested, put a star
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
    """
    Creates a CSV file of the current schedule and sends it as an attachment.
    """
    final_schedule = generate_schedule()
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
    """
    Writes out a detailed TXT file for debugging that includes:
      1) The store's config (WEEK_WORKING_DAYS, per-day min staff).
      2) The final day-by-day schedule for all employees.
    """
    final_schedule = generate_schedule()

    config = current_app.config
    week_working_days = config.get("WEEK_WORKING_DAYS", 7)
    fallback_min_staff = config.get("MIN_STAFF_PER_SHIFT", 3)
    min_staff_day = config.get("MIN_STAFF_PER_SHIFT_DAY", {})

    txt_file = "schedule.txt"
    with open(txt_file, "w", encoding="utf-8") as f:
        # 1) Write config info
        f.write("=== DEBUG CONFIG SETTINGS ===\n")
        f.write(f"WEEK_WORKING_DAYS: {week_working_days}\n")
        f.write(f"Default MIN_STAFF_PER_SHIFT (fallback): {fallback_min_staff}\n")
        f.write("MIN_STAFF_PER_SHIFT_DAY:\n")
        for d, staff_conf in min_staff_day.items():
            m = staff_conf.get("morning", fallback_min_staff)
            e = staff_conf.get("evening", fallback_min_staff)
            f.write(f"  {d}: morning={m}, evening={e}\n")
        f.write("\n")

        # 2) Write final schedule day-by-day
        for day, assignments in final_schedule.items():
            staff_conf = min_staff_day.get(day, {})
            day_morn = staff_conf.get("morning", fallback_min_staff)
            day_even = staff_conf.get("evening", fallback_min_staff)
            f.write(f"=== {day} (MinStaff: morning={day_morn}, evening={day_even}) ===\n")
            for assign in assignments:
                f.write(f"{assign['employee']}: {assign['shift']}\n")
            f.write("\n")

    return send_file(txt_file, as_attachment=True)
