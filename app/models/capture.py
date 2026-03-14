from __future__ import annotations
from pydantic import BaseModel, Field
from typing import Optional, Literal, Any
from datetime import datetime
from enum import Enum


class SourceType(str, Enum):
    image = "image"
    url = "url"
    text = "text"


class CaptureType(str, Enum):
    job = "Job"
    internship = "Internship"
    event = "Event"
    article = "Article / Learning Resource"
    product = "Product"
    expense = "Expense / Receipt"
    lead = "Lead / Contact"
    travel = "Travel"
    social = "Social Post / Content Idea"
    research = "Research / Reference"
    reminder = "Personal Reminder"
    other = "Other"


class ReviewStatus(str, Enum):
    ok = "OK"
    needs_review = "Needs Review"
    duplicate = "Duplicate"


# ─── Raw inbound request ─────────────────────────────────────────────────────

class CaptureRequest(BaseModel):
    channel: str = "telegram"
    source_type: SourceType
    raw_input: str                 # URL, text, or image base64/path
    user_note: Optional[str] = None
    telegram_user_id: Optional[int] = None


# ─── Extracted Job/Internship ─────────────────────────────────────────────────

class JobCapture(BaseModel):
    capture_type: Literal["Job", "Internship"]
    job_link: Optional[str] = None
    position_title: Optional[str] = None
    company_name: Optional[str] = None
    published_date: Optional[str] = None
    deadline: Optional[str] = None
    location: Optional[str] = None
    work_mode: Optional[str] = None
    source_platform: Optional[str] = None
    summary: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    confidence: float = Field(0.5, ge=0, le=1)
    review_status: ReviewStatus = ReviewStatus.ok
    notes: Optional[str] = None
    raw_ai_json: Optional[str] = None

    @property
    def needs_review(self) -> bool:
        return self.confidence < 0.7 or self.review_status == ReviewStatus.needs_review


# ─── Extracted generic capture ───────────────────────────────────────────────

class OtherCapture(BaseModel):
    capture_type: str = "Other"
    title: Optional[str] = None
    summary: Optional[str] = None
    main_category: Optional[str] = None
    subcategory: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    dates_mentioned: list[str] = Field(default_factory=list)
    links_found: list[str] = Field(default_factory=list)
    priority: Optional[str] = None
    action_needed: Optional[str] = None
    confidence: float = Field(0.5, ge=0, le=1)
    review_status: ReviewStatus = ReviewStatus.ok
    notes: Optional[str] = None
    raw_ai_json: Optional[str] = None

    @property
    def needs_review(self) -> bool:
        return self.confidence < 0.7 or self.review_status == ReviewStatus.needs_review


# ─── Final result sent back to the user ──────────────────────────────────────

class CaptureResult(BaseModel):
    status: Literal["saved", "duplicate", "error"]
    sheet_tab: Optional[str] = None
    capture_type: Optional[str] = None
    title: Optional[str] = None
    company: Optional[str] = None
    summary: Optional[str] = None
    review_needed: bool = False
    duplicate_of: Optional[str] = None
    error_message: Optional[str] = None
    extra_fields: dict[str, Any] = Field(default_factory=dict)

    def to_telegram_message(self) -> str:
        if self.status == "error":
            return f"❌ *Error saving capture*\n{self.error_message or 'Unknown error'}"

        if self.status == "duplicate":
            return (
                f"⚠️ *Duplicate detected* — this was already captured.\n"
                f"Original: `{self.duplicate_of}`"
            )

        lines = [f"✅ *Saved to* `{self.sheet_tab}`"]
        if self.capture_type:
            lines.append(f"*Type:* {self.capture_type}")
        if self.title:
            lines.append(f"*Title/Role:* {self.title}")
        if self.company:
            lines.append(f"*Company:* {self.company}")
        if self.extra_fields.get("published_date"):
            lines.append(f"*Published:* {self.extra_fields['published_date']}")
        if self.extra_fields.get("deadline"):
            lines.append(f"*Deadline:* {self.extra_fields['deadline']}")
        if self.extra_fields.get("location"):
            lines.append(f"*Location:* {self.extra_fields['location']}")
        if self.extra_fields.get("work_mode"):
            lines.append(f"*Work Mode:* {self.extra_fields['work_mode']}")
        if self.extra_fields.get("source_platform"):
            lines.append(f"*Platform:* {self.extra_fields['source_platform']}")
        if self.summary:
            lines.append(f"*Summary:* {self.summary}")
        if self.review_needed:
            lines.append("🔍 *Review Needed:* Yes — confidence below threshold")
        else:
            lines.append("*Review Needed:* No")
        return "\n".join(lines)
