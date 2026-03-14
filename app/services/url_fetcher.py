"""
Fetch and clean web page content from a URL.
Uses trafilatura for main content extraction with BeautifulSoup as fallback.
"""
import httpx
import trafilatura
from bs4 import BeautifulSoup
from typing import Optional
import structlog

from app.config import get_settings

log = structlog.get_logger(__name__)
settings = get_settings()


async def fetch_url_content(url: str) -> dict:
    """
    Returns:
      {
        "url": str,
        "title": str | None,
        "main_text": str,        # cleaned article text (≤ 4000 chars)
        "meta_description": str | None,
        "raw_html_snippet": str,  # first 1000 chars of raw HTML
        "fetch_error": str | None
      }
    """
    result = {
        "url": url,
        "title": None,
        "main_text": "",
        "meta_description": None,
        "raw_html_snippet": "",
        "fetch_error": None,
    }

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }

    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=settings.url_fetch_timeout,
        ) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            html = resp.text
    except httpx.HTTPStatusError as e:
        result["fetch_error"] = f"HTTP {e.response.status_code}: {url}"
        log.warning("url_fetch_http_error", url=url, status=e.response.status_code)
        return result
    except Exception as e:
        result["fetch_error"] = f"Fetch failed: {str(e)[:200]}"
        log.warning("url_fetch_error", url=url, error=str(e))
        return result

    result["raw_html_snippet"] = html[:1000]

    # ── BeautifulSoup: title + meta ──────────────────────────
    try:
        soup = BeautifulSoup(html, "lxml")
        if soup.title and soup.title.string:
            result["title"] = soup.title.string.strip()[:200]
        meta_desc = soup.find("meta", attrs={"name": "description"})
        if meta_desc and meta_desc.get("content"):
            result["meta_description"] = meta_desc["content"][:400]
    except Exception as e:
        log.debug("bs4_parse_error", error=str(e))

    # ── trafilatura: main body text ───────────────────────────
    try:
        main_text = trafilatura.extract(
            html,
            include_comments=False,
            include_tables=True,
            no_fallback=False,
        )
        if main_text:
            result["main_text"] = main_text[:4000]
    except Exception:
        pass

    # ── Fallback: strip tags with BS4 ────────────────────────
    if not result["main_text"]:
        try:
            soup = BeautifulSoup(html, "lxml")
            for tag in soup(["script", "style", "nav", "footer", "header"]):
                tag.decompose()
            text = soup.get_text(separator="\n", strip=True)
            result["main_text"] = text[:4000]
        except Exception:
            result["main_text"] = ""

    log.info("url_fetched", url=url, text_len=len(result["main_text"]))
    return result


def build_url_context(fetched: dict) -> str:
    """Format fetched URL data into a single string for the AI."""
    parts = []
    if fetched.get("title"):
        parts.append(f"Page Title: {fetched['title']}")
    if fetched.get("meta_description"):
        parts.append(f"Meta Description: {fetched['meta_description']}")
    parts.append(f"URL: {fetched['url']}")
    if fetched.get("main_text"):
        parts.append(f"\nPage Content:\n{fetched['main_text']}")
    return "\n".join(parts)
