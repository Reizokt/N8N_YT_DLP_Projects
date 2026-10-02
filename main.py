import os
import uuid
import threading
import subprocess

import yt_dlp
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

DOWNLOAD_FOLDER = "/app/downloads"
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

app = FastAPI()

JOBS: dict[str, dict] = {}
CUT_JOBS: dict[str, dict] = {}


class DownloadRequest(BaseModel):
    url: str
    audio_only: bool = False


class ClipItem(BaseModel):
    title: str
    start: str
    end: str


class CutRequest(BaseModel):
    download_job_id: str
    clips: list[ClipItem]


def download(job_id: str, url: str, audio_only: bool):
    JOBS[job_id]["status"] = "downloading"
    format_selector = "bestaudio/best" if audio_only else "bestvideo+bestaudio/best"
    ydl_opts = {
        "format": format_selector,
        "outtmpl": os.path.join(DOWNLOAD_FOLDER, "%(title)s.%(ext)s"),
        "merge_output_format": "mp4",
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filepath = ydl.prepare_filename(info)
        JOBS[job_id].update({
            "status": "done",
            "title": info.get("title"),
            "filepath": filepath,
        })
    except Exception as e:
        JOBS[job_id].update({"status": "error", "error": str(e)})


def run_cut(cut_job_id: str, video_path: str, clips: list[ClipItem]):
    CUT_JOBS[cut_job_id]["status"] = "cutting"
    results = []
    try:
        for clip in clips:
            output_path = os.path.join(DOWNLOAD_FOLDER, f"{clip.title}.mp4")
            result = subprocess.run([
                "ffmpeg",
                "-ss", clip.start,
                "-to", clip.end,
                "-i", video_path,
                "-c:v", "libx264",
                "-c:a", "aac",
                "-crf", "23",
                "-preset", "fast",
                "-y",
                output_path
            ], capture_output=True, text=True)

            if result.returncode != 0:
                raise Exception(f"ffmpeg error on {clip.title}: {result.stderr}")

            results.append({
                "title": clip.title,
                "clip_path": output_path,
                "start": clip.start,
                "end": clip.end
            })

        CUT_JOBS[cut_job_id].update({"status": "done", "clips": results})
    except Exception as e:
        CUT_JOBS[cut_job_id].update({"status": "error", "error": str(e)})


@app.post("/download")
def start_download(req: DownloadRequest):
    if not req.url.strip():
        raise HTTPException(status_code=400, detail="url is required")
    job_id = uuid.uuid4().hex[:8]
    JOBS[job_id] = {"status": "queued"}
    threading.Thread(target=download, args=(job_id, req.url, req.audio_only), daemon=True).start()
    return {"job_id": job_id, "status": "queued"}


@app.get("/status/{job_id}")
def get_status(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    return job


@app.post("/cut")
def cut_video(req: CutRequest):
    download_job = JOBS.get(req.download_job_id)
    if not download_job:
        raise HTTPException(status_code=404, detail="Download job not found")
    if download_job["status"] != "done":
        raise HTTPException(status_code=400, detail="Download not finished yet")
    video_path = download_job["filepath"]
    cut_job_id = uuid.uuid4().hex[:8]
    CUT_JOBS[cut_job_id] = {
        "status": "queued",
        "download_job_id": req.download_job_id,
        "total_clips": len(req.clips)
    }
    threading.Thread(
        target=run_cut,
        args=(cut_job_id, video_path, req.clips),
        daemon=True
    ).start()
    return {"cut_job_id": cut_job_id, "status": "queued"}


@app.get("/cut/status/{cut_job_id}")
def cut_status(cut_job_id: str):
    job = CUT_JOBS.get(cut_job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Cut job not found")
    return job