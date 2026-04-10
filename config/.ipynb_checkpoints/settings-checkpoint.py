import os

# ── Default DB ────────────────────────────────────────────────
DB_TYPE        = "sqlite"
SQLITE_DB_PATH = "/workspace/talha/texttosql/text2sql_test.db"

# ── Google Gemini (Google AI Studio) ─────────────────────────
GOOGLE_API_KEY    = "AIzaSyAA0eSxCQjY5GZ_c-BGqNCrg914gYPf0Ko"
MODEL_NAME        = "gemini-2.5-flash"
MODEL_TEMPERATURE = 0.0
MODEL_MAX_TOKENS  = 1024

# ── Safety ────────────────────────────────────────────────────
FORBIDDEN_SQL_KEYWORDS = [
    "DROP","DELETE","UPDATE","ALTER","INSERT",
    "TRUNCATE","REPLACE","CREATE","EXEC",
]

# ── Logging ───────────────────────────────────────────────────
LOG_LEVEL  = os.getenv("LOG_LEVEL", "INFO")
LOG_FORMAT = "%(asctime)s - %(levelname)s - %(message)s"

# ── App ───────────────────────────────────────────────────────
MAX_ROWS = int(os.getenv("MAX_ROWS", "500"))
HOST     = os.getenv("HOST", "0.0.0.0")
PORT     = int(os.getenv("PORT", "7863"))
