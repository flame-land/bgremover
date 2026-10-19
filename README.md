# BGremover by Feral

An experimental web app that turns short anime clips into green-screen MP4s for chroma-key editing.

**Status: public beta / proof of concept.** The app sends each extracted frame to the public SkyTNT anime segmentation demo. That third-party service can change, queue requests, throttle usage, or become unavailable. Do not rely on this for private footage or production workloads.

## Current beta limits

- MP4, MOV, MKV, WebM, AVI
- Maximum upload: 100 MB
- Maximum clip duration: 10 seconds
- One active job at a time
- Output is an MP4 with a green (#00FF00) background; audio is preserved when FFmpeg can map it
- Job status is held in memory and temporary files may disappear when the hosting service restarts
- Designed for short anime clips, not arbitrary footage or long videos

The frame-by-frame model calls can be slow. Processing time depends on frame count, remote queue load, network speed, and the third-party model's availability. Progress is an estimate based on frames completed.

## Deploy online with Render

This repository includes a Dockerfile and render.yaml. Docker installs FFmpeg and Python dependencies; Gunicorn serves Flask on the port provided by the hosting platform.

1. Open Render at https://render.com/ and sign in.
2. Choose **New + → Web Service** and connect your GitHub account if asked.
3. Select the `flame-land/bgremover` repository and the `main` branch.
4. Choose **Docker** as the runtime, with Dockerfile path `./Dockerfile` if requested.
5. Choose the **Free** plan for testing. Free instances can sleep when idle and have limited CPU/memory; video jobs may be slow or fail under load.
6. Click **Deploy Web Service** and wait for the build/deploy logs to finish.
7. Open the `onrender.com` URL Render provides. Test with a short, non-sensitive 2–3 second clip first.
8. If deployment fails, open the Render service's **Logs** page and inspect the first error.

Alternatively, Render may detect `render.yaml` as a Blueprint. Review the service and plan before applying it.

### Render configuration

- Runtime: Docker
- Dockerfile: `./Dockerfile`
- Health check: `/health`
- App server: Gunicorn, one worker, four threads
- No API keys are required for the current public demo integration

A custom domain is optional. Once the app works, open the service's **Settings → Custom Domains** and follow Render's DNS instructions.

## Run locally

Requirements: Python 3.11 recommended, FFmpeg (including `ffprobe`), and internet access.

Install Python packages:

```bash
python -m pip install -r requirements.txt
```

On Termux, install FFmpeg with:

```bash
pkg install python ffmpeg
```

Start the web interface:

```bash
python webapp.py
```

Open `http://127.0.0.1:5000` in the same device's browser.

Command-line usage:

```bash
python remover.py input.mp4 output_green.mp4
```

## Privacy and safety

- Video frames are sent to a third-party hosted AI service. Do not upload private, identifiable, or sensitive footage.
- The hosting service temporarily receives the full uploaded video so it can extract frames. Use only clips you have permission to process.
- The hosted demo's own privacy policy and terms apply to its processing.
- The beta does not promise persistent storage. Download output as soon as it is ready.
- Do not put access tokens or secrets in source code. The current demo integration does not require a token.
- The app restricts file size, clip duration, and concurrent processing, but these safeguards do not replace production-grade abuse prevention.

## Known limitations

- The integration relies on the third-party Space's current Gradio API name (`/rmbg_fn`) and expected mask response. If the API changes, processing will fail until the integration is updated.
- Every frame is processed separately, so even short clips can take a while.
- Anime outlines, hair spikes, glow, motion blur, and energy auras may be cut off or have artifacts.
- The public demo may queue, rate-limit, or reject requests.
- Free Render services spin down after inactivity and local files are ephemeral, so free hosting is for testing rather than guaranteed always-on availability.

## Project and model

- Source: https://github.com/flame-land/bgremover
- Model demo: https://huggingface.co/spaces/skytnt/anime-remove-background
- Upstream project: https://github.com/SkyTNT/anime-segmentation

Project code: MIT. The model, model weights, and third-party hosting service are subject to their own licenses and terms.
