#!/usr/bin/env python3
"""Local web UI for the BGremover command-line processor."""

import os
import subprocess
import sys
import uuid
from pathlib import Path

from flask import Flask, render_template_string, request, send_file
from werkzeug.utils import secure_filename

BASE = Path(__file__).resolve().parent
UPLOAD_DIR = BASE / "uploads"
OUTPUT_DIR = BASE / "output"
MAX_UPLOAD_BYTES = 300 * 1024 * 1024  # 300 MB

UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES

PAGE = """
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>BGremover by Feral</title>
<style>
:root{color-scheme:dark;--bg:#080b12;--panel:#111827;--line:#253249;--muted:#9aabc4;--blue:#60a5fa}
*{box-sizing:border-box}body{margin:0;min-height:100vh;background:radial-gradient(ellipse at top,#14213a 0%,var(--bg) 55%);color:#f3f6fb;font:16px system-ui,-apple-system,sans-serif;display:grid;place-items:center;padding:22px}
main{width:min(100%,560px)}.brand{font-size:13px;letter-spacing:.18em;color:var(--blue);font-weight:800}.panel{margin-top:14px;background:#101827eF;border:1px solid var(--line);border-radius:22px;padding:clamp(20px,5vw,34px);box-shadow:0 24px 90px #0007}h1{font-size:clamp(27px,7vw,38px);line-height:1.1;margin:10px 0}p{color:var(--muted);line-height:1.6}.drop{display:block;margin:22px 0;border:1px dashed #496080;border-radius:16px;padding:24px 14px;text-align:center;background:#0b1220;cursor:pointer}.drop strong{display:block;margin-bottom:7px}.drop input{display:block;width:100%;margin-top:16px;color:var(--muted)}button,.download{display:block;width:100%;border:0;border-radius:12px;padding:14px 16px;background:var(--blue);color:#07111f;font-weight:800;font-size:16px;text-align:center;text-decoration:none;cursor:pointer}button:disabled{opacity:.55;cursor:wait}.note{font-size:12px;margin-top:16px}.status{margin-top:18px;padding:13px;border-radius:12px;background:#0b1220;border:1px solid var(--line);overflow-wrap:anywhere}.error{color:#fca5a5}.success{color:#86efac}footer{font-size:12px;color:#71829c;margin:16px 4px}
</style>
</head>
<body><main>
<div class="brand">FERAL LABS / VIDEO TOOLS</div>
<section class="panel">
<div class="brand">BGREMOVER</div>
<h1>Remove the anime background.</h1>
<p>Choose a short video. BGremover will process it and return an MP4 with a green background for chroma keying.</p>
<form method="post" enctype="multipart/form-data" id="form">
<label class="drop"><strong>Choose your video</strong><span id="filename">MP4, MOV, MKV or WebM · up to 300 MB</span><input type="file" name="video" accept="video/*,.mkv" required></label>
<button id="submit" type="submit">Upload &amp; Remove Background</button>
</form>
{% if error %}<div class="status error">{{ error }}</div>{% endif %}
{% if output %}<div class="status success"><strong>Processing finished.</strong><p>Your result is ready to download.</p><a class="download" href="{{ url_for('download', token=token) }}">Download processed MP4</a></div>{% endif %}
<div class="status">Processing logs appear in the Termux terminal. Start with a 2–3 second clip; each frame is sent to a remote AI service.</div>
</section>
<footer>Local interface · Your browser connects to this phone only. Video frames are still sent to the configured remote AI demo for processing.</footer>
</main>
<script>
const input=document.querySelector('input[type=file]'), label=document.querySelector('#filename'), form=document.querySelector('#form'), button=document.querySelector('#submit');
input.addEventListener('change',()=>{label.textContent=input.files[0]?.name || 'MP4, MOV, MKV or WebM · up to 300 MB';});
form.addEventListener('submit',()=>{if(input.files.length){button.disabled=true;button.textContent='Uploading & processing… keep this page open';}});
</script>
</body></html>
"""


@app.get("/")
def index():
    return render_template_string(PAGE)


@app.post("/")
def upload():
    video = request.files.get("video")
    if not video or not video.filename:
        return render_template_string(PAGE, error="Choose a video file first."), 400

    original_name = secure_filename(video.filename)
    suffix = Path(original_name).suffix.lower()
    if suffix not in {".mp4", ".mov", ".mkv", ".webm", ".avi"}:
        return render_template_string(PAGE, error="Unsupported file type. Choose MP4, MOV, MKV, WebM or AVI."), 400

    token = uuid.uuid4().hex
    source = UPLOAD_DIR / f"{token}{suffix}"
    output = OUTPUT_DIR / f"{Path(original_name).stem}_{token[:8]}_green.mp4"
    video.save(source)

    print(f"\n[BGremover web] Received {original_name}", flush=True)
    print("[BGremover web] Processing started; progress will appear below.", flush=True)
    try:
        # Inherit stdout/stderr so frame progress and errors appear in Termux.
        subprocess.run(
            [sys.executable, str(BASE / "remover.py"), str(source), str(output)],
            cwd=BASE,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        print(f"[BGremover web] Processing failed (exit {exc.returncode}).", flush=True)
        source.unlink(missing_ok=True)
        return render_template_string(
            PAGE,
            error="Processing failed. Check the Termux terminal for the full error, then try a short clip.",
        ), 500
    except Exception as exc:
        print(f"[BGremover web] Error: {exc}", flush=True)
        source.unlink(missing_ok=True)
        return render_template_string(PAGE, error="Unexpected error. Check the Termux terminal."), 500

    source.unlink(missing_ok=True)
    return render_template_string(PAGE, output=True, token=output.name)


@app.get("/download/<token>")
def download(token):
    # Only allow downloading generated files from the output directory.
    filename = secure_filename(token)
    path = OUTPUT_DIR / filename
    if not path.is_file() or path.parent.resolve() != OUTPUT_DIR.resolve():
        return "File not found", 404
    return send_file(path, as_attachment=True, download_name=filename, mimetype="video/mp4")


@app.errorhandler(413)
def too_large(_error):
    return render_template_string(PAGE, error="File is too large. Maximum upload size is 300 MB."), 413


if __name__ == "__main__":
    print("BGremover is starting. Open http://127.0.0.1:5000 in your phone browser.", flush=True)
    app.run(host="127.0.0.1", port=5000, debug=False)
