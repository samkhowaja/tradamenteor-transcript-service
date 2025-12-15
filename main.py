from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from youtube_transcript_api import YouTubeTranscriptApi, TranscriptsDisabled, NoTranscriptFound
import re

app = FastAPI()

class Req(BaseModel):
    url: str
    lang: str = "en"

def extract_video_id(url: str) -> str:
    m = re.search(r"(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})", url)
    if not m:
        raise ValueError("Invalid YouTube URL (cannot find video id)")
    return m.group(1)

@app.get("/health")
def health():
    return {"ok": True}

@app.post("/transcript")
def transcript(req: Req):
    try:
        vid = extract_video_id(req.url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        try:
            items = YouTubeTranscriptApi.get_transcript(vid, languages=[req.lang])
        except Exception:
            items = YouTubeTranscriptApi.get_transcript(vid)

        text = "\n".join([x.get("text", "") for x in items]).strip()

        return {
            "videoId": vid,
            "lang": req.lang,
            "text": text,
            "segments": items,  # [{text, start, duration}]
        }

    except TranscriptsDisabled:
        raise HTTPException(status_code=404, detail="Transcripts disabled for this video.")
    except NoTranscriptFound:
        raise HTTPException(status_code=404, detail="No transcript found (no caption tracks).")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Transcript fetch failed: {e}")
