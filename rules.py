"""Business rules. Every important validation lives here (backend), not in the HTML.

Each function returns (True, message) or (False, error message) so routes can just flash it.
"""
import sqlite3
from datetime import datetime

from database import execute, query, transaction
from helpers import clean, now_str, to_float, to_int


def get_timeline(exam_id):
    return query("SELECT * FROM examination_timeline WHERE exam_id = ?", (exam_id,), one=True)


def add_history(booking_id, action, actor_role, actor_id, old_slot=None, new_slot=None, note=""):
    """Write one row to BookingHistory. Always called inside the same transaction as the change."""
    execute(
        "INSERT INTO booking_history (booking_id, action, old_slot_id, new_slot_id, actor_role, actor_id, note) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (booking_id, action, old_slot, new_slot, actor_role, actor_id, note),
    )


def book_slot(student_id, slot_id):
    slot = query(
        "SELECT s.*, e.is_active AS exam_active, c.is_active AS course_active, x.status AS examiner_status "
        "FROM slot s JOIN examination e ON e.id = s.exam_id JOIN course c ON c.id = e.course_id "
        "JOIN examiner x ON x.id = s.examiner_id WHERE s.id = ?", (slot_id,), one=True)
    if not slot:
        return False, "Slot not found."
    if (slot["status"] != "active" or not slot["exam_active"] or not slot["course_active"]
            or slot["examiner_status"] != "approved"):
        return False, "This slot is not open for booking."

    timeline = get_timeline(slot["exam_id"])
    now = now_str()
    if not timeline["booking_enabled"]:
        return False, "Bookings are currently disabled for this examination."
    if timeline["booking_start"] and now < timeline["booking_start"]:
        return False, "Booking for this examination has not opened yet."
    if timeline["booking_deadline"] and now > timeline["booking_deadline"]:
        return False, "The booking deadline for this examination has passed."
    if f"{slot['slot_date']}T{slot['start_time']}" <= now:
        return False, "This slot has already started or finished."

    duplicate = query(
        "SELECT id FROM booking WHERE student_id = ? AND exam_id = ? AND status IN ('booked', 'completed')",
        (student_id, slot["exam_id"]), one=True)
    if duplicate:
        return False, "You already have a booking for this examination."

    try:
        with transaction():
            cur = execute("UPDATE slot SET booked_count = booked_count + 1 "
                          "WHERE id = ? AND status = 'active' AND booked_count < capacity", (slot_id,))
            if cur.rowcount == 0:
                raise ValueError("Sorry, this slot is full.")
            cur = execute("INSERT INTO booking (student_id, slot_id, exam_id) VALUES (?, ?, ?)",
                          (student_id, slot_id, slot["exam_id"]))
            add_history(cur.lastrowid, "booked", "student", student_id, new_slot=slot_id)
    except ValueError as error:
        return False, str(error)
    except sqlite3.IntegrityError:
        return False, "You already have a booking for this examination."
    return True, "Slot booked successfully."


def cancel_booking(booking_id, actor_role, actor_id, enforce_deadlines=True):
    booking = query(
        "SELECT b.*, s.slot_date, s.start_time FROM booking b JOIN slot s ON s.id = b.slot_id "
        "WHERE b.id = ?", (booking_id,), one=True)
    if not booking:
        return False, "Booking not found."
    if actor_role == "student" and booking["student_id"] != actor_id:
        return False, "You can only cancel your own bookings."
    if booking["status"] != "booked":
        return False, "Only active bookings can be cancelled."

    if enforce_deadlines:  # the Admin can override deadlines
        timeline = get_timeline(booking["exam_id"])
        now = now_str()
        if timeline["cancellation_deadline"]:
            if now > timeline["cancellation_deadline"]:
                return False, "The cancellation deadline for this examination has passed."
        elif f"{booking['slot_date']}T{booking['start_time']}" <= now:
            return False, "The slot has already started, so it cannot be cancelled."

    with transaction():
        cur = execute("UPDATE booking SET status = 'cancelled', cancelled_at = datetime('now','localtime') "
                      "WHERE id = ? AND status = 'booked'", (booking_id,))
        if cur.rowcount == 1:
            execute("UPDATE slot SET booked_count = booked_count - 1 WHERE id = ?", (booking["slot_id"],))
            add_history(booking_id, "cancelled", actor_role, actor_id, old_slot=booking["slot_id"])
    return True, "Booking cancelled. The seat is available again."


