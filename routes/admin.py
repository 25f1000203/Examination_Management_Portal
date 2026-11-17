"""All Admin pages: dashboard, courses, examinations, rubrics, timelines, examiners,
students, slots, bookings, history and search."""

from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

import config
import rules
from database import execute, query, transaction
from helpers import admin_required, clean, to_float, to_int, valid_datetime

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


def count(sql, args=()):
    return query(sql, args, one=True)[0]


def like(text):
    return f"%{text}%"


@admin_bp.route("/")
@admin_required
def dashboard():
    counts = {
        "Students": count("SELECT COUNT(*) FROM student"),
        "Examiners": count("SELECT COUNT(*) FROM examiner"),
        "Courses": count("SELECT COUNT(*) FROM course"),
        "Examinations": count("SELECT COUNT(*) FROM examination"),
        "Slots": count("SELECT COUNT(*) FROM slot"),
        "Total Bookings": count("SELECT COUNT(*) FROM booking"),
        "Active Bookings": count("SELECT COUNT(*) FROM booking WHERE status = ?", ("booked",)),
    }
    pending = count("SELECT COUNT(*) FROM examiner WHERE status = 'pending'")
    courses = query("SELECT * FROM course ORDER BY id DESC LIMIT 5")
    exams = query("SELECT e.*, c.code AS course_code FROM examination e JOIN course c ON c.id = e.course_id "
                  "ORDER BY e.id DESC LIMIT 5")
    examiners = query("SELECT * FROM examiner ORDER BY (status = 'pending') DESC, id DESC LIMIT 5")
    return render_template("admin/dashboard.html", counts=counts, pending=pending,
                           courses=courses, exams=exams, examiners=examiners)


@admin_bp.route("/profile", methods=["GET", "POST"])
@admin_required
def profile():
    admin = query("SELECT * FROM admin WHERE id = ?", (session["user_id"],), one=True)
    if request.method == "POST":
        if request.form.get("form") == "password":
            current, new = request.form.get("current_password", ""), request.form.get("new_password", "")
            if not check_password_hash(admin["password_hash"], current):
                flash("Current password is wrong.", "danger")
            elif len(new) < 6:
                flash("New password must be at least 6 characters.", "danger")
            else:
                execute("UPDATE admin SET password_hash = ? WHERE id = ?", (generate_password_hash(new), session["user_id"]))
                flash("Password changed.", "success")
                return redirect(url_for("admin.profile"))
        else:
            name = clean(request.form, "name")
            if not name:
                flash("Enter a valid name.", "danger")
            else:
                execute("UPDATE admin SET name = ? WHERE id = ?", (name, session["user_id"]))
                session["name"] = name
                flash("Profile updated.", "success")
                return redirect(url_for("admin.profile"))
    return render_template("admin/profile.html", admin=admin)


@admin_bp.route("/courses")
@admin_required
def courses():
    q = request.args.get("q", "").strip()
    rows = query("SELECT * FROM course WHERE code LIKE ? OR name LIKE ? ORDER BY id", (like(q), like(q)))
    return render_template("admin/courses.html", courses=rows, q=q)


@admin_bp.route("/courses/new", methods=["GET", "POST"])
@admin_bp.route("/courses/<int:course_id>/edit", methods=["GET", "POST"])
@admin_required
def course_form(course_id=None):
    course = query("SELECT * FROM course WHERE id = ?", (course_id,), one=True) if course_id else None
    if course_id and not course:
        abort(404)
    data = dict(course) if course else {"is_active": 1}

    if request.method == "POST":
        data = {"code": clean(request.form, "code").upper(), "name": clean(request.form, "name"),
                "description": clean(request.form, "description"),
                "is_active": 1 if request.form.get("is_active") else 0}
        errors = []
        if not data["code"] or not data["name"]:
            errors.append("Course code and name are required.")
        if query("SELECT id FROM course WHERE code = ? AND id != ?", (data["code"], course_id or 0), one=True):
            errors.append("This course code already exists.")
        if errors:
            for e in errors:
                flash(e, "danger")
        else:
            if course_id:
                execute("UPDATE course SET code=?, name=?, description=?, is_active=? WHERE id=?",
                        (data["code"], data["name"], data["description"], data["is_active"], course_id))
                flash("Course updated.", "success")
            else:
                execute("INSERT INTO course (code, name, description, is_active) VALUES (?,?,?,?)",
                        (data["code"], data["name"], data["description"], data["is_active"]))
                flash("Course added.", "success")
            return redirect(url_for("admin.courses"))
    return render_template("admin/course_form.html", data=data, course=course)


