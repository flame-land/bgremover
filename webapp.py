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
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#080b12">
<meta name="description" content="Image and anime video background remover.">
<title>Background remover</title>
<style>:root{color-scheme:light;--bg:#f5f5f2;--surface:#fff;--text:#22231f;--muted:#777970;--line:#e3e3dd;--accent:#426b55;--accent-hover:#31513f}
*{box-sizing:border-box}body{margin:0;min-height:100vh;padding:24px 14px;background:var(--bg);color:var(--text);font:15px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}
main{width:min(100%,500px);margin:8vh auto 0}.panel{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:24px}
h1{font-size:22px;line-height:1.25;letter-spacing:-.025em;margin:0 0 5px;font-weight:700}.intro{margin:0 0 20px;color:var(--muted);font-size:14px}
.section-label{font-weight:650;font-size:14px;margin-bottom:8px}.drop{display:block;padding:18px;border:1px dashed #c8cbc3;border-radius:9px;text-align:center;background:#fafaf8;cursor:pointer}.drop:hover{border-color:var(--accent)}
.drop strong{display:block;font-size:14px;font-weight:650;margin-bottom:4px}.drop span{display:block;color:var(--muted);font-size:12px;overflow-wrap:anywhere}.drop input{display:block;width:100%;margin-top:14px;font:inherit;font-size:12px;color:var(--muted)}
button,.download{display:block;width:100%;border:0;border-radius:8px;padding:12px 14px;background:var(--accent);color:white;font:600 14px system-ui,sans-serif;text-align:center;text-decoration:none;cursor:pointer}.download{margin-top:14px}button:hover,.download:hover{background:var(--accent-hover)}button:disabled{opacity:.6;cursor:wait}
.limits{display:flex;gap:7px;flex-wrap:wrap;margin:10px 0 14px}.limit{font-size:11px;color:var(--muted);background:#f1f2ee;border-radius:5px;padding:4px 7px}
.status{display:none;margin-top:16px;padding:14px;border:1px solid var(--line);border-radius:9px;background:#fafaf8;overflow-wrap:anywhere}.status.show{display:block}.error{color:#b42318}.success{color:var(--accent)}
.bar{height:5px;background:#e5e6e0;border-radius:99px;overflow:hidden;margin:12px 0 7px}.fill{height:100%;width:0;background:var(--accent);transition:width .25s}#percent{font-size:12px;font-weight:650}.small{font-size:12px;color:var(--muted);margin-bottom:0}
.fineprint{font-size:11px;line-height:1.5;color:var(--muted);margin:14px 1px 0}.links{display:flex;gap:14px;margin-top:12px}.links a{font-size:12px;color:var(--muted);text-underline-offset:3px}
@media(max-width:480px){body{padding:14px 10px}main{margin:4vh auto 0}.panel{padding:18px}}</style></head>
<body><main>

<section class="panel">
  <h1>Background remover</h1>
  <p class="intro">Upload a photo or anime clip to remove its background.</p>
  <form id="form">
    <div class="section-label">Choose your video</div>
    <label class="drop">
      <strong>Choose a video</strong>
      <span id="filename">Photos or videos · up to 100 MB</span>
      <input id="video" type="file" name="video" accept=".png,.jpg,.jpeg,.webp,.bmp,.mp4,.mov,.mkv,.webm,.avi,image/*,video/*" required>
    </label>
    <div class="limits"><span class="limit">JPG, PNG, WebP, BMP</span><span class="limit">MP4, MOV, MKV, WebM, AVI</span><span class="limit">100 MB max · video 10 sec max</span></div>
    <button id="submit" type="submit">Remove background</button>
  </form>
  <div id="status" class="status" role="status" aria-live="polite"><strong id="stage">Getting things ready…</strong><div class="bar"><div id="fill" class="fill"></div></div><div id="percent">0%</div><p id="detail" class="small">Processing time depends on the clip and the remote model queue.</p><div id="download"></div></div>
  <p class="fineprint"><strong>Quick privacy note:</strong> processing uses a third-party hosted AI service, so video frames are sent to that service. Please don't upload private or sensitive footage. Files are temporary and may be cleared when the server restarts. Results can vary, especially around fast motion, glow, and motion blur.</p>
  <nav class="links" aria-label="Project links"><a href="https://github.com/flame-land/bgremover">View source code</a><a href="https://huggingface.co/spaces/skytnt/anime-remove-background">About the AI service</a></nav>
</section>

</main>
<script>
const form=document.querySelector('#form'), input=document.querySelector('#video'), btn=document.querySelector('#submit');
const statusBox=document.querySelector('#status'), stage=document.querySelector('#stage'), fill=document.querySelector('#fill'), percent=document.querySelector('#percent'), detail=document.querySelector('#detail'), download=document.querySelector('#download'), filename=document.querySelector('#filename');
input.addEventListener('change',()=>{filename.textContent=input.files[0]?.name || 'Select a file from your device';});
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
async function readJsonResponse(response, label){
 const raw=await response.text();
 if(!raw.trim()){
  throw new Error(label+' returned an empty response (HTTP '+response.status+'). The server may have restarted or the job may have crashed; check Render logs.');
 }
 try{return JSON.parse(raw);}
 catch(_){
  const preview=raw.slice(0,160).replace(/\\s+/g,' ');
  throw new Error(label+' returned invalid JSON (HTTP '+response.status+'): '+(preview||'empty response'));
 }
}
form.addEventListener('submit',async e=>{
 e.preventDefault(); if(!input.files.length)return;
 btn.disabled=true;btn.textContent='Uploading…';statusBox.classList.add('show');download.innerHTML='';fill.style.width='2%';percent.textContent='Uploading';stage.textContent='Uploading video';detail.textContent='Your file is being sent securely to the hosting server over HTTPS.';
 try{
  const fd=new FormData();fd.append('video',input.files[0]);
  const res=await fetch('/api/jobs',{method:'POST',body:fd});const data=await readJsonResponse(res,'Upload endpoint');
  if(!res.ok)throw new Error(data.error||'Upload failed.');
  stage.textContent='Queued';detail.textContent='Connecting to the remote AI model. Each video frame is processed separately, so this can take a while.';
  while(true){
   await sleep(1800);const sr=await fetch('/api/jobs/'+encodeURIComponent(data.job_id));const s=await readJsonResponse(sr,'Job status endpoint');
   if(!sr.ok)throw new Error(s.error||'Could not read job status.');
   stage.textContent=s.stage||'Processing';percent.textContent=(s.percent??0)+'%';fill.style.width=(s.percent??0)+'%';detail.textContent=s.detail||'Processing…';
   if(s.state==='done'){fill.style.width='100%';percent.textContent='100%';stage.textContent='Ready to download';stage.classList.add('success');download.innerHTML='<a class="download" href="/api/jobs/'+encodeURIComponent(data.job_id)+'/download">Download '+(s.is_image?'green-screen PNG':'green-screen MP4')+'</a>';break;}
   if(s.state==='error')throw new Error(s.error||'Processing failed.');
  }
 }catch(err){stage.textContent='Could not finish';stage.className='error';detail.textContent=err.message;fill.style.width='0%';}
 finally{btn.disabled=false;btn.textContent='Remove background';}
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
