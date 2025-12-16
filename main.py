from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from typing import Optional, Dict, Any, List
import asyncio
import json
import re
import httpx
import xml.etree.ElementTree as ET

from youtube_transcript_api import (
    YouTubeTranscriptApi,
    TranscriptsDisabled,
    NoTranscriptFound,
    VideoUnavailable,
    TooManyRequests,
)

app = FastAPI(title="TradeMentor Transcript Service")


# -------------------------
# Helpers
# -------------------------

def extract_youtube_id(url: str) -> Optional[str]:
    url = (url or "").strip()
    if not url:
        return None

    if "youtu.be/" in url:
        return url.split("youtu.be/")[1].split("?")[0].split("&")[0]

    if "watch?v=" in url:
        return url.split("watch?v=")[1].split("&")[0]

    if "/shorts/" in url:
        return url.split("/shorts/")[1].split("?")[0].split("&")[0]

    if re.fullmatch(r"[A-Za-z0-9_-]{11}", url):
        return url

    return None


async def read_request_fields(request: Request) -> Dict[str, str]:
    content_type = (request.headers.get("content-type") or "").lower()
    data: Dict[str, Any] = {}

    try:
        if "application/json" in content_type:
            data = await request.json()
        else:
            form = await request.form()
            data = dict(form)
    except Exception:
        pass

    return {
        "url": (data.get("url") or "").strip(),
        "lang": (data.get("lang") or "en").strip(),
        "mode": (data.get("mode") or "auto").strip(),
    }


# -------------------------
# Caption method 1
# -------------------------

async def captions_method_1(video_id: str, lang: str) -> Dict[str, Any]:
    transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)

    preferred = [lang]
    if lang == "en":
        preferred += ["en-US", "en-GB"]

    chosen = None

    for code in preferred:
        try:
            chosen = transcript_list.find_manually_created_transcript([code])
            break
        except Exception:
            pass

    if not chosen:
        for code in preferred:
            try:
                chosen = transcript_list.find_generated_transcript([code])
                break
            except Exception:
                pass

    if not chosen:
        chosen = next(iter(transcript_list))

    segments = chosen.fetch()
    text = "\n".join([s["text"] for s in segments])

    return {
        "ok": True,
        "provider": "youtube-transcript-api",
        "language": getattr(chosen, "language_code", None),
        "segments": segments,
        "transcriptText": text,
    }


# -------------------------
# Caption method 2 (XML / VTT)
# -------------------------

async def captions_method_2(video_id: str, lang: str) -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        base = "https://www.youtube.com/api/timedtext"

        # Try VTT
        r = await client.get(base, params={"v": video_id, "lang": lang, "fmt": "vtt"})
        if r.status_code == 200 and "WEBVTT" in r.text:
            lines = []
            for line in r.text.splitlines():
                line = line.strip()
                if not line or "-->" in line or line.isdigit() or line.startswith("WEBVTT"):
                    continue
                lines.append(line)
            if lines:
                return {
                    "ok": True,
                    "provider": "timedtext-vtt",
                    "language": lang,
                    "segments": [],
                    "transcriptText": "\n".join(lines),
                }

        # Try XML
        r = await client.get(base, params={"v": video_id, "lang": lang})
        if r.status_code == 200 and "<transcript" in r.text:
            root = ET.fromstring(r.text)
            segments: List[Dict[str, Any]] = []
            parts: List[str] = []

            for node in root.findall("text"):
                t = (node.text or "").replace("\n", " ").strip()
                if not t:
                    continue
                parts.append(t)
                segments.append({
                    "text": t,
                    "start": float(node.attrib.get("start", 0)),
                    "duration": float(node.attrib.get("dur", 0)),
                })

            if parts:
                return {
                    "ok": True,
                    "provider": "timedtext-xml",
                    "language": lang,
                    "segments": segments,
                    "transcriptText": "\n".join(parts),
                }

    return {"ok": False}


# -------------------------
# Health
# -------------------------

@app.get("/")
def health():
    return {"ok": True, "service": "tradementor-transcript-service"}


# -------------------------
# Streaming transcript endpoint
# -------------------------

@app.post("/transcript/stream")
async def transcript_stream(request: Request):
    fields = await read_request_fields(request)
    url = fields["url"]
    lang = fields["lang"]

    if not url:
        async def err():
            yield f"data: {json.dumps({'type':'error','message':'Missing URL'})}\n\n"
        return StreamingResponse(err(), media_type="text/event-stream")

    video_id = extract_youtube_id(url)
    if not video_id:
        async def err():
            yield f"data: {json.dumps({'type':'error','message':'Invalid YouTube URL'})}\n\n"
        return StreamingResponse(err(), media_type="text/event-stream")

    async def event_stream():
        # Method 1
        yield f"data: {json.dumps({'type':'progress','step':'captions_1','message':'Trying captions (method 1)'})}\n\n"
        await asyncio.sleep(0.2)
        try:
            res1 = await captions_method_1(video_id, lang)
            if res1.get("ok"):
                yield f"data: {json.dumps({'type':'done','mode':'captions','provider':res1['provider'],'data':res1})}\n\n"
                return
        except (TooManyRequests, TranscriptsDisabled, NoTranscriptFound, VideoUnavailable):
            pass
        except Exception:
            pass

        # Method 2
        yield f"data: {json.dumps({'type':'progress','step':'captions_2','message':'Trying captions (XML fallback)'})}\n\n"
        await asyncio.sleep(0.2)
        res2 = await captions_method_2(video_id, lang)
        if res2.get("ok"):
            yield f"data: {json.dumps({'type':'done','mode':'captions','provider':res2['provider'],'data':res2})}\n\n"
            return

        # Whisper placeholder
        yield f"data: {json.dumps({'type':'progress','step':'whisper','message':'Captions unavailable. Whisper fallback next'})}\n\n"
        await asyncio.sleep(0.2)
        yield f"data: {json.dumps({'type':'error','code':'WHISPER_NOT_IMPLEMENTED','message':'Whisper not implemented yet'})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
