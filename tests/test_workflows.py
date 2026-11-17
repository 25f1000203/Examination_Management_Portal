"""End-to-end tests using Flask's test client and a temporary database.
Run from the project folder:   python tests/test_workflows.py
"""
import os
import re
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta

TMP = tempfile.mkdtemp()
os.environ["EMP_DB_PATH"] = os.path.join(TMP, "test.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app  # noqa: E402
import config  # noqa: E402

app = create_app()
passed = 0


def check(condition, label):
    global passed
    if not condition:
        print("FAIL:", label)
        sys.exit(1)
    passed += 1
    print("ok  :", label)


class Client:
    def __init__(self):
        self.c = app.test_client()
        with self.c.session_transaction() as s:
            s["_csrf"] = "t"

    def post(self, url, **data):
        data["_csrf"] = "t"
        return self.c.post(url, data=data, follow_redirects=True)

    def get(self, url):
        return self.c.get(url, follow_redirects=True)

    def login(self, email, password):
        r = self.post("/login", email=email, password=password)
        with self.c.session_transaction() as s:  # login clears the session, so restore our test token
            s["_csrf"] = "t"
        return r


def text(r):
    return r.get_data(as_text=True)


def dashboard_stat(response, label):
    match = re.search(r'<div class="num">(\d+)</div>\s*<div class="lbl">' + re.escape(label) + r'</div>',
                      text(response))
    return int(match.group(1)) if match else None


tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
day2 = (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%d")
yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M")

admin, ex1, ex2, st1, st2, st3 = (Client() for _ in range(6))

check("Invalid email or password" in text(admin.login("admin@emp.com", "wrong")), "wrong admin password rejected")
check("Admin Dashboard" in text(admin.login(config.ADMIN_EMAIL, config.ADMIN_PASSWORD)), "admin login works")
dashboard = admin.get("/admin/")
check((dashboard_stat(dashboard, "Total Bookings"), dashboard_stat(dashboard, "Active Bookings")) == (0, 0),
      "dashboard shows zero bookings initially")
for cl, name, email, kind in [(ex1, "Exam One", "e1@x.com", "examiner"), (ex2, "Exam Two", "e2@x.com", "examiner"),
                              (st1, "Stu One", "s1@x.com", "student"), (st2, "Stu Two", "s2@x.com", "student"),
                              (st3, "Stu Three", "s3@x.com", "student")]:
    r = cl.post("/register", email=email, password="secret1", name=name, user_type=kind, phone="9876543210", department="CS")
    check("Registration successful" in text(r), f"register {name}")
check("already registered" in text(st1.post("/register", email="s1@x.com", password="secret1", name="x", user_type="student", phone="9876543210")), "duplicate email blocked")
check("Phone number" in text(st1.post("/register", email="n@x.com", password="secret1", name="x", user_type="student", phone="abc")), "bad phone blocked")

r = ex1.login("e1@x.com", "secret1")
check("waiting for Admin approval" in text(r), "pending examiner sees pending page")
check("waiting for Admin approval" in text(ex1.get("/examiner/slots")), "pending examiner cannot open examiner pages")
check(ex1.get("/admin/").status_code == 403, "examiner cannot open admin pages")
check(st1.login("s1@x.com", "secret1") and st1.get("/admin/courses").status_code == 403, "student cannot open admin pages")
check("Please log in" in text(Client().get("/student/")), "anonymous redirected to login")
check(Client().c.post("/login", data={"email": "a", "password": "b"}).status_code == 400, "POST without CSRF token rejected")

admin.post("/admin/examiners/1", action="approve")
admin.post("/admin/examiners/2", action="approve")
check("Examiner Dashboard" in text(ex1.get("/examiner/")), "approved examiner reaches dashboard")
ex2.login("e2@x.com", "secret1")
st2.login("s2@x.com", "secret1")
st3.login("s3@x.com", "secret1")

check("Course added" in text(admin.post("/admin/courses/new", code="cs101", name="Intro CS", description="d", is_active="on")), "course added")
check("already exists" in text(admin.post("/admin/courses/new", code="CS101", name="Dup", is_active="on")), "duplicate course code blocked")
check("Examination added" in text(admin.post("/admin/exams/new", course_id="1", name="Midterm", exam_type="Viva",
      duration_minutes="30", max_marks="20", examiner_id="1", description="", is_active="on")), "exam added")
exam_form = text(admin.get("/admin/exams/new"))
type_select = re.search(
    r'<select class="form-select" id="exam_type" name="exam_type" required>(.*?)</select>', exam_form, re.S)
type_options = re.findall(r'<option value="([^"]*)"', type_select.group(1)) if type_select else []
check(type_options == ["", "Viva", "Practical", "Project Demo", "Assessment"],
      "examination form offers exactly the four official types")
for exam_type in config.EXAM_TYPES:
    check("Examination added" in text(admin.post(
        "/admin/exams/new", course_id="1", name=f"Type check {exam_type}", exam_type=exam_type,
        duration_minutes="30", max_marks="20", examiner_id="1", description="", is_active="on")),
        f"exam type {exam_type} accepted")
check('option value="Viva" selected>Viva</option>' in text(admin.get("/admin/exams/1/edit")),
      "editing an examination selects its current official type")
check("Examination updated" in text(admin.post(
    "/admin/exams/1/edit", course_id="1", name="Midterm", exam_type="Viva",
    duration_minutes="30", max_marks="20", examiner_id="1", description="", is_active="on")),
    "existing examination can be edited")
invalid_exam_type = admin.post(
    "/admin/exams/1/edit", course_id="1", name="Midterm", exam_type="Written",
    duration_minutes="30", max_marks="20", examiner_id="1", description="", is_active="on")
check("Choose a valid examination type." in text(invalid_exam_type)
      and 'option value="Viva" selected>Viva</option>' in text(admin.get("/admin/exams/1/edit")),
      "invalid examination type is rejected without changing the existing record")
connection = sqlite3.connect(os.environ["EMP_DB_PATH"])
legacy_exam_id = connection.execute(
    "INSERT INTO examination (course_id, name, exam_type, duration_minutes, max_marks) "
    "VALUES (?, ?, ?, ?, ?)", (1, "Legacy exam", "Written", 30, 20)
).lastrowid
connection.commit()
connection.close()
legacy_form = text(admin.get(f"/admin/exams/{legacy_exam_id}/edit"))
check("existing value: Written" in legacy_form and 'option value="Written"' not in legacy_form,
      "legacy examination value is shown without adding an invalid type option")
legacy_rejection = text(admin.post(
    f"/admin/exams/{legacy_exam_id}/edit", course_id="1", name="Legacy exam", exam_type="Written",
    duration_minutes="30", max_marks="20", examiner_id="", description="", is_active="on"))
check("Choose a valid examination type." in legacy_rejection
      and "existing value: Written" in text(admin.get(f"/admin/exams/{legacy_exam_id}/edit")),
    "legacy examination remains unchanged until an official type is selected")
for url, role, client in (("/admin/exams", "admin", admin), ("/examiner/exams", "examiner", ex1),
                          ("/student/exams", "student", st1)):
    rendered = text(client.get(url))
    check(all(exam_type in rendered for exam_type in config.EXAM_TYPES),
          f"official examination types display in {role} pages")
check("exceeds" in text(admin.post("/admin/exams/1/rubrics/new", criteria_name="Too big", max_marks="25", weightage="10")), "rubric above exam max blocked")
admin.post("/admin/exams/1/rubrics/new", criteria_name="Concepts", max_marks="10", weightage="50")
admin.post("/admin/exams/1/rubrics/new", criteria_name="Communication", max_marks="10", weightage="50")
check("exceeds" in text(admin.post("/admin/exams/1/rubrics/new", criteria_name="Extra", max_marks="1", weightage="0")), "rubric total capped at exam max")

slot_form = dict(exam_id="1", slot_date=tomorrow, start_time="10:00", end_time="11:00", capacity="2", status="active")
check("Slot created" in text(ex1.post("/examiner/slots/new", **slot_form)), "examiner creates slot")
check("overlapping" in text(ex1.post("/examiner/slots/new", **dict(slot_form, start_time="10:30", end_time="11:30"))), "overlapping slot blocked")
check("not assigned" in text(ex2.post("/examiner/slots/new", **dict(slot_form, start_time="12:00", end_time="13:00"))), "examiner cannot create slot for exam not assigned")
check("future" in text(ex1.post("/examiner/slots/new", **dict(slot_form, slot_date="2020-01-01"))), "past slot blocked")
check("End time" in text(ex1.post("/examiner/slots/new", **dict(slot_form, start_time="15:00", end_time="14:00"))), "end before start blocked")
admin.post("/admin/exams/1/timeline", slot_creation_deadline=yesterday, booking_start="", booking_deadline="", cancellation_deadline="", slot_creation_enabled="on", booking_enabled="on")
check("deadline" in text(ex1.post("/examiner/slots/new", **dict(slot_form, start_time="12:00", end_time="13:00"))), "slot creation deadline enforced")
admin.post("/admin/exams/1/timeline", slot_creation_deadline="", booking_start="", booking_deadline="", cancellation_deadline="", booking_enabled="on")
check("disabled" in text(admin.post("/admin/exams/1/timeline", slot_creation_deadline="", booking_start="", booking_deadline="", cancellation_deadline="") and ex1.post("/examiner/slots/new", **dict(slot_form, start_time="12:00", end_time="13:00"))), "slot creation switch enforced")
admin.post("/admin/exams/1/timeline", slot_creation_deadline="", booking_start="", booking_deadline="", cancellation_deadline="", slot_creation_enabled="on", booking_enabled="on")
check("Slot created" in text(ex1.post("/examiner/slots/new", **dict(slot_form, slot_date=day2, start_time="09:00", end_time="10:00", capacity="5"))), "second slot created")

check("Slot booked successfully" in text(st1.post("/student/book/1")), "student books slot")
dashboard = admin.get("/admin/")
check((dashboard_stat(dashboard, "Total Bookings"), dashboard_stat(dashboard, "Active Bookings")) == (1, 1),
      "dashboard counts only active booking records")
check("already have a booking" in text(st1.post("/student/book/2")), "duplicate booking for same exam blocked")
check("Slot booked successfully" in text(st2.post("/student/book/1")), "second student books slot")
dashboard = admin.get("/admin/")
check((dashboard_stat(dashboard, "Total Bookings"), dashboard_stat(dashboard, "Active Bookings")) == (2, 2),
      "dashboard counts multiple active bookings")
check("slot is full" in text(st3.post("/student/book/1")), "capacity enforced (no overbooking)")
check("2 / 2" in text(admin.get("/admin/slots")), "slot availability auto-updated")
check("Booking cancelled" in text(st2.post("/student/cancel/2")), "student cancels booking")
dashboard = admin.get("/admin/")
check((dashboard_stat(dashboard, "Total Bookings"), dashboard_stat(dashboard, "Active Bookings")) == (2, 1),
      "dashboard total includes cancelled bookings but active count excludes them")
check("1 / 2" in text(admin.get("/admin/slots")), "cancel frees the seat")
check(st3.get("/student/cancel/1").status_code == 404, "student cannot cancel someone else's booking")
admin.post("/admin/exams/1/timeline", slot_creation_deadline="", booking_start="", booking_deadline="", cancellation_deadline=yesterday, slot_creation_enabled="on", booking_enabled="on")
check("cancellation deadline" in text(st1.post("/student/cancel/1")), "cancellation deadline enforced")
admin.post("/admin/exams/1/timeline", slot_creation_deadline="", booking_start="", booking_deadline="", cancellation_deadline="", slot_creation_enabled="on")
check("disabled" in text(st3.post("/student/book/2")), "booking switch enforced")
admin.post("/admin/exams/1/timeline", slot_creation_deadline="", booking_start="", booking_deadline=yesterday, cancellation_deadline="", slot_creation_enabled="on", booking_enabled="on")
check("deadline" in text(st3.post("/student/book/2")), "booking deadline enforced")
admin.post("/admin/exams/1/timeline", slot_creation_deadline="", booking_start="", booking_deadline="", cancellation_deadline="", slot_creation_enabled="on", booking_enabled="on")

check("Booking rescheduled" in text(admin.post("/admin/bookings/1/reschedule", slot_id="2")), "admin reschedules booking")
check("rescheduled" in text(admin.get("/admin/history")), "reschedule recorded in history")
check("Slot updated" in text(admin.post("/admin/slots/2/edit", examiner_id="2", slot_date=day2, start_time="09:00", end_time="10:00", capacity="5", status="active")), "admin reassigns examiner")
check(ex1.get("/examiner/evaluate/1").status_code == 403, "old examiner cannot evaluate after reassignment")
admin.post("/admin/slots/2/edit", examiner_id="1", slot_date=day2, start_time="09:00", end_time="10:00", capacity="5", status="active")

check(ex2.get("/examiner/evaluate/1").status_code == 403, "examiner cannot evaluate another examiner's student")
check("cannot exceed" in text(ex1.post("/examiner/evaluate/1", marks_1="11", marks_2="5", remarks="x")), "marks above rubric limit rejected")
check("valid marks" in text(ex1.post("/examiner/evaluate/1", marks_1="-1", marks_2="5")), "negative marks rejected")
check("Evaluation submitted" in text(ex1.post("/examiner/evaluate/1", marks_1="8", marks_2="7.5", remarks="Good work")), "evaluation submitted")
dashboard = admin.get("/admin/")
check((dashboard_stat(dashboard, "Total Bookings"), dashboard_stat(dashboard, "Active Bookings")) == (2, 0),
      "dashboard total includes completed bookings but active count excludes them")
check("already been evaluated" in text(ex1.post("/examiner/evaluate/1", marks_1="1", marks_2="1")), "duplicate evaluation blocked")
check("15.5" in text(ex1.get("/examiner/history")), "evaluation history shows total")
check("locked" in text(admin.get("/admin/exams/1/rubrics/1/edit")), "rubric locked after evaluation")
check("Only active" in text(st1.post("/student/cancel/1")), "completed booking cannot be cancelled")
check("not published" in text(st1.get("/student/results/1")), "result hidden until published")
admin.post("/admin/exams/1/timeline", slot_creation_deadline="", booking_start="", booking_deadline="", cancellation_deadline="", slot_creation_enabled="on", booking_enabled="on", results_published="on")
r = text(st1.get("/student/results/1"))
check("Good work" in r and "15.5" in r, "published result shows marks and feedback")
check("Slot booked successfully" not in text(st1.post("/student/book/2")), "student cannot re-book an evaluated exam")
check("Slot booked successfully" in text(st3.post("/student/book/2")),
      "student can book a separate available slot after another student completes the exam")
dashboard = admin.get("/admin/")
check((dashboard_stat(dashboard, "Total Bookings"), dashboard_stat(dashboard, "Active Bookings")) == (3, 1),
      "dashboard counts active, cancelled and completed records together")

check("cannot be deleted" in text(admin.post("/admin/exams/1/delete")), "exam with bookings cannot be deleted")
check("booking records" in text(admin.post("/admin/slots/1/delete")), "slot with bookings cannot be deleted")
check("cannot be deleted" in text(admin.post("/admin/courses/1/delete")), "course with exams cannot be deleted")

admin.post("/admin/examiners/2", action="deactivate")
check("deactivated" in text(ex2.get("/examiner/")), "deactivated examiner blocked")
admin.post("/admin/students/3", action="deactivate")
check("deactivated" in text(st3.get("/student/")), "deactivated student blocked")
check("Intro CS" in text(admin.get("/admin/search?q=Intro")), "admin search works")
check("Midterm" in text(st1.get("/student/exams?q=Mid&exam_type=Viva")), "student exam search/filter works")
check("Profile updated" in text(st1.post("/student/profile", form="details", name="Stu Uno", phone="9876500000", department="IT")), "student profile update")
check("Stu Uno" in text(admin.get("/admin/bookings?exam_id=1&student_id=1")), "admin registered-students filter works")

_c = sqlite3.connect(os.environ["EMP_DB_PATH"]); _c.execute("UPDATE slot SET slot_date='2020-01-01' WHERE id=1"); _c.commit(); _c.close()
check("Slot updated" in text(admin.post("/admin/slots/1/edit", examiner_id="1", slot_date="2020-01-01", start_time="10:00", end_time="11:00", capacity="2", status="closed")), "admin can close a past slot")
check("Slot updated" in text(ex1.post("/examiner/slots/1/edit", slot_date="2020-01-01", start_time="10:00", end_time="11:00", capacity="2", status="closed")), "examiner can close a past slot")

admin_pages = ["/admin/", "/admin/courses", "/admin/courses/1/edit", "/admin/exams", "/admin/exams/1/edit", "/admin/exams/1/rubrics",
               "/admin/exams/1/rubrics/new", "/admin/exams/1/timeline", "/admin/examiners", "/admin/examiners/1", "/admin/students",
               "/admin/students/1", "/admin/slots", "/admin/slots/new", "/admin/slots/1/edit", "/admin/bookings", "/admin/history",
               "/admin/search?q=a", "/admin/courses/1/delete", "/admin/slots/1/delete"]
examiner_pages = ["/examiner/", "/examiner/exams", "/examiner/slots", "/examiner/slots/new", "/examiner/slots/1", "/examiner/history", "/examiner/search?q=a"]
student_pages = ["/student/", "/student/exams", "/student/exams/1", "/student/slots", "/student/history", "/student/profile", "/student/search?q=a", "/student/book/2"]
for client, pages in ((admin, admin_pages), (ex1, examiner_pages), (st1, student_pages)):
    for page in pages:
        check(client.c.get(page, follow_redirects=True).status_code == 200, f"page renders: {page}")

admin.post("/admin/examiners/1", action="deactivate")
check("not open for booking" in text(st2.post("/student/book/2")), "slot of deactivated examiner cannot be booked")
check("Book" not in text(st2.get("/student/slots")).replace("Booked", "").replace("Bookings", "").replace("Booking", ""), "slot of deactivated examiner is hidden from students")

conn = sqlite3.connect(os.environ["EMP_DB_PATH"])
conn.execute("PRAGMA foreign_keys=ON")
try:
    conn.execute("UPDATE slot SET booked_count = capacity + 1 WHERE id = 1")
    check(False, "DB should refuse overbooking")
except sqlite3.IntegrityError:
    check(True, "database CHECK constraint blocks overbooking")
try:
    conn.execute("INSERT INTO booking (student_id, slot_id, exam_id) VALUES (1, 2, 1)")
    check(False, "DB should refuse duplicate booking")
except sqlite3.IntegrityError:
    check(True, "database UNIQUE index blocks duplicate live bookings")

print(f"\nALL {passed} CHECKS PASSED")
