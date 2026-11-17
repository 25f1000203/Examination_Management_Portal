"""Student pages: dashboard, exams, slots, booking/cancelling, history, results, profile."""
from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

import config
import rules
from database import execute, query
from helpers import clean, now_str, student_required, to_int, valid_phone

student_bp = Blueprint("student", __name__, url_prefix="/student")


def me():
    return session["user_id"]


def like(text):
    return f"%{text}%"


OPEN_SLOTS = (
    "SELECT s.*, e.name AS exam_name, e.exam_type, e.max_marks, e.duration_minutes, c.code AS course_code, "
    "x.name AS examiner_name, (s.capacity - s.booked_count) AS available "
    "FROM slot s JOIN examination e ON e.id = s.exam_id JOIN course c ON c.id = e.course_id "
    "JOIN examiner x ON x.id = s.examiner_id JOIN examination_timeline t ON t.exam_id = e.id "
    "WHERE s.status = 'active' AND e.is_active = 1 AND c.is_active = 1 AND x.status = 'approved' "
    "AND s.booked_count < s.capacity "
    "AND (s.slot_date || 'T' || s.start_time) > :now AND t.booking_enabled = 1 "
    "AND (t.booking_start IS NULL OR t.booking_start <= :now) "
    "AND (t.booking_deadline IS NULL OR t.booking_deadline >= :now) "
    "AND NOT EXISTS (SELECT 1 FROM booking b WHERE b.student_id = :me AND b.exam_id = s.exam_id "
    "AND b.status IN ('booked', 'completed'))")

MY_BOOKINGS = (
    "SELECT b.*, e.name AS exam_name, e.exam_type, e.max_marks, e.duration_minutes, c.code AS course_code, "
    "s.slot_date, s.start_time, s.end_time, x.name AS examiner_name, t.results_published, "
    "ev.id AS evaluation_id, ev.total_marks "
    "FROM booking b JOIN slot s ON s.id = b.slot_id JOIN examination e ON e.id = b.exam_id "
    "JOIN course c ON c.id = e.course_id JOIN examiner x ON x.id = s.examiner_id "
    "JOIN examination_timeline t ON t.exam_id = e.id LEFT JOIN evaluation ev ON ev.booking_id = b.id "
    "WHERE b.student_id = ?")


def open_slots(extra="", args=None, limit=None):
    params = {"now": now_str(), "me": me()}
    params.update(args or {})
    sql = OPEN_SLOTS + extra + " ORDER BY s.slot_date, s.start_time" + (f" LIMIT {limit}" if limit else "")
    return query(sql, params)


@student_bp.route("/")
@student_required
def dashboard():
    available = open_slots(limit=8)
    upcoming = query(MY_BOOKINGS + " AND b.status = 'booked' ORDER BY s.slot_date, s.start_time", (me(),))
    results = [b for b in query(MY_BOOKINGS + " AND ev.id IS NOT NULL ORDER BY ev.id DESC", (me(),))
               if b["results_published"]]
    stats = {
        "Available slots": len(open_slots()),
        "Upcoming exams": len(upcoming),
        "Total bookings": query("SELECT COUNT(*) FROM booking WHERE student_id = ?", (me(),), one=True)[0],
        "Published results": len(results),
    }
    return render_template("student/dashboard.html", available=available, upcoming=upcoming,
                           results=results, stats=stats)


@student_bp.route("/exams")
@student_required
def exams():
    q = request.args.get("q", "").strip()
    course_id = to_int(request.args.get("course_id"))
    exam_type = request.args.get("exam_type", "")
    sql = ("SELECT e.*, c.code AS course_code, c.name AS course_name FROM examination e "
           "JOIN course c ON c.id = e.course_id WHERE e.is_active = 1 AND c.is_active = 1 "
           "AND (e.name LIKE ? OR c.code LIKE ? OR c.name LIKE ?)")
    args = [like(q)] * 3
    if course_id:
        sql += " AND e.course_id = ?"
        args.append(course_id)
    if exam_type:
        sql += " AND e.exam_type = ?"
        args.append(exam_type)
    return render_template("student/exams.html", exams=query(sql + " ORDER BY c.code, e.name", args), q=q,
                           course_id=course_id, exam_type=exam_type, exam_types=config.EXAM_TYPES,
                           courses=query("SELECT * FROM course WHERE is_active = 1 ORDER BY code"))


@student_bp.route("/exams/<int:exam_id>")
@student_required
def exam_detail(exam_id):
    exam = query("SELECT e.*, c.code AS course_code, c.name AS course_name, x.name AS examiner_name "
                 "FROM examination e JOIN course c ON c.id = e.course_id LEFT JOIN examiner x ON x.id = e.examiner_id "
                 "WHERE e.id = ? AND e.is_active = 1", (exam_id,), one=True) or abort(404)
    rubrics = query("SELECT * FROM rubric WHERE exam_id = ? ORDER BY id", (exam_id,))
    my_booking = query("SELECT * FROM booking WHERE student_id = ? AND exam_id = ? AND status IN ('booked','completed')",
                       (me(), exam_id), one=True)
    return render_template("student/exam_detail.html", exam=exam, rubrics=rubrics, tl=rules.get_timeline(exam_id),
                           slots=open_slots(" AND s.exam_id = :exam_id", {"exam_id": exam_id}),
                           my_booking=my_booking)


