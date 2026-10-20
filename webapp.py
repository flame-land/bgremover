#!/usr/bin/env python3
"""Web UI for BGremover. Intended for a small beta deployment, not high traffic."""

import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from flask import Flask, jsonify, render_template_string, request, send_file, abort
from werkzeug.utils import secure_filename

BASE = Path(__file__).resolve().parent
UPLOAD_DIR = BASE / "uploads"
OUTPUT_DIR = BASE / "output"
WORK_DIR = BASE / "frames"
MAX_UPLOAD_BYTES = 100 * 1024 * 1024
MAX_DURATION_SECONDS = 10
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
ALLOWED_EXTENSIONS = VIDEO_EXTENSIONS | IMAGE_EXTENSIONS

for directory in (UPLOAD_DIR, OUTPUT_DIR, WORK_DIR):
    directory.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES
app.config["JSON_SORT_KEYS"] = False

# In-memory job tracking is suitable only for a single-instance beta. Jobs are
# lost on restarts and files on free hosts may be ephemeral.
jobs = {}
jobs_lock = threading.Lock()
worker_lock = threading.Lock()

PAGE = r"""
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#111111">
<title>BGremover</title>
<style>
*{box-sizing:border-box}body{margin:0;min-height:100vh;display:grid;place-items:center;background:#111;color:#f5f5f5;font:16px system-ui,-apple-system,sans-serif;padding:20px}
main{width:min(100%,360px);text-align:center}p{margin:0 0 24px;color:#c8c8c8;line-height:1.5}
button{width:100%;padding:15px;border:0;border-radius:10px;background:#fff;color:#111;font:600 16px system-ui;cursor:pointer}
button:disabled{opacity:.55;cursor:wait}input{display:none}
#status{margin-top:18px;color:#c8c8c8;font-size:14px;overflow-wrap:anywhere}#status:empty{display:none}
a{display:block;margin-top:18px;color:#fff;font-weight:600}
</style>
</head>
<body>
<main>
<p>Remove the background from a photo or anime clip.</p>
<input id="file" type="file" accept=".png,.jpg,.jpeg,.webp,.bmp,.mp4,.mov,.mkv,.webm,.avi,image/*,video/*">
<button id="choose" type="button">Choose photo or video</button>
<div id="status" role="status" aria-live="polite"></div>
<div id="result"></div>
</main>
<script>
const input=document.querySelector('#file'),button=document.querySelector('#choose'),status=document.querySelector('#status'),result=document.querySelector('#result');
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function json(response,label){
 const raw=await response.text();
 if(!raw.trim())throw new Error(label+' failed (HTTP '+response.status+'). Try again later.');
 try{return JSON.parse(raw)}catch(_){throw new Error(label+' returned an invalid response. Try again later.')}
}
button.addEventListener('click',()=>input.click());
input.addEventListener('change',async()=>{
 if(!input.files.length)return;
 button.disabled=true;button.textContent='Processing…';status.textContent='Uploading and removing background…';result.innerHTML='';
 try{
  const form=new FormData();form.append('video',input.files[0]);
  const response=await fetch('/api/jobs',{method:'POST',body:form});
  const job=await json(response,'Upload');
  if(!response.ok)throw new Error(job.error||'Upload failed.');
  while(true){
   await sleep(1800);
   const sr=await fetch('/api/jobs/'+encodeURIComponent(job.job_id));
   const state=await json(sr,'Status');
   if(!sr.ok)throw new Error(state.error||'Could not check processing status.');
   status.textContent=state.stage||'Processing…';
   if(state.state==='done'){
    status.textContent='Done.';
    const link=document.createElement('a');link.href='/api/jobs/'+encodeURIComponent(job.job_id)+'/download';link.textContent='Download result';result.appendChild(link);break;
   }
   if(state.state==='error')throw new Error(state.error||'Processing failed.');
  }
 }catch(error){status.textContent=error.message||'Something went wrong. Please try again.'}
 finally{button.disabled=false;button.textContent='Choose photo or video';input.value=''}
});
</script>
</body>
</html>
"""


def update_job(job_id, **values):
    with jobs_lock:
        if job_id in jobs:
            jobs[job_id].update(values)
            jobs[job_id]["updated_at"] = time.time()


def probe_duration(path):
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, timeout=20, check=True,
    )
    return float(result.stdout.strip())


