import sqlite3
from typing import Dict, List
from .base import BaseDatabase

class SQLiteDatabase(BaseDatabase):
    db_type = "sqlite"
    def __init__(self, db_path):
        self.db_path = db_path
        self.conn = None
    def connect(self):
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        return self.conn
    def execute(self, sql, params=None):
        cur = self.conn.cursor()
        cur.execute(sql, params or [])
        rows = cur.fetchall()
        return [_clean_row(dict(r)) for r in rows]
    def get_schema(self):
        schema = {}
        tables = self.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")
        for t in tables:
            tbl = t["name"]
            cols = self.execute(f"PRAGMA table_info('{tbl}')")
            fks  = self.execute(f"PRAGMA foreign_key_list('{tbl}')")
            count = self.execute(f"SELECT COUNT(*) AS n FROM [{tbl}]")[0]["n"]
            samples = {}
            for c in cols:
                try:
                    sv = self.execute(f"SELECT DISTINCT [{c['name']}] FROM [{tbl}] WHERE [{c['name']}] IS NOT NULL LIMIT 4")
                    samples[c["name"]] = [list(r.values())[0] for r in sv]
                except: samples[c["name"]] = []
            schema[tbl] = {
                "row_count": count,
                "columns": [{"column": c["name"], "type": c["type"] or "TEXT", "nullable": not c["notnull"], "primary_key": bool(c["pk"]), "samples": samples.get(c["name"],[])} for c in cols],
                "foreign_keys": [{"column": fk["from"], "ref_table": fk["table"], "ref_column": fk["to"]} for fk in fks],
            }
        return schema
    def disconnect(self):
        if self.conn: self.conn.close(); self.conn = None
    def close(self): self.disconnect()

def _clean_row(row):
    import decimal, datetime, uuid
    clean = {}
    for k, v in row.items():
        if v is None or isinstance(v, (bool, int, float, str)): clean[k] = v
        elif isinstance(v, decimal.Decimal): f=float(v); clean[k]=int(f) if f==int(f) else f
        elif isinstance(v, (datetime.datetime, datetime.date, datetime.time)): clean[k]=v.isoformat()
        elif isinstance(v, uuid.UUID): clean[k]=str(v)
        elif isinstance(v, bytes): clean[k]=v.decode("utf-8","replace")
        else: clean[k]=str(v)
    return clean