def reschedule_booking(booking_id, new_slot_id, admin_id):
    booking = query("SELECT * FROM booking WHERE id = ?", (booking_id,), one=True)
    new_slot = query("SELECT * FROM slot WHERE id = ?", (new_slot_id,), one=True)
    if not booking or booking["status"] != "booked":
        return False, "Only active (booked) bookings can be rescheduled."
    if not new_slot or new_slot["exam_id"] != booking["exam_id"]:
        return False, "Choose a slot of the same examination."
    if new_slot["id"] == booking["slot_id"]:
        return False, "The booking is already in that slot."
    if new_slot["status"] != "active":
        return False, "The selected slot is closed."
    try:
        with transaction():
            cur = execute("UPDATE slot SET booked_count = booked_count + 1 "
                          "WHERE id = ? AND booked_count < capacity", (new_slot_id,))
            if cur.rowcount == 0:
                raise ValueError("The selected slot is full.")
            execute("UPDATE slot SET booked_count = booked_count - 1 WHERE id = ?", (booking["slot_id"],))
            execute("UPDATE booking SET slot_id = ? WHERE id = ?", (new_slot_id, booking_id))
            add_history(booking_id, "rescheduled", "admin", admin_id,
                        old_slot=booking["slot_id"], new_slot=new_slot_id)
    except ValueError as error:
        return False, str(error)
    return True, "Booking rescheduled."


def reassign_slot_examiner(slot_id, new_examiner_id, admin_id):
    slot = query("SELECT * FROM slot WHERE id = ?", (slot_id,), one=True)
    examiner = query("SELECT * FROM examiner WHERE id = ? AND status = 'approved'", (new_examiner_id,), one=True)
    if not slot or not examiner:
        return False, "Choose an approved examiner."
    if slot["examiner_id"] == new_examiner_id:
        return True, ""
    evaluated = query("SELECT b.id FROM booking b JOIN evaluation ev ON ev.booking_id = b.id "
                      "WHERE b.slot_id = ?", (slot_id,))
    if evaluated:
        return False, "This slot already has evaluations, so its examiner cannot be changed."
    clash = query("SELECT id FROM slot WHERE examiner_id = ? AND slot_date = ? AND id != ? "
                  "AND start_time < ? AND end_time > ?",
                  (new_examiner_id, slot["slot_date"], slot_id, slot["end_time"], slot["start_time"]), one=True)
    if clash:
        return False, "That examiner already has an overlapping slot."
    with transaction():
        execute("UPDATE slot SET examiner_id = ? WHERE id = ?", (new_examiner_id, slot_id))
        for b in query("SELECT id FROM booking WHERE slot_id = ? AND status = 'booked'", (slot_id,)):
            add_history(b["id"], "examiner_reassigned", "admin", admin_id, old_slot=slot_id, new_slot=slot_id,
                        note=f"Examiner changed from #{slot['examiner_id']} to #{new_examiner_id}")
    return True, ""


def examiner_slot_permission(exam_id, examiner_id):
    """Can this examiner create/edit/delete slots for this exam right now? Returns an error text or None."""
    exam = query("SELECT * FROM examination WHERE id = ?", (exam_id,), one=True)
    if not exam or exam["examiner_id"] != examiner_id:
        return "This examination is not assigned to you."
    if not exam["is_active"]:
        return "This examination is not active."
    timeline = get_timeline(exam_id)
    if not timeline["slot_creation_enabled"]:
        return "Slot creation has been disabled by the Admin for this examination."
    if timeline["slot_creation_deadline"] and now_str() > timeline["slot_creation_deadline"]:
        return "The slot creation deadline for this examination has passed."
    return None


def validate_slot_form(form, examiner_id, slot_id=None, min_capacity=1, original=None):
    """Check the slot form. Returns (clean_data, list_of_errors).
    original = the existing slot row when editing. The "must be in the future" rule is skipped if the
    date and start time were not changed, so an old slot can still be closed."""
    errors = []
    data = {
        "slot_date": clean(form, "slot_date"),
        "start_time": clean(form, "start_time")[:5],
        "end_time": clean(form, "end_time")[:5],
        "capacity": to_int(clean(form, "capacity")),
        "status": clean(form, "status") or "active",
    }
    try:
        datetime.strptime(data["slot_date"], "%Y-%m-%d")
        datetime.strptime(data["start_time"], "%H:%M")
        datetime.strptime(data["end_time"], "%H:%M")
    except ValueError:
        errors.append("Enter a valid date, start time and end time.")
    if data["capacity"] is None or data["capacity"] < 1:
        errors.append("Capacity must be a whole number of at least 1.")
    elif data["capacity"] < min_capacity:
        errors.append(f"Capacity cannot be lower than the {min_capacity} student(s) already booked.")
    if data["status"] not in ("active", "closed"):
        errors.append("Invalid status.")
    if not errors:
        if data["start_time"] >= data["end_time"]:
            errors.append("End time must be after start time.")
        time_changed = original is None or (original["slot_date"], original["start_time"]) != \
                       (data["slot_date"], data["start_time"])
        if time_changed and f"{data['slot_date']}T{data['start_time']}" <= now_str():
            errors.append("The slot must start in the future.")
        clash = query("SELECT id FROM slot WHERE examiner_id = ? AND slot_date = ? AND id != ? "
                      "AND start_time < ? AND end_time > ?",
                      (examiner_id, data["slot_date"], slot_id or 0, data["end_time"], data["start_time"]), one=True)
        if clash:
            errors.append("This examiner already has an overlapping slot at that time.")
    return data, errors


