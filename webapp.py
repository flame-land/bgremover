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
ALLOWED_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi"}

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
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#080b12">
<meta name="description" content="Experimental anime video background remover. Export a green-screen MP4 for chroma key editing.">
<title>BGremover — Feral Labs</title>
<style>
:root{color-scheme:dark;--bg:#080b12;--panel:#101827;--line:#253249;--muted:#9aabc4;--blue:#60a5fa;--green:#86efac}
*{box-sizing:border-box}body{margin:0;min-height:100vh;background:radial-gradient(ellipse at top,#14213a 0%,var(--bg) 60%);color:#f3f6fb;font:16px/1.5 system-ui,-apple-system,sans-serif;padding:24px;display:grid;place-items:center}
main{width:min(100%,620px)}.brand{font-size:12px;letter-spacing:.18em;color:var(--blue);font-weight:800}.panel{margin-top:12px;background:#101827ed;border:1px solid var(--line);border-radius:22px;padding:clamp(20px,5vw,34px);box-shadow:0 24px 90px #0007}h1{font-size:clamp(28px,7vw,40px);line-height:1.1;margin:10px 0}p{color:var(--muted)}.drop{display:block;margin:22px 0;border:1px dashed #496080;border-radius:16px;padding:22px 14px;text-align:center;background:#0b1220;cursor:pointer}.drop strong{display:block;margin-bottom:7px}.drop input{display:block;width:100%;margin-top:14px;color:var(--muted)}button,.download{display:block;width:100%;border:0;border-radius:12px;padding:14px 16px;background:var(--blue);color:#07111f;font-weight:800;font-size:16px;text-align:center;text-decoration:none;cursor:pointer}button:disabled{opacity:.55;cursor:wait}.status{display:none;margin-top:18px;padding:14px;border-radius:12px;background:#0b1220;border:1px solid var(--line);overflow-wrap:anywhere}.status.show{display:block}.error{color:#fca5a5}.success{color:var(--green)}.bar{height:9px;background:#263349;border-radius:99px;overflow:hidden;margin:12px 0}.fill{height:100%;width:0;background:var(--blue);transition:width .25s}.small{font-size:12px}.fineprint{font-size:12px;color:#8292aa;margin-top:18px}.links{display:flex;gap:16px;flex-wrap:wrap;margin-top:12px}.links a{color:#93c5fd}footer{font-size:12px;color:#71829c;margin:16px 4px}
</style></head>
<body><main>
<div class="brand">FERAL LABS / VIDEO TOOLS</div>
<section class="panel">
<div class="brand">BGREMOVER · EXPERIMENTAL BETA</div>
<h1>Remove the anime background.</h1>
<p>Upload a short anime clip and get an MP4 with a pure green background for chroma key editing.</p>
<form id="form">
<label class="drop"><strong>Choose a video</strong><span id="filename">MP4, MOV, MKV, WebM or AVI · maximum 100 MB · maximum 10 seconds</span><input id="video" type="file" name="video" accept=".mp4,.mov,.mkv,.webm,.avi,video/*" required></label>
<button id="submit" type="submit">Upload &amp; remove background</button>
</form>
<div id="status" class="status" role="status" aria-live="polite"><strong id="stage">Preparing…</strong><div class="bar"><div id="fill" class="fill"></div></div><div id="percent">0%</div><p id="detail" class="small">Keep this page open. Processing time depends on the clip and the remote model queue.</p><div id="download"></div></div>
<p class="fineprint">Privacy: video frames are sent to a third-party hosted AI demo. Do not upload private, personal, or sensitive footage. Uploaded files are temporary and may be removed when the service restarts. This is an experimental project, not a guaranteed or always-available service.</p>
<div class="links"><a href="https://github.com/flame-land/bgremover">Source code</a><a href="https://huggingface.co/spaces/skytnt/anime-remove-background">AI service details</a></div>
</section><footer>Best for short anime clips. Fine edges, motion blur, and auras may not be preserved perfectly.</footer>
</main>
<script>
const form=document.querySelector('#form'), input=document.querySelector('#video'), btn=document.querySelector('#submit');
const statusBox=document.querySelector('#status'), stage=document.querySelector('#stage'), fill=document.querySelector('#fill'), percent=document.querySelector('#percent'), detail=document.querySelector('#detail'), download=document.querySelector('#download'), filename=document.querySelector('#filename');
input.addEventListener('change',()=>{filename.textContent=input.files[0]?.name || 'MP4, MOV, MKV, WebM or AVI · maximum 100 MB · maximum 10 seconds';});
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
form.addEventListener('submit',async e=>{
 e.preventDefault(); if(!input.files.length)return;
 btn.disabled=true;btn.textContent='Uploading…';statusBox.classList.add('show');download.innerHTML='';fill.style.width='2%';percent.textContent='Uploading';stage.textContent='Uploading video';detail.textContent='Your file is being sent securely to the hosting server over HTTPS.';
 try{
  const fd=new FormData();fd.append('video',input.files[0]);
  const res=await fetch('/api/jobs',{method:'POST',body:fd});const data=await res.json();
  if(!res.ok)throw new Error(data.error||'Upload failed.');
  stage.textContent='Queued';detail.textContent='Connecting to the remote AI model. Each video frame is processed separately, so this can take a while.';
  while(true){
   await sleep(1800);const sr=await fetch('/api/jobs/'+encodeURIComponent(data.job_id));const s=await sr.json();
   if(!sr.ok)throw new Error(s.error||'Could not read job status.');
   stage.textContent=s.stage||'Processing';percent.textContent=(s.percent??0)+'%';fill.style.width=(s.percent??0)+'%';detail.textContent=s.detail||'Processing…';
   if(s.state==='done'){fill.style.width='100%';percent.textContent='100%';stage.textContent='Ready to download';stage.classList.add('success');download.innerHTML='<a class="download" href="/api/jobs/'+encodeURIComponent(data.job_id)+'/download">Download green-screen MP4</a>';break;}
   if(s.state==='error')throw new Error(s.error||'Processing failed.');
  }
 }catch(err){stage.textContent='Could not finish';stage.className='error';detail.textContent=err.message;fill.style.width='0%';}
 finally{btn.disabled=false;btn.textContent='Upload another video';}
});
</script></body></html>
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
                   detail="Your green-screen video is ready.", output=output.name)
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
        return jsonify(error="Unsupported file type. Use MP4, MOV, MKV, WebM or AVI."), 400
    # Only accept one queued/running job per server to protect the small beta host.
    with jobs_lock:
        if any(j.get("state") in {"queued", "running"} for j in jobs.values()):
            return jsonify(error="The server is busy processing another clip. Please try again in a few minutes."), 429
        job_id = uuid.uuid4().hex
        source = UPLOAD_DIR / f"{job_id}{suffix}"
        output = OUTPUT_DIR / f"{job_id}_green.mp4"
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
        safe = {k: job[k] for k in ("state", "stage", "percent", "detail", "error") if k in job}
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
    return send_file(path, as_attachment=True, download_name="bgremover_green.mp4", mimetype="video/mp4")


@app.errorhandler(413)
def too_large(_error):
    return jsonify(error="File is too large. Maximum upload size is 100 MB."), 413


@app.get("/health")
def health():
    return jsonify(status="ok")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), debug=False)
