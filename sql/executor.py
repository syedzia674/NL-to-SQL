import time, decimal, datetime, uuid
from typing import Dict, Any, List
from sql.validator import is_safe_sql

def _safe(val):
    if val is None or isinstance(val,(bool,int,float,str)): return val
    if isinstance(val,decimal.Decimal): f=float(val); return int(f) if f==int(f) else f
    if isinstance(val,(datetime.datetime,datetime.date,datetime.time)): return val.isoformat()
    if isinstance(val,uuid.UUID): return str(val)
    if isinstance(val,bytes): return val.decode("utf-8","replace")
    return str(val)

def _sanitise(rows): return [{k:_safe(v) for k,v in r.items()} for r in rows]

def execute_sql(db, sql):
    if not is_safe_sql(sql):
        raise ValueError("Query blocked: only SELECT queries allowed")
    start = time.time()
    try:
        rows    = db.execute(sql)
        elapsed = round((time.time()-start)*1000, 2)
        if not rows:
            return {"success":True,"data":[],"columns":[],"row_count":0,"execution_time_ms":elapsed}
        clean = _sanitise(rows)
        return {"success":True,"data":clean,"columns":list(clean[0].keys()),"row_count":len(clean),"execution_time_ms":elapsed}
    except Exception as e:
        return {"success":False,"error":str(e),"data":[],"columns":[],"row_count":0,"execution_time_ms":round((time.time()-start)*1000,2)}