@admin_bp.route("/courses/<int:course_id>/delete", methods=["GET", "POST"])
@admin_required
def course_delete(course_id):
    course = query("SELECT * FROM course WHERE id = ?", (course_id,), one=True) or abort(404)
    if request.method == "POST":
        if query("SELECT id FROM examination WHERE course_id = ?", (course_id,), one=True):
            flash("This course has examinations, so it cannot be deleted. Deactivate it instead.", "danger")
        else:
            execute("DELETE FROM course WHERE id = ?", (course_id,))
            flash("Course deleted.", "success")
        return redirect(url_for("admin.courses"))
    return render_template("confirm.html", title="Delete course",
                           message=f"Delete course {course['code']} - {course['name']}? This cannot be undone.",
                           back=url_for("admin.courses"))


def exam_form_data(form):
    return {"course_id": to_int(form.get("course_id")), "name": clean(form, "name"),
            "exam_type": clean(form, "exam_type"), "duration_minutes": to_int(clean(form, "duration_minutes")),
            "max_marks": to_float(clean(form, "max_marks")), "examiner_id": to_int(form.get("examiner_id")),
            "description": clean(form, "description"), "is_active": 1 if form.get("is_active") else 0}


@admin_bp.route("/exams")
@admin_required
def exams():
    q = request.args.get("q", "").strip()
    course_id = to_int(request.args.get("course_id"))
    sql = ("SELECT e.*, c.code AS course_code, x.name AS examiner_name FROM examination e "
           "JOIN course c ON c.id = e.course_id LEFT JOIN examiner x ON x.id = e.examiner_id "
           "WHERE (e.name LIKE ? OR c.code LIKE ? OR e.exam_type LIKE ?)")
    args = [like(q)] * 3
    if course_id:
        sql += " AND e.course_id = ?"
        args.append(course_id)
    rows = query(sql + " ORDER BY e.id", args)
    return render_template("admin/exams.html", exams=rows, q=q, course_id=course_id,
                           courses=query("SELECT * FROM course ORDER BY code"))


@admin_bp.route("/exams/new", methods=["GET", "POST"])
@admin_bp.route("/exams/<int:exam_id>/edit", methods=["GET", "POST"])
@admin_required
def exam_form(exam_id=None):
    exam = query("SELECT * FROM examination WHERE id = ?", (exam_id,), one=True) if exam_id else None
    if exam_id and not exam:
        abort(404)
    data = dict(exam) if exam else {"is_active": 1, "course_id": to_int(request.args.get("course_id"))}

    if request.method == "POST":
        data = exam_form_data(request.form)
        errors = []
        if not query("SELECT id FROM course WHERE id = ?", (data["course_id"],), one=True):
            errors.append("Choose a valid course.")
        if not data["name"]:
            errors.append("Examination name is required.")
        if data["exam_type"] not in config.EXAM_TYPES:
            errors.append("Choose a valid examination type.")
        if data["duration_minutes"] is None or data["duration_minutes"] < 1:
            errors.append("Duration must be a positive number of minutes.")
        if data["max_marks"] is None or data["max_marks"] <= 0:
            errors.append("Max marks must be greater than 0.")
        if data["examiner_id"] and not query("SELECT id FROM examiner WHERE id = ? AND status = 'approved'",
                                             (data["examiner_id"],), one=True):
            errors.append("Only approved examiners can be assigned.")
        if exam_id and data["max_marks"] and not errors:
            rubric_total = query("SELECT COALESCE(SUM(max_marks), 0) FROM rubric WHERE exam_id = ?",
                                 (exam_id,), one=True)[0]
            if data["max_marks"] < rubric_total:
                errors.append(f"Max marks cannot be below the rubric total ({rubric_total:g}).")
        if errors:
            for e in errors:
                flash(e, "danger")
        else:
            values = (data["course_id"], data["name"], data["exam_type"], data["duration_minutes"],
                      data["max_marks"], data["examiner_id"], data["description"], data["is_active"])
            if exam_id:
                execute("UPDATE examination SET course_id=?, name=?, exam_type=?, duration_minutes=?, max_marks=?, "
                        "examiner_id=?, description=?, is_active=? WHERE id=?", values + (exam_id,))
                flash("Examination updated. (Existing slots keep their examiner; use Slots > Edit to reassign.)",
                      "success")
            else:
                with transaction():
                    cur = execute("INSERT INTO examination (course_id, name, exam_type, duration_minutes, max_marks, "
                                  "examiner_id, description, is_active) VALUES (?,?,?,?,?,?,?,?)", values)
                    execute("INSERT INTO examination_timeline (exam_id) VALUES (?)", (cur.lastrowid,))
                flash("Examination added. Set its timeline and rubric next.", "success")
            return redirect(url_for("admin.exams"))
    return render_template("admin/exam_form.html", data=data, exam=exam, exam_types=config.EXAM_TYPES,
                           courses=query("SELECT * FROM course ORDER BY code"),
                           examiners=query("SELECT * FROM examiner WHERE status = 'approved' ORDER BY name"))


