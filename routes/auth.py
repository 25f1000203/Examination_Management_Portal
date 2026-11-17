"""Login, registration and logout (one landing page, like the wireframe)."""
from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from database import execute, query
from helpers import clean, valid_email, valid_phone

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/")
def index():
    if "role" in session:
        return redirect(url_for(f"{session['role']}.dashboard"))
    return render_template("auth/index.html", form={})


@auth_bp.route("/login", methods=["POST"])
def login():
    email = clean(request.form, "email").lower()
    password = request.form.get("password", "")

    user, role = None, None
    for table in ("admin", "student", "examiner"):
        user = query(f"SELECT * FROM {table} WHERE email = ?", (email,), one=True)
        if user:
            role = table
            break

    if not user or not check_password_hash(user["password_hash"], password):
        flash("Invalid email or password.", "danger")
        return render_template("auth/index.html", form={})
    if role == "student" and not user["is_active"]:
        flash("Your account is deactivated. Contact the Admin.", "danger")
        return render_template("auth/index.html", form={})

    session.clear()
    session["user_id"], session["role"], session["name"] = user["id"], role, user["name"]
    return redirect(url_for(f"{role}.dashboard"))


@auth_bp.route("/register", methods=["POST"])
def register():
    form = request.form
    email = clean(form, "email").lower()
    name = clean(form, "name")
    password = form.get("password", "")
    user_type = clean(form, "user_type")
    phone = clean(form, "phone")
    department = clean(form, "department")

    errors = []
    if not name:
        errors.append("Full name is required.")
    if not valid_email(email):
        errors.append("Enter a valid email address.")
    if len(password) < 6:
        errors.append("Password must be at least 6 characters.")
    if user_type not in ("student", "examiner"):
        errors.append("Choose Examiner or Student.")
    if not valid_phone(phone):
        errors.append("Phone number must have 7-15 digits.")
    if user_type == "examiner" and not department:
        errors.append("Department is required for examiners.")
    for table in ("admin", "student", "examiner"):  # email must be unique across all roles
        if query(f"SELECT id FROM {table} WHERE email = ?", (email,), one=True):
            errors.append("This email is already registered.")
            break

    if errors:
        for message in errors:
            flash(message, "danger")
        return render_template("auth/index.html", form=form, show_register=True)

    if user_type == "student":
        execute("INSERT INTO student (name, email, password_hash, phone, department) VALUES (?,?,?,?,?)",
                (name, email, generate_password_hash(password), phone, department))
        flash("Registration successful. You can log in now.", "success")
    else:
        execute("INSERT INTO examiner (name, email, password_hash, phone, department) VALUES (?,?,?,?,?)",
                (name, email, generate_password_hash(password), phone, department))
        flash("Registration successful. Your account must be approved by the Admin before you can use it.", "success")
    return redirect(url_for("auth.index"))


@auth_bp.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("auth.index"))
