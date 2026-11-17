"""Small helper functions shared by all routes: login protection, CSRF, input cleaning."""
import re
import secrets
from datetime import datetime
from functools import wraps

from flask import abort, flash, redirect, request, session, url_for

from database import query


def now_str():
    """Current time as 'YYYY-MM-DDTHH:MM' (same format as the deadline fields, so text comparison works)."""
    return datetime.now().strftime("%Y-%m-%dT%H:%M")


def clean(form, name):
    return (form.get(name) or "").strip()


def to_int(text):
    try:
        return int(text)
    except (TypeError, ValueError):
        return None


def to_float(text):
    try:
        value = float(text)
    except (TypeError, ValueError):
        return None
    return value if value == value and abs(value) != float("inf") else None  # reject nan / inf


def valid_email(email):
    return re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email) is not None


def valid_phone(phone):
    return re.fullmatch(r"\+?\d{7,15}", phone) is not None


def valid_datetime(text):
    try:
        datetime.strptime(text, "%Y-%m-%dT%H:%M")
        return True
    except ValueError:
        return False


def csrf_token():
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_hex(16)
    return session["_csrf"]


def check_csrf():
    if request.method == "POST":
        token = request.form.get("_csrf")
        if not token or token != session.get("_csrf"):
            abort(400)


def _protect(role, need_approval=True):
    def decorator(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            if "user_id" not in session:
                flash("Please log in first.", "warning")
                return redirect(url_for("auth.index"))
            if session.get("role") != role:
                abort(403)  # logged in, but wrong role
            if role == "student":
                row = query("SELECT is_active FROM student WHERE id = ?", (session["user_id"],), one=True)
                if not row or not row["is_active"]:
                    session.clear()
                    flash("Your account is deactivated.", "danger")
                    return redirect(url_for("auth.index"))
            if role == "examiner":
                row = query("SELECT status FROM examiner WHERE id = ?", (session["user_id"],), one=True)
                if not row:
                    session.clear()
                    return redirect(url_for("auth.index"))
                if need_approval and row["status"] != "approved":
                    return redirect(url_for("examiner.pending"))
            return view(*args, **kwargs)
        return wrapper
    return decorator


admin_required = _protect("admin")
student_required = _protect("student")
examiner_required = _protect("examiner")                       # approved examiners only
examiner_any_status = _protect("examiner", need_approval=False)  # used only by the 'pending' page
