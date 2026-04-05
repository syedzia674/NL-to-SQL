import re
def is_safe_sql(sql):
    if not sql or not isinstance(sql,str): return False
    u = sql.strip().lstrip("(").strip().upper()
    if "SELECT" not in u: return False
    for p in [r"\bINSERT\b",r"\bUPDATE\b",r"\bDELETE\b",r"\bDROP\b",
              r"\bALTER\b",r"\bTRUNCATE\b",r"\bCREATE\b",r"\bREPLACE\b",
              r"\bEXEC\b",r"\bATTACH\b",r"\bDETACH\b",r"\bPRAGMA\b"]:
        if re.search(p,u): return False
    return True

def explain_sql(sql):
    u = sql.upper()
    exp = {"type":"select","operations":[],"complexity":"simple"}
    if "JOIN" in u: exp["type"]="join"; exp["complexity"]="complex"
    elif any(x in u for x in ["COUNT","SUM","AVG"]): exp["type"]="aggregation"; exp["complexity"]="medium"
    if "GROUP BY" in u: exp["operations"].append("grouping")
    if "ORDER BY" in u: exp["operations"].append("sorting")
    if "WHERE" in u: exp["operations"].append("filtering")
    return exp
