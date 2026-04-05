import os

DB_TYPE        = "sqlite"
SQLITE_DB_PATH = "/workspace/talha/texttosql/text2sql_test.db"

LOCAL_LLM_API_BASE = "http://127.0.0.1:7860/v1"
LOCAL_LLM_API_KEY  = ""
MODEL_NAME         = "Gemma3Vision4b"
MODEL_TEMPERATURE  = 0.0
MODEL_MAX_TOKENS   = 512
API_TIMEOUT        = 30

FORBIDDEN_SQL_KEYWORDS = ["DROP","DELETE","UPDATE","ALTER","INSERT","TRUNCATE","REPLACE","CREATE","EXEC"]

LOG_LEVEL  = os.getenv("LOG_LEVEL", "INFO")
LOG_FORMAT = "%(asctime)s - %(levelname)s - %(message)s"
MAX_ROWS   = int(os.getenv("MAX_ROWS", "500"))
HOST       = os.getenv("HOST", "0.0.0.0")
PORT       = int(os.getenv("PORT", "7863"))
