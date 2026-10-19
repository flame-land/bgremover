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
:root{color-scheme:light;--paper:#f7f5f0;--card:#fff;--ink:#252923;--muted:#6d7169;--line:#e4e1d8;--accent:#e56b42;--accent-dark:#c9512b;--soft:#fff1e9;--green:#26734d}
*{box-sizing:border-box}
body{margin:0;min-height:100vh;background:var(--paper);color:var(--ink);font:16px/1.55 Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;padding:28px 18px 20px}
main{width:min(100%,720px);margin:0 auto}
.topbar{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:5px 2px 28px}
.brandmark{display:flex;align-items:center;gap:10px;font-size:17px;font-weight:800;letter-spacing:-.04em}
.mark{display:grid;place-items:center;width:34px;height:34px;background:var(--accent);color:white;border-radius:10px;font-size:18px;font-weight:900}
.topnote{font-size:12px;color:var(--muted);border:1px solid var(--line);border-radius:99px;padding:6px 10px;background:#fff}
.panel{background:var(--card);border:1px solid var(--line);border-radius:20px;padding:clamp(22px,5vw,42px);box-shadow:0 8px 28px #28261b0a}
.eyebrow{font-size:12px;text-transform:uppercase;letter-spacing:.12em;font-weight:800;color:var(--accent-dark);margin:0 0 12px}
h1{font-size:clamp(30px,6vw,46px);letter-spacing:-.055em;line-height:1.08;margin:0;max-width:540px}
.intro{font-size:16px;color:var(--muted);max-width:560px;margin:16px 0 26px}
.section-label{font-size:14px;font-weight:750;margin-bottom:9px}
.drop{display:block;border:1.5px dashed #d4cfc4;border-radius:14px;padding:24px 18px;text-align:center;background:#fbfaf7;cursor:pointer;transition:border-color .15s,background .15s}
.drop:hover{border-color:var(--accent);background:#fffaf6}
.file-icon{display:grid;place-items:center;width:42px;height:42px;border-radius:12px;background:var(--soft);color:var(--accent-dark);font-size:21px;margin:0 auto 12px}
.drop strong{display:block;font-size:15px;margin-bottom:4px}
.drop span{display:block;font-size:13px;color:var(--muted);overflow-wrap:anywhere}
.drop input{display:block;width:min(100%,300px);margin:16px auto 0;font:inherit;font-size:13px;color:var(--muted)}
button,.download{display:block;width:100%;border:0;border-radius:11px;padding:14px 16px;margin-top:14px;background:var(--accent);color:#fff;font-weight:750;font-size:15px;text-align:center;text-decoration:none;cursor:pointer;transition:background .15s}
button:hover,.download:hover{background:var(--accent-dark)}
button:disabled{opacity:.6;cursor:wait}
.limits{display:flex;flex-wrap:wrap;gap:8px;margin-top:13px}
.limit{font-size:12px;color:var(--muted);background:#f4f2ec;border-radius:7px;padding:5px 8px}
.status{display:none;margin-top:20px;padding:17px;border-radius:12px;background:#faf9f6;border:1px solid var(--line);overflow-wrap:anywhere}
.status.show{display:block}
.error{color:#b9382b}.success{color:var(--green)}
.bar{height:7px;background:#e9e6df;border-radius:99px;overflow:hidden;margin:13px 0 8px}
.fill{height:100%;width:0;background:var(--accent);transition:width .25s}
#percent{font-size:13px;font-weight:750}
.small{font-size:13px}
.fineprint{font-size:12px;line-height:1.6;color:var(--muted);margin:20px 0 0}
.links{display:flex;gap:18px;flex-wrap:wrap;margin-top:19px;padding-top:17px;border-top:1px solid var(--line)}
.links a{font-size:13px;color:#655d51;text-underline-offset:3px}
.links a:hover{color:var(--accent-dark)}
footer{font-size:12px;color:var(--muted);line-height:1.6;margin:17px 3px}
@media(max-width:480px){body{padding:17px 12px}.topbar{padding-bottom:18px}.panel{border-radius:16px}.topnote{font-size:11px}.drop{padding:20px 12px}}
</style></head>
<body><main>
<header class="topbar">
  <div class="brandmark"><span class="mark">B</span><span>BGremover</span></div>
  <div class="topnote">Experimental beta</div>
</header>
<section class="panel">
  <p class="eyebrow">A simple tool for anime editors</p>
  <h1>Get your clip ready for a clean key.</h1>
  <p class="intro">Remove the background from a short anime video and export it on a solid green screen. Drop the result into your editor and key out the green.</p>
  <form id="form">
    <div class="section-label">Choose your video</div>
    <label class="drop">
      <div class="file-icon" aria-hidden="true">↑</div>
      <strong>Pick a video from your device</strong>
      <span id="filename">Your original file stays yours. We only need it to process the clip.</span>
      <input id="video" type="file" name="video" accept=".mp4,.mov,.mkv,.webm,.avi,video/*" required>
    </label>
    <div class="limits"><span class="limit">MP4, MOV, MKV, WebM, AVI</span><span class="limit">Up to 100 MB</span><span class="limit">Up to 10 seconds</span></div>
    <button id="submit" type="submit">Remove background</button>
  </form>
  <div id="status" class="status" role="status" aria-live="polite"><strong id="stage">Getting things ready…</strong><div class="bar"><div id="fill" class="fill"></div></div><div id="percent">0%</div><p id="detail" class="small">Processing time depends on the clip and the remote model queue.</p><div id="download"></div></div>
  <p class="fineprint"><strong>Quick privacy note:</strong> processing uses a third-party hosted AI service, so video frames are sent to that service. Please don't upload private or sensitive footage. Files are temporary and may be cleared when the server restarts. Results can vary, especially around fast motion, glow, and motion blur.</p>
  <nav class="links" aria-label="Project links"><a href="https://github.com/flame-land/bgremover">View source code</a><a href="https://huggingface.co/spaces/skytnt/anime-remove-background">About the AI service</a></nav>
</section>
<footer>Made for quick editing workflows. This is an experimental community project, so processing may be slow or temporarily unavailable.</footer>
</main>
<script>
const form=document.querySelector('#form'), input=document.querySelector('#video'), btn=document.querySelector('#submit');
const statusBox=document.querySelector('#status'), stage=document.querySelector('#stage'), fill=document.querySelector('#fill'), percent=document.querySelector('#percent'), detail=document.querySelector('#detail'), download=document.querySelector('#download'), filename=document.querySelector('#filename');
input.addEventListener('change',()=>{filename.textContent=input.files[0]?.name || 'Your original file stays yours. We only need it to process the clip.';});
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