@admin_bp.route("/exams/<int:exam_id>/delete", methods=["GET", "POST"])
@admin_required
def exam_delete(exam_id):
    exam = query("SELECT * FROM examination WHERE id = ?", (exam_id,), one=True) or abort(404)
    if request.method == "POST":
        if query("SELECT id FROM slot WHERE exam_id = ?", (exam_id,), one=True) or \
           query("SELECT id FROM booking WHERE exam_id = ?", (exam_id,), one=True):
            flash("This examination has slots/bookings, so it cannot be deleted. Deactivate it instead.", "danger")
        else:
            with transaction():
                execute("DELETE FROM rubric WHERE exam_id = ?", (exam_id,))
                execute("DELETE FROM examination_timeline WHERE exam_id = ?", (exam_id,))
                execute("DELETE FROM examination WHERE id = ?", (exam_id,))
            flash("Examination deleted.", "success")
        return redirect(url_for("admin.exams"))
    return render_template("confirm.html", title="Delete examination",
                           message=f"Delete examination '{exam['name']}' with its rubric and timeline?",
                           back=url_for("admin.exams"))


@admin_bp.route("/exams/<int:exam_id>/timeline", methods=["GET", "POST"])
@admin_required
def timeline(exam_id):
    exam = query("SELECT * FROM examination WHERE id = ?", (exam_id,), one=True) or abort(404)
    tl = dict(rules.get_timeline(exam_id))
    if request.method == "POST":
        fields = ("slot_creation_deadline", "booking_start", "booking_deadline", "cancellation_deadline")
        tl = {f: clean(request.form, f) or None for f in fields}
        for f in ("slot_creation_enabled", "booking_enabled", "results_published"):
            tl[f] = 1 if request.form.get(f) else 0
        errors = [f"Invalid date for {f.replace('_', ' ')}." for f in fields if tl[f] and not valid_datetime(tl[f])]
        if not errors and tl["booking_start"] and tl["booking_deadline"] and tl["booking_start"] >= tl["booking_deadline"]:
            errors.append("Booking start must be before the booking deadline.")
        if errors:
            for e in errors:
                flash(e, "danger")
        else:
            execute("UPDATE examination_timeline SET slot_creation_deadline=?, booking_start=?, booking_deadline=?, "
                    "cancellation_deadline=?, slot_creation_enabled=?, booking_enabled=?, results_published=? "
                    "WHERE exam_id=?", tuple(tl[f] for f in fields) +
                    (tl["slot_creation_enabled"], tl["booking_enabled"], tl["results_published"], exam_id))
            flash("Timeline saved.", "success")
            return redirect(url_for("admin.exams"))
    return render_template("admin/timeline.html", exam=exam, tl=tl)


