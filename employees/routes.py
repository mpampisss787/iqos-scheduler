# employees/routes.py

from flask import Blueprint, render_template, request, redirect, url_for, flash
from models import db, Employee

employees_bp = Blueprint('employees', __name__, template_folder='templates')

@employees_bp.route('/', methods=['GET','POST'])
def list_or_create():
    if request.method == 'POST':
        name = request.form['name']
        shift_type = request.form['shift_type']

        # single day from dropdown
        pref_day = request.form.get('preferred_day_off', '')
        preferred_days = []
        if pref_day:
            preferred_days = [pref_day]

        # multiple manual days
        manual_days = request.form.getlist('manual_days_off') or []

        # shift requests
        shift_requests = {}
        for day in ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]:
            val = request.form.get(f"shift_request_{day}")
            if val and val != "No Request":
                shift_requests[day] = val

        new_emp = Employee(
            name=name,
            shift_type=shift_type,
            preferred_day_off=preferred_days,
            manual_days_off=manual_days,
            shift_requests=shift_requests
        )
        db.session.add(new_emp)
        db.session.commit()
        flash("Employee added.")
        return redirect(url_for('employees.list_or_create'))

    employees = Employee.query.all()
    return render_template("employees.html", employees=employees)

@employees_bp.route('/edit/<int:employee_id>', methods=['GET','POST'])
def edit_employee(employee_id):
    emp = Employee.query.get_or_404(employee_id)

    if request.method == 'POST':
        emp.name = request.form['name']
        emp.shift_type = request.form['shift_type']

        emp.preferred_day_off = request.form.getlist('preferred_day_off') or []
        emp.manual_days_off = request.form.getlist('manual_days_off') or []

        # shift requests
        shift_req = {}
        for day in ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]:
            v = request.form.get(f"shift_request_{day}")
            if v and v != "No Request":
                shift_req[day] = v

        emp.shift_requests = shift_req

        db.session.commit()
        flash("Employee updated.")
        return redirect(url_for('employees.list_or_create'))

    return render_template("edit.html", employee=emp)

@employees_bp.route('/delete/<int:employee_id>', methods=['POST'])
def delete_employee(employee_id):
    emp = Employee.query.get_or_404(employee_id)
    db.session.delete(emp)
    db.session.commit()
    flash(f"Employee {emp.name} deleted.")
    return redirect(url_for('employees.list_or_create'))
