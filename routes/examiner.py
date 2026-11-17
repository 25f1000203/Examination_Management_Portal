"""Examiner pages: dashboard, assigned exams, own slots, evaluation, history."""
from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

import rules
from database import execute, query
from helpers import clean, examiner_any_status, examiner_required, to_int, valid_phone

examiner_bp = Blueprint("examiner", __name__, url_prefix="/examiner")


def me():
    return session["user_id"]


def like(text):
    return f"%{text}%"


MY_BOOKINGS = (
    "SELECT b.*, st.name AS student_name, e.name AS exam_name, e.max_marks, s.slot_date, s.start_time, s.end_time, "
    "ev.id AS evaluation_id, ev.total_marks "
    "FROM booking b JOIN slot s ON s.id = b.slot_id JOIN student st ON st.id = b.student_id "
    "JOIN examination e ON e.id = b.exam_id LEFT JOIN evaluation ev ON ev.booking_id = b.id "
    "WHERE s.examiner_id = ? AND b.status IN ('booked', 'completed')")


@examiner_bp.route("/pending")
@examiner_any_status
def pending():
    examiner = query("SELECT * FROM examiner WHERE id = ?", (me(),), one=True)
    if examiner["status"] == "approved":
        return redirect(url_for("examiner.dashboard"))
    return render_template("examiner/pending.html", examiner=examiner)


@examiner_bp.route("/profile", methods=["GET", "POST"])
@examiner_required
def profile():
    examiner = query("SELECT * FROM examiner WHERE id = ?", (me(),), one=True)
    if request.method == "POST":
        if request.form.get("form") == "password":
            current, new = request.form.get("current_password", ""), request.form.get("new_password", "")
            if not check_password_hash(examiner["password_hash"], current):
                flash("Current password is wrong.", "danger")
            elif len(new) < 6:
                flash("New password must be at least 6 characters.", "danger")
            else:
                execute("UPDATE examiner SET password_hash = ? WHERE id = ?", (generate_password_hash(new), me()))
                flash("Password changed.", "success")
                return redirect(url_for("examiner.profile"))
        else:
            name, phone, dept = clean(request.form, "name"), clean(request.form, "phone"), clean(request.form, "department")
            if not name or not valid_phone(phone):
                flash("Enter your name and a valid phone number (7-15 digits).", "danger")
            else:
                execute("UPDATE examiner SET name = ?, phone = ?, department = ? WHERE id = ?",
                        (name, phone, dept, me()))
                session["name"] = name
                flash("Profile updated.", "success")
                return redirect(url_for("examiner.profile"))
    return render_template("examiner/profile.html", examiner=examiner)


@examiner_bp.route("/")
@examiner_required
def dashboard():
    exams = query("SELECT e.*, c.code AS course_code FROM examination e JOIN course c ON c.id = e.course_id "
                  "WHERE e.examiner_id = ? AND e.is_active = 1 ORDER BY e.id", (me(),))
    booked = query(MY_BOOKINGS + " ORDER BY s.slot_date, s.start_time", (me(),))
    stats = {
        "Assigned exams": len(exams),
        "My slots": query("SELECT COUNT(*) FROM slot WHERE examiner_id = ?", (me(),), one=True)[0],
        "Booked students": len(booked),
        "Pending evaluations": sum(1 for b in booked if not b["evaluation_id"]),
        "Evaluated": sum(1 for b in booked if b["evaluation_id"]),
    }
    return render_template("examiner/dashboard.html", exams=exams, booked=booked, stats=stats)


@examiner_bp.route("/exams")
@examiner_required
def exams():
    rows = query("SELECT e.*, c.code AS course_code, t.slot_creation_deadline, t.slot_creation_enabled "
                 "FROM examination e JOIN course c ON c.id = e.course_id "
                 "JOIN examination_timeline t ON t.exam_id = e.id WHERE e.examiner_id = ? ORDER BY e.id", (me(),))
    return render_template("examiner/exams.html", exams=rows)


@examiner_bp.route("/slots")
@examiner_required
def slots():
    rows = query("SELECT s.*, e.name AS exam_name, c.code AS course_code FROM slot s "
                 "JOIN examination e ON e.id = s.exam_id JOIN course c ON c.id = e.course_id "
                 "WHERE s.examiner_id = ? ORDER BY s.slot_date, s.start_time", (me(),))
    return render_template("examiner/slots.html", slots=rows)