@student_bp.route("/slots")
@student_required
def slots():
    exam_id = to_int(request.args.get("exam_id"))
    date = request.args.get("date", "").strip()
    extra, args = "", {}
    if exam_id:
        extra += " AND s.exam_id = :exam_id"
        args["exam_id"] = exam_id
    if date:
        extra += " AND s.slot_date = :date"
        args["date"] = date
    return render_template("student/slots.html", slots=open_slots(extra, args), exam_id=exam_id, date=date,
                           exams=query("SELECT * FROM examination WHERE is_active = 1 ORDER BY name"))


@student_bp.route("/book/<int:slot_id>", methods=["GET", "POST"])
@student_required
def book(slot_id):
    slot = query("SELECT s.*, e.name AS exam_name, c.code AS course_code, x.name AS examiner_name "
                 "FROM slot s JOIN examination e ON e.id = s.exam_id JOIN course c ON c.id = e.course_id "
                 "JOIN examiner x ON x.id = s.examiner_id WHERE s.id = ?", (slot_id,), one=True) or abort(404)
    if request.method == "POST":
        ok, message = rules.book_slot(me(), slot_id)
        flash(message, "success" if ok else "danger")
        return redirect(url_for("student.history" if ok else "student.slots"))
    return render_template("student/confirm_booking.html", slot=slot, cancel=False)


@student_bp.route("/cancel/<int:booking_id>", methods=["GET", "POST"])
@student_required
def cancel(booking_id):
    booking = query(MY_BOOKINGS + " AND b.id = ?", (me(), booking_id), one=True) or abort(404)
    if request.method == "POST":
        ok, message = rules.cancel_booking(booking_id, "student", me())
        flash(message, "success" if ok else "danger")
        return redirect(url_for("student.history"))
    return render_template("student/confirm_booking.html", slot=booking, cancel=True, booking=booking)


@student_bp.route("/history")
@student_required
def history():
    bookings = query(MY_BOOKINGS + " ORDER BY b.id DESC", (me(),))
    log = query("SELECT h.* FROM booking_history h JOIN booking b ON b.id = h.booking_id "
                "WHERE b.student_id = ? ORDER BY h.id DESC LIMIT 100", (me(),))
    return render_template("student/history.html", bookings=bookings, log=log)


@student_bp.route("/results/<int:booking_id>")
@student_required
def result(booking_id):
    booking = query(MY_BOOKINGS + " AND b.id = ?", (me(), booking_id), one=True) or abort(404)
    if not booking["evaluation_id"] or not booking["results_published"]:
        flash("The result is not published yet.", "info")
        return redirect(url_for("student.history"))
    marks = query("SELECT r.criteria_name, r.max_marks, m.marks FROM evaluation_mark m "
                  "JOIN rubric r ON r.id = m.rubric_id WHERE m.evaluation_id = ? ORDER BY r.id",
                  (booking["evaluation_id"],))
    evaluation = query("SELECT * FROM evaluation WHERE id = ?", (booking["evaluation_id"],), one=True)
    return render_template("student/result.html", booking=booking, marks=marks, evaluation=evaluation)


@student_bp.route("/profile", methods=["GET", "POST"])
@student_required
def profile():
    student = query("SELECT * FROM student WHERE id = ?", (me(),), one=True)
    if request.method == "POST":
        if request.form.get("form") == "password":
            current, new = request.form.get("current_password", ""), request.form.get("new_password", "")
            if not check_password_hash(student["password_hash"], current):
                flash("Current password is wrong.", "danger")
            elif len(new) < 6:
                flash("New password must be at least 6 characters.", "danger")
            else:
                execute("UPDATE student SET password_hash = ? WHERE id = ?", (generate_password_hash(new), me()))
                flash("Password changed.", "success")
                return redirect(url_for("student.profile"))
        else:
            name, phone, dept = clean(request.form, "name"), clean(request.form, "phone"), clean(request.form, "department")
            if not name or not valid_phone(phone):
                flash("Enter your name and a valid phone number (7-15 digits).", "danger")
            else:
                execute("UPDATE student SET name = ?, phone = ?, department = ? WHERE id = ?", (name, phone, dept, me()))
                session["name"] = name
                flash("Profile updated.", "success")
                return redirect(url_for("student.profile"))
    return render_template("student/profile.html", student=student)


@student_bp.route("/search")
@student_required
def search():
    q = request.args.get("q", "").strip()
    if not q:
        return redirect(url_for("student.dashboard"))
    exams = query("SELECT e.*, c.code AS course_code, c.name AS course_name FROM examination e "
                  "JOIN course c ON c.id = e.course_id WHERE e.is_active = 1 AND c.is_active = 1 "
                  "AND (e.name LIKE ? OR c.code LIKE ? OR c.name LIKE ? OR e.exam_type LIKE ?)",
                  (like(q),) * 4)
    return render_template("student/search.html", q=q, exams=exams)