@admin_bp.route("/exams/<int:exam_id>/rubrics")
@admin_required
def rubrics(exam_id):
    exam = query("SELECT e.*, c.code AS course_code FROM examination e JOIN course c ON c.id = e.course_id "
                 "WHERE e.id = ?", (exam_id,), one=True) or abort(404)
    rows = query("SELECT * FROM rubric WHERE exam_id = ? ORDER BY id", (exam_id,))
    total = sum(r["max_marks"] for r in rows)
    return render_template("admin/rubrics.html", exam=exam, rubrics=rows, total=total)


def exam_has_evaluations(exam_id):
    return query("SELECT ev.id FROM evaluation ev JOIN booking b ON b.id = ev.booking_id WHERE b.exam_id = ?",
                 (exam_id,), one=True) is not None


@admin_bp.route("/exams/<int:exam_id>/rubrics/new", methods=["GET", "POST"])
@admin_bp.route("/exams/<int:exam_id>/rubrics/<int:rubric_id>/edit", methods=["GET", "POST"])
@admin_required
def rubric_form(exam_id, rubric_id=None):
    exam = query("SELECT * FROM examination WHERE id = ?", (exam_id,), one=True) or abort(404)
    rubric = query("SELECT * FROM rubric WHERE id = ? AND exam_id = ?", (rubric_id, exam_id), one=True) if rubric_id else None
    if rubric_id and not rubric:
        abort(404)
    if exam_has_evaluations(exam_id):
        flash("Evaluations already exist for this examination, so its rubric is locked.", "warning")
        return redirect(url_for("admin.rubrics", exam_id=exam_id))
    data = dict(rubric) if rubric else {}

    if request.method == "POST":
        data = {"criteria_name": clean(request.form, "criteria_name"),
                "max_marks": to_float(clean(request.form, "max_marks")),
                "weightage": to_float(clean(request.form, "weightage") or "0"),
                "description": clean(request.form, "description")}
        errors = []
        if not data["criteria_name"]:
            errors.append("Criteria name is required.")
        if data["max_marks"] is None or data["max_marks"] <= 0:
            errors.append("Max marks must be greater than 0.")
        if data["weightage"] is None or not 0 <= data["weightage"] <= 100:
            errors.append("Weightage must be between 0 and 100.")
        if not errors:
            other = query("SELECT COALESCE(SUM(max_marks), 0) FROM rubric WHERE exam_id = ? AND id != ?",
                          (exam_id, rubric_id or 0), one=True)[0]
            if other + data["max_marks"] > exam["max_marks"]:
                errors.append(f"Total rubric marks would be {other + data['max_marks']:g}, "
                              f"which exceeds the examination's max marks ({exam['max_marks']:g}).")
        if errors:
            for e in errors:
                flash(e, "danger")
        else:
            values = (data["criteria_name"], data["max_marks"], data["weightage"], data["description"])
            if rubric_id:
                execute("UPDATE rubric SET criteria_name=?, max_marks=?, weightage=?, description=? WHERE id=?",
                        values + (rubric_id,))
            else:
                execute("INSERT INTO rubric (criteria_name, max_marks, weightage, description, exam_id) "
                        "VALUES (?,?,?,?,?)", values + (exam_id,))
            flash("Rubric criteria saved.", "success")
            return redirect(url_for("admin.rubrics", exam_id=exam_id))
    return render_template("admin/rubric_form.html", data=data, exam=exam, rubric=rubric)


@admin_bp.route("/exams/<int:exam_id>/rubrics/<int:rubric_id>/delete", methods=["GET", "POST"])
@admin_required
def rubric_delete(exam_id, rubric_id):
    rubric = query("SELECT * FROM rubric WHERE id = ? AND exam_id = ?", (rubric_id, exam_id), one=True) or abort(404)
    if exam_has_evaluations(exam_id):
        flash("Evaluations already exist for this examination, so its rubric is locked.", "warning")
        return redirect(url_for("admin.rubrics", exam_id=exam_id))
    if request.method == "POST":
        execute("DELETE FROM rubric WHERE id = ?", (rubric_id,))
        flash("Rubric criteria deleted.", "success")
        return redirect(url_for("admin.rubrics", exam_id=exam_id))
    return render_template("confirm.html", title="Delete rubric criteria",
                           message=f"Delete criteria '{rubric['criteria_name']}'?",
                           back=url_for("admin.rubrics", exam_id=exam_id))


