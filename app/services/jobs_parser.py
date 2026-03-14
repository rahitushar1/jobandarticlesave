"""
Jobs/Internship parser: takes raw AI extraction dict and produces a
validated JobCapture model.
"""
import json
import re
import structlog
from typing import Optional
from dateutil import parser as dateutil_parser

from app.models.capture import JobCapture, ReviewStatus

log = structlog.get_logger(__name__)

_DATE_NORMALIZE_FALLBACK = re.compile(r"\d{4}-\d{2}-\d{2}")


def _normalize_date(raw: Optional[str]) -> Optional[str]:
    """Try to convert any date string to YYYY-MM-DD."""
    if not raw:
        return None
    raw = raw.strip()
    # Already correct format
    if _DATE_NORMALIZE_FALLBACK.fullmatch(raw):
        return raw
    try:
        dt = dateutil_parser.parse(raw, fuzzy=True)
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return None


def _normalize_work_mode(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    raw = raw.lower()
    if "remote" in raw:
        return "Remote"
    if "hybrid" in raw:
        return "Hybrid"
    if "on-site" in raw or "onsite" in raw or "in-person" in raw or "office" in raw:
        return "On-site"
    return raw.title()


def build_job_capture(ai_dict: dict, classification_confidence: float = 0.8) -> JobCapture:
    """
    Merge classification confidence with AI extraction output,
    normalise fields, and return a validated JobCapture.
    """
    capture_type = ai_dict.get("capture_type", "Job")
    if capture_type not in ("Job", "Internship"):
        capture_type = "Job"

    ai_confidence = float(ai_dict.get("confidence", 0.5))
    # Use the minimum of classification + extraction confidence
    confidence = round(min(ai_confidence, classification_confidence), 2)

    review_str = str(ai_dict.get("review_status", "OK")).upper()
    review_status = (
        ReviewStatus.needs_review
        if "REVIEW" in review_str or confidence < 0.7
        else ReviewStatus.ok
    )

    published_date = _normalize_date(ai_dict.get("published_date"))
    deadline = _normalize_date(ai_dict.get("deadline"))

    # Build notes: combine AI notes + raw deadline text if present
    notes_parts = []
    if ai_dict.get("notes"):
        notes_parts.append(str(ai_dict["notes"]))
    if ai_dict.get("deadline_raw") and ai_dict["deadline_raw"] != deadline:
        notes_parts.append(f"Raw deadline: {ai_dict['deadline_raw']}")
    notes = " | ".join(notes_parts) if notes_parts else None

    tags = ai_dict.get("tags", [])
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]

    return JobCapture(
        capture_type=capture_type,
        job_link=ai_dict.get("job_link"),
        position_title=ai_dict.get("position_title"),
        company_name=ai_dict.get("company_name"),
        published_date=published_date,
        deadline=deadline,
        location=ai_dict.get("location"),
        work_mode=_normalize_work_mode(ai_dict.get("work_mode")),
        source_platform=ai_dict.get("source_platform"),
        summary=ai_dict.get("summary"),
        tags=tags[:8],
        confidence=confidence,
        review_status=review_status,
        notes=notes,
        raw_ai_json=json.dumps(ai_dict),
    )
