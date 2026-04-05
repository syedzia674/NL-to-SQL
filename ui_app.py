"""
ui_app.py — Infusion Solutions · NL→SQL Intelligence Platform
Port 7863 · FastAPI + HTML/CSS/JS UI

All fixes applied:
  ✅ Decimal/datetime/UUID JSON serialisation (PostgreSQL Neon)
  ✅ NoneType cursor → _ensure_connected() guard
  ✅ MODEL_TEMPERATURE/MAX_TOKENS/API_TIMEOUT as proper types
  ✅ LangChain lazy init + base_url/openai_api_base compat
  ✅ Data browser shows data for all DB types
  ✅ DB Switcher with named connections
  ✅ clear_cache() on every DB switch
  ✅ db_type passed to prompt for correct SQL dialect
"""

import os, json, logging, decimal, datetime, uuid
from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel

from db.sqlite import SQLiteDatabase
from db.universal_connector import connect as uc_connect
import pipeline.text_to_sql as pipe
from reporting.exporter import export_report
from config import settings

logging.basicConfig(level=getattr(logging, settings.LOG_LEVEL, "INFO"), format=settings.LOG_FORMAT)
logger = logging.getLogger(__name__)
app = FastAPI(title="NL→SQL · Infusion Solutions")

# ── JSON encoder that handles all PostgreSQL types ─────────────────────────
class _Enc(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, decimal.Decimal):
            f = float(obj); return int(f) if f == int(f) else f
        if isinstance(obj, (datetime.datetime, datetime.date, datetime.time)):
            return obj.isoformat()
        if isinstance(obj, uuid.UUID): return str(obj)
        if isinstance(obj, bytes): return obj.decode("utf-8", "replace")
        return super().default(obj)

def JSONResponse(content, status_code=200, headers=None):
    return Response(content=json.dumps(content, cls=_Enc, ensure_ascii=False),
                    status_code=status_code, headers=headers, media_type="application/json")

# ── State ──────────────────────────────────────────────────────────────────
db = None
_db_meta = {"label": "Default DB", "db_type": "sqlite"}
_registered: dict = {}

# ── Lifecycle ──────────────────────────────────────────────────────────────
@app.on_event("startup")
def startup():
    global db, _db_meta
    os.makedirs("exports", exist_ok=True); os.makedirs("data", exist_ok=True)
    db = SQLiteDatabase(settings.SQLITE_DB_PATH); db.connect()
    name = os.path.basename(settings.SQLITE_DB_PATH)
    _db_meta = {"label": name, "db_type": "sqlite"}
    logger.info(f"Connected: {name}")

@app.on_event("shutdown")
def shutdown():
    if db:
        try: db.disconnect()
        except: pass

# ── Health ─────────────────────────────────────────────────────────────────
@app.get("/health")
def health():
    return JSONResponse({"status":"ok","db":_db_meta.get("label",""),"db_type":_db_meta.get("db_type",""),"llm":settings.MODEL_NAME})

# ── Connect ────────────────────────────────────────────────────────────────
class ConnectRequest(BaseModel):
    connection_string: str
    label: str = ""

@app.post("/connect")
def connect_db(req: ConnectRequest):
    global db, _db_meta
    cs = req.connection_string.strip()
    if not cs: raise HTTPException(400, "connection_string required")
    try:
        new_db = uc_connect(cs)
        ok, msg = new_db.test_connection()
        if not ok: raise Exception(msg)
        pipe.clear_cache()
        if db:
            try: db.disconnect()
            except: pass
        db = new_db
        schema = db.get_schema()
        label = req.label.strip() or cs.split("/")[-1].split("?")[0] or "database"
        _db_meta = {"label": label, "db_type": new_db.db_type}
        return JSONResponse({"success":True,"label":label,"db_type":new_db.db_type,"tables":len(schema),"total_rows":sum(v.get("row_count",0) for v in schema.values())})
    except Exception as e:
        logger.exception("Connect failed"); raise HTTPException(400, str(e))

# ── Named connections (DB Switcher) ────────────────────────────────────────
class RegisterRequest(BaseModel):
    name: str
    connection_string: str

@app.post("/register")
def register_connection(req: RegisterRequest):
    name = req.name.strip(); cs = req.connection_string.strip()
    if not name or not cs: raise HTTPException(400, "name and connection_string required")
    _registered[name] = cs
    return JSONResponse({"success":True,"name":name,"total":len(_registered)})

@app.get("/connections")
def list_connections():
    return JSONResponse({"connections":[{"name":n,"connection_string":cs} for n,cs in _registered.items()],"current":_db_meta.get("label","")})

class SwitchRequest(BaseModel):
    name: str

@app.post("/switch")
def switch_connection(req: SwitchRequest):
    global db, _db_meta
    name = req.name.strip()
    if name not in _registered: raise HTTPException(404, f"No connection named '{name}'")
    cs = _registered[name]
    try:
        new_db = uc_connect(cs)
        ok, msg = new_db.test_connection()
        if not ok: raise Exception(f"Connection test failed: {msg}")
        pipe.clear_cache()
        if db:
            try: db.disconnect()
            except: pass
        db = new_db
        schema = db.get_schema()
        _db_meta = {"label": name, "db_type": new_db.db_type}
        return JSONResponse({"success":True,"label":name,"db_type":new_db.db_type,"tables":len(schema),"total_rows":sum(v.get("row_count",0) for v in schema.values())})
    except Exception as e:
        logger.exception(f"Switch to '{name}' failed"); raise HTTPException(400, str(e))

@app.delete("/connections/{name}")
def delete_connection(name: str):
    if name in _registered: del _registered[name]; return JSONResponse({"success":True,"deleted":name})
    raise HTTPException(404, f"No connection named '{name}'")

# ── Connection info ────────────────────────────────────────────────────────
@app.get("/connection-info")
def connection_info():
    try:
        tables = 0; total_rows = 0
        if db:
            try: schema=db.get_schema(); tables=len(schema); total_rows=sum(v.get("row_count",0) for v in schema.values())
            except: pass
        return JSONResponse({"label":_db_meta.get("label",""),"db_type":_db_meta.get("db_type",""),"tables":tables,"total_rows":total_rows,"connected":db is not None})
    except Exception as e: raise HTTPException(500, str(e))

# ── Schema ─────────────────────────────────────────────────────────────────
@app.get("/schema")
def get_schema():
    try:
        schema = db.get_schema(); result = {}
        for table, info in schema.items():
            result[table] = {
                "row_count": info.get("row_count",0),
                "columns": [{"name": c.get("column") or c.get("name",""), "type": c.get("type",""), "pk": c.get("primary_key",False)} for c in info.get("columns",[])],
                "foreign_keys": info.get("foreign_keys",[]),
            }
        return JSONResponse(result)
    except Exception as e: raise HTTPException(500, str(e))