@examiner_bp.route("/slots/new", methods=["GET", "POST"])
@examiner_bp.route("/slots/<int:slot_id>/edit", methods=["GET", "POST"])
@examiner_required
def slot_form(slot_id=None):
    slot = None
    if slot_id:
        slot = query("SELECT * FROM slot WHERE id = ? AND examiner_id = ?", (slot_id, me()), one=True)
        if not slot:
            abort(404)
    my_exams = query("SELECT * FROM examination WHERE examiner_id = ? AND is_active = 1 ORDER BY name", (me(),))
    data = dict(slot) if slot else {"exam_id": to_int(request.args.get("exam_id")), "status": "active", "capacity": 1}

    if request.method == "POST":
        exam_id = slot["exam_id"] if slot else to_int(request.form.get("exam_id"))
        problem = rules.examiner_slot_permission(exam_id, me())
        slot_data, errors = rules.validate_slot_form(request.form, me(), slot_id,
                                                     min_capacity=slot["booked_count"] if slot else 1,
                                                     original=slot)
        data = dict(slot_data, exam_id=exam_id)
        if problem:
            errors.insert(0, problem)
        if errors:
            for e in errors:
                flash(e, "danger")
        elif slot:
            rules.update_slot(slot, slot_data, "examiner", me())
            flash("Slot updated.", "success")
            return redirect(url_for("examiner.slots"))
        else:
            rules.create_slot(exam_id, me(), slot_data)
            flash("Slot created.", "success")
            return redirect(url_for("examiner.slots"))
    return render_template("examiner/slot_form.html", data=data, slot=slot, exams=my_exams)


@examiner_bp.route("/slots/<int:slot_id>/delete", methods=["GET", "POST"])
@examiner_required
def slot_delete(slot_id):
    slot = query("SELECT * FROM slot WHERE id = ? AND examiner_id = ?", (slot_id, me()), one=True) or abort(404)
    if request.method == "POST":
        problem = rules.examiner_slot_permission(slot["exam_id"], me())
        if problem:
            flash(problem, "danger")
        else:
            ok, message = rules.delete_slot(slot_id)
            flash(message, "success" if ok else "danger")
        return redirect(url_for("examiner.slots"))
    return render_template("confirm.html", title="Delete slot",
                           message=f"Delete slot #{slot_id} on {slot['slot_date']} {slot['start_time']}?",
                           back=url_for("examiner.slots"))


@examiner_bp.route("/slots/<int:slot_id>")
@examiner_required
def slot_students(slot_id):
    slot = query("SELECT s.*, e.name AS exam_name FROM slot s JOIN examination e ON e.id = s.exam_id "
                 "WHERE s.id = ? AND s.examiner_id = ?", (slot_id, me()), one=True) or abort(404)
    students = query(MY_BOOKINGS + " AND b.slot_id = ? ORDER BY b.id", (me(), slot_id))
    return render_template("examiner/slot_students.html", slot=slot, students=students)


@examiner_bp.route("/evaluate/<int:booking_id>", methods=["GET", "POST"])
@examiner_required
def evaluate(booking_id):
    booking = query(MY_BOOKINGS + " AND b.id = ?", (me(), booking_id), one=True)
    if not booking:
        abort(403)
    if booking["evaluation_id"]:
        flash("This student has already been evaluated.", "info")
        return redirect(url_for("examiner.history"))
    rubrics = query("SELECT * FROM rubric WHERE exam_id = ? ORDER BY id", (booking["exam_id"],))
    if request.method == "POST":
        ok, message = rules.save_evaluation(me(), booking_id, request.form)
        flash(message, "success" if ok else "danger")
        if ok:
            return redirect(url_for("examiner.history"))
    return render_template("examiner/evaluate.html", booking=booking, rubrics=rubrics, form=request.form)


@examiner_bp.route("/history")
@examiner_required
def history():
    rows = query(
        "SELECT ev.*, st.name AS student_name, e.name AS exam_name, e.max_marks, s.slot_date "
        "FROM evaluation ev JOIN booking b ON b.id = ev.booking_id JOIN slot s ON s.id = b.slot_id "
        "JOIN student st ON st.id = b.student_id JOIN examination e ON e.id = b.exam_id "
        "WHERE ev.examiner_id = ? ORDER BY ev.id DESC", (me(),))
    details = {}
    for ev in rows:
        details[ev["id"]] = query("SELECT r.criteria_name, r.max_marks, m.marks FROM evaluation_mark m "
                                  "JOIN rubric r ON r.id = m.rubric_id WHERE m.evaluation_id = ?", (ev["id"],))
    return render_template("examiner/history.html", evaluations=rows, details=details)


@examiner_bp.route("/search")
@examiner_required
def search():
    q = request.args.get("q", "").strip()
    if not q:
        return redirect(url_for("examiner.dashboard"))
    exams = query("SELECT e.*, c.code AS course_code FROM examination e JOIN course c ON c.id = e.course_id "
                  "WHERE e.examiner_id = ? AND (e.name LIKE ? OR c.code LIKE ?)", (me(), like(q), like(q)))
    students = query(MY_BOOKINGS + " AND (st.name LIKE ? OR CAST(st.id AS TEXT) = ? OR e.name LIKE ?)",
                     (me(), like(q), q, like(q)))
    return render_template("examiner/search.html", q=q, exams=exams, students=students)
