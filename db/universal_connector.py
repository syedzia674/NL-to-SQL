import re, logging, decimal, datetime, uuid
from typing import Dict, List, Optional, Tuple
logger = logging.getLogger(__name__)

def _clean(val):
    if val is None or isinstance(val, (bool, int, float, str)): return val
    if isinstance(val, decimal.Decimal): f=float(val); return int(f) if f==int(f) else f
    if isinstance(val, (datetime.datetime, datetime.date, datetime.time)): return val.isoformat()
    if isinstance(val, uuid.UUID): return str(val)
    if isinstance(val, bytes): return val.decode("utf-8","replace")
    return str(val)

def _clean_rows(rows): return [{k: _clean(v) for k,v in r.items()} for r in rows]

class UniversalDB:
    db_type = "unknown"
    def __init__(self, cs):
        self.connection_string = cs
        self.conn = None
        self._schema_cache = None
    def connect(self): raise NotImplementedError
    def disconnect(self):
        if self.conn:
            try: self.conn.close()
            except: pass
            self.conn = None
    def close(self): self.disconnect()
    def _ensure_connected(self):
        if self.conn is None: self.connect()
    def execute(self, sql, params=None): raise NotImplementedError
    def get_schema(self): raise NotImplementedError
    def test_connection(self):
        try: self._ensure_connected(); self.execute("SELECT 1"); return True, "OK"
        except Exception as e: return False, str(e)
    def invalidate_schema_cache(self): self._schema_cache = None
    @staticmethod
    def detect_type(cs):
        c = cs.strip().lower()
        if c.startswith("sqlite"): return "sqlite"
        if c.startswith("postgresql") or c.startswith("postgres"): return "postgresql"
        if c.startswith("mysql") or c.startswith("mariadb"): return "mysql"
        if c.startswith("mssql") or c.startswith("sqlserver"): return "mssql"
        if c.endswith((".db",".sqlite",".sqlite3")): return "sqlite"
        return "unknown"

class SQLiteConnector(UniversalDB):
    db_type = "sqlite"
    def __init__(self, cs):
        super().__init__(cs)
        c = cs.strip()
        if c.lower().startswith("sqlite:///"): self.db_path = c[10:]
        elif c.lower().startswith("sqlite://"): self.db_path = c[9:]
        else: self.db_path = c
    def connect(self):
        import sqlite3
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
    def execute(self, sql, params=None):
        self._ensure_connected()
        cur = self.conn.cursor(); cur.execute(sql, params or [])
        return _clean_rows([dict(r) for r in cur.fetchall()])
    def get_schema(self):
        if self._schema_cache: return self._schema_cache
        self._ensure_connected()
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
                    sv = self.execute(f"SELECT DISTINCT [{c['column']}] FROM [{tbl}] WHERE [{c['column']}] IS NOT NULL LIMIT 4")
                    samples[c["column"]] = [list(r.values())[0] for r in sv]
                except: samples[c["column"]] = []
            schema[tbl] = {
                "row_count": count,
                "columns": [{"column": c["name"], "type": c["type"] or "TEXT", "nullable": not c["notnull"], "primary_key": bool(c["pk"]), "samples": samples.get(c.get("name",""),[])} for c in cols],
                "foreign_keys": [{"column": fk["from"], "ref_table": fk["table"], "ref_column": fk["to"]} for fk in fks],
            }
        self._schema_cache = schema; return schema

class PostgreSQLConnector(UniversalDB):
    db_type = "postgresql"
    def connect(self):
        try: import psycopg2, psycopg2.extras
        except ImportError: raise ImportError("pip install psycopg2-binary")
        cs = re.sub(r"^postgres://", "postgresql://", self.connection_string)
        self.conn = psycopg2.connect(cs, cursor_factory=psycopg2.extras.RealDictCursor)
        self.conn.autocommit = True
        logger.info("[PostgreSQL] connected")
    def execute(self, sql, params=None):
        self._ensure_connected()
        try:
            with self.conn.cursor() as cur:
                cur.execute(sql, params)
                if cur.description is None: return []
                return _clean_rows([dict(r) for r in cur.fetchall()])
        except Exception as e:
            logger.error(f"[PostgreSQL] execute error: {e} | SQL: {sql[:200]}")
            raise
    def get_schema(self):
        if self._schema_cache: return self._schema_cache
        self._ensure_connected()
        schema = {}
        tables = self.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE' ORDER BY table_name")
        for t in tables:
            tbl = t["table_name"]
            cols = self.execute(f"SELECT column_name,data_type,is_nullable FROM information_schema.columns WHERE table_schema='public' AND table_name='{tbl}' ORDER BY ordinal_position")
            fks  = self.execute(f"""SELECT kcu.column_name,ccu.table_name AS ref_table,ccu.column_name AS ref_column FROM information_schema.table_constraints tc JOIN information_schema.key_column_usage kcu ON tc.constraint_name=kcu.constraint_name AND tc.table_schema=kcu.table_schema JOIN information_schema.constraint_column_usage ccu ON ccu.constraint_name=tc.constraint_name AND ccu.table_schema=tc.table_schema WHERE tc.constraint_type='FOREIGN KEY' AND tc.table_name='{tbl}'""")
            pks  = {r["column_name"] for r in self.execute(f"""SELECT kcu.column_name FROM information_schema.table_constraints tc JOIN information_schema.key_column_usage kcu ON tc.constraint_name=kcu.constraint_name AND tc.table_schema=kcu.table_schema WHERE tc.constraint_type='PRIMARY KEY' AND tc.table_name='{tbl}'""")}
            try: count = self.execute(f'SELECT COUNT(*) AS n FROM "{tbl}"')[0]["n"]
            except: count = 0
            samples = {}
            for c in cols[:6]:
                cn = c["column_name"]
                try:
                    sv = self.execute(f'SELECT DISTINCT "{cn}" FROM "{tbl}" WHERE "{cn}" IS NOT NULL LIMIT 4')
                    samples[cn] = [list(r.values())[0] for r in sv]
                except: samples[cn] = []
            schema[tbl] = {
                "row_count": count,
                "columns": [{"column": c["column_name"], "type": c["data_type"].upper(), "nullable": c["is_nullable"]=="YES", "primary_key": c["column_name"] in pks, "samples": samples.get(c["column_name"],[])} for c in cols],
                "foreign_keys": [{"column": f["column_name"], "ref_table": f["ref_table"], "ref_column": f["ref_column"]} for f in fks],
            }
        self._schema_cache = schema; return schema