# ── Table preview ──────────────────────────────────────────────────────────
@app.get("/table/{table_name}")
def preview_table(table_name: str, limit: int = 50):
    try:
        db_type = getattr(db, "db_type", "sqlite")
        sql = f"SELECT TOP {limit} * FROM [{table_name}]" if db_type=="mssql" else \
              f"SELECT * FROM `{table_name}` LIMIT {limit}" if db_type=="mysql" else \
              f'SELECT * FROM "{table_name}" LIMIT {limit}'
        # db.execute() already returns clean JSON-safe dicts
        rows = db.execute(sql)
        if rows:
            columns = list(rows[0].keys())
        else:
            schema = db.get_schema(); info = schema.get(table_name,{})
            columns = [c.get("column") or c.get("name","") for c in info.get("columns",[])]
        return JSONResponse({"columns":columns,"rows":rows,"table":table_name})
    except Exception as e:
        logger.exception(f"preview_table error: {e}"); raise HTTPException(500, str(e))

# ── NL Query ───────────────────────────────────────────────────────────────
class QueryRequest(BaseModel):
    question: str
    verbose: bool = False

@app.post("/query")
def query(req: QueryRequest):
    if not db: raise HTTPException(503, "No database connected")
    try:
        response = pipe.run_text_to_sql(db, req.question, verbose=req.verbose)
        return JSONResponse(response)
    except Exception as e:
        logger.exception("Query failed"); raise HTTPException(500, str(e))

# ── Export ─────────────────────────────────────────────────────────────────
class ExportRequest(BaseModel):
    data: list; columns: list; format: str

@app.post("/export")
def export(req: ExportRequest):
    try: return JSONResponse({"file": export_report(req.data, req.columns, req.format)})
    except Exception as e: raise HTTPException(500, str(e))

@app.get("/download")
def download(path: str):
    exports_dir = os.path.abspath("exports"); requested = os.path.abspath(path)
    if not requested.startswith(exports_dir): raise HTTPException(403, "Forbidden")
    if not os.path.exists(requested): raise HTTPException(404, "File not found")
    return FileResponse(requested, filename=os.path.basename(requested), media_type="application/octet-stream")

