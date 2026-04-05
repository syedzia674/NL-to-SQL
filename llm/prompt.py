DIALECT = {
    "sqlite":     "SQLite syntax. Double-quote identifiers. Use LIMIT not TOP.",
    "postgresql": "PostgreSQL syntax. Double-quote identifiers. Use LIMIT not TOP.",
    "mysql":      "MySQL syntax. Backtick identifiers. Use LIMIT not TOP.",
    "mssql":      "T-SQL syntax. Square-bracket identifiers. Use TOP not LIMIT.",
    "unknown":    "Standard ANSI SQL.",
}

def build_prompt(user_query, schema, relationships=None, db_type="sqlite"):
    lines = ["DATABASE SCHEMA:\n"]
    for table, info in schema.items():
        lines.append(f"Table: {table}  ({info.get('row_count',0):,} rows)")
        aliases = info.get("aliases",[])
        if aliases: lines.append(f"  Also known as: {', '.join(aliases)}")
        lines.append("  Columns:")
        for col in info.get("columns",[]):
            pk  = " [PK]" if col.get("primary_key") else ""
            smp = col.get("samples",[])
            s   = f"  -- e.g. {', '.join(str(x) for x in smp[:3])}" if smp else ""
            lines.append(f"    - {col['column']} ({col['type']}){pk}{s}")
        lines.append("")
    if relationships:
        lines.append("RELATIONSHIPS:")
        for r in relationships: lines.append(f"  {r['from']} -> {r['to']}")
        lines.append("")
    schema_text = "\n".join(lines)
    dialect = DIALECT.get(db_type, DIALECT["unknown"])
    return f"""{schema_text}
INSTRUCTIONS:
- Use {dialect}
- Return ONLY the SQL query. No explanations, no markdown fences.
- Use table aliases in JOINs. Use explicit JOIN...ON syntax.
- Qualify column names with table aliases when joining.
- Limit results to 500 rows unless user asks for more.
- If schema cannot answer the question return: SELECT 'Cannot answer: data not available' AS message;

User Question: {user_query}
SQL:"""
