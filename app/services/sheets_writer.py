"""
Google Sheets writer service.
Appends rows to Jobs_Internships or Other_Captures tabs.
Uses a service account for auth.
"""
import json
import structlog
from datetime import datetime
from typing import Optional, Union
from functools import lru_cache

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app.config import get_settings
from app.models.capture import JobCapture, OtherCapture

log = structlog.get_logger(__name__)
settings = get_settings()

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# ─── Column headers ───────────────────────────────────────────────────────────

JOBS_HEADERS = [
    "Captured At", "Source Channel", "Source Type", "Original Input",
    "Job Link", "Position Title", "Company Name", "Job Type",
    "Published Date", "Deadline To Apply", "Location", "Work Mode",
    "Source Platform", "Summary", "Tags", "Confidence",
    "Review Status", "Notes", "Raw AI JSON",
]

OTHER_HEADERS = [
    "Captured At", "Source Channel", "Source Type", "Original Input",
    "Title", "Summary", "Main Category", "Subcategory", "Tags",
    "Entities", "Dates Mentioned", "Links Found", "Priority",
    "Action Needed", "Confidence", "Review Status", "Notes", "Raw AI JSON",
]


def _get_service():
    """Build and return the Sheets service (cached via module-level singleton)."""
    sa_info = json.loads(settings.google_service_account_json)
    creds = service_account.Credentials.from_service_account_info(
        sa_info, scopes=SCOPES
    )
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


_sheets_service = None


def get_sheets_service():
    global _sheets_service
    if _sheets_service is None:
        _sheets_service = _get_service()
    return _sheets_service


# ─── Sheet bootstrap ──────────────────────────────────────────────────────────

async def ensure_sheet_tabs() -> None:
    """
    Verify both tabs exist with correct headers.
    Creates them and writes headers if missing.
    (Run once at startup.)
    """
    service = get_sheets_service()
    spreadsheet_id = settings.google_spreadsheet_id

    try:
        meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
        existing_tabs = {s["properties"]["title"] for s in meta.get("sheets", [])}
    except Exception as e:
        log.error("sheets_meta_error", error=str(e))
        return

    requests = []
    for tab_name in [settings.sheet_jobs_tab, settings.sheet_other_tab]:
        if tab_name not in existing_tabs:
            requests.append({
                "addSheet": {"properties": {"title": tab_name}}
            })
            log.info("sheet_tab_will_create", tab=tab_name)

    if requests:
        service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": requests},
        ).execute()

    # Write headers if row 1 is empty
    for tab_name, headers in [
        (settings.sheet_jobs_tab, JOBS_HEADERS),
        (settings.sheet_other_tab, OTHER_HEADERS),
    ]:
        range_str = f"'{tab_name}'!A1:Z1"
        result = service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id, range=range_str
        ).execute()
        if not result.get("values"):
            service.spreadsheets().values().update(
                spreadsheetId=spreadsheet_id,
                range=range_str,
                valueInputOption="RAW",
                body={"values": [headers]},
            ).execute()
            log.info("sheet_headers_written", tab=tab_name)


# ─── Row builders ─────────────────────────────────────────────────────────────

def _build_jobs_row(
    job: JobCapture,
    channel: str,
    source_type: str,
    original_input: str,
    captured_at: str,
) -> list:
    return [
        captured_at,
        channel,
        source_type,
        original_input[:200],
        job.job_link or "",
        job.position_title or "",
        job.company_name or "",
        job.capture_type,
        job.published_date or "",
        job.deadline or "",
        job.location or "",
        job.work_mode or "",
        job.source_platform or "",
        job.summary or "",
        ", ".join(job.tags),
        str(job.confidence),
        job.review_status.value,
        job.notes or "",
        job.raw_ai_json or "",
    ]


def _build_other_row(
    capture: OtherCapture,
    channel: str,
    source_type: str,
    original_input: str,
    captured_at: str,
) -> list:
    return [
        captured_at,
        channel,
        source_type,
        original_input[:200],
        capture.title or "",
        capture.summary or "",
        capture.main_category or "",
        capture.subcategory or "",
        ", ".join(capture.tags),
        ", ".join(capture.entities),
        ", ".join(capture.dates_mentioned),
        ", ".join(capture.links_found),
        capture.priority or "Medium",
        capture.action_needed or "",
        str(capture.confidence),
        capture.review_status.value,
        capture.notes or "",
        capture.raw_ai_json or "",
    ]


# ─── Main write functions ──────────────────────────────────────────────────────

def _append_row(tab_name: str, row: list) -> int:
    """Appends a row and returns the new row index (1-based)."""
    service = get_sheets_service()
    result = service.spreadsheets().values().append(
        spreadsheetId=settings.google_spreadsheet_id,
        range=f"'{tab_name}'!A1",
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body={"values": [row]},
    ).execute()
    updated_range = result.get("updates", {}).get("updatedRange", "")
    # Parse row number from range like: 'Jobs_Internships'!A42:S42
    try:
        row_num = int(updated_range.split("!")[1].split(":")[0][1:])
    except Exception:
        row_num = -1
    log.info("sheet_row_appended", tab=tab_name, row=row_num)
    return row_num


async def write_job(
    job: JobCapture,
    channel: str,
    source_type: str,
    original_input: str,
) -> int:
    """Append to Jobs_Internships tab. Returns row number."""
    captured_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    row = _build_jobs_row(job, channel, source_type, original_input, captured_at)
    try:
        return _append_row(settings.sheet_jobs_tab, row)
    except HttpError as e:
        log.error("sheets_write_error", error=str(e))
        raise


async def write_other(
    capture: OtherCapture,
    channel: str,
    source_type: str,
    original_input: str,
) -> int:
    """Append to Other_Captures tab. Returns row number."""
    captured_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    row = _build_other_row(capture, channel, source_type, original_input, captured_at)
    try:
        return _append_row(settings.sheet_other_tab, row)
    except HttpError as e:
        log.error("sheets_write_error", error=str(e))
        raise