# ── UI ─────────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
def home():
    return r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Infusion Solutions · NL→SQL</title>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{
  --accent:#1a56db;--accent-lt:#eff6ff;--accent2:#7c3aed;
  --success:#059669;--success-lt:#ecfdf5;
  --warn:#d97706;--danger:#dc2626;--danger-lt:#fef2f2;
  --text:#0f172a;--text2:#374151;--muted:#6b7280;--dim:#9ca3af;
  --bg:#f8fafc;--surface:#f1f5f9;--border:#e2e8f0;--border2:#cbd5e1;
  --r:8px;--mono:'DM Mono',monospace;--sans:'Inter',system-ui,sans-serif;
}
html,body{height:100%;font-family:var(--sans);background:var(--bg);color:var(--text);font-size:13px}
#app{display:flex;flex-direction:column;height:100vh;overflow:hidden}
#topbar{height:52px;display:flex;align-items:center;gap:12px;padding:0 18px;background:#fff;border-bottom:1.5px solid var(--border);flex-shrink:0;z-index:10}
.logo-mark{width:34px;height:34px;border-radius:8px;display:flex;align-items:center;justify-content:center;font-weight:800;font-size:13px;color:#fff;background:linear-gradient(135deg,#1a56db,#3b82f6);flex-shrink:0}
.logo-text b{font-size:14px;color:var(--text)}.logo-text b span{color:var(--accent)}
.logo-text small{font-size:10px;color:var(--muted);font-weight:500;display:block}
.divider{width:1px;height:22px;background:var(--border)}
.nav-btn{display:inline-flex;align-items:center;gap:6px;padding:6px 13px;border-radius:var(--r);border:1.5px solid var(--border2);background:#fff;color:var(--text2);font-size:12px;font-weight:600;cursor:pointer;transition:all .15s}
.nav-btn:hover{border-color:var(--accent);color:var(--accent);background:var(--accent-lt)}
.nav-btn.primary{background:var(--accent);color:#fff;border-color:var(--accent)}
.nav-btn.primary:hover{background:#1648c0}
.topbar-right{margin-left:auto;display:flex;align-items:center;gap:8px}
.db-pill{display:flex;align-items:center;gap:6px;padding:5px 12px;border-radius:20px;background:var(--surface);border:1px solid var(--border);font-size:11px;font-family:var(--mono);color:var(--text2);max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;cursor:pointer;transition:all .15s}
.db-pill:hover{border-color:var(--accent);color:var(--accent)}
.status-pill{display:flex;align-items:center;gap:7px;padding:5px 12px;border-radius:20px;background:var(--success-lt);border:1px solid rgba(5,150,105,.2);font-size:11px;font-weight:600;color:var(--success)}
.sdot{width:7px;height:7px;border-radius:50%;background:var(--success);box-shadow:0 0 6px rgba(5,150,105,.5);animation:blink 2.5s infinite;flex-shrink:0}
@keyframes blink{0%,100%{opacity:1}50%{opacity:.5}}
#query-section{padding:12px 18px 8px;background:#fff;border-bottom:1.5px solid var(--border);flex-shrink:0}
.ql{font-size:9px;font-weight:700;letter-spacing:.12em;text-transform:uppercase;color:var(--muted);margin-bottom:6px}
.qrow{display:flex;gap:8px;align-items:flex-start}
#nl-input{flex:1;padding:9px 14px;border:1.5px solid var(--border2);border-radius:var(--r);font-size:13px;color:var(--text);background:#fff;outline:none;resize:none;overflow:hidden;line-height:1.5;min-height:38px;font-family:var(--sans);transition:border-color .2s,box-shadow .2s}
#nl-input:focus{border-color:var(--accent);box-shadow:0 0 0 3px rgba(26,86,219,.1)}
#run-btn{padding:9px 18px;background:var(--accent);color:#fff;border:none;border-radius:var(--r);font-size:12px;font-weight:700;cursor:pointer;display:flex;align-items:center;gap:7px;white-space:nowrap;transition:background .15s;flex-shrink:0}
#run-btn:hover{background:#1648c0}#run-btn:disabled{background:#93afd4;cursor:not-allowed}
.sugg-row{display:flex;gap:6px;flex-wrap:wrap;margin-top:7px;max-height:0;overflow:hidden;opacity:0;transition:all .25s}
.sugg-row.open{max-height:80px;opacity:1}
.chip{padding:4px 11px;border-radius:20px;border:1px solid var(--border2);background:#fff;color:var(--text2);font-size:11px;cursor:pointer;transition:all .15s}
.chip:hover{border-color:var(--accent);color:var(--accent);background:var(--accent-lt)}
.ex-toggle{display:inline-flex;align-items:center;gap:4px;padding:4px 10px;border-radius:20px;border:1px solid var(--border);background:#fff;color:var(--muted);font-size:11px;cursor:pointer;margin-top:6px;transition:all .15s}
.ex-toggle:hover{color:var(--accent)}.ex-toggle svg{transition:transform .2s}.ex-toggle.open svg{transform:rotate(180deg)}
#drag-handle{height:7px;background:var(--surface);cursor:ns-resize;display:flex;align-items:center;justify-content:center;border-bottom:1px solid var(--border);flex-shrink:0;transition:background .15s}
#drag-handle:hover,#drag-handle.drag{background:#e4ecff}
#drag-handle::after{content:'';width:36px;height:3px;border-radius:2px;background:var(--border2)}
#drag-handle:hover::after,#drag-handle.drag::after{background:var(--accent)}
#results-section{flex:1;overflow:hidden;display:flex;flex-direction:column;background:#fff}
#results-header{display:none;padding:9px 18px;border-bottom:1px solid var(--border);align-items:center;gap:10px;flex-shrink:0}
.rh-count{font-size:12px;font-weight:700;color:var(--text)}.rh-time{font-size:11px;color:var(--muted)}
.exp-btn{margin-left:auto;display:inline-flex;align-items:center;gap:5px;padding:5px 12px;border-radius:var(--r);border:1.5px solid var(--border2);background:#fff;color:var(--text2);font-size:11px;font-weight:600;cursor:pointer;transition:all .15s}
.exp-btn+.exp-btn{margin-left:6px}.exp-btn:hover{border-color:var(--accent);color:var(--accent);background:var(--accent-lt)}
#sql-strip{display:none;padding:7px 18px;background:#f8faff;border-bottom:1px solid var(--border);font-family:var(--mono);font-size:11px;color:#4338ca;white-space:nowrap;overflow-x:auto;flex-shrink:0}
#sql-strip .label{color:var(--dim)}
#empty-state{flex:1;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:10px;padding:40px}
.ei{font-size:36px;opacity:.18}.et{font-size:16px;font-weight:600;color:var(--text2)}.es{font-size:12px;color:var(--muted)}
#table-wrap{flex:1;overflow:auto;display:none}
table{width:100%;border-collapse:collapse;font-size:12px}
thead th{position:sticky;top:0;z-index:2;padding:9px 14px;text-align:left;font-size:11px;font-weight:700;letter-spacing:.04em;text-transform:uppercase;color:var(--accent);background:#f0f5ff;border-bottom:1.5px solid #dbe4ff;white-space:nowrap}
tbody tr{border-bottom:1px solid var(--border);transition:background .1s}tbody tr:hover{background:#f8faff}
tbody td{padding:8px 14px;max-width:280px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
td.num{color:#1d4ed8;text-align:right;font-family:var(--mono)}td.dt{color:#0f766e;font-family:var(--mono);font-size:11px}td.nil{color:var(--dim);font-style:italic}
.spin{width:15px;height:15px;border:2px solid rgba(255,255,255,.4);border-top-color:#fff;border-radius:50%;animation:spin .6s linear infinite;display:inline-block}
@keyframes spin{to{transform:rotate(360deg)}}@keyframes slideUp{from{transform:translateY(8px);opacity:0}to{transform:translateY(0);opacity:1}}
.overlay{position:fixed;inset:0;background:rgba(15,23,42,.52);backdrop-filter:blur(4px);display:none;align-items:center;justify-content:center;z-index:100}
.overlay.open{display:flex}
.mbox{background:#fff;border-radius:14px;width:92%;max-width:1100px;max-height:88vh;display:flex;flex-direction:column;overflow:hidden;box-shadow:0 20px 60px rgba(0,0,0,.18);animation:popIn .18s ease}
@keyframes popIn{from{transform:scale(.96);opacity:0}to{transform:scale(1);opacity:1}}
.mbar{display:flex;align-items:center;gap:12px;padding:16px 20px;border-bottom:1px solid var(--border);flex-shrink:0}
.micon{width:34px;height:34px;border-radius:8px;display:flex;align-items:center;justify-content:center;font-size:16px;flex-shrink:0}
.micon.blue{background:#eff6ff;color:var(--accent)}.micon.green{background:#ecfdf5;color:var(--success)}.micon.purple{background:#f5f3ff;color:var(--accent2)}
.mtitle{font-size:15px;font-weight:700;color:var(--text)}.msub{font-size:11px;color:var(--muted);margin-top:1px}
.mclose{margin-left:auto;width:30px;height:30px;border-radius:6px;border:none;background:transparent;cursor:pointer;font-size:16px;color:var(--muted);display:flex;align-items:center;justify-content:center;transition:all .15s}
.mclose:hover{background:var(--danger-lt);color:var(--danger)}.mbody{flex:1;overflow:auto}.dload{padding:24px;text-align:center;color:var(--muted);font-size:12px}
.dtabs{display:flex;flex-wrap:wrap;gap:4px;padding:10px 14px;border-bottom:1px solid var(--border);background:var(--surface);flex-shrink:0}
.dtab{padding:4px 12px;border-radius:20px;border:1px solid var(--border2);background:#fff;color:var(--text2);font-size:11px;font-weight:600;cursor:pointer;transition:all .15s;font-family:var(--mono)}
.dtab:hover{border-color:var(--accent);color:var(--accent)}.dtab.active{background:var(--accent);color:#fff;border-color:var(--accent)}
.tbl-meta{padding:7px 18px;background:#fafbff;border-bottom:1px solid var(--border);font-size:11px;color:var(--muted);display:flex;gap:16px;flex-shrink:0}.tbl-meta b{color:var(--text2)}
.schema-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:14px;padding:18px}
.scard{border:1.5px solid var(--border);border-radius:10px;overflow:hidden;background:#fff;transition:all .2s;cursor:pointer}
.scard:hover{border-color:var(--accent);box-shadow:0 4px 16px rgba(26,86,219,.1)}
.scard-head{display:flex;align-items:center;gap:10px;padding:11px 14px;background:var(--surface);border-bottom:1px solid var(--border)}
.scard-name{font-size:12px;font-weight:700;color:var(--text);font-family:var(--mono)}.scard-rows{margin-left:auto;font-size:10px;color:var(--muted);font-family:var(--mono)}
.scard-body{padding:8px 14px;display:none;flex-direction:column;gap:3px}
.scard:hover .scard-body,.scard.open .scard-body{display:flex}
.scol{display:flex;align-items:center;gap:6px;font-size:11px;padding:2px 0}.scol-name{color:var(--text2);font-family:var(--mono)}.scol-type{color:var(--muted);font-size:10px}
.scol-pk{font-size:9px;padding:1px 5px;border-radius:3px;background:#fffbeb;color:var(--warn);border:1px solid #fde68a;font-weight:700}
.scol-fk{font-size:10px;color:var(--accent2);font-family:var(--mono);margin-top:2px;padding:0 14px 6px}
.scard-prev{margin:6px 14px 12px;padding:5px 10px;border-radius:6px;border:1px solid var(--border);font-size:10px;font-weight:600;color:var(--accent);background:var(--accent-lt);text-align:center;transition:all .15s}
.scard-prev:hover{background:var(--accent);color:#fff}
.preset-btn{padding:5px 14px;font-size:11px;font-weight:600;border-radius:20px;border:1.5px solid var(--border2);background:#fff;color:var(--text2);cursor:pointer;transition:all .15s;font-family:var(--mono)}
.preset-btn:hover,.preset-btn.active{border-color:var(--accent);color:var(--accent);background:var(--accent-lt)}
.conn-lbl{font-size:11px;font-weight:600;color:var(--text2);display:block;margin-bottom:5px}
.conn-inp{width:100%;background:var(--surface);border:1.5px solid var(--border);border-radius:var(--r);padding:9px 12px;color:var(--text);font-family:var(--mono);font-size:12px;outline:none;transition:border-color .2s,box-shadow .2s}
.conn-inp:focus{border-color:var(--accent);box-shadow:0 0 0 3px rgba(26,86,219,.1);background:#fff}
.msg-err{padding:10px 14px;background:var(--danger-lt);border:1px solid rgba(220,38,38,.2);border-radius:var(--r);color:var(--danger);font-size:12px;font-family:var(--mono)}
.msg-ok{padding:10px 14px;background:var(--success-lt);border:1px solid rgba(5,150,105,.2);border-radius:var(--r);color:var(--success);font-size:12px}
.conn-card{display:flex;align-items:center;gap:12px;padding:12px 16px;border:1.5px solid var(--border);border-radius:10px;background:#fff;transition:all .15s}
.conn-card.current{border-color:var(--success);background:var(--success-lt)}
.conn-name{font-size:13px;font-weight:700;color:var(--text);font-family:var(--mono)}.conn-cs{font-size:10px;color:var(--muted);font-family:var(--mono);margin-top:2px;max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.type-badge{padding:2px 8px;border-radius:20px;font-size:10px;font-weight:700;background:var(--accent-lt);color:var(--accent);border:1px solid #dbeafe}
.sw-btn{margin-left:auto;padding:5px 14px;border-radius:var(--r);border:1.5px solid var(--accent);background:var(--accent);color:#fff;font-size:11px;font-weight:700;cursor:pointer;transition:all .15s;white-space:nowrap}
.sw-btn:hover{background:#1648c0}.sw-btn:disabled{opacity:.5;cursor:not-allowed}
.del-btn{padding:4px 8px;border-radius:6px;border:1px solid var(--border2);background:#fff;color:var(--muted);font-size:11px;cursor:pointer;transition:all .15s}
.del-btn:hover{border-color:var(--danger);color:var(--danger);background:var(--danger-lt)}
.reg-form{padding:16px;background:var(--surface);border-radius:10px;border:1.5px dashed var(--border2);display:flex;flex-direction:column;gap:10px}
</style>
</head>
<body>
<div id="app">
<div id="topbar">
  <div class="logo-mark">IS</div>
  <div class="logo-text"><b>Infusion <span>Solutions</span></b><small>NL → SQL Intelligence Platform</small></div>
  <div class="divider"></div>
  <button class="nav-btn" onclick="openDataModal()"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><rect x="3" y="3" width="18" height="18" rx="2"/><line x1="3" y1="9" x2="21" y2="9"/><line x1="3" y1="15" x2="21" y2="15"/><line x1="9" y1="9" x2="9" y2="21"/></svg>Data</button>
  <button class="nav-btn" onclick="openSchemaModal()"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"/><path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"/></svg>Schema</button>
  <div class="topbar-right">
    <button class="nav-btn" onclick="openSwitchModal()" id="sw-db-btn"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M7 16V4m0 0L3 8m4-4l4 4"/><path d="M17 8v12m0 0l4-4m-4 4l-4-4"/></svg>Switch DB<span id="reg-badge" style="display:none;background:var(--accent);color:#fff;border-radius:20px;padding:1px 6px;font-size:10px;font-weight:800;margin-left:2px"></span></button>
    <button class="nav-btn" onclick="openConnectModal()"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M13 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V9z"/><polyline points="13 2 13 9 20 9"/></svg>Connect DB</button>
    <div class="db-pill" onclick="openSwitchModal()"><svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"/><path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"/></svg><span id="db-label">Loading…</span></div>
    <div class="status-pill"><div class="sdot" id="sdot"></div><span id="conn-status">Connected</span></div>
  </div>
</div>
<div id="query-section">
  <div class="ql">Natural Language Query</div>
  <div class="qrow">
    <textarea id="nl-input" rows="1" placeholder="Ask anything… e.g. top 10 customers by revenue" onkeydown="if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();runQuery()}" oninput="autoResize(this)"></textarea>
    <button id="run-btn" onclick="runQuery()"><span id="run-txt">Run Query ↵</span><span id="run-spin" class="spin" style="display:none"></span></button>
  </div>
  <button class="ex-toggle" id="ex-btn" onclick="toggleSugg()">Examples<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="6 9 12 15 18 9"/></svg></button>
  <div class="sugg-row" id="sugg-row">
    <span class="chip" onclick="useChip(this)">top 10 customers by revenue</span>
    <span class="chip" onclick="useChip(this)">total sales by branch</span>
    <span class="chip" onclick="useChip(this)">show all products</span>
    <span class="chip" onclick="useChip(this)">accounts receivable outstanding</span>
    <span class="chip" onclick="useChip(this)">open orders with profit margin</span>
    <span class="chip" onclick="useChip(this)">sales by state last month</span>
    <span class="chip" onclick="useChip(this)">total AR balance by customer</span>
    <span class="chip" onclick="useChip(this)">show all tables row counts</span>
  </div>
</div>
<div id="drag-handle"></div>
<div id="results-section">
  <div id="results-header"><span class="rh-count" id="rh-count"></span><span class="rh-time" id="rh-time"></span><button class="exp-btn" onclick="doExport('excel')">↓ Excel</button><button class="exp-btn" onclick="doExport('csv')">↓ CSV</button><button class="exp-btn" onclick="doExport('pdf')">↓ PDF</button></div>
  <div id="sql-strip"><span class="label">SQL: </span><span id="sql-text"></span></div>
  <div id="empty-state"><div class="ei">◈</div><div class="et">Ask anything about your data</div><div class="es">Type a question above · Press Enter to run</div></div>
  <div id="table-wrap"><table><thead id="result-head"></thead><tbody id="result-body"></tbody></table></div>
</div>
</div>

<!-- DATA MODAL -->
<div class="overlay" id="data-modal" onclick="if(event.target===this)closeModal('data-modal')">
  <div class="mbox">
    <div class="mbar"><div class="micon blue">⬡</div><div><div class="mtitle">Data Browser</div><div class="msub" id="data-sub">Preview table rows</div></div><button class="mclose" onclick="closeModal('data-modal')">✕</button></div>
    <div class="dtabs" id="dtabs"><div class="dload">Loading…</div></div>
    <div class="tbl-meta" id="tbl-meta" style="display:none"></div>
    <div class="mbody" id="data-panel"><div class="dload">Select a table above</div></div>
  </div>
</div>

<!-- SCHEMA MODAL -->
<div class="overlay" id="schema-modal" onclick="if(event.target===this)closeModal('schema-modal')">
  <div class="mbox">
    <div class="mbar"><div class="micon green">◈</div><div><div class="mtitle">Schema Explorer</div><div class="msub" id="schema-sub">Database structure</div></div><button class="mclose" onclick="closeModal('schema-modal')">✕</button></div>
    <div class="mbody"><div class="schema-grid" id="schema-grid"><div class="dload">Loading…</div></div></div>
  </div>
</div>

<!-- CONNECT DB MODAL -->
<div class="overlay" id="connect-modal" onclick="if(event.target===this)closeModal('connect-modal')">
  <div class="mbox" style="max-width:680px">
    <div class="mbar"><div class="micon blue">⚡</div><div><div class="mtitle">Connect to Database</div><div class="msub">20+ databases supported — LLM adapts to any DB automatically</div></div><button class="mclose" onclick="closeModal('connect-modal')">✕</button></div>
    <div style="padding:22px;display:flex;flex-direction:column;gap:18px;overflow-y:auto;max-height:78vh">
      <div>
        <div style="font-size:10px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;color:var(--muted);margin-bottom:10px">Select Database Type</div>
        <div style="font-size:9px;font-weight:700;text-transform:uppercase;color:var(--dim);margin-bottom:6px">── Microsoft</div>
        <div style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:12px"><button class="preset-btn" onclick="setPreset('mssql',this)">SQL Server</button><button class="preset-btn" onclick="setPreset('azure_sql',this)">Azure SQL</button><button class="preset-btn" onclick="setPreset('azure_synapse',this)">Azure Synapse</button></div>
        <div style="font-size:9px;font-weight:700;text-transform:uppercase;color:var(--dim);margin-bottom:6px">── Open Source / Classic</div>
        <div style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:12px"><button class="preset-btn" onclick="setPreset('postgresql',this)">PostgreSQL</button><button class="preset-btn" onclick="setPreset('mysql',this)">MySQL</button><button class="preset-btn" onclick="setPreset('mariadb',this)">MariaDB</button><button class="preset-btn" onclick="setPreset('sqlite',this)">SQLite</button></div>
        <div style="font-size:9px;font-weight:700;text-transform:uppercase;color:var(--dim);margin-bottom:6px">── Cloud / Managed</div>
        <div style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:12px"><button class="preset-btn" onclick="setPreset('aws_rds_pg',this)">AWS RDS (PG)</button><button class="preset-btn" onclick="setPreset('aws_rds_mysql',this)">AWS RDS (MySQL)</button><button class="preset-btn" onclick="setPreset('redshift',this)">Redshift</button><button class="preset-btn" onclick="setPreset('supabase',this)">Supabase</button><button class="preset-btn" onclick="setPreset('neon',this)">Neon</button><button class="preset-btn" onclick="setPreset('planetscale',this)">PlanetScale</button><button class="preset-btn" onclick="setPreset('tidb',this)">TiDB Cloud</button></div>
        <div style="font-size:9px;font-weight:700;text-transform:uppercase;color:var(--dim);margin-bottom:6px">── Enterprise / Analytics</div>
        <div style="display:flex;flex-wrap:wrap;gap:6px"><button class="preset-btn" onclick="setPreset('oracle',this)">Oracle DB</button><button class="preset-btn" onclick="setPreset('snowflake',this)">Snowflake</button><button class="preset-btn" onclick="setPreset('bigquery',this)">BigQuery</button><button class="preset-btn" onclick="setPreset('databricks',this)">Databricks</button><button class="preset-btn" onclick="setPreset('cockroachdb',this)">CockroachDB</button><button class="preset-btn" onclick="setPreset('clickhouse',this)">ClickHouse</button></div>
      </div>
      <div><label class="conn-lbl">Display Name <span style="font-weight:400;color:var(--dim)">(optional)</span></label><input class="conn-inp" id="inp-label" placeholder="e.g. Production ERP · Sales DB"/></div>
      <div>
        <label class="conn-lbl">Connection String</label>
        <textarea class="conn-inp" id="inp-cs" rows="3" style="resize:vertical;line-height:1.9;font-size:12px" placeholder="sqlite:///absolute/path/to/file.db&#10;postgresql://user:pass@host:5432/dbname?sslmode=require&#10;mysql://user:pass@host:3306/dbname&#10;mssql://user:pass@host:1433/dbname"></textarea>
        <div style="font-size:10px;color:var(--dim);margin-top:5px">🔒 Credentials used for this session only — never stored.</div>
      </div>
      <div><label style="display:flex;align-items:center;gap:8px;font-size:11px;color:var(--text2);cursor:pointer"><input type="checkbox" id="inp-register" style="width:13px;height:13px"/> Also save as named connection (Switch DB)</label></div>
      <div id="conn-msg" style="display:none"></div>
      <div style="display:flex;gap:10px;justify-content:flex-end">
        <button class="exp-btn" onclick="closeModal('connect-modal')">Cancel</button>
        <button class="exp-btn" id="test-btn" onclick="testConn()" style="border-color:#7c3aed;color:#7c3aed">Test Connection</button>
        <button class="nav-btn primary" id="do-btn" onclick="doConnect()"><span id="do-txt">Connect →</span><span id="do-spin" class="spin" style="display:none;border-color:rgba(255,255,255,.4);border-top-color:#fff"></span></button>
      </div>
    </div>
  </div>
</div>

<!-- SWITCH DB MODAL -->
<div class="overlay" id="switch-modal" onclick="if(event.target===this)closeModal('switch-modal')">
  <div class="mbox" style="max-width:620px">
    <div class="mbar"><div class="micon purple">⇄</div><div><div class="mtitle">Database Switcher</div><div class="msub">Register named connections · Switch between them instantly</div></div><button class="mclose" onclick="closeModal('switch-modal')">✕</button></div>
    <div style="padding:18px;display:flex;flex-direction:column;gap:12px;overflow-y:auto;max-height:70vh">
      <div id="conn-list"><div class="dload">Loading…</div></div>
      <div style="font-size:10px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;color:var(--muted);margin-top:6px">Register a New Connection</div>
      <div class="reg-form">
        <div style="display:flex;gap:10px">
          <div style="flex:0 0 150px"><label class="conn-lbl">Name</label><input class="conn-inp" id="reg-name" placeholder="e.g. neon / prod"/></div>
          <div style="flex:1"><label class="conn-lbl">Connection String</label><input class="conn-inp" id="reg-cs" placeholder="postgresql://… or sqlite:///…"/></div>
        </div>
        <div style="font-size:10px;color:var(--dim)">Quick: <span style="color:var(--accent);cursor:pointer;text-decoration:underline" onclick="document.getElementById('reg-cs').value='postgresql://user:pass@host:5432/db'">PostgreSQL</span> · <span style="color:var(--accent);cursor:pointer;text-decoration:underline" onclick="document.getElementById('reg-cs').value='sqlite:///path/to/file.db'">SQLite</span> · <span style="color:var(--accent);cursor:pointer;text-decoration:underline" onclick="document.getElementById('reg-cs').value='mysql://user:pass@host:3306/db'">MySQL</span></div>
        <div style="display:flex;justify-content:flex-end"><button class="nav-btn primary" onclick="registerConn()" id="reg-btn">+ Register</button></div>
        <div id="reg-msg" style="display:none"></div>
      </div>
    </div>
  </div>
</div>

<script>
let lastData=[],lastCols=[],schemaCache=null;

async function runQuery(){
  const q=document.getElementById('nl-input').value.trim();if(!q)return;
  setBusy(true);hide('results-header');hide('sql-strip');hide('table-wrap');showEmpty(false);
  try{
    const r=await fetch('/query',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({question:q})});
    const d=await r.json();
    if(d.query?.sql){document.getElementById('sql-text').textContent=d.query.sql;show('sql-strip');}
    if(d.success&&d.result?.data?.length){
      lastData=d.result.data;lastCols=d.result.columns;
      renderTable(d.result.data,d.result.columns);
      document.getElementById('rh-count').textContent=d.result.row_count.toLocaleString()+' rows';
      document.getElementById('rh-time').textContent=d.result.execution_time_ms+'ms';
      show('results-header');show('table-wrap');
    }else if(d.success&&d.result?.row_count===0){
      showEmpty(true,'Query returned 0 rows','Try rephrasing or broaden your question.');
    }else{showEmpty(true,'Query error',d.error||d.detail||'Something went wrong.');}
  }catch(e){showEmpty(true,'Network error',e.message);}
  finally{setBusy(false);}
}
function setBusy(on){document.getElementById('run-btn').disabled=on;document.getElementById('run-txt').style.display=on?'none':'inline';document.getElementById('run-spin').style.display=on?'inline-block':'none';}
function showEmpty(on,title='Ask anything about your data',sub='Type a question above · Press Enter to run'){const el=document.getElementById('empty-state');el.style.display=on?'flex':'none';el.querySelector('.et').textContent=title;el.querySelector('.es').textContent=sub;}
function show(id){document.getElementById(id).style.display='flex'}function hide(id){document.getElementById(id).style.display='none'}
function renderTable(rows,cols){
  document.getElementById('result-head').innerHTML='<tr>'+cols.map(c=>`<th>${c}</th>`).join('')+'</tr>';
  document.getElementById('result-body').innerHTML=rows.map(row=>'<tr>'+cols.map(c=>{const v=row[c];if(v===null||v===undefined)return'<td class="nil">—</td>';if(typeof v==='number')return`<td class="num">${v.toLocaleString()}</td>`;const s=String(v);if(/^\d{4}-\d{2}-\d{2}/.test(s))return`<td class="dt">${s.slice(0,16)}</td>`;return`<td title="${s.replace(/"/g,'&quot;')}">${s}</td>`;}).join('')+'</tr>').join('');
}
function autoResize(el){el.style.height='auto';el.style.height=Math.min(el.scrollHeight,120)+'px';}
function toggleSugg(){document.getElementById('sugg-row').classList.toggle('open');document.getElementById('ex-btn').classList.toggle('open');}
function useChip(el){document.getElementById('nl-input').value=el.textContent;runQuery();}
async function doExport(fmt){if(!lastData.length)return;try{const r=await fetch('/export',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({data:lastData,columns:lastCols,format:fmt})});const d=await r.json();if(d.file)window.location.href='/download?path='+encodeURIComponent(d.file);}catch(e){alert('Export failed: '+e.message);}}
(function(){const h=document.getElementById('drag-handle'),q=document.getElementById('query-section');let on=false,sy=0,sh=0;h.addEventListener('mousedown',e=>{on=true;sy=e.clientY;sh=q.offsetHeight;h.classList.add('drag');document.body.style.userSelect='none';});document.addEventListener('mousemove',e=>{if(!on)return;q.style.height=Math.min(Math.max(sh+(e.clientY-sy),52),260)+'px';});document.addEventListener('mouseup',()=>{on=false;h.classList.remove('drag');document.body.style.userSelect='';});})();
function closeModal(id){document.getElementById(id).classList.remove('open')}
async function openDataModal(){
  document.getElementById('data-modal').classList.add('open');
  const s=await getSchema();const tables=Object.keys(s);const total=tables.reduce((a,t)=>a+(s[t].row_count||0),0);
  document.getElementById('data-sub').textContent=`${tables.length} tables · ${total.toLocaleString()} total rows`;
  document.getElementById('dtabs').innerHTML=tables.map(t=>`<button class="dtab" onclick="loadTab('${t}',this)">${t}</button>`).join('');
  if(tables.length)loadTab(tables[0],document.querySelector('.dtab'));
}
async function loadTab(tbl,btn){
  document.querySelectorAll('.dtab').forEach(b=>b.classList.remove('active'));btn.classList.add('active');
  document.getElementById('data-panel').innerHTML='<div class="dload">Loading…</div>';document.getElementById('tbl-meta').style.display='none';
  try{
    const r=await fetch(`/table/${encodeURIComponent(tbl)}?limit=50`);const d=await r.json();
    const s=await getSchema();const info=s[tbl]||{};
    document.getElementById('tbl-meta').style.display='flex';
    document.getElementById('tbl-meta').innerHTML=`<span>Table: <b>${tbl}</b></span><span>Total rows: <b>${(info.row_count||0).toLocaleString()}</b></span><span>Columns: <b>${(info.columns||[]).length}</b></span><span>Showing: <b>${(d.rows||[]).length}</b></span>`;
    const rows=d.rows||[];const cols=d.columns||Object.keys(rows[0]||{});
    if(!rows.length){document.getElementById('data-panel').innerHTML='<div class="dload">No data in this table</div>';return;}
    document.getElementById('data-panel').innerHTML=`<table style="width:100%;border-collapse:collapse;font-size:12px"><thead><tr>${cols.map(c=>`<th style="position:sticky;top:0;z-index:2;padding:9px 14px;font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.04em;color:var(--accent);background:#f0f5ff;border-bottom:1.5px solid #dbe4ff;white-space:nowrap;text-align:left">${c}</th>`).join('')}</tr></thead><tbody>${rows.map(row=>'<tr style="border-bottom:1px solid var(--border)">'+cols.map(c=>{const v=row[c];if(v===null||v===undefined)return'<td style="padding:7px 14px;color:var(--dim);font-style:italic">—</td>';if(typeof v==='number')return`<td style="padding:7px 14px;color:#1d4ed8;text-align:right;font-family:var(--mono)">${v.toLocaleString()}</td>`;return`<td style="padding:7px 14px;max-width:240px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${String(v)}</td>`;}).join('')+'</tr>').join('')}</tbody></table>`;
  }catch(e){document.getElementById('data-panel').innerHTML=`<div class="dload" style="color:var(--danger)">Error: ${e.message}</div>`;}
}
async function openSchemaModal(){
  document.getElementById('schema-modal').classList.add('open');
  const s=await getSchema();const tables=Object.keys(s);const total=tables.reduce((a,t)=>a+(s[t].row_count||0),0);
  document.getElementById('schema-sub').textContent=`${tables.length} tables · ${total.toLocaleString()} rows`;
  const ICONS={fact:'⬡',dimension:'◈',lookup:'▪',reference:'○'};const COLORS={fact:'#0ea5e9',dimension:'#8b5cf6',lookup:'#f59e0b',reference:'#10b981'};
  document.getElementById('schema-grid').innerHTML=tables.map(tbl=>{
    const info=s[tbl];const cols=info.columns||[];const fks=info.foreign_keys||[];
    const role=cols.length<=5&&info.row_count<50?'lookup':fks.length>=2||info.row_count>300?'fact':fks.length>=1?'dimension':'reference';
    return`<div class="scard" onclick="this.classList.toggle('open')"><div class="scard-head"><span style="font-size:16px;color:${COLORS[role]}">${ICONS[role]}</span><span class="scard-name">${tbl}</span><span class="scard-rows">${(info.row_count||0).toLocaleString()} rows</span></div><div class="scard-body">${cols.slice(0,12).map(c=>`<div class="scol">${c.pk?'<span class="scol-pk">PK</span>':''}<span class="scol-name">${c.name}</span><span class="scol-type">${c.type}</span></div>`).join('')}${cols.length>12?`<div style="font-size:10px;color:var(--muted)">+${cols.length-12} more…</div>`:''}${fks.map(f=>`<div class="scol-fk">↳ ${f.column} → ${f.ref_table}.${f.ref_column}</div>`).join('')}</div><div class="scard-prev" onclick="event.stopPropagation();fromSchema('${tbl}')">Preview data →</div></div>`;
  }).join('');
}
async function fromSchema(tbl){closeModal('schema-modal');await openDataModal();document.querySelectorAll('.dtab').forEach(b=>{if(b.textContent===tbl)loadTab(tbl,b);});}
async function getSchema(){if(schemaCache)return schemaCache;const r=await fetch('/schema');schemaCache=await r.json();return schemaCache;}
const PRESETS={mssql:'mssql://username:password@host:1433/dbname',azure_sql:'mssql://username:password@yourserver.database.windows.net:1433/dbname',azure_synapse:'mssql://username:password@yourworkspace.sql.azuresynapse.net:1433/dbname',postgresql:'postgresql://username:password@localhost:5432/dbname',mysql:'mysql://username:password@localhost:3306/dbname',mariadb:'mysql://username:password@localhost:3306/dbname',sqlite:'sqlite:///absolute/path/to/file.db',aws_rds_pg:'postgresql://username:password@yourdb.us-east-1.rds.amazonaws.com:5432/dbname',aws_rds_mysql:'mysql://username:password@yourdb.us-east-1.rds.amazonaws.com:3306/dbname',redshift:'postgresql://username:password@yourcluster.us-east-1.redshift.amazonaws.com:5439/dbname',supabase:'postgresql://postgres:password@db.xxxxxxxxxxxx.supabase.co:5432/postgres',neon:'postgresql://username:password@ep-xyz.us-east-2.aws.neon.tech/dbname?sslmode=require&channel_binding=require',planetscale:'mysql://username:password@aws.connect.psdb.cloud:3306/dbname',tidb:'mysql://username:password@gateway01.us-east-1.prod.aws.tidbcloud.com:4000/dbname',oracle:'oracle://username:password@host:1521/service_name',snowflake:'snowflake://username:password@account.snowflakecomputing.com/dbname?warehouse=WH&schema=PUBLIC',bigquery:'bigquery://project-id/dataset',databricks:'databricks://token:dapi@workspace.azuredatabricks.net:443/dbname?http_path=/sql/1.0/warehouses/id',cockroachdb:'postgresql://username:password@free-tier.gcp-us-central1.cockroachlabs.cloud:26257/dbname?sslmode=verify-full',clickhouse:'clickhouse://username:password@host:9000/dbname'};
let _aPBtn=null;
function openConnectModal(){document.getElementById('conn-msg').style.display='none';document.getElementById('inp-label').value='';document.getElementById('inp-cs').value='';document.getElementById('inp-register').checked=false;if(_aPBtn){_aPBtn.classList.remove('active');_aPBtn=null;}document.getElementById('connect-modal').classList.add('open');}
function setPreset(t,btn){document.getElementById('inp-cs').value=PRESETS[t]||'';document.getElementById('inp-cs').focus();if(_aPBtn)_aPBtn.classList.remove('active');btn.classList.add('active');_aPBtn=btn;}
function showMsg(type,msg,tid='conn-msg'){const el=document.getElementById(tid);el.className=type==='ok'?'msg-ok':'msg-err';el.textContent=msg;el.style.display='block';}
async function testConn(){const cs=document.getElementById('inp-cs').value.trim();const lb=document.getElementById('inp-label').value.trim();if(!cs){showMsg('err','Please enter a connection string.');return;}const btn=document.getElementById('test-btn');btn.disabled=true;btn.textContent='Testing…';document.getElementById('conn-msg').style.display='none';try{const r=await fetch('/connect',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({connection_string:cs,label:lb})});const d=await r.json();if(r.ok){showMsg('ok',`✅ Connected — ${d.tables} tables · ${(d.total_rows||0).toLocaleString()} rows · ${d.db_type}`);schemaCache=null;updateStatusBar();}else showMsg('err','✕ '+(d.detail||'Connection failed'));}catch(e){showMsg('err','✕ '+e.message);}finally{btn.disabled=false;btn.textContent='Test Connection';}}
async function doConnect(){const cs=document.getElementById('inp-cs').value.trim();const lb=document.getElementById('inp-label').value.trim();const doReg=document.getElementById('inp-register').checked;if(!cs){showMsg('err','Please enter a connection string.');return;}const btn=document.getElementById('do-btn'),txt=document.getElementById('do-txt'),sp=document.getElementById('do-spin');btn.disabled=true;txt.style.display='none';sp.style.display='inline-block';document.getElementById('conn-msg').style.display='none';try{const r=await fetch('/connect',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({connection_string:cs,label:lb})});const d=await r.json();if(r.ok){if(doReg){const name=lb||cs.split('/').pop().split('?')[0]||'db';await fetch('/register',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name,connection_string:cs})});updateRegBadge();}schemaCache=null;updateStatusBar();closeModal('connect-modal');hide('table-wrap');hide('results-header');hide('sql-strip');showEmpty(true,'New database connected — ask anything',`${d.label} · ${d.tables} tables · ${d.db_type}`);showToast(`Connected: ${d.label} (${d.db_type}) — ${d.tables} tables`,'success');}else showMsg('err','✕ '+(d.detail||'Connection failed'));}catch(e){showMsg('err','✕ '+e.message);}finally{btn.disabled=false;txt.style.display='inline';sp.style.display='none';}}
async function openSwitchModal(){document.getElementById('switch-modal').classList.add('open');document.getElementById('reg-msg').style.display='none';await renderConnList();}
function guessDbType(cs){if(cs.startsWith('postgresql')||cs.startsWith('postgres'))return'postgresql';if(cs.startsWith('mysql')||cs.startsWith('mariadb'))return'mysql';if(cs.startsWith('sqlite'))return'sqlite';if(cs.startsWith('mssql'))return'mssql';return'db';}
async function renderConnList(){const list=document.getElementById('conn-list');list.innerHTML='<div class="dload">Loading…</div>';try{const r=await fetch('/connections');const d=await r.json();const conns=d.connections||[];const current=d.current||'';if(!conns.length){list.innerHTML='<div class="dload" style="color:var(--muted)">No saved connections — register one below.</div>';return;}list.innerHTML=conns.map(c=>{const isCur=c.name===current;const dbType=guessDbType(c.connection_string);return`<div class="conn-card ${isCur?'current':''}"><div><div class="conn-name">${c.name} ${isCur?'<span style="font-size:10px;color:var(--success);font-weight:600">● active</span>':''}</div><div class="conn-cs">${c.connection_string.replace(/:[^:@]+@/,':*****@')}</div></div><span class="type-badge">${dbType}</span><button class="sw-btn" id="sw-${c.name}" onclick="doSwitch('${c.name.replace(/'/g,"\\'")}') " ${isCur?'disabled':''}>⇄ Switch</button><button class="del-btn" onclick="doDelete('${c.name.replace(/'/g,"\\'")}')">✕</button></div>`;}).join('');}catch(e){list.innerHTML=`<div class="dload" style="color:var(--danger)">Error: ${e.message}</div>`;}}
async function doSwitch(name){const btn=document.getElementById(`sw-${name}`);if(btn){btn.disabled=true;btn.textContent='Switching…';}try{const r=await fetch('/switch',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name})});const d=await r.json();if(r.ok){schemaCache=null;updateStatusBar();await renderConnList();closeModal('switch-modal');hide('table-wrap');hide('results-header');hide('sql-strip');showEmpty(true,`Switched to ${name}`,'Ask anything about your new database.');showToast(`Switched to ${name} (${d.db_type}) — ${d.tables} tables`,'success');}else{showToast(d.detail||'Switch failed','error');if(btn){btn.disabled=false;btn.innerHTML='⇄ Switch';}}}catch(e){showToast('Switch failed: '+e.message,'error');if(btn){btn.disabled=false;btn.innerHTML='⇄ Switch';}}}
async function doDelete(name){if(!confirm(`Remove "${name}" from saved connections?`))return;await fetch(`/connections/${encodeURIComponent(name)}`,{method:'DELETE'});await renderConnList();updateRegBadge();}
async function registerConn(){const name=document.getElementById('reg-name').value.trim();const cs=document.getElementById('reg-cs').value.trim();document.getElementById('reg-msg').style.display='none';if(!name||!cs){showMsg('err','Name and connection string are required.','reg-msg');return;}const btn=document.getElementById('reg-btn');btn.disabled=true;btn.textContent='Registering…';try{const r=await fetch('/register',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name,connection_string:cs})});const d=await r.json();if(r.ok){document.getElementById('reg-name').value='';document.getElementById('reg-cs').value='';await renderConnList();updateRegBadge();showMsg('ok',`✅ "${name}" registered.`,'reg-msg');}else showMsg('err','✕ '+(d.detail||'Failed'),'reg-msg');}catch(e){showMsg('err','✕ '+e.message,'reg-msg');}finally{btn.disabled=false;btn.textContent='+ Register';}}
async function updateRegBadge(){try{const r=await fetch('/connections');const d=await r.json();const n=(d.connections||[]).length;const b=document.getElementById('reg-badge');b.style.display=n?'inline':'none';b.textContent=n;}catch(e){}}
async function updateStatusBar(){try{const r=await fetch('/connection-info');const d=await r.json();document.getElementById('db-label').textContent=d.label||'Unknown';document.getElementById('conn-status').textContent=d.connected?'Connected':'Disconnected';const dot=document.getElementById('sdot');dot.style.background=d.connected?'var(--success)':'var(--danger)';dot.style.boxShadow=d.connected?'0 0 6px rgba(5,150,105,.5)':'0 0 6px rgba(220,38,38,.5)';}catch(e){console.warn(e);}}
function showToast(msg,type='info'){const t=document.createElement('div');const bg=type==='success'?'var(--success)':type==='error'?'var(--danger)':'var(--accent)';t.style.cssText=`position:fixed;bottom:24px;right:24px;padding:13px 22px;border-radius:10px;font-size:13px;font-weight:600;z-index:9999;color:#fff;background:${bg};box-shadow:0 4px 20px rgba(0,0,0,.2);animation:slideUp .3s ease`;t.textContent=msg;document.body.appendChild(t);setTimeout(()=>t.remove(),3500);}
updateStatusBar();updateRegBadge();
</script>
</body>
</html>"""

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("ui_app:app", host=settings.HOST, port=settings.PORT, reload=False)
