"""
Generic capture parser: takes raw AI extraction dict and produces a
validated OtherCapture model.
"""
import json
import structlog
from typing import Optional
from dateutil import parser as dateutil_parser

from app.models.capture import OtherCapture, ReviewStatus

log = structlog.get_logger(__name__)

VALID_CATEGORIES = {
    "Event", "Article / Learning Resource", "Product",
    "Expense / Receipt", "Lead / Contact", "Travel",
    "Social Post / Content Idea", "Research / Reference",
    "Personal Reminder", "Other",
}

VALID_PRIORITIES = {"High", "Medium", "Low"}


def _normalize_date(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    try:
        dt = dateutil_parser.parse(str(raw).strip(), fuzzy=True)
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return str(raw)[:20]


def _normalize_list(val, coerce_str_split=",") -> list[str]:
    if not val:
        return []
    if isinstance(val, list):
        return [str(v).strip() for v in val if str(v).strip()]
    if isinstance(val, str):
        return [v.strip() for v in val.split(coerce_str_split) if v.strip()]
    return []


def build_other_capture(ai_dict: dict, classification_confidence: float = 0.6) -> OtherCapture:
    ai_confidence = float(ai_dict.get("confidence", 0.5))
    confidence = round(min(ai_confidence, classification_confidence), 2)

    review_str = str(ai_dict.get("review_status", "OK")).upper()
    review_status = (
        ReviewStatus.needs_review
        if "REVIEW" in review_str or confidence < 0.7
        else ReviewStatus.ok
    )

    capture_type = ai_dict.get("capture_type", "Other")
    if capture_type not in VALID_CATEGORIES:
        capture_type = "Other"

    priority = ai_dict.get("priority", "Medium")
    if priority not in VALID_PRIORITIES:
        priority = "Medium"

    dates_raw = _normalize_list(ai_dict.get("dates_mentioned"))
    dates_normalized = []
    for d in dates_raw:
        nd = _normalize_date(d)
        if nd:
            dates_normalized.append(nd)

    return OtherCapture(
        capture_type=capture_type,
        title=ai_dict.get("title"),
        summary=ai_dict.get("summary"),
        main_category=ai_dict.get("main_category") or capture_type,
        subcategory=ai_dict.get("subcategory"),
        tags=_normalize_list(ai_dict.get("tags"))[:8],
        entities=_normalize_list(ai_dict.get("entities"))[:10],
        dates_mentioned=dates_normalized,
        links_found=_normalize_list(ai_dict.get("links_found"))[:5],
        priority=priority,
        action_needed=ai_dict.get("action_needed"),
        confidence=confidence,
        review_status=review_status,
        notes=ai_dict.get("notes"),
        raw_ai_json=json.dumps(ai_dict),
    )
