"""
Web adapter: optional lightweight REST API for programmatic access
and a mobile-friendly web form.
Enabled via WEB_ENABLED=true in .env.
"""
import base64
import os
import structlog
from typing import Optional

from fastapi import APIRouter, UploadFile, File, Form, Header, HTTPException, Depends
from fastapi.responses import HTMLResponse

from app.config import get_settings
from app.models.capture import CaptureRequest, SourceType, CaptureResult
from app.services.pipeline import process_capture
from app import database as db

log = structlog.get_logger(__name__)
settings = get_settings()

router = APIRouter(prefix="/api", tags=["web"])


def _check_api_key(x_api_key: Optional[str] = Header(None)) -> None:
    if settings.web_api_key:
        if x_api_key != settings.web_api_key:
            raise HTTPException(status_code=401, detail="Invalid API key")


# ─── REST endpoints ───────────────────────────────────────────────────────────

@router.post("/capture/url", response_model=CaptureResult)
async def capture_url(
    url: str = Form(...),
    note: Optional[str] = Form(None),
    _: None = Depends(_check_api_key),
):
    request = CaptureRequest(
        channel="web",
        source_type=SourceType.url,
        raw_input=url,
        user_note=note,
    )
    return await process_capture(request)


@router.post("/capture/text", response_model=CaptureResult)
async def capture_text(
    text: str = Form(...),
    note: Optional[str] = Form(None),
    _: None = Depends(_check_api_key),
):
    request = CaptureRequest(
        channel="web",
        source_type=SourceType.text,
        raw_input=text,
        user_note=note,
    )
    return await process_capture(request)


@router.post("/capture/image", response_model=CaptureResult)
async def capture_image(
    file: UploadFile = File(...),
    note: Optional[str] = Form(None),
    _: None = Depends(_check_api_key),
):
    if file.content_type and not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Only image files accepted")

    image_bytes = await file.read()
    max_bytes = settings.max_image_size_mb * 1024 * 1024
    if len(image_bytes) > max_bytes:
        raise HTTPException(status_code=413, detail=f"Image exceeds {settings.max_image_size_mb}MB limit")

    os.makedirs(settings.upload_dir, exist_ok=True)
    ext = (file.filename or "upload").rsplit(".", 1)[-1].lower()
    file_path = os.path.join(settings.upload_dir, f"web_{file.filename}")
    with open(file_path, "wb") as f:
        f.write(image_bytes)

    request = CaptureRequest(
        channel="web",
        source_type=SourceType.image,
        raw_input=file_path,
        user_note=note,
    )
    return await process_capture(request)


@router.get("/recent", response_model=list[dict])
async def get_recent(limit: int = 5, _: None = Depends(_check_api_key)):
    return await db.get_recent_captures(limit=min(limit, 20))


@router.get("/health")
async def health():
    return {"status": "ok"}


# ─── Mobile-friendly web form ─────────────────────────────────────────────────

