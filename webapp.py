#!/usr/bin/env python3
"""Web UI for BGremover. Intended for a small beta deployment, not high traffic."""

import json
import ipaddress
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

from flask import Flask, jsonify, render_template_string, request, send_file, abort
from werkzeug.utils import secure_filename

BASE = Path(__file__).resolve().parent
UPLOAD_DIR = BASE / "uploads"
OUTPUT_DIR = BASE / "output"
WORK_DIR = BASE / "frames"
DOWNLOAD_DIR = BASE / "downloads"
MAX_UPLOAD_BYTES = 100 * 1024 * 1024
MAX_DURATION_SECONDS = 10
MAX_DOWNLOAD_BYTES = 100 * 1024 * 1024
MAX_DOWNLOAD_DURATION_SECONDS = 600
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
ALLOWED_EXTENSIONS = VIDEO_EXTENSIONS | IMAGE_EXTENSIONS

for directory in (UPLOAD_DIR, OUTPUT_DIR, WORK_DIR, DOWNLOAD_DIR):
    directory.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES
app.config["JSON_SORT_KEYS"] = False

# In-memory job tracking is suitable only for a single-instance beta. Jobs are
# lost on restarts and files on free hosts may be ephemeral.
jobs = {}
jobs_lock = threading.Lock()
worker_lock = threading.Lock()
download_jobs = {}
download_jobs_lock = threading.Lock()
download_worker_lock = threading.Lock()

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
main{width:min(100%,380px);text-align:center}p{margin:0 0 20px;color:#c8c8c8;line-height:1.5}
section{padding:22px 0}section+section{border-top:1px solid #333}h2{font-size:17px;font-weight:650;margin:0 0 14px}
button,select,#videoUrl{width:100%;padding:13px;border:0;border-radius:10px;font:500 15px system-ui}
button{background:#fff;color:#111;font-weight:650;cursor:pointer}button:disabled{opacity:.55;cursor:wait}
input[type=file]{display:none}#videoUrl,select{display:block;background:#222;color:#f5f5f5;border:1px solid #393939;margin-bottom:10px;min-width:0}
#status,#downloadStatus{margin-top:14px;color:#c8c8c8;font-size:14px;overflow-wrap:anywhere}#status:empty,#downloadStatus:empty{display:none}
a{display:block;margin-top:16px;color:#fff;font-weight:600}#downloadResult:empty{display:none}
</style>
</head>
<body>
<main>
<section>
<p>Remove the background from a photo or anime clip.</p>
<input id="file" type="file" accept=".png,.jpg,.jpeg,.webp,.bmp,.mp4,.mov,.mkv,.webm,.avi,image/*,video/*">
<button id="choose" type="button">Choose photo or video</button>
<div id="status" role="status" aria-live="polite"></div>
<div id="result"></div>
</section>
<section id="video-downloader">
<h2>Download a video</h2>
<form id="downloadForm">
<input id="videoUrl" type="url" placeholder="Paste YouTube or social video link" autocomplete="url" required>
<select id="downloadType" aria-label="Download type">
<option value="video">Video (MP4)</option><option value="audio">Audio (MP3)</option>
</select>
<select id="quality" aria-label="Quality">
<option value="720">720p</option><option value="480">480p</option><option value="360">360p</option><option value="1080">1080p</option><option value="best">Best available</option>
</select>
<div class="cf-turnstile" data-sitekey="{{ turnstile_site_key }}" data-action="download" style="margin:12px 0"></div>
<button id="downloadButton" type="submit">Download</button>
</form>
<div id="downloadStatus" role="status" aria-live="polite"></div>
<div id="downloadResult"></div>
</section>
</main>
<script src="https://challenges.cloudflare.com/turnstile/v0/api.js" async defer></script>
<script>
const input=document.querySelector('#file'),button=document.querySelector('#choose'),status=document.querySelector('#status'),result=document.querySelector('#result');
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function json(response,label){
 const raw=await response.text();
 if(!raw.trim())throw new Error(label+' failed (HTTP '+response.status+'). Try again later.');
 try{return JSON.parse(raw)}catch(_){throw new Error(label+' returned an invalid response. Try again later.')}
}
button.addEventListener('click',()=>input.click());
const downloadForm=document.querySelector('#downloadForm'),videoUrl=document.querySelector('#videoUrl'),downloadType=document.querySelector('#downloadType'),quality=document.querySelector('#quality'),downloadButton=document.querySelector('#downloadButton'),downloadStatus=document.querySelector('#downloadStatus'),downloadResult=document.querySelector('#downloadResult');
const qualityOptions={video:[['best','Best available'],['1080','1080p'],['720','720p'],['480','480p'],['360','360p']],audio:[['128','128 kbps'],['192','192 kbps'],['320','320 kbps']]};
downloadType.addEventListener('change',()=>{const previous=quality.value;quality.replaceChildren(...qualityOptions[downloadType.value].map(item=>{const option=document.createElement('option');option.value=item[0];option.textContent=item[1];return option}));if(qualityOptions[downloadType.value].some(item=>item[0]===previous))quality.value=previous;});
downloadForm.addEventListener('submit',async event=>{
 event.preventDefault();if(!videoUrl.value.trim())return;
 downloadButton.disabled=true;downloadButton.textContent='Preparing…';downloadStatus.textContent='Starting download…';downloadResult.replaceChildren();
 try{
  const response=await fetch('/api/downloads',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url:videoUrl.value.trim(),type:downloadType.value,quality:quality.value,turnstile_token:(document.querySelector('[name="cf-turnstile-response"]')||{}).value||''})});
  const job=await json(response,'Download');if(!response.ok)throw new Error(job.error||'Could not start download.');
  while(true){
   await sleep(1800);
   const sr=await fetch('/api/downloads/'+encodeURIComponent(job.job_id));const state=await json(sr,'Status');
   if(!sr.ok)throw new Error(state.error||'Could not check download status.');
   downloadStatus.textContent=state.detail||state.stage||'Processing…';
   if(state.state==='done'){downloadStatus.textContent='Your file is ready.';const link=document.createElement('a');link.href='/api/downloads/'+encodeURIComponent(job.job_id)+'/file';link.textContent='Download '+(downloadType.value==='audio'?'MP3 audio':'MP4 video');downloadResult.appendChild(link);break;}
   if(state.state==='error')throw new Error(state.error||'Download failed.');
  }
 }catch(error){downloadStatus.textContent=error.message||'Download failed. Please try again.'}
 finally{downloadButton.disabled=false;downloadButton.textContent='Download'}
});
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
    return render_template_string(PAGE, turnstile_site_key=os.environ.get("TURNSTILE_SITE_KEY", ""))


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


def validate_download_url(value):
    parsed = urlparse(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Enter a valid public video URL beginning with https://.")
    host = parsed.hostname.lower().rstrip(".")
    if host in {"localhost", "localhost.localdomain"} or host.endswith((".local", ".internal", ".localhost")):
        raise ValueError("That host is not allowed. Use a public social-video link.")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ValueError("Direct IP-address URLs are not supported. Paste the public video page link.")
    if "." not in host:
        raise ValueError("Enter a public video page link.")
    return parsed.geturl()



def validate_turnstile_token(token):
    """Validate a Turnstile token server-side; never trust the browser alone."""
    secret = os.environ.get("TURNSTILE_SECRET_KEY", "").strip()
    sitekey = os.environ.get("TURNSTILE_SITE_KEY", "").strip()
    if not secret or not sitekey:
        return False, "CAPTCHA is not configured yet. Please try again later."
    if not token or len(token) > 2048:
        return False, "Complete the verification before downloading."
    try:
        import requests
        response = requests.post(
            "https://challenges.cloudflare.com/turnstile/v0/siteverify",
            data={"secret": secret, "response": token},
            timeout=10,
        )
        response.raise_for_status()
        result = response.json()
    except Exception:
        app.logger.exception("Turnstile verification request failed")
        return False, "Verification is temporarily unavailable. Please try again."
    if not result.get("success"):
        return False, "Verification failed or expired. Please verify again."
    expected_hostname = os.environ.get("TURNSTILE_HOSTNAME", "").strip().lower()
    if expected_hostname and str(result.get("hostname", "")).lower() != expected_hostname:
        return False, "Verification failed for this website. Please refresh and try again."
    if result.get("action") and result.get("action") != "download":
        return False, "Verification failed. Please refresh and try again."
    return True, None


def run_download_job(job_id, url, mode, quality):
    if not download_worker_lock.acquire(blocking=False):
        with download_jobs_lock:
            download_jobs[job_id].update(state="error", stage="Busy",
                error="Another download is running. Please try again shortly.")
        return
    try:
        import yt_dlp

        def progress_hook(data):
            status = data.get("status")
            if status == "downloading":
                total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
                current = data.get("downloaded_bytes") or 0
                detail = ("Downloading: " + str(min(99, int(current * 100 / total))) + "%") if total else "Downloading video…"
                with download_jobs_lock:
                    if job_id in download_jobs:
                        download_jobs[job_id].update(stage="Downloading", detail=detail)
            elif status == "finished":
                with download_jobs_lock:
                    if job_id in download_jobs:
                        download_jobs[job_id].update(stage="Rendering file", detail="Preparing the final file…")

        template = str(DOWNLOAD_DIR / (job_id + ".%(ext)s"))
        match_filter = lambda info, incomplete: (
            "Videos longer than 10 minutes are not supported."
            if info.get("duration") and info["duration"] > MAX_DOWNLOAD_DURATION_SECONDS else None
        )
        options = {
            "format": "bestaudio/best" if mode == "audio" else (
                "bestvideo+bestaudio/best" if quality == "best"
                else "bestvideo[height<=" + quality + "]+bestaudio/best[height<=" + quality + "]"
            ),
            "outtmpl": template,
            "noplaylist": True,
            "max_filesize": MAX_DOWNLOAD_BYTES,
            "socket_timeout": 20,
            "retries": 1,
            "quiet": True,
            "no_warnings": True,
            "restrictfilenames": True,
            "progress_hooks": [progress_hook],
            "match_filter": match_filter,
        }
        if mode == "audio":
            options["postprocessors"] = [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": quality,
            }]
        else:
            options["merge_output_format"] = "mp4"
            options["postprocessors"] = [{
                "key": "FFmpegVideoConvertor",
                "preferedformat": "mp4",
            }]

        with download_jobs_lock:
            download_jobs[job_id].update(state="running", stage="Connecting", detail="Connecting to the video source…")
        with yt_dlp.YoutubeDL(options) as ydl:
            ydl.download([url])
        candidates = [
            path for path in DOWNLOAD_DIR.glob(job_id + ".*")
            if path.is_file() and not path.name.endswith((".part", ".ytdl", ".temp"))
        ]
        expected_ext = ".mp3" if mode == "audio" else ".mp4"
        preferred = [path for path in candidates if path.suffix.lower() == expected_ext]
        output = preferred[0] if preferred else (candidates[0] if candidates else None)
        if output is None:
            raise RuntimeError("The source did not produce a downloadable file.")
        if output.stat().st_size > MAX_DOWNLOAD_BYTES:
            output.unlink(missing_ok=True)
            raise RuntimeError("The final file exceeds the 100 MB limit. Try a lower quality.")
        with download_jobs_lock:
            download_jobs[job_id].update(state="done", stage="Complete", detail="File ready.",
                output=output.name, mode=mode)
    except Exception as exc:
        message = str(exc).strip()
        lowered = message.lower()
        if "sign in to confirm you're not a bot" in lowered or "confirm you're not a bot" in lowered:
            message = (
                "YouTube is blocking downloads from this server right now. "
                "Try another supported public-video link, or use YouTube's official options."
            )
        elif "video unavailable" in lowered or "private video" in lowered:
            message = "This video is unavailable or private. Try a public link."
        elif "unsupported url" in lowered:
            message = "This site or link is not supported by the downloader."
        elif "maximum file size" in lowered or "max-filesize" in lowered:
            message = "The file is too large. Try a lower quality or a shorter video."
        elif not message:
            message = "Download failed. The link may be unsupported or the site may be temporarily unavailable."
        with download_jobs_lock:
            if job_id in download_jobs:
                download_jobs[job_id].update(state="error", stage="Failed", error=message[:350], detail=message[:350])
        for path in DOWNLOAD_DIR.glob(job_id + ".*"):
            path.unlink(missing_ok=True)
    finally:
        download_worker_lock.release()


@app.post("/api/downloads")
def create_download():
    data = request.get_json(silent=True) or {}
    valid_captcha, captcha_error = validate_turnstile_token(str(data.get("turnstile_token", "")))
    if not valid_captcha:
        status = 503 if "not configured" in (captcha_error or "").lower() else 400
        return jsonify(error=captcha_error), status
    try:
        url = validate_download_url(str(data.get("url", "")))
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    mode = str(data.get("type", "video")).lower()
    quality = str(data.get("quality", "720"))
    if mode not in {"video", "audio"}:
        return jsonify(error="Choose video or audio."), 400
    allowed_quality = {"video": {"best", "1080", "720", "480", "360"}, "audio": {"128", "192", "320"}}
    if quality not in allowed_quality[mode]:
        return jsonify(error="Choose one of the available quality options."), 400
    with download_jobs_lock:
        if any(job.get("state") in {"queued", "running"} for job in download_jobs.values()):
            return jsonify(error="The downloader is busy. Please try again in a few minutes."), 429
        job_id = uuid.uuid4().hex
        download_jobs[job_id] = {"state": "queued", "stage": "Queued", "detail": "Waiting for the downloader."}
    threading.Thread(target=run_download_job, args=(job_id, url, mode, quality), daemon=True).start()
    return jsonify(job_id=job_id), 202


@app.get("/api/downloads/<job_id>")
def download_status(job_id):
    with download_jobs_lock:
        job = download_jobs.get(job_id)
        if not job:
            return jsonify(error="Download not found. It may have expired after a restart."), 404
        safe = {key: job[key] for key in ("state", "stage", "detail", "error") if key in job}
    return jsonify(safe)


@app.get("/api/downloads/<job_id>/file")
def download_file(job_id):
    with download_jobs_lock:
        job = download_jobs.get(job_id)
        if not job or job.get("state") != "done":
            abort(404)
        filename = job.get("output", "")
        mode = job.get("mode", "video")
    path = DOWNLOAD_DIR / filename
    if not filename or not path.is_file() or path.parent.resolve() != DOWNLOAD_DIR.resolve():
        abort(404)
    download_name = "download.mp3" if mode == "audio" else "download.mp4"
    mimetype = "audio/mpeg" if mode == "audio" else "video/mp4"
    return send_file(path, as_attachment=True, download_name=download_name, mimetype=mimetype)


@app.errorhandler(413)
def too_large(_error):
    return jsonify(error="File is too large. Maximum upload size is 100 MB."), 413


@app.get("/health")
def health():
    return jsonify(status="ok")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), debug=False)
