# settings/routes.py

from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app

settings_bp = Blueprint('settings', __name__, template_folder='templates')

@settings_bp.route('/', methods=['GET','POST'])
def settings_view():
    days_list = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]

    if request.method == 'POST':
        # Workweek
        workweek_str = request.form.get('workweek','7')
        if workweek_str in ['6','7']:
            current_app.config['WEEK_WORKING_DAYS'] = int(workweek_str)
            msg = f"Workweek updated to {workweek_str} days."
        else:
            msg = "Invalid workweek selection."

        # Per-day min staff
        min_staff_day = {}
        fallback = current_app.config['MIN_STAFF_PER_SHIFT']
        for d in days_list:
            try:
                m_val = int(request.form.get(f"min_morning_{d}", str(fallback)))
                e_val = int(request.form.get(f"min_evening_{d}", str(fallback)))
            except ValueError:
                m_val, e_val = fallback, fallback
            min_staff_day[d] = {"morning": m_val, "evening": e_val}

        current_app.config['MIN_STAFF_PER_SHIFT_DAY'] = min_staff_day

        flash(msg)
        return redirect(url_for('settings.settings_view'))

    workweek_val = current_app.config.get('WEEK_WORKING_DAYS', 7)
    min_staff_data = current_app.config.get('MIN_STAFF_PER_SHIFT_DAY', {})
    return render_template('settings.html',
                           workweek=workweek_val,
                           min_staff_day=min_staff_data)
