"""
Classification service: decides whether content is Job, Internship, or Other.
Uses heuristics first (fast), then AI as fallback.
"""
import re
import json
import structlog
from typing import Literal
from tenacity import retry, stop_after_attempt, wait_exponential

from openai import AsyncOpenAI, APIError, RateLimitError

from app.config import get_settings
from app.prompts.prompts import CLASSIFICATION_SYSTEM, CLASSIFICATION_USER
from app.services.extraction_service import get_openai_client, _parse_json_response

log = structlog.get_logger(__name__)
settings = get_settings()

ClassificationResult = Literal["Job", "Internship", "Other"]

# ── Quick heuristics ─────────────────────────────────────────────────────────

_INTERN_PATTERNS = re.compile(
    r"\b(intern(?:ship)?|co.?op|trainee|apprentice|placement|summer\s+program)\b",
    re.IGNORECASE,
)

_JOB_PATTERNS = re.compile(
    r"\b(job|position|role|vacancy|opening|career|hiring|we.?re\s+hiring|"
    r"apply\s+now|full.?time|part.?time|contract|freelance|engineer|developer|"
    r"designer|analyst|manager|director|coordinator|specialist|associate)\b",
    re.IGNORECASE,
)

_JOB_URL_PATTERNS = re.compile(
    r"(linkedin\.com/jobs|jobs\.|careers\.|greenhouse\.io|lever\.co|"
    r"workday\.com|jobvite|myworkdayjobs|indeed\.com|glassdoor\.com|"
    r"angel\.co/jobs|wellfound|simplyhired)",
    re.IGNORECASE,
)


def _heuristic_classify(content: str) -> tuple[ClassificationResult | None, float]:
    """
    Returns (label, confidence) or (None, 0) if heuristics are inconclusive.
    """
    text = content[:2000]

    intern_hits = len(_INTERN_PATTERNS.findall(text))
    job_hits = len(_JOB_PATTERNS.findall(text))
    url_job = bool(_JOB_URL_PATTERNS.search(text))

    if intern_hits >= 2:
        return "Internship", min(0.7 + intern_hits * 0.05, 0.95)
    if url_job and job_hits >= 1:
        if intern_hits >= 1:
            return "Internship", 0.75
        return "Job", 0.80
    if job_hits >= 4:
        return "Job", 0.70

    return None, 0.0


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=8),
    retry=retry_if_exception_type((APIError, RateLimitError)),
)
async def _ai_classify(content: str) -> tuple[ClassificationResult, float]:
    client = get_openai_client()
    user_msg = CLASSIFICATION_SYSTEM + "\n\n" + CLASSIFICATION_USER.format(content=content[:2500])
    resp = await client.chat.completions.create(
        model=settings.ai_text_model,
        messages=[
            {"role": "system", "content": CLASSIFICATION_SYSTEM},
            {"role": "user",   "content": CLASSIFICATION_USER.format(content=content[:2500])},
        ],
        temperature=0,
        response_format={"type": "json_object"},
        max_tokens=60,
    )
    raw = resp.choices[0].message.content
    parsed = _parse_json_response(raw)
    label = parsed.get("type", "Other")
    confidence = float(parsed.get("confidence", 0.5))
    if label not in ("Job", "Internship", "Other"):
        label = "Other"
    return label, confidence


async def classify_content(content: str) -> tuple[ClassificationResult, float]:
    """
    Main entry point. Returns (label, confidence).
    Tries heuristics first; falls back to AI if inconclusive.
    """
    label, confidence = _heuristic_classify(content)
    if label is not None:
        log.info("classified_by_heuristic", label=label, confidence=confidence)
        return label, confidence

    log.info("heuristics_inconclusive_using_ai")
    try:
        label, confidence = await _ai_classify(content)
    except Exception as e:
        log.error("classification_ai_failed", error=str(e))
        label, confidence = "Other", 0.4

    log.info("classified_by_ai", label=label, confidence=confidence)
    return label, confidence
