"""Project settings. Change values here, not inside the code."""
import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

SECRET_KEY = os.environ.get("EMP_SECRET_KEY", "emp-local-secret-key-change-me")

DATABASE_PATH = os.environ.get("EMP_DB_PATH", os.path.join(BASE_DIR, "database", "emp.db"))

ADMIN_NAME = "System Administrator"
ADMIN_EMAIL = "admin@emp.com"
ADMIN_PASSWORD = "Admin@123"

EXAM_TYPES = ["Viva", "Practical", "Project Demo", "Assessment"]