def process_job(job_id, source, output):
    if not worker_lock.acquire(blocking=False):
        update_job(job_id, state="error", stage="Busy", error="Another video is being processed. Please try again later.")
        source.unlink(missing_ok=True)
        return
    try:
        is_image = source.suffix.lower() in IMAGE_EXTENSIONS
        if is_image:
            update_job(job_id, state="running", stage="Removing background", percent=10,
                       detail="Sending photo to the background-removal model.")
        else:
            update_job(job_id, state="running", stage="Checking video", percent=2,
                       detail="Checking duration and preparing the AI request.")
            duration = probe_duration(source)
            if duration > MAX_DURATION_SECONDS:
                raise ValueError(f"Clip is {duration:.1f} seconds long. The current beta limit is {MAX_DURATION_SECONDS} seconds.")
            update_job(job_id, stage="Extracting frames", percent=5,
                       detail="FFmpeg is splitting the clip into frames.")
        process = subprocess.Popen(
            [sys.executable, "-u", str(BASE / "remover.py"), str(source), str(output)],
            cwd=BASE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1,
        )
        total_frames = None
        last_line = ""
        assert process.stdout is not None
        for line in process.stdout:
            line = line.strip()
            if not line:
                continue
            last_line = line
            match = re.search(r"Found (\d+) frames", line)
            if match:
                total_frames = max(1, int(match.group(1)))
                update_job(job_id, stage="Sending frames to AI", percent=8,
                           detail=f"Found {total_frames} frames. The remote model processes them one by one.")
                continue
            match = re.search(r"Processing frame (\d+)/(\d+)", line)
            if match:
                current, total = int(match.group(1)), max(1, int(match.group(2)))
                pct = min(94, 8 + int(84 * current / total))
                update_job(job_id, stage="Removing background", percent=pct,
                           detail=f"Processed frame {current} of {total}. Remote queue speed varies.")
            elif "Encoding output video" in line:
                update_job(job_id, stage="Encoding MP4", percent=96,
                           detail="Rebuilding the video and preserving source audio when possible.")
            elif line.startswith("Error:"):
                update_job(job_id, detail=line)
        code = process.wait()
        if code != 0:
            raise RuntimeError(last_line or f"Processor exited with code {code}. The remote model may be unavailable.")
        if not output.is_file():
            raise RuntimeError("Processing ended but no output file was created.")
        update_job(job_id, state="done", stage="Complete", percent=100,
                   detail="Your processed file is ready.", output=output.name, is_image=is_image)
    except Exception as exc:
        update_job(job_id, state="error", stage="Processing failed", error=str(exc),
                   detail="Try a shorter MP4 clip. If the remote model is down, try again later.")
        output.unlink(missing_ok=True)
    finally:
        source.unlink(missing_ok=True)
        worker_lock.release()


@app.get("/")
def index():
    return render_template_string(PAGE)


@app.post("/api/jobs")
def create_job():
    video = request.files.get("video")
    if not video or not video.filename:
        return jsonify(error="Choose a video file first."), 400
    name = secure_filename(video.filename)
    suffix = Path(name).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        return jsonify(error="Unsupported file type. Use JPG, PNG, WebP, BMP or a supported video format."), 400
    # Only accept one queued/running job per server to protect the small beta host.
    with jobs_lock:
        if any(j.get("state") in {"queued", "running"} for j in jobs.values()):
            return jsonify(error="The server is busy processing another clip. Please try again in a few minutes."), 429
        job_id = uuid.uuid4().hex
        source = UPLOAD_DIR / f"{job_id}{suffix}"
        output = OUTPUT_DIR / (f"{job_id}_green.png" if suffix in IMAGE_EXTENSIONS else f"{job_id}_green.mp4")
        jobs[job_id] = {"state": "queued", "stage": "Queued", "percent": 1,
                        "detail": "Waiting for the processor.", "created_at": time.time()}
    try:
        video.save(source)
        if source.stat().st_size > MAX_UPLOAD_BYTES:
            source.unlink(missing_ok=True)
            with jobs_lock:
                jobs.pop(job_id, None)
            return jsonify(error="File is too large. Maximum upload size is 100 MB."), 413
    except Exception:
        source.unlink(missing_ok=True)
        with jobs_lock:
            jobs.pop(job_id, None)
        return jsonify(error="Could not save the upload. Please try again."), 500
    threading.Thread(target=process_job, args=(job_id, source, output), daemon=True).start()
    return jsonify(job_id=job_id), 202


@app.get("/api/jobs/<job_id>")
def job_status(job_id):
    with jobs_lock:
        job = jobs.get(job_id)
        if not job:
            return jsonify(error="Job not found. It may have expired after a restart."), 404
        safe = {k: job[k] for k in ("state", "stage", "percent", "detail", "error", "is_image") if k in job}
    return jsonify(safe)


@app.get("/api/jobs/<job_id>/download")
def job_download(job_id):
    with jobs_lock:
        job = jobs.get(job_id)
        if not job or job.get("state") != "done":
            abort(404)
        filename = job.get("output", "")
    path = OUTPUT_DIR / filename
    if not filename or not path.is_file() or path.parent.resolve() != OUTPUT_DIR.resolve():
        abort(404)
    return send_file(path, as_attachment=True, download_name=("bgremover_green.png" if path.suffix.lower() == ".png" else "bgremover_green.mp4"), mimetype=("image/png" if path.suffix.lower() == ".png" else "video/mp4"))


@app.errorhandler(413)
def too_large(_error):
    return jsonify(error="File is too large. Maximum upload size is 100 MB."), 413


@app.get("/health")
def health():
    return jsonify(status="ok")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), debug=False)