@admin_bp.route("/examiners")
@admin_required
def examiners():
    q = request.args.get("q", "").strip()
    status = request.args.get("status", "")
    sql = "SELECT * FROM examiner WHERE (name LIKE ? OR email LIKE ? OR department LIKE ?)"
    args = [like(q)] * 3
    if status:
        sql += " AND status = ?"
        args.append(status)
    return render_template("admin/examiners.html", examiners=query(sql + " ORDER BY id", args), q=q, status=status)


@admin_bp.route("/examiners/<int:examiner_id>", methods=["GET", "POST"])
@admin_required
def examiner_view(examiner_id):
    examiner = query("SELECT * FROM examiner WHERE id = ?", (examiner_id,), one=True) or abort(404)
    if request.method == "POST":
        action = request.form.get("action")
        new_status = {"approve": "approved", "reject": "rejected", "deactivate": "deactivated"}.get(action)
        if not new_status:
            flash("Choose an action.", "danger")
        elif new_status == examiner["status"]:
            flash(f"Examiner is already {new_status}.", "warning")
        else:
            execute("UPDATE examiner SET status = ? WHERE id = ?", (new_status, examiner_id))
            flash(f"Examiner status changed to {new_status}.", "success")
            return redirect(url_for("admin.examiners"))
    exams = query("SELECT e.*, c.code AS course_code FROM examination e JOIN course c ON c.id = e.course_id "
                  "WHERE e.examiner_id = ?", (examiner_id,))
    slots = query("SELECT COUNT(*) FROM slot WHERE examiner_id = ?", (examiner_id,), one=True)[0]
    return render_template("admin/examiner_view.html", examiner=examiner, exams=exams, slot_count=slots)


@admin_bp.route("/students")
@admin_required
def students():
    q = request.args.get("q", "").strip()
    rows = query("SELECT s.*, (SELECT COUNT(*) FROM booking b WHERE b.student_id = s.id AND b.status = 'booked') AS active_bookings "
                 "FROM student s WHERE s.name LIKE ? OR s.email LIKE ? OR s.department LIKE ? OR CAST(s.id AS TEXT) = ? "
                 "ORDER BY s.id", (like(q), like(q), like(q), q))
    return render_template("admin/students.html", students=rows, q=q)


@admin_bp.route("/students/<int:student_id>", methods=["GET", "POST"])
@admin_required
def student_view(student_id):
    student = query("SELECT * FROM student WHERE id = ?", (student_id,), one=True) or abort(404)
    if request.method == "POST":
        new_value = 1 if request.form.get("action") == "activate" else 0
        execute("UPDATE student SET is_active = ? WHERE id = ?", (new_value, student_id))
        flash("Student activated." if new_value else "Student deactivated.", "success")
        return redirect(url_for("admin.student_view", student_id=student_id))
    bookings = query(BOOKING_SELECT + " WHERE b.student_id = ? ORDER BY b.id DESC", (student_id,))
    return render_template("admin/student_view.html", student=student, bookings=bookings)


@admin_bp.route("/slots")
@admin_required
def slots():
    exam_id = to_int(request.args.get("exam_id"))
    examiner_id = to_int(request.args.get("examiner_id"))
    sql = ("SELECT s.*, e.name AS exam_name, c.code AS course_code, x.name AS examiner_name "
           "FROM slot s JOIN examination e ON e.id = s.exam_id JOIN course c ON c.id = e.course_id "
           "JOIN examiner x ON x.id = s.examiner_id WHERE 1 = 1")
    args = []
    if exam_id:
        sql += " AND s.exam_id = ?"
        args.append(exam_id)
    if examiner_id:
        sql += " AND s.examiner_id = ?"
        args.append(examiner_id)
    rows = query(sql + " ORDER BY s.slot_date, s.start_time", args)
    return render_template("admin/slots.html", slots=rows, exam_id=exam_id, examiner_id=examiner_id,
                           exams=query("SELECT * FROM examination ORDER BY name"),
                           examiners=query("SELECT * FROM examiner WHERE status = 'approved' ORDER BY name"))