WEB_FORM_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Capture Assistant</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         background: #f5f5f5; padding: 20px; }
  .container { max-width: 600px; margin: 0 auto; }
  h1 { color: #1a1a1a; margin-bottom: 24px; font-size: 1.5rem; }
  .card { background: white; border-radius: 12px; padding: 24px;
          box-shadow: 0 2px 8px rgba(0,0,0,0.08); margin-bottom: 16px; }
  label { display: block; font-weight: 600; margin-bottom: 8px; color: #333; }
  input, textarea { width: 100%; padding: 12px; border: 1px solid #ddd;
                     border-radius: 8px; font-size: 16px; }
  textarea { height: 100px; resize: vertical; }
  button { background: #2563eb; color: white; border: none; padding: 14px 24px;
           border-radius: 8px; font-size: 16px; font-weight: 600; cursor: pointer;
           width: 100%; margin-top: 12px; }
  button:hover { background: #1d4ed8; }
  .tabs { display: flex; gap: 8px; margin-bottom: 20px; }
  .tab { flex: 1; padding: 10px; text-align: center; border-radius: 8px;
         cursor: pointer; border: 2px solid #ddd; background: white; font-weight: 600; }
  .tab.active { border-color: #2563eb; background: #eff6ff; color: #2563eb; }
  .section { display: none; }
  .section.active { display: block; }
  #result { margin-top: 16px; padding: 16px; background: #f0fdf4;
            border-radius: 8px; white-space: pre-wrap; font-size: 14px;
            border: 1px solid #86efac; display: none; }
</style>
</head>
<body>
<div class="container">
  <h1>📋 Capture Assistant</h1>

  <div class="tabs">
    <div class="tab active" onclick="switchTab('url')">🔗 URL</div>
    <div class="tab" onclick="switchTab('text')">📝 Text</div>
    <div class="tab" onclick="switchTab('image')">📸 Image</div>
  </div>

  <div id="url-section" class="section card active">
    <label>URL to capture</label>
    <input type="url" id="url-input" placeholder="https://..." />
    <label style="margin-top:12px">Note (optional)</label>
    <input type="text" id="url-note" placeholder="e.g. interesting role" />
    <button onclick="submitUrl()">Capture URL</button>
  </div>

  <div id="text-section" class="section card">
    <label>Text or note</label>
    <textarea id="text-input" placeholder="Paste job description, article text, or quick note..."></textarea>
    <label style="margin-top:12px">Note (optional)</label>
    <input type="text" id="text-note" placeholder="e.g. from LinkedIn" />
    <button onclick="submitText()">Capture Text</button>
  </div>

  <div id="image-section" class="section card">
    <label>Screenshot or image</label>
    <input type="file" id="image-input" accept="image/*" />
    <label style="margin-top:12px">Note (optional)</label>
    <input type="text" id="image-note" placeholder="e.g. job at Apple" />
    <button onclick="submitImage()">Capture Image</button>
  </div>

  <div id="result"></div>
</div>

<script>
function switchTab(name) {
  document.querySelectorAll('.tab').forEach((t,i) => {
    t.classList.toggle('active', ['url','text','image'][i] === name);
  });
  document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
  document.getElementById(name + '-section').classList.add('active');
}

async function post(path, body, isForm) {
  const res = await fetch('/api' + path, { method:'POST', body });
  const data = await res.json();
  const el = document.getElementById('result');
  el.style.display = 'block';
  if (data.status === 'saved') {
    el.style.background = '#f0fdf4';
    el.textContent = '✅ Saved to ' + data.sheet_tab + '\\nType: ' + data.capture_type
      + (data.title ? '\\nTitle: ' + data.title : '')
      + (data.company ? '\\nCompany: ' + data.company : '')
      + (data.summary ? '\\nSummary: ' + data.summary : '')
      + '\\nReview needed: ' + (data.review_needed ? 'Yes' : 'No');
  } else if (data.status === 'duplicate') {
    el.style.background = '#fffbeb';
    el.textContent = '⚠️ Duplicate: ' + data.duplicate_of;
  } else {
    el.style.background = '#fef2f2';
    el.textContent = '❌ Error: ' + (data.error_message || JSON.stringify(data));
  }
}

function submitUrl() {
  const fd = new FormData();
  fd.append('url', document.getElementById('url-input').value);
  fd.append('note', document.getElementById('url-note').value);
  post('/capture/url', fd);
}
function submitText() {
  const fd = new FormData();
  fd.append('text', document.getElementById('text-input').value);
  fd.append('note', document.getElementById('text-note').value);
  post('/capture/text', fd);
}
async function submitImage() {
  const file = document.getElementById('image-input').files[0];
  if (!file) { alert('Select an image'); return; }
  const fd = new FormData();
  fd.append('file', file);
  fd.append('note', document.getElementById('image-note').value);
  post('/capture/image', fd);
}
</script>
</body>
</html>"""


@router.get("/", response_class=HTMLResponse)
async def web_form():
    return HTMLResponse(WEB_FORM_HTML)
