#!/usr/bin/env python3
"""
main.py — Infusion Solutions NL→SQL · Terminal CLI
Usage:
  python main.py                         # interactive REPL
  python main.py -q "show all orders"    # single query
  python main.py --db postgresql://...   # any DB
  python main.py --schema                # print schema and exit
  python main.py --data orders           # preview table
"""
import sys, os, json, argparse, logging
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import settings
from db.universal_connector import connect as uc_connect
from pipeline.text_to_sql import run_text_to_sql, clear_cache

USE_COLOR = sys.stdout.isatty()
def _c(t,c): return f"\033[{c}m{t}\033[0m" if USE_COLOR else t
def bold(t):    return _c(t,"1")
def green(t):   return _c(t,"32")
def blue(t):    return _c(t,"34")
def cyan(t):    return _c(t,"36")
def yellow(t):  return _c(t,"33")
def red(t):     return _c(t,"31")
def dim(t):     return _c(t,"2")
def magenta(t): return _c(t,"35")
def hr(w=72):   print(dim("─"*w))

def print_banner():
    print()
    print(bold(blue("  ╔══════════════════════════════════════════════════╗")))
    print(bold(blue("  ║  Infusion Solutions · NL→SQL Terminal CLI        ║")))
    print(bold(blue("  ╚══════════════════════════════════════════════════╝")))
    print()

def _fmt(val, w):
    s = "—" if val is None else str(val)
    return (s[:w-1]+"…") if len(s)>w else s.ljust(w)

def _print_table(data, columns, max_rows=50, cw=22):
    rows = data[:max_rows]
    widths = {c: min(max(len(c), max((len(str(r.get(c,""))) for r in rows),default=0)),cw) for c in columns}
    sep = "  "+"  ".join("─"*widths[c] for c in columns)
    print(dim(sep))
    print("  "+"  ".join(bold(blue(_fmt(c,widths[c]))) for c in columns))
    print(dim(sep))
    for row in rows:
        parts = []
        for c in columns:
            v = row.get(c); w = widths[c]; s = _fmt(v,w)
            parts.append(dim(s) if v is None else cyan(s.rjust(w)) if isinstance(v,(int,float)) else s)
        print("  "+"  ".join(parts))
    print(dim(sep))
    if len(data)>max_rows: print(dim(f"  … {len(data)-max_rows} more rows"))
    print()

def print_results(resp):
    hr()
    if resp["success"]:
        q=resp["query"]; res=resp["result"]
        print(f"  {bold('Question:')}  {q['natural_language']}")
        print(f"\n  {bold('SQL:')}")
        for line in (q["sql"] or "").splitlines(): print(f"    {cyan(line)}")
        print(f"\n  {green('✓')} {bold(str(res['row_count']))} rows  {dim(str(res['execution_time_ms'])+'ms')}")
        if res.get("data"): print(); _print_table(res["data"],res["columns"])
    else:
        print(f"  {red('✗ Error:')} {resp.get('error','unknown')}")
        sql = resp.get("query",{}).get("sql")
        if sql:
            for line in sql.splitlines(): print(f"    {dim(line)}")
    hr(); print()

def show_schema(db):
    schema = db.get_schema()
    print(f"\n  {bold('DB:')} {getattr(db,'db_type','?').upper()}  {bold('Tables:')} {len(schema)}\n")
    for table, info in schema.items():
        print(f"  {bold(blue(table))}  {dim(str(info.get('row_count',0))+' rows')}")
        for col in info.get("columns",[]):
            pk = yellow(" PK") if col.get("primary_key") else ""
            smp = col.get("samples",[])
            s = dim("  e.g. "+", ".join(str(x) for x in smp[:3])) if smp else ""
            print(f"    {col['column']}  {dim(col['type'])}{pk}{s}")
        for fk in info.get("foreign_keys",[]): print(dim(f"    ↳ {fk['column']} → {fk['ref_table']}.{fk['ref_column']}"))
        print()

def show_data(db, table=None, limit=20):
    schema = db.get_schema(); db_type = getattr(db,"db_type","sqlite")
    tables = [table] if (table and table in schema) else list(schema.keys())
    for tbl in tables:
        sql = f"SELECT TOP {limit} * FROM [{tbl}]" if db_type=="mssql" else \
              f"SELECT * FROM `{tbl}` LIMIT {limit}" if db_type=="mysql" else \
              f'SELECT * FROM "{tbl}" LIMIT {limit}'
        print(f"\n  {bold(magenta(tbl))}")
        hr(60)
        try:
            rows = db.execute(sql)
            if rows: _print_table(rows, list(rows[0].keys()), max_rows=limit)
            else: print(dim("  (no data)\n"))
        except Exception as e: print(red(f"  Error: {e}\n"))

