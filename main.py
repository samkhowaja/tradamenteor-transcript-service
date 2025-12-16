from fastapi import FastAPI, Query, Body
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import re
import requests

app = FastAPI(title="TradeMentor Transcript Service")

# Allow your Vercel app to call this (set to "*" for now, tighten later)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

class TranscriptReq(BaseModel):
    url: str
    lang: str = "en"

def extract_youtube_id(url: str) -> str | None:
    url = (url or "").strip()
    # youtu.be/<id>
    m = re.search(r"youtu\.be/([A-Za-z0-9_-]{6,})", url)
    if m:
        return m.group(1)
    # youtube.com/watch?v=<id>
    m = re.search(r"[?&]v=([A-Za-z0-9_-]{6,})", url)
    if m:
        return m.group(1)
    # youtube.com/shorts/<id>
    m = re.search(r"youtube\.com/shorts/([A-Za-z0-9_-]{6,})", url)
    if m:
        return m.group(1)
    # youtube.com/embed/<id>
    m = re.search(r"youtube\.com/embed/([A-Za-z0-9_-]{6,})", url)
    if m:
        return m.group(1)
    return None

def decode_xml_entities(s: str) -> str:
    return (
        s.replace("&amp;", "&")
         .replace("&quot;", '"')
         .replace("&#39;", "'")
         .replace("&apos;", "'")
         .replace("&lt;", "<")
         .replace("&gt;", ">")
    )

def parse_timedtext_xml(xml: str) -> dict:
    # Extract <text start="..." dur="..."> ... </text>
    segs = []
    for m in re.finditer(r'<text\b([^>]*)>([\s\S]*?)</text>', xml):
        attrs = m.group(1) or ""
        body = m.group(2) or ""

        start_m = re.search(r'start="([^"]+)"', attrs)
        dur_m = re.search(r'dur="([^"]+)"', attrs)

        start = float(start_m.group(1)) if start_m else 0.0
        dur = float(dur_m.group(1)) if dur_m else 0.0

        txt = decode_xml_entities(body)
        txt = re.sub(r"<[^>]*>", "", txt)
        txt = re.sub(r"\s+", " ", txt).strip()
        if not txt:
            continue

        segs.append({"start": start, "dur": dur, "text": txt})

    return {"segments": segs, "text": "\n".join([s["text"] for s in segs])}

def fetch_watch_page_captiontracks(video_id: str) -> list:
    watch_url = f"https://www.youtube.com/watch?v={video_id}&hl=en&gl=US"
    r = requests.get(
        watch_url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept-Language": "en-US,en;q=0.9",
        },
        timeout=20,
    )
    if r.status_code != 200:
        return []

    html = r.text

    # Look for captionTracks json array
    m = re.search(r'"captionTracks"\s*:\s*(\[[\s\S]*?\])\s*,\s*"audioTracks"', html)
    if not m:
        return []

    try:
        import json
        arr = json.loads(m.group(1))
        return arr if isinstance(arr, list) else []
    except:
        return []

def pick_track(tracks: list, lang: str) -> dict | None:
    want = (lang or "en").lower()

    # prefer manual captions in requested language
    for t in tracks:
        if (t.get("languageCode") or "").lower() == want and t.get("kind") != "asr":
            return t
    # then any in requested language
    for t in tracks:
        if (t.get("languageCode") or "").lower() == want:
            return t
    # then any manual
    for t in tracks:
        if t.get("kind") != "asr":
            return t
    # fallback first
    return tracks[0] if tracks else None

def fetch_timedtext_xml(base_url: str) -> str:
    # baseUrl is a timedtext url. add fmt=srv3 if missing
    if "fmt=" not in base_url:
        sep = "&" if "?" in base_url else "?"
        base_url = base_url + f"{sep}fmt=srv3"

    r = requests.get(
        base_url,
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=20,
    )
    if r.status_code != 200:
        raise RuntimeError(f"Timedtext fetch failed ({r.status_code})")
    return r.text