@admin_bp.route("/slots/new", methods=["GET", "POST"])
@admin_bp.route("/slots/<int:slot_id>/edit", methods=["GET", "POST"])
@admin_required
def slot_form(slot_id=None):
    slot = query("SELECT * FROM slot WHERE id = ?", (slot_id,), one=True) if slot_id else None
    if slot_id and not slot:
        abort(404)
    data = dict(slot) if slot else {"exam_id": to_int(request.args.get("exam_id")), "status": "active", "capacity": 1}
    if not slot and data["exam_id"]:  # preselect the exam's assigned examiner
        exam = query("SELECT examiner_id FROM examination WHERE id = ?", (data["exam_id"],), one=True)
        data["examiner_id"] = exam["examiner_id"] if exam else None

    if request.method == "POST":
        exam_id = slot["exam_id"] if slot else to_int(request.form.get("exam_id"))
        examiner_id = to_int(request.form.get("examiner_id"))
        slot_data, errors = rules.validate_slot_form(request.form, examiner_id, slot_id,
                                                     min_capacity=slot["booked_count"] if slot else 1,
                                                     original=slot)
        data = dict(slot_data, exam_id=exam_id, examiner_id=examiner_id)
        if not query("SELECT id FROM examination WHERE id = ? AND is_active = 1", (exam_id,), one=True) and not slot:
            errors.append("Choose an active examination.")
        if not query("SELECT id FROM examiner WHERE id = ? AND status = 'approved'", (examiner_id,), one=True):
            errors.append("Choose an approved examiner.")
        if errors:
            for e in errors:
                flash(e, "danger")
        elif slot:
            ok, message = rules.reassign_slot_examiner(slot_id, examiner_id, session["user_id"])
            if not ok:
                flash(message, "danger")
            else:
                rules.update_slot(slot, slot_data, "admin", session["user_id"])
                flash("Slot updated.", "success")
                return redirect(url_for("admin.slots"))
        else:
            rules.create_slot(exam_id, examiner_id, slot_data)
            flash("Slot created.", "success")
            return redirect(url_for("admin.slots"))
    return render_template("admin/slot_form.html", data=data, slot=slot,
                           exams=query("SELECT * FROM examination WHERE is_active = 1 ORDER BY name"),
                           examiners=query("SELECT * FROM examiner WHERE status = 'approved' ORDER BY name"))


@admin_bp.route("/slots/<int:slot_id>/delete", methods=["GET", "POST"])
@admin_required
def slot_delete(slot_id):
    slot = query("SELECT * FROM slot WHERE id = ?", (slot_id,), one=True) or abort(404)
    if request.method == "POST":
        ok, message = rules.delete_slot(slot_id)
        flash(message, "success" if ok else "danger")
        return redirect(url_for("admin.slots"))
    return render_template("confirm.html", title="Delete slot",
                           message=f"Delete slot #{slot_id} on {slot['slot_date']} {slot['start_time']}?",
                           back=url_for("admin.slots"))


BOOKING_SELECT = (
    "SELECT b.*, st.name AS student_name, st.email AS student_email, e.name AS exam_name, c.code AS course_code, "
    "c.id AS course_id, s.slot_date, s.start_time, s.end_time, x.name AS examiner_name "
    "FROM booking b JOIN student st ON st.id = b.student_id JOIN slot s ON s.id = b.slot_id "
    "JOIN examination e ON e.id = b.exam_id JOIN course c ON c.id = e.course_id "
    "JOIN examiner x ON x.id = s.examiner_id")


@admin_bp.route("/bookings")
@admin_required
def bookings():
    exam_id = to_int(request.args.get("exam_id"))
    student_id = to_int(request.args.get("student_id"))
    status = request.args.get("status", "")
    sql, args = BOOKING_SELECT + " WHERE 1 = 1", []
    if exam_id:
        sql += " AND b.exam_id = ?"
        args.append(exam_id)
    if student_id:
        sql += " AND b.student_id = ?"
        args.append(student_id)
    if status:
        sql += " AND b.status = ?"
        args.append(status)
    rows = query(sql + " ORDER BY b.id DESC", args)
    return render_template("admin/bookings.html", bookings=rows, exam_id=exam_id, student_id=student_id,
                           status=status, exams=query("SELECT * FROM examination ORDER BY name"))