def make_db(cs=None):
    cs = cs or f"sqlite:///{settings.SQLITE_DB_PATH}"
    print(dim(f"\n  Connecting: {cs[:80]}"))
    try:
        db = uc_connect(cs); ok, msg = db.test_connection()
        if not ok: raise RuntimeError(msg)
        schema = db.get_schema()
        print(green(f"  ✓ Connected  [{getattr(db,'db_type','?').upper()}  ·  {len(schema)} tables]"))
        return db
    except Exception as e: print(red(f"\n  ✗ {e}\n")); sys.exit(1)

def interactive_mode(db, verbose=False):
    print_banner()
    schema = db.get_schema()
    print(f"  {green('✓')} Connected  {dim(getattr(db,'db_type','?').upper()+' · '+str(len(schema))+' tables')}")
    print(dim("  Type a question, 'help' for commands, 'quit' to exit.\n"))
    verbose_on = verbose
    while True:
        try: user = input(f"\n  {bold(blue('▶'))} ").strip()
        except (KeyboardInterrupt, EOFError): print(f"\n\n  {dim('Goodbye!')}\n"); break
        if not user: continue
        cmd = user.lower()
        if cmd in ("quit","exit","q"): print(f"\n  {dim('Goodbye!')}\n"); break
        elif cmd in ("help","?"): print(f"""
  {bold('Commands:')}
    {cyan('<question>')}    Natural language query
    {cyan('schema')}        Show tables and columns
    {cyan('tables')}        List table names
    {cyan('data [table]')}  Preview data
    {cyan('connect <cs>')}  Switch database
    {cyan('verbose')}       Toggle verbose mode
    {cyan('quit')}          Exit
""")
        elif cmd == "schema": show_schema(db)
        elif cmd == "tables":
            print()
            for t,i in db.get_schema().items(): print(f"  {blue(t)}  {dim(str(i.get('row_count',0))+' rows')}")
            print()
        elif cmd.startswith("data"):
            parts = user.split(maxsplit=1); show_data(db, parts[1].strip() if len(parts)>1 else None)
        elif cmd.startswith("connect "):
            cs = user[8:].strip()
            try:
                new_db = uc_connect(cs); ok,msg = new_db.test_connection()
                if ok: db=new_db; clear_cache(); print(green(f"  ✓ Switched to {getattr(db,'db_type','?').upper()}"))
                else: print(red(f"  ✗ {msg}"))
            except Exception as e: print(red(f"  ✗ {e}"))
        elif cmd == "verbose":
            verbose_on = not verbose_on
            print(f"  Verbose: {green('ON') if verbose_on else dim('OFF')}")
        elif cmd == "clear": os.system("cls" if os.name=="nt" else "clear")
        else:
            print(dim("  Thinking…"))
            try: print_results(run_text_to_sql(db, user, verbose=verbose_on))
            except Exception as e: print(red(f"\n  ✗ {e}\n"))

def main():
    p = argparse.ArgumentParser(description="Infusion Solutions NL→SQL CLI")
    p.add_argument("-q","--query")
    p.add_argument("--db", default=None)
    p.add_argument("-i","--interactive", action="store_true")
    p.add_argument("--schema", action="store_true")
    p.add_argument("--data", metavar="TABLE", nargs="?", const="__all__")
    p.add_argument("--format", choices=["table","json"], default="table")
    p.add_argument("-v","--verbose", action="store_true")
    p.add_argument("--log-level", default="WARNING")
    args = p.parse_args()
    logging.basicConfig(level=getattr(logging,args.log_level), format=settings.LOG_FORMAT)
    print(dim(f"\n  LLM: {settings.MODEL_NAME} @ {settings.LOCAL_LLM_API_BASE}"))
    db = make_db(args.db)
    if args.schema: show_schema(db); sys.exit(0)
    if args.data is not None: show_data(db, None if args.data=="__all__" else args.data); sys.exit(0)
    if args.query:
        resp = run_text_to_sql(db, args.query, verbose=args.verbose)
        if args.format == "json": print(json.dumps(resp, indent=2, default=str))
        else: print_results(resp)
        sys.exit(0 if resp["success"] else 1)
    interactive_mode(db, verbose=args.verbose)

if __name__ == "__main__":
    main()