@app.get("/", response_class=HTMLResponse)
def home():
    # Playground UI: input box + output box
    return """
<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <title>Transcript Playground</title>
    <style>
      body { font-family: system-ui, sans-serif; padding: 20px; max-width: 980px; margin: 0 auto; }
      input, textarea, button { width: 100%; padding: 10px; margin-top: 8px; font-size: 14px; }
      textarea { height: 280px; }
      .row { display: grid; grid-template-columns: 1fr 120px; gap: 12px; }
      .ok { color: #0a0; }
      .bad { color: #a00; }
      .small { opacity: 0.7; font-size: 12px; }
    </style>
  </head>
  <body>
    <h2>Transcript Playground</h2>
    <div class="small">Paste a YouTube URL. This tests your Render service directly.</div>

    <div class="row">
      <input id="url" placeholder="https://www.youtube.com/watch?v=..." />
      <input id="lang" placeholder="en" value="en" />
    </div>

    <button onclick="run()">Fetch transcript</button>

    <div id="status" class="small"></div>
    <textarea id="out" placeholder="Transcript output..."></textarea>

    <script>
      async function run(){
        const url = document.getElementById('url').value.trim();
        const lang = document.getElementById('lang').value.trim() || 'en';
        const status = document.getElementById('status');
        const out = document.getElementById('out');
        status.textContent = 'Loading...';
        out.value = '';

        try {
          const res = await fetch('/transcript', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url, lang })
          });
          const data = await res.json();
          if(!res.ok || !data.ok) throw new Error(data.error || 'failed');

          status.innerHTML = '<span class="ok">OK</span> videoId=' + data.videoId + ' chars=' + (data.text||'').length;
          out.value = data.text || '';
        } catch(e){
          status.innerHTML = '<span class="bad">ERROR</span> ' + e.message;
        }
      }
    </script>
  </body>
</html>
"""

@app.get("/health")
def health():
    return {"ok": True}

# ✅ GET: test in browser address bar:
# /transcript?url=...&lang=en
@app.get("/transcript")
def transcript_get(url: str = Query(...), lang: str = Query("en")):
    return transcript_core(url, lang)

# ✅ POST: use from Next app:
@app.post("/transcript")
def transcript_post(body: TranscriptReq = Body(...)):
    return transcript_core(body.url, body.lang)

def transcript_core(url: str, lang: str):
    vid = extract_youtube_id(url)
    if not vid:
        return JSONResponse({"ok": False, "error": "Invalid YouTube URL"}, status_code=400)

    tracks = fetch_watch_page_captiontracks(vid)
    if not tracks:
        return JSONResponse(
            {"ok": False, "videoId": vid, "error": "No caption tracks exposed on watch page."},
            status_code=400,
        )

    chosen = pick_track(tracks, lang)
    if not chosen or not chosen.get("baseUrl"):
        return JSONResponse(
            {"ok": False, "videoId": vid, "error": "Caption tracks found but no usable baseUrl."},
            status_code=400,
        )

    try:
        xml = fetch_timedtext_xml(chosen["baseUrl"])
        parsed = parse_timedtext_xml(xml)
        if not parsed["segments"]:
            return JSONResponse(
                {"ok": False, "videoId": vid, "error": "Transcript returned empty."},
                status_code=400,
            )

        return {
            "ok": True,
            "videoId": vid,
            "lang": chosen.get("languageCode", ""),
            "kind": chosen.get("kind", ""),  # "asr" means auto-captions
            "text": parsed["text"],
            "segments": parsed["segments"],
            "tracks": [
                {"lang": t.get("languageCode", ""), "kind": t.get("kind", "")}
                for t in tracks
            ],
        }
    except Exception as e:
        return JSONResponse(
            {"ok": False, "videoId": vid, "error": str(e)},
            status_code=400,
        )
