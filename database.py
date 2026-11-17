"""Database layer: schema, connection helpers and the transaction helper.

We use Python's built-in sqlite3 module (no ORM) so every SQL statement is visible.
"""
import os
import sqlite3
from contextlib import contextmanager

from flask import g
from werkzeug.security import generate_password_hash

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS admin (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS student (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    phone TEXT,
    department TEXT,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS examiner (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    phone TEXT,
    department TEXT,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'rejected', 'deactivated')),
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS course (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    description TEXT,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1))
);

CREATE TABLE IF NOT EXISTS examination (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id INTEGER NOT NULL REFERENCES course(id),
    name TEXT NOT NULL,
    exam_type TEXT NOT NULL,
    duration_minutes INTEGER NOT NULL CHECK (duration_minutes > 0),
    max_marks REAL NOT NULL CHECK (max_marks > 0),
    examiner_id INTEGER REFERENCES examiner(id),
    description TEXT,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1))
);

-- Rubric = the marking criteria of one examination (this is also the "RubricCriteria" table).
CREATE TABLE IF NOT EXISTS rubric (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    exam_id INTEGER NOT NULL REFERENCES examination(id),
    criteria_name TEXT NOT NULL,
    max_marks REAL NOT NULL CHECK (max_marks > 0),
    weightage REAL NOT NULL DEFAULT 0 CHECK (weightage >= 0 AND weightage <= 100),
    description TEXT
);

-- One row per examination: deadlines and on/off switches. Dates are 'YYYY-MM-DDTHH:MM' text.
CREATE TABLE IF NOT EXISTS examination_timeline (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    exam_id INTEGER NOT NULL UNIQUE REFERENCES examination(id),
    slot_creation_deadline TEXT,
    booking_start TEXT,
    booking_deadline TEXT,
    cancellation_deadline TEXT,
    slot_creation_enabled INTEGER NOT NULL DEFAULT 1 CHECK (slot_creation_enabled IN (0, 1)),
    booking_enabled INTEGER NOT NULL DEFAULT 1 CHECK (booking_enabled IN (0, 1)),
    results_published INTEGER NOT NULL DEFAULT 0 CHECK (results_published IN (0, 1))
);

-- booked_count + the CHECK below make overbooking impossible at database level.
CREATE TABLE IF NOT EXISTS slot (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    exam_id INTEGER NOT NULL REFERENCES examination(id),
    examiner_id INTEGER NOT NULL REFERENCES examiner(id),
    slot_date TEXT NOT NULL,
    start_time TEXT NOT NULL,
    end_time TEXT NOT NULL,
    capacity INTEGER NOT NULL CHECK (capacity > 0),
    booked_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'closed')),
    CHECK (booked_count >= 0 AND booked_count <= capacity)
);

CREATE TABLE IF NOT EXISTS booking (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL REFERENCES student(id),
    slot_id INTEGER NOT NULL REFERENCES slot(id),
    exam_id INTEGER NOT NULL REFERENCES examination(id),
    status TEXT NOT NULL DEFAULT 'booked' CHECK (status IN ('booked', 'cancelled', 'completed')),
    booked_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    cancelled_at TEXT
);

-- A student can hold only ONE live (booked/completed) booking per exam. Cancelled rows are ignored.
CREATE UNIQUE INDEX IF NOT EXISTS one_live_booking_per_exam
    ON booking(student_id, exam_id) WHERE status IN ('booked', 'completed');

CREATE TABLE IF NOT EXISTS evaluation (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    booking_id INTEGER NOT NULL UNIQUE REFERENCES booking(id),
    examiner_id INTEGER NOT NULL REFERENCES examiner(id),
    total_marks REAL NOT NULL CHECK (total_marks >= 0),
    remarks TEXT,
    submitted_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS evaluation_mark (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    evaluation_id INTEGER NOT NULL REFERENCES evaluation(id),
    rubric_id INTEGER NOT NULL REFERENCES rubric(id),
    marks REAL NOT NULL CHECK (marks >= 0),
    UNIQUE (evaluation_id, rubric_id)
);

CREATE TABLE IF NOT EXISTS booking_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    booking_id INTEGER NOT NULL REFERENCES booking(id),
    action TEXT NOT NULL,
    old_slot_id INTEGER,
    new_slot_id INTEGER,
    actor_role TEXT NOT NULL,
    actor_id INTEGER,
    note TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
"""


def _connect():
    conn = sqlite3.connect(config.DATABASE_PATH, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def get_db():
    """One connection per request, stored in Flask's 'g' object."""
    if "db" not in g:
        g.db = _connect()
    return g.db


def close_db(error=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    """Create the database file, all tables and the Admin account (only if missing)."""
    os.makedirs(os.path.dirname(config.DATABASE_PATH), exist_ok=True)
    conn = _connect()
    conn.executescript(SCHEMA)
    if conn.execute("SELECT COUNT(*) FROM admin").fetchone()[0] == 0:
        conn.execute(
            "INSERT INTO admin (name, email, password_hash) VALUES (?, ?, ?)",
            (config.ADMIN_NAME, config.ADMIN_EMAIL, generate_password_hash(config.ADMIN_PASSWORD)),
        )
    conn.close()


def query(sql, args=(), one=False):
    """Run a SELECT. Returns a list of rows, or a single row/None when one=True."""
    rows = get_db().execute(sql, args).fetchall()
    if one:
        return rows[0] if rows else None
    return rows


def execute(sql, args=()):
    """Run INSERT/UPDATE/DELETE. Returns the cursor (use .lastrowid / .rowcount)."""
    return get_db().execute(sql, args)


@contextmanager
def transaction():
    """All-or-nothing block:  with transaction() as db: ...
    BEGIN IMMEDIATE locks the database for writing so two students cannot grab the last seat together.
    If anything raises, everything inside is rolled back."""
    db = get_db()
    db.execute("BEGIN IMMEDIATE")
    try:
        yield db
        db.execute("COMMIT")
    except Exception:
        db.execute("ROLLBACK")
        raise
