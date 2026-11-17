# Examination Management Portal (EMP)

IITM MAD-1 project. A Flask + SQLite web application where **Admins** manage courses, examinations, rubrics,
examiners and slots, **Examiners** publish exam slots and evaluate students with rubrics, and **Students** book/cancel
slots and view published results.

**Stack:** Python 3 · Flask · Jinja2 · HTML/CSS · Bootstrap 5 (CSS file bundled locally, no internet needed) · SQLite.
No JavaScript is used. All forms are normal HTML forms handled by Flask routes.

## 1. Requirements
* Python 3.9 or newer
* Flask (only external dependency, see `requirements.txt`). Werkzeug and Jinja2 come with Flask.

## 2. Setup (about 2 minutes)
```bash
cd EMP_Project
python -m venv venv                 # optional but recommended
venv\Scripts\activate               # Windows      (Mac/Linux: source venv/bin/activate)
pip install -r requirements.txt
```
Do this **before** the viva so nothing has to be installed during it.

## 3. Run
```bash
python app.py
```
Open **http://127.0.0.1:5000** in the browser. The first run automatically creates `database/emp.db`
(all tables + the Admin account). To reset all data, stop the app and delete `database/emp.db`.

## 4. Default Admin login (pre-created, no registration)
| Field    | Value          |
|----------|----------------|
| Email    | `admin@emp.com` |
| Password | `Admin@123`     |

Change these in `config.py` **before the first run** (or delete `database/emp.db` afterwards).
Examiners and Students register themselves on the home page. **Examiner accounts stay "pending" until the Admin approves them.**

## 5. Suggested first walk-through
1. Log in as Admin → add a **Course** → add an **Examination** (leave examiner empty for now).
2. Register an Examiner and a Student from the home page (log out first).
3. Admin → **Examiners** → open the examiner → *Approve*. Admin → Examinations → *Edit* → assign the examiner.
4. Admin → Examinations → **Rubrics** → add criteria (their max marks must add up to at most the exam's max marks).
5. Examiner logs in → **+ Fill availability** → create a slot (future date).
6. Student logs in → **Slots** → *Book* → confirm. Try booking the same exam twice or a full slot to see the validations.
7. Examiner → dashboard → **+ Rubrics** → enter marks and remarks → Submit.
8. Admin → Examinations → **Timeline** → tick *Results published*. Student → History → open the result.

## 6. Run the automated tests
```bash
python tests/test_workflows.py
```
Uses a temporary database (your data is untouched) and checks registration, login, role permissions, slot creation,
deadlines, booking statistics across statuses, official examination types, booking, capacity, cancellation,
rescheduling, examiner reassignment, evaluation, duplicates, results and database constraints (125 checks).

## 7. Project structure
```
EMP_Project/
├── app.py              Creates the Flask app, registers blueprints, error pages, CSRF check
├── config.py           Secret key, DB path, default Admin credentials, exam types
├── database.py         SQLite schema (all tables), connection helpers, transaction() helper
├── helpers.py          Login/role decorators, CSRF token, input cleaning/validation helpers
├── rules.py            ALL business rules (booking, cancel, reschedule, slots, evaluation)
├── routes/
│   ├── auth.py         Landing page, login, register, logout
│   ├── admin.py        Admin pages
│   ├── examiner.py     Examiner pages
│   └── student.py      Student pages
├── templates/          Jinja2 pages (base.html, _macros.html, auth/, admin/, examiner/, student/)
├── static/css/         bootstrap.min.css (local copy) and style.css (theme colours)
├── static/images/      logo.svg
├── database/           emp.db is created here at first run
├── tests/              test_workflows.py
├── docs/               Project_Report (docx + pdf), VIVA_GUIDE.md, DEMO_VIDEO_OUTLINE.md, AI assistance declaration, ER diagram
└── requirements.txt
```
Full explanation of every module, database table and likely viva questions: **`docs/VIVA_GUIDE.md`**.

## 8. Notes and design decisions
* **Rubric / RubricCriteria:** one `rubric` table holds the marking criteria of an exam, so no separate RubricCriteria table is needed.
* **Timeline per exam:** `examination_timeline` stores slot-creation deadline, booking open/close, cancellation deadline and the
  switches *slot creation enabled*, *booking enabled* and *results published*.
* **Admin overrides:** the Admin can create/edit slots and cancel/reschedule bookings regardless of deadlines.
* **Time zone:** deadlines use the computer's local time.
* **Demo data:** none is bundled. Everything is created through the application itself.
