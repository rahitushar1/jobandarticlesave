"""
Extraction service: calls OpenAI to understand and extract structured data
from images, URLs, and text.
"""
import base64
import json
import structlog
from pathlib import Path
from typing import Optional
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from openai import AsyncOpenAI, APIError, RateLimitError

from app.config import get_settings
from app.prompts.prompts import (
    IMAGE_EXTRACTION_SYSTEM, IMAGE_EXTRACTION_USER,
    URL_UNDERSTANDING_SYSTEM,
    JOBS_EXTRACTION_SYSTEM, JOBS_EXTRACTION_USER,
    OTHER_EXTRACTION_SYSTEM, OTHER_EXTRACTION_USER,
)
from app.services.url_fetcher import fetch_url_content, build_url_context

log = structlog.get_logger(__name__)
settings = get_settings()

_client: Optional[AsyncOpenAI] = None


def get_openai_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        client_kwargs = {"api_key": settings.ai_api_key}
        if settings.ai_base_url:
            client_kwargs["base_url"] = settings.ai_base_url
        _client = AsyncOpenAI(**client_kwargs)
    return _client


def _parse_json_response(text: str) -> dict:
    """Extract JSON from model output, handling markdown fences."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1]) if len(lines) > 2 else text
    return json.loads(text)


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((APIError, RateLimitError)),
)
async def _chat(system: str, user: str, model: Optional[str] = None) -> str:
    client = get_openai_client()
    resp = await client.chat.completions.create(
        model=model or settings.ai_text_model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
        temperature=0,
        response_format={"type": "json_object"},
    )
    return resp.choices[0].message.content


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((APIError, RateLimitError)),
)
async def _vision_chat(system: str, image_b64: str, media_type: str = "image/jpeg") -> str:
    client = get_openai_client()
    resp = await client.chat.completions.create(
        model=settings.ai_vision_model,
        messages=[
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{media_type};base64,{image_b64}",
                            "detail": "high",
                        },
                    },
                    {"type": "text", "text": IMAGE_EXTRACTION_USER},
                ],
            },
        ],
        temperature=0,
        response_format={"type": "json_object"},
    )
    return resp.choices[0].message.content


# ─────────────────────────────────────────────────────────────────────────────
# Public extraction functions
# ─────────────────────────────────────────────────────────────────────────────

async def extract_from_image(
    image_data: bytes,
    media_type: str = "image/jpeg",
    user_note: Optional[str] = None,
) -> dict:
    """
    Step 1: Use GPT-4o vision to OCR and describe the image.
    Returns raw dict with keys: image_type, description, extracted_text,
                                 urls_found, is_job_posting, confidence_ocr
    """
    b64 = base64.b64encode(image_data).decode()
    log.info("extracting_image", size_kb=len(image_data) // 1024)
    raw = await _vision_chat(IMAGE_EXTRACTION_SYSTEM, b64, media_type)
    result = _parse_json_response(raw)
    if user_note:
        result["user_note"] = user_note
    log.info("image_extracted", image_type=result.get("image_type"), is_job=result.get("is_job_posting"))
    return result


async def extract_from_url(url: str, user_note: Optional[str] = None) -> dict:
    """
    Fetch URL content, then classify the page type.
    Returns raw fetched data + AI page_type assessment.
    """
    log.info("extracting_url", url=url)
    fetched = await fetch_url_content(url)
    context = build_url_context(fetched)

    system = URL_UNDERSTANDING_SYSTEM
    user_msg = f"URL: {url}\n\n{context[:3000]}"
    try:
        raw = await _chat(system, user_msg)
        meta = _parse_json_response(raw)
    except Exception as e:
        log.warning("url_ai_error", url=url, error=str(e))
        meta = {"page_type": "other", "is_job_or_internship": False}

    fetched["ai_meta"] = meta
    fetched["content_for_extraction"] = context
    if user_note:
        fetched["user_note"] = user_note
    return fetched


async def extract_from_text(text: str, user_note: Optional[str] = None) -> dict:
    """Minimal pre-processing — text passes through directly."""
    log.info("extracting_text", length=len(text))
    content = text
    if user_note:
        content = f"{content}\n\nUser note: {user_note}"
    return {"raw_text": text, "content_for_extraction": content, "user_note": user_note}


# ─────────────────────────────────────────────────────────────────────────────
# Deep extraction: run the right parser after classification
# ─────────────────────────────────────────────────────────────────────────────

async def deep_extract_job(content: str, source_type: str, user_note: str = "") -> dict:
    """Run the job/internship extraction prompt."""
    label_map = {"image": "OCR-extracted text from screenshot", "url": "Web page content", "text": "Text"}
    source_label = label_map.get(source_type, source_type)
    user_msg = JOBS_EXTRACTION_USER.format(
        source_type_label=source_label,
        content=content[:4000],
        user_note=user_note or "None",
    )
    raw = await _chat(JOBS_EXTRACTION_SYSTEM, user_msg)
    return _parse_json_response(raw)


async def deep_extract_other(content: str, source_type: str, user_note: str = "") -> dict:
    """Run the generic capture extraction prompt."""
    label_map = {"image": "OCR-extracted text from screenshot", "url": "Web page content", "text": "Text"}
    source_label = label_map.get(source_type, source_type)
    user_msg = OTHER_EXTRACTION_USER.format(
        source_type_label=source_label,
        content=content[:4000],
        user_note=user_note or "None",
    )
    raw = await _chat(OTHER_EXTRACTION_SYSTEM, user_msg)
    return _parse_json_response(raw)
