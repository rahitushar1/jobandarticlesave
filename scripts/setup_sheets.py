#!/usr/bin/env python3
"""
scripts/setup_sheets.py
-----------------------
Run this ONCE to:
  1. Create the Jobs_Internships and Other_Captures tabs
  2. Write headers
  3. Apply basic column formatting (widths, bold headers, frozen row)

Usage:
  python scripts/setup_sheets.py

Requires .env to be configured.
"""
import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from google.oauth2 import service_account
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

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

COLUMN_WIDTHS = {
    "Jobs_Internships": {
        0: 160,   # Captured At
        1: 100,   # Source Channel
        2: 80,    # Source Type
        3: 200,   # Original Input
        4: 250,   # Job Link
        5: 200,   # Position Title
        6: 160,   # Company Name
        7: 90,    # Job Type
        8: 110,   # Published Date
        9: 130,   # Deadline
        10: 140,  # Location
        11: 90,   # Work Mode
        12: 120,  # Source Platform
        13: 300,  # Summary
        14: 160,  # Tags
        15: 90,   # Confidence
        16: 110,  # Review Status
        17: 200,  # Notes
        18: 60,   # Raw AI JSON (hidden / narrow)
    },
    "Other_Captures": {
        0: 160, 1: 100, 2: 80, 3: 200, 4: 200, 5: 300,
        6: 140, 7: 120, 8: 160, 9: 200, 10: 130, 11: 200,
        12: 80, 13: 200, 14: 90, 15: 110, 16: 200, 17: 60,
    },
}


def main():
    sa_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    spreadsheet_id = os.environ.get("GOOGLE_SPREADSHEET_ID")
    jobs_tab = os.environ.get("SHEET_JOBS_TAB", "Jobs_Internships")
    other_tab = os.environ.get("SHEET_OTHER_TAB", "Other_Captures")

    if not sa_json or not spreadsheet_id:
        print("ERROR: GOOGLE_SERVICE_ACCOUNT_JSON and GOOGLE_SPREADSHEET_ID must be set in .env")
        sys.exit(1)

    creds = service_account.Credentials.from_service_account_info(
        json.loads(sa_json), scopes=SCOPES
    )
    service = build("sheets", "v4", credentials=creds, cache_discovery=False)

    # ── Get existing sheets ───────────────────────────────────
    meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    existing = {s["properties"]["title"]: s["properties"]["sheetId"] for s in meta.get("sheets", [])}
    print(f"Existing tabs: {list(existing.keys())}")

    # ── Create missing tabs ───────────────────────────────────
    add_requests = []
    for tab_name in [jobs_tab, other_tab]:
        if tab_name not in existing:
            add_requests.append({"addSheet": {"properties": {"title": tab_name}}})
            print(f"  → Will create tab: {tab_name}")

    if add_requests:
        result = service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": add_requests},
        ).execute()
        # Refresh meta
        meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
        existing = {s["properties"]["title"]: s["properties"]["sheetId"] for s in meta.get("sheets", [])}

    # ── Write headers ─────────────────────────────────────────
    for tab_name, headers in [(jobs_tab, JOBS_HEADERS), (other_tab, OTHER_HEADERS)]:
        range_str = f"'{tab_name}'!A1"
        service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=range_str,
            valueInputOption="RAW",
            body={"values": [headers]},
        ).execute()
        print(f"  ✓ Headers written to: {tab_name}")

    # ── Format headers (bold + freeze row 1) ─────────────────
    format_requests = []
    for tab_name, headers in [(jobs_tab, JOBS_HEADERS), (other_tab, OTHER_HEADERS)]:
        sheet_id = existing.get(tab_name)
        if sheet_id is None:
            continue

        # Bold + background on row 1
        format_requests.append({
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 0,
                    "endRowIndex": 1,
                },
                "cell": {
                    "userEnteredFormat": {
                        "textFormat": {"bold": True},
                        "backgroundColor": {"red": 0.23, "green": 0.47, "blue": 0.85},
                        "horizontalAlignment": "CENTER",
                    }
                },
                "fields": "userEnteredFormat(textFormat,backgroundColor,horizontalAlignment)",
            }
        })

        # Freeze row 1
        format_requests.append({
            "updateSheetProperties": {
                "properties": {
                    "sheetId": sheet_id,
                    "gridProperties": {"frozenRowCount": 1},
                },
                "fields": "gridProperties.frozenRowCount",
            }
        })

        # Column widths
        widths = COLUMN_WIDTHS.get(tab_name, {})
        for col_idx, width in widths.items():
            format_requests.append({
                "updateDimensionProperties": {
                    "range": {
                        "sheetId": sheet_id,
                        "dimension": "COLUMNS",
                        "startIndex": col_idx,
                        "endIndex": col_idx + 1,
                    },
                    "properties": {"pixelSize": width},
                    "fields": "pixelSize",
                }
            })

    if format_requests:
        service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": format_requests},
        ).execute()
        print("  ✓ Formatting applied (bold headers, frozen row 1, column widths)")

    print(f"\n✅ Setup complete!")
    print(f"   Spreadsheet: https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit")
    print(f"   Tabs created: {jobs_tab}, {other_tab}")


if __name__ == "__main__":
    main()
