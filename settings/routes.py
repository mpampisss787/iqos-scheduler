from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app

settings_bp = Blueprint('settings', __name__, template_folder='templates')

@settings_bp.route('/', methods=['GET','POST'])
def settings_view():
    days_list = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

    if request.method == 'POST':
        # Workweek
        workweek_str = request.form.get('workweek', '7')
        if workweek_str in ['6', '7']:
            current_app.config['WEEK_WORKING_DAYS'] = int(workweek_str)
            msg = f"Workweek updated to {workweek_str} days."
        else:
            msg = "Invalid workweek selection."

        # Per-day minimum staffing
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

        # Maximum consecutive shifts setting
        try:
            max_consecutive = int(request.form.get('max_consecutive', current_app.config.get('MAX_CONSECUTIVE_SHIFTS', 3)))
        except ValueError:
            max_consecutive = current_app.config.get('MAX_CONSECUTIVE_SHIFTS', 3)
        current_app.config['MAX_CONSECUTIVE_SHIFTS'] = max_consecutive

        # New settings for Preferred Overrides:
        lock_pref = request.form.get('lock_preferred', "True")
        current_app.config['LOCK_PREFERRED_OVERRIDES'] = True if lock_pref == "True" else False

        try:
            preferred_threshold = int(request.form.get('preferred_threshold', current_app.config.get('PREFERRED_OVERRIDE_THRESHOLD', 2)))
        except ValueError:
            preferred_threshold = current_app.config.get('PREFERRED_OVERRIDE_THRESHOLD', 2)
        current_app.config['PREFERRED_OVERRIDE_THRESHOLD'] = preferred_threshold

        flash(msg)
        return redirect(url_for('settings.settings_view'))

    workweek_val = current_app.config.get('WEEK_WORKING_DAYS', 7)
    min_staff_data = current_app.config.get('MIN_STAFF_PER_SHIFT_DAY', {})
    max_consecutive = current_app.config.get('MAX_CONSECUTIVE_SHIFTS', 3)
    lock_pref = current_app.config.get('LOCK_PREFERRED_OVERRIDES', True)
    preferred_threshold = current_app.config.get('PREFERRED_OVERRIDE_THRESHOLD', 2)

    return render_template('settings.html',
                           workweek=workweek_val,
                           min_staff_day=min_staff_data,
                           max_consecutive=max_consecutive,
                           lock_preferred_overrides=lock_pref,
                           preferred_override_threshold=preferred_threshold)
