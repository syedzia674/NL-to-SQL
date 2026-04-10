import logging
from schema.introspector import enrich_schema_with_synonyms, analyze_relationships
from schema.synonyms import SCHEMA_SYNONYMS
from llm.prompt import build_prompt
from llm.client import generate_sql
from sql.executor import execute_sql
from sql.validator import explain_sql
logger = logging.getLogger(__name__)
_cache = {}; _cache_id = ""

def clear_cache():
    global _cache, _cache_id; _cache = {}; _cache_id = ""

def _get_schema(db):
    global _cache, _cache_id
    did = str(id(db))
    if did != _cache_id or not _cache: _cache = db.get_schema(); _cache_id = did
    return _cache

def run_text_to_sql(db, user_query, verbose=False):
    try:
        raw      = _get_schema(db)
        enriched = enrich_schema_with_synonyms(raw, SCHEMA_SYNONYMS)
        rels     = analyze_relationships(raw)
        db_type  = getattr(db,"db_type","sqlite")
        prompt   = build_prompt(user_query, enriched, rels, db_type=db_type)
        if verbose: print("\nPROMPT (500 chars):\n", prompt[:500])
        sql     = generate_sql(prompt)
        if verbose: print(f"\nSQL:\n{sql}")
        result  = execute_sql(db, sql)
        exp     = explain_sql(sql)
        return {
            "success": result["success"],
            "query":   {"natural_language":user_query,"sql":sql,"explanation":exp},
            "result":  result,
            "error":   result.get("error"),
        }
    except Exception as e:
        logger.exception(f"Pipeline error: {user_query!r}")
        return {"success":False,"query":{"natural_language":user_query,"sql":None,"explanation":None},"result":{"data":[],"columns":[],"row_count":0,"execution_time_ms":0},"error":str(e)}