def create_slot(exam_id, examiner_id, data):
    cur = execute("INSERT INTO slot (exam_id, examiner_id, slot_date, start_time, end_time, capacity, status) "
                  "VALUES (?, ?, ?, ?, ?, ?, ?)",
                  (exam_id, examiner_id, data["slot_date"], data["start_time"], data["end_time"],
                   data["capacity"], data["status"]))
    return cur.lastrowid


def update_slot(slot, data, actor_role, actor_id):
    with transaction():
        execute("UPDATE slot SET slot_date=?, start_time=?, end_time=?, capacity=?, status=? WHERE id=?",
                (data["slot_date"], data["start_time"], data["end_time"], data["capacity"], data["status"], slot["id"]))
        changed = (slot["slot_date"], slot["start_time"], slot["end_time"]) != \
                  (data["slot_date"], data["start_time"], data["end_time"])
        if changed:  # keep a trace for every booked student
            for b in query("SELECT id FROM booking WHERE slot_id = ? AND status = 'booked'", (slot["id"],)):
                add_history(b["id"], "slot_time_changed", actor_role, actor_id, old_slot=slot["id"],
                            new_slot=slot["id"],
                            note=f"New time: {data['slot_date']} {data['start_time']}-{data['end_time']}")


def delete_slot(slot_id):
    if query("SELECT id FROM booking WHERE slot_id = ?", (slot_id,), one=True):
        return False, "This slot has booking records (history is preserved). Close it instead of deleting."
    execute("DELETE FROM slot WHERE id = ?", (slot_id,))
    return True, "Slot deleted."


def save_evaluation(examiner_id, booking_id, form):
    booking = query(
        "SELECT b.*, s.examiner_id AS slot_examiner FROM booking b JOIN slot s ON s.id = b.slot_id "
        "WHERE b.id = ?", (booking_id,), one=True)
    if not booking or booking["slot_examiner"] != examiner_id:
        return False, "You can only evaluate students booked into your own slots."
    if query("SELECT id FROM evaluation WHERE booking_id = ?", (booking_id,), one=True):
        return False, "This student has already been evaluated. Evaluations cannot be repeated."
    if booking["status"] != "booked":
        return False, "Only active bookings can be evaluated."

    rubrics = query("SELECT * FROM rubric WHERE exam_id = ? ORDER BY id", (booking["exam_id"],))
    if not rubrics:
        return False, "No rubric has been defined for this examination yet. Ask the Admin to add one."

    marks, total, errors = {}, 0.0, []
    for r in rubrics:
        value = to_float(clean(form, f"marks_{r['id']}"))
        if value is None or value < 0:
            errors.append(f"Enter valid marks (0 or more) for '{r['criteria_name']}'.")
        elif value > r["max_marks"]:
            errors.append(f"Marks for '{r['criteria_name']}' cannot exceed {r['max_marks']:g}.")
        else:
            marks[r["id"]] = round(value, 2)
            total += value
    if errors:
        return False, " ".join(errors)

    try:
        with transaction():
            cur = execute("INSERT INTO evaluation (booking_id, examiner_id, total_marks, remarks) VALUES (?,?,?,?)",
                          (booking_id, examiner_id, round(total, 2), clean(form, "remarks")))
            for rubric_id, value in marks.items():
                execute("INSERT INTO evaluation_mark (evaluation_id, rubric_id, marks) VALUES (?,?,?)",
                        (cur.lastrowid, rubric_id, value))
            execute("UPDATE booking SET status = 'completed' WHERE id = ?", (booking_id,))
            add_history(booking_id, "evaluated", "examiner", examiner_id, old_slot=booking["slot_id"],
                        note=f"Total marks {round(total, 2):g}")
    except sqlite3.IntegrityError:
        return False, "This student has already been evaluated."
    return True, "Evaluation submitted."
