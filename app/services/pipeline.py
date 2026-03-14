"""
Capture pipeline: orchestrates extraction → classification → parsing → saving.
Called by all input adapters (Telegram, web, etc.)
"""
import json
import structlog
import os
from typing import Optional

from app.config import get_settings
from app.models.capture import CaptureRequest, CaptureResult, SourceType, ReviewStatus
from app.services.extraction_service import (
    extract_from_image, extract_from_url, extract_from_text,
    deep_extract_job, deep_extract_other,
)
from app.services.classification_service import classify_content
from app.services.jobs_parser import build_job_capture
from app.services.capture_parser import build_other_capture
from app.services.sheets_writer import write_job, write_other
from app import database as db

log = structlog.get_logger(__name__)
settings = get_settings()


async def process_capture(request: CaptureRequest) -> CaptureResult:
    """
    Full pipeline:
      1. Validate & extract raw content
      2. Get extractable text
      3. Classify: Job / Internship / Other
      4. Deep extract using the right parser
      5. Dedup check
      6. Write to Google Sheets
      7. Persist to SQLite
      8. Return CaptureResult
    """
    try:
        return await _run_pipeline(request)
    except Exception as e:
        log.error("pipeline_error", error=str(e), exc_info=True)
        return CaptureResult(
            status="error",
            error_message=f"Internal error: {str(e)[:200]}",
        )


async def _run_pipeline(request: CaptureRequest) -> CaptureResult:
    # ── Step 1: Primary extraction ────────────────────────────
    image_bytes: Optional[bytes] = None
    media_type = "image/jpeg"

    if request.source_type == SourceType.image:
        # raw_input is either a file path or base64 string
        if os.path.isfile(request.raw_input):
            with open(request.raw_input, "rb") as f:
                image_bytes = f.read()
            ext = os.path.splitext(request.raw_input)[1].lower()
            media_type = {
                ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".gif": "image/gif", ".webp": "image/webp",
            }.get(ext, "image/jpeg")
        else:
            # Assume base64
            import base64
            image_bytes = base64.b64decode(request.raw_input)

        image_meta = await extract_from_image(image_bytes, media_type, request.user_note)
        extractable_text = image_meta.get("extracted_text", "")
        hint_is_job = image_meta.get("is_job_posting", False)
        original_input_repr = image_meta.get("description", "Image upload")
        extra_context = f"\n\n[Image description: {image_meta.get('description', '')}]"

    elif request.source_type == SourceType.url:
        url_data = await extract_from_url(request.raw_input, request.user_note)
        extractable_text = url_data.get("content_for_extraction", "")
        hint_is_job = url_data.get("ai_meta", {}).get("is_job_or_internship", False)
        original_input_repr = request.raw_input
        extra_context = ""

    else:  # text
        text_data = await extract_from_text(request.raw_input, request.user_note)
        extractable_text = text_data.get("content_for_extraction", "")
        hint_is_job = False
        original_input_repr = request.raw_input[:300]
        extra_context = ""

    if not extractable_text.strip():
        extractable_text = request.raw_input[:2000]

    full_content = extractable_text + extra_context
    if request.user_note:
        full_content += f"\n\n[User note: {request.user_note}]"

    # ── Step 2: Classification ─────────────────────────────────
    # If URL/image pre-check already says it's a job, bias confidence
    classification_label, classification_confidence = await classify_content(full_content)
    if hint_is_job and classification_label == "Other":
        log.info("hint_overriding_classification", hint=hint_is_job)
        classification_label = "Job"
        classification_confidence = max(classification_confidence, 0.6)

    is_job_type = classification_label in ("Job", "Internship")

    # ── Step 3: Deep extraction ────────────────────────────────
    user_note_str = request.user_note or ""
    if is_job_type:
        ai_dict = await deep_extract_job(full_content, request.source_type.value, user_note_str)
        # If classification said Internship but extraction overrides, trust extraction
        if classification_label == "Internship" and ai_dict.get("capture_type") == "Job":
            ai_dict["capture_type"] = "Internship"
        elif classification_label == "Job":
            ai_dict.setdefault("capture_type", "Job")
        job_capture = build_job_capture(ai_dict, classification_confidence)
        title = job_capture.position_title
        company = job_capture.company_name
    else:
        ai_dict = await deep_extract_other(full_content, request.source_type.value, user_note_str)
        other_capture = build_other_capture(ai_dict, classification_confidence)
        title = other_capture.title
        company = None

    # ── Step 4: Deduplication ──────────────────────────────────
    raw_for_dedup = request.raw_input if request.source_type != SourceType.image else original_input_repr
    existing = await db.check_duplicate(
        source_type=request.source_type.value,
        raw_input=raw_for_dedup,
        title=title,
        company=company,
    )
    if existing:
        log.info("duplicate_detected", existing_id=existing["id"])
        return CaptureResult(
            status="duplicate",
            duplicate_of=f"Row saved at {existing['captured_at']} (Tab: {existing['sheet_tab']})",
            capture_type=existing.get("capture_type"),
            title=existing.get("title"),
            company=existing.get("company"),
        )

    # ── Step 5: Write to Google Sheets ────────────────────────
    if is_job_type:
        row_num = await write_job(
            job_capture,
            channel=request.channel,
            source_type=request.source_type.value,
            original_input=original_input_repr,
        )
        sheet_tab = settings.sheet_jobs_tab
        raw_ai_json = job_capture.raw_ai_json or "{}"
        review_needed = job_capture.needs_review
        extra_fields = {
            "published_date": job_capture.published_date,
            "deadline": job_capture.deadline,
            "location": job_capture.location,
            "work_mode": job_capture.work_mode,
            "source_platform": job_capture.source_platform,
        }
        summary = job_capture.summary
        capture_type_str = job_capture.capture_type
    else:
        row_num = await write_other(
            other_capture,
            channel=request.channel,
            source_type=request.source_type.value,
            original_input=original_input_repr,
        )
        sheet_tab = settings.sheet_other_tab
        raw_ai_json = other_capture.raw_ai_json or "{}"
        review_needed = other_capture.needs_review
        extra_fields = {
            "main_category": other_capture.main_category,
            "priority": other_capture.priority,
            "action_needed": other_capture.action_needed,
        }
        summary = other_capture.summary
        capture_type_str = other_capture.capture_type

    # ── Step 6: Persist to SQLite ─────────────────────────────
    capture_id = await db.save_capture(
        source_type=request.source_type.value,
        channel=request.channel,
        capture_type=capture_type_str,
        title=title,
        company=company,
        sheet_tab=sheet_tab,
        sheets_row=row_num,
        raw_input=raw_for_dedup,
        raw_ai_json=raw_ai_json,
        status="ok",
    )
    await db.log_event(capture_id, "saved", f"Tab={sheet_tab} Row={row_num}")

    return CaptureResult(
        status="saved",
        sheet_tab=sheet_tab,
        capture_type=capture_type_str,
        title=title,
        company=company,
        summary=summary,
        review_needed=review_needed,
        extra_fields=extra_fields,
    )
