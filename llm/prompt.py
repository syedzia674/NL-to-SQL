"""
llm/prompt.py — Universal prompt builder.
Generates correct SQL dialect per DB type so Gemini produces valid queries.
"""

DIALECT = {
    "sqlite":     "SQLite syntax. Double-quote identifiers. Use LIMIT not TOP.",
    "postgresql": "PostgreSQL syntax. Double-quote identifiers. Use LIMIT not TOP.",
    "mysql":      "MySQL syntax. Backtick identifiers (`table`). Use LIMIT not TOP.",
    "mssql":      "T-SQL (SQL Server) syntax. Square-bracket identifiers. Use TOP N not LIMIT.",
    "unknown":    "Standard ANSI SQL syntax.",
}


def build_prompt(user_query, schema, relationships=None, db_type="sqlite"):
    dialect = DIALECT.get(db_type, DIALECT["unknown"])

    # ── Schema block ──────────────────────────────────────────
    lines = ["DATABASE SCHEMA:\n"]
    for table, info in schema.items():
        row_count = info.get("row_count", 0)
        aliases   = info.get("aliases", [])
        lines.append(f"Table: {table}  ({row_count:,} rows)")
        if aliases:
            lines.append(f"  Also known as: {', '.join(aliases)}")
        lines.append("  Columns:")
        for col in info.get("columns", []):
            pk      = " [PK]" if col.get("primary_key") else ""
            col_aliases = col.get("aliases", [])
            al      = f" [also: {', '.join(col_aliases)}]" if col_aliases else ""
            samples = col.get("samples", [])
            smp     = f"  -- e.g. {', '.join(str(x) for x in samples[:3])}" if samples else ""
            lines.append(f"    - {col['column']} ({col['type']}){pk}{al}{smp}")
        lines.append("")

    if relationships:
        lines.append("RELATIONSHIPS:")
        for r in relationships:
            lines.append(f"  {r['from']} -> {r['to']}")
        lines.append("")

    schema_text = "\n".join(lines)

    return f"""{schema_text}
INSTRUCTIONS:
- Use {dialect}
- Return ONLY the SQL query — no explanation, no markdown fences, no backticks around the query.
- Use table aliases in JOINs. Always use explicit JOIN ... ON syntax.
- Qualify column names with table aliases when joining multiple tables.
- If asked for "top N", use the correct syntax for the DB type.
- Limit results to 500 rows maximum unless the user specifies otherwise.
- Use ONLY the exact table and column names shown in the schema above.
- If the question cannot be answered with the available schema, return:
  SELECT 'Cannot answer: required data not in schema' AS message;

EXAMPLES:
Q: show all customers
A: SELECT * FROM "Customer" LIMIT 500

Q: top 5 customers by total revenue
A: SELECT c."CustomerID", c."Name", SUM(s."Amount") AS total_revenue FROM "Customer" c JOIN "SalesJournal" s ON c."CustomerID" = s."CustomerID" GROUP BY c."CustomerID", c."Name" ORDER BY total_revenue DESC LIMIT 5

User Question: {user_query}
SQL:"""
