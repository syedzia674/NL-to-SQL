def enrich_schema_with_synonyms(schema, synonyms):
    enriched = {}
    for table, info in schema.items():
        cols = info.get("columns",[])
        enriched[table] = {
            "row_count":    info.get("row_count",0),
            "aliases":      synonyms.get(table,[]),
            "columns":      cols,
            "foreign_keys": info.get("foreign_keys",[]),
        }
        for col in cols:
            col["aliases"] = synonyms.get(col["column"],[])
    return enriched

def analyze_relationships(schema):
    rels = []
    tables = list(schema.keys())
    for i, t1 in enumerate(tables):
        cols = schema[t1].get("columns",[]) if isinstance(schema[t1],dict) else schema[t1]
        for t2 in tables[i+1:]:
            for col in cols:
                if col["column"] == f"{t2}_id":
                    rels.append({"from":f"{t1}.{col['column']}","to":f"{t2}.id","type":"foreign_key"})
    return rels
