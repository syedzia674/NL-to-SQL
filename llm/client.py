import re, logging
logger = logging.getLogger(__name__)
_llm = None

def get_llm():
    global _llm
    if _llm: return _llm
    from langchain_openai import ChatOpenAI
    from config import settings
    kw = dict(model=settings.MODEL_NAME, temperature=settings.MODEL_TEMPERATURE,
              max_tokens=settings.MODEL_MAX_TOKENS, timeout=settings.API_TIMEOUT)
    try:
        _llm = ChatOpenAI(base_url=settings.LOCAL_LLM_API_BASE,
                          api_key=settings.LOCAL_LLM_API_KEY or "none", **kw)
    except TypeError:
        _llm = ChatOpenAI(openai_api_base=settings.LOCAL_LLM_API_BASE,
                          openai_api_key=settings.LOCAL_LLM_API_KEY or "none", **kw)
    return _llm

def extract_sql(text):
    if not text: return ""
    text = re.sub(r"```sql|```","",text,flags=re.IGNORECASE).strip()
    m = re.search(r"((?:WITH|SELECT)\s+.*?)(?:;|\Z)",text,flags=re.IGNORECASE|re.DOTALL)
    if not m: return ""
    return re.sub(r"[ \t]+"," ",m.group(1)).strip()

def generate_sql(prompt):
    from config import settings
    llm = get_llm()
    try:
        response = llm.invoke(prompt)
    except Exception as e:
        err = str(e)
        if any(x in err.lower() for x in ["connection","refused","timeout"]):
            raise ConnectionError(f"Cannot reach LLM at {settings.LOCAL_LLM_API_BASE}. Check LOCAL_LLM_API_BASE/KEY. Error: {err}")
        raise
    sql = extract_sql(response.content)
    if not sql:
        raise ValueError(f"LLM returned no SQL. Response: {response.content[:300]}")
    return sql