@admin_bp.route("/bookings/<int:booking_id>/reschedule", methods=["GET", "POST"])
@admin_required
def booking_reschedule(booking_id):
    booking = query(BOOKING_SELECT + " WHERE b.id = ?", (booking_id,), one=True) or abort(404)
    if booking["status"] != "booked":
        flash("Only active bookings can be rescheduled.", "warning")
        return redirect(url_for("admin.bookings"))
    if request.method == "POST":
        ok, message = rules.reschedule_booking(booking_id, to_int(request.form.get("slot_id")), session["user_id"])
        flash(message, "success" if ok else "danger")
        if ok:
            return redirect(url_for("admin.bookings"))
    options = query("SELECT s.*, x.name AS examiner_name FROM slot s JOIN examiner x ON x.id = s.examiner_id "
                    "WHERE s.exam_id = ? AND s.id != ? AND s.status = 'active' AND s.booked_count < s.capacity "
                    "ORDER BY s.slot_date, s.start_time", (booking["exam_id"], booking["slot_id"]))
    return render_template("admin/reschedule.html", booking=booking, options=options)


@admin_bp.route("/bookings/<int:booking_id>/cancel", methods=["GET", "POST"])
@admin_required
def booking_cancel(booking_id):
    booking = query("SELECT * FROM booking WHERE id = ?", (booking_id,), one=True) or abort(404)
    if request.method == "POST":
        ok, message = rules.cancel_booking(booking_id, "admin", session["user_id"], enforce_deadlines=False)
        flash(message, "success" if ok else "danger")
        return redirect(url_for("admin.bookings"))
    return render_template("confirm.html", title="Cancel booking",
                           message=f"Cancel booking #{booking_id}? The seat will be released.",
                           back=url_for("admin.bookings"))


@admin_bp.route("/history")
@admin_required
def history():
    q = request.args.get("q", "").strip()
    action = request.args.get("action", "")
    sql = ("SELECT h.*, st.name AS student_name, e.name AS exam_name FROM booking_history h "
           "JOIN booking b ON b.id = h.booking_id JOIN student st ON st.id = b.student_id "
           "JOIN examination e ON e.id = b.exam_id WHERE (st.name LIKE ? OR e.name LIKE ? OR h.note LIKE ?)")
    args = [like(q)] * 3
    if action:
        sql += " AND h.action = ?"
        args.append(action)
    log = query(sql + " ORDER BY h.id DESC LIMIT 300", args)
    evaluations = query(
        "SELECT ev.*, st.name AS student_name, e.name AS exam_name, e.max_marks, x.name AS examiner_name "
        "FROM evaluation ev JOIN booking b ON b.id = ev.booking_id JOIN student st ON st.id = b.student_id "
        "JOIN examination e ON e.id = b.exam_id JOIN examiner x ON x.id = ev.examiner_id "
        "WHERE st.name LIKE ? OR e.name LIKE ? ORDER BY ev.id DESC LIMIT 300", (like(q), like(q)))
    actions = [r[0] for r in query("SELECT DISTINCT action FROM booking_history ORDER BY action")]
    return render_template("admin/history.html", log=log, evaluations=evaluations, q=q, action=action, actions=actions)


@admin_bp.route("/search")
@admin_required
def search():
    q = request.args.get("q", "").strip()
    if not q:
        return redirect(url_for("admin.dashboard"))
    return render_template(
        "admin/search.html", q=q,
        students=query("SELECT * FROM student WHERE name LIKE ? OR email LIKE ? OR CAST(id AS TEXT) = ?", (like(q), like(q), q)),
        examiners=query("SELECT * FROM examiner WHERE name LIKE ? OR email LIKE ? OR department LIKE ?", (like(q),) * 3),
        courses=query("SELECT * FROM course WHERE code LIKE ? OR name LIKE ?", (like(q), like(q))),
        exams=query("SELECT e.*, c.code AS course_code FROM examination e JOIN course c ON c.id = e.course_id "
                    "WHERE e.name LIKE ? OR e.exam_type LIKE ?", (like(q), like(q))))