class MySQLConnector(UniversalDB):
    db_type = "mysql"
    def connect(self):
        try: import mysql.connector
        except ImportError: raise ImportError("pip install mysql-connector-python")
        cs = re.sub(r"^(mysql|mariadb)://","",self.connection_string)
        m  = re.match(r"([^:]+):([^@]+)@([^:/]+):?(\d+)?/(.+)", cs)
        if not m: raise ValueError(f"Cannot parse MySQL string: {self.connection_string}")
        user,pwd,host,port,db = m.groups()
        self.conn = mysql.connector.connect(host=host,port=int(port or 3306),database=db,user=user,password=pwd,autocommit=True)
        self._dbname = db
    def execute(self, sql, params=None):
        self._ensure_connected()
        cur = self.conn.cursor(dictionary=True); cur.execute(sql,params); rows=cur.fetchall(); cur.close()
        return _clean_rows(rows)
    def get_schema(self):
        if self._schema_cache: return self._schema_cache
        self._ensure_connected()
        schema = {}
        tables = self.execute(f"SELECT table_name FROM information_schema.tables WHERE table_schema='{self._dbname}' AND table_type='BASE TABLE' ORDER BY table_name")
        for t in tables:
            tbl = t["table_name"]
            cols = self.execute(f"SELECT column_name,column_type,is_nullable,column_key FROM information_schema.columns WHERE table_schema='{self._dbname}' AND table_name='{tbl}' ORDER BY ordinal_position")
            fks  = self.execute(f"SELECT column_name,referenced_table_name AS ref_table,referenced_column_name AS ref_column FROM information_schema.key_column_usage WHERE table_schema='{self._dbname}' AND table_name='{tbl}' AND referenced_table_name IS NOT NULL")
            count = self.execute(f"SELECT COUNT(*) AS n FROM `{tbl}`")[0]["n"]
            schema[tbl] = {
                "row_count": count,
                "columns": [{"column": c["column_name"],"type": c["column_type"].upper(),"nullable": c["is_nullable"]=="YES","primary_key": c["column_key"]=="PRI","samples":[]} for c in cols],
                "foreign_keys": [{"column": f["column_name"],"ref_table": f["ref_table"],"ref_column": f["ref_column"]} for f in fks if f.get("ref_table")],
            }
        self._schema_cache = schema; return schema

class MSSQLConnector(UniversalDB):
    db_type = "mssql"
    def connect(self):
        try: import pyodbc
        except ImportError: raise ImportError("pip install pyodbc")
        cs = re.sub(r"^mssql(\+pyodbc)?://","",self.connection_string)
        m  = re.match(r"([^:]+):([^@]+)@([^:/]+):?(\d+)?/(.+)",cs)
        if not m: raise ValueError(f"Cannot parse MSSQL: {self.connection_string}")
        user,pwd,host,port,db = m.groups()
        self.conn = pyodbc.connect(f"DRIVER={{ODBC Driver 17 for SQL Server}};SERVER={host},{port or 1433};DATABASE={db};UID={user};PWD={pwd}",autocommit=True)
    def execute(self, sql, params=None):
        self._ensure_connected()
        cur = self.conn.cursor(); cur.execute(sql,params or [])
        if cur.description:
            cols=[d[0] for d in cur.description]
            return _clean_rows([dict(zip(cols,row)) for row in cur.fetchall()])
        return []
    def get_schema(self):
        if self._schema_cache: return self._schema_cache
        self._ensure_connected()
        schema = {}
        tables = self.execute("SELECT table_name FROM information_schema.tables WHERE table_type='BASE TABLE' AND table_catalog=DB_NAME() ORDER BY table_name")
        for t in tables:
            tbl = t["table_name"]
            cols = self.execute(f"SELECT column_name,data_type,is_nullable FROM information_schema.columns WHERE table_name='{tbl}' ORDER BY ordinal_position")
            count = self.execute(f"SELECT COUNT(*) AS n FROM [{tbl}]")[0]["n"]
            schema[tbl] = {"row_count":count,"columns":[{"column":c["column_name"],"type":c["data_type"].upper(),"nullable":c["is_nullable"]=="YES","primary_key":False,"samples":[]} for c in cols],"foreign_keys":[]}
        self._schema_cache = schema; return schema

def connect(connection_string):
    cs   = connection_string.strip()
    kind = UniversalDB.detect_type(cs)
    mapping = {"sqlite":SQLiteConnector,"postgresql":PostgreSQLConnector,"mysql":MySQLConnector,"mssql":MSSQLConnector}
    cls = mapping.get(kind)
    if not cls: raise ValueError(f"Unsupported DB: {kind}")
    instance = cls(cs); instance.connect(); return instance
