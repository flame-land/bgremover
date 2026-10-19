# BGremover by Feral

An experimental anime-video background remover for Termux on Android. It processes a video frame by frame with the public SkyTNT anime segmentation demo, then composites the character over pure green and exports an MP4.

> **Status: early prototype.** The hosted model API, queue limits, and availability can change. Test with a short clip first.

## Features

- Intended for anime footage and illustrations
- Green-screen output (`#00FF00`) for video editors
- Keeps the source audio when FFmpeg can copy/re-encode it
- Runs video decoding and final encoding locally; AI segmentation uses a remote service

## Requirements

- Android with Termux
- Internet connection
- Python 3
- FFmpeg
- Python packages in `requirements.txt`

## Install in Termux

Install Termux packages:

```bash
pkg update
pkg install python ffmpeg
termux-setup-storage
```

Inside the project directory, install Python dependencies:

```bash
python -m pip install -r requirements.txt
```

## Usage

Put a test clip in the project folder or use a path from shared storage. For example:

```bash
mkdir -p input output
cp ~/storage/shared/Movies/clip.mp4 input/clip.mp4
python remover.py input/clip.mp4
```

By default, output is saved as `output/clip_green.mp4`. You can specify a different output path:

```bash
python remover.py input/clip.mp4 output/my_green_clip.mp4
```

Start with a 2–3 second clip. Each video frame requires a separate request to the hosted model, so long clips can be slow or hit service limits.

## Privacy and limitations

- **Frames are uploaded to a third-party hosted AI demo.** Do not process private or sensitive footage.
- A network connection is required.
- Public demo availability, API endpoints, and usage limits may change.
- Fine details such as hair spikes, motion blur, glow, and energy auras may be cut off or leave artifacts.
- The green background is intended for chroma keying; edges may still contain colors from the original image.
- The script attempts to preserve audio, but unusual variable-frame-rate videos may need additional handling.

## Model

Uses the public [SkyTNT Anime Remove Background Space](https://huggingface.co/spaces/skytnt/anime-remove-background), which demonstrates the [SkyTNT anime segmentation model](https://github.com/SkyTNT/anime-segmentation/).

## License

Project code: MIT. The hosted model and its weights remain subject to their own license and terms.
