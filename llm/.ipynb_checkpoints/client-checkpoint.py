"""
llm/client.py — Google Gemini via langchain-google-genai
"""
import re
import logging
from config import settings

logger = logging.getLogger(__name__)
_llm = None


def get_llm():
    global _llm
    if _llm:
        return _llm
    from langchain_google_genai import ChatGoogleGenerativeAI
    _llm = ChatGoogleGenerativeAI(
        model=settings.MODEL_NAME,
        google_api_key=settings.GOOGLE_API_KEY,
        temperature=settings.MODEL_TEMPERATURE,
        max_output_tokens=settings.MODEL_MAX_TOKENS,
    )
    logger.info(f"Gemini LLM initialised: {settings.MODEL_NAME}")
    return _llm


def extract_sql(text: str) -> str:
    """Extract SQL from LLM response — handles markdown fences, SELECT, WITH/CTE."""
    if not text:
        return ""
    # Strip markdown fences
    text = re.sub(r"```sql|```", "", text, flags=re.IGNORECASE).strip()
    # Match SELECT or WITH (CTE)
    match = re.search(
        r"((?:WITH|SELECT)\s+.*?)(?:;|\Z)",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return ""
    sql = match.group(1)
    sql = re.sub(r"[ \t]+", " ", sql).strip()
    return sql


def generate_sql(prompt: str) -> str:
    """Call Gemini and return extracted SQL."""
    llm = get_llm()
    try:
        response = llm.invoke(prompt)
        raw = str(response.content).strip()
        logger.debug(f"Gemini raw response: {raw[:300]}")
        sql = extract_sql(raw)
        if not sql:
            raise ValueError(f"No SQL found in LLM response:\n{raw[:500]}")
        return sql
    except Exception as e:
        raise RuntimeError(f"LLM Error: {e}") from e
