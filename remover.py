#!/usr/bin/env python3
"""Anime video background remover: remote segmentation + green-screen MP4."""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image
from gradio_client import Client, handle_file

SPACE_ID = "skytnt/anime-remove-background"
GREEN = (0, 255, 0, 255)


def run(command):
    """Run a command and show a useful error if it fails."""
    subprocess.run(command, check=True)


def probe_fps(video_path):
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=avg_frame_rate",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(video_path),
        ],
        check=True, capture_output=True, text=True,
    )
    fps = result.stdout.strip()
    return fps if fps and fps != "0/0" else "30/1"


def result_path(item):
    """Get a local file path from common Gradio output shapes."""
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        return item.get("path") or item.get("name")
    return None


def main():
    parser = argparse.ArgumentParser(
        description="Remove anime backgrounds and export a green-screen MP4."
    )
    parser.add_argument("input", help="Path to source image or video")
    parser.add_argument(
        "output", nargs="?",
        help="Output MP4 path (default: output/<name>_green.mp4)",
    )
    args = parser.parse_args()

    source = Path(args.input).expanduser().resolve()
    if not source.is_file():
        parser.error(f"Input file not found: {source}")

    image_extensions = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
    if source.suffix.lower() in image_extensions:
        output = (
            Path(args.output).expanduser().resolve()
            if args.output
            else Path("output") / f"{source.stem}_green.png"
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        print("Connecting to the background-removal service...", flush=True)
        client = Client(SPACE_ID, verbose=False)
        result = client.predict(handle_file(str(source)), api_name="/rmbg_fn")
        if not isinstance(result, (list, tuple)) or not result:
            raise RuntimeError("Unexpected model response. The public Space API may have changed.")
        mask_path = result_path(result[0])
        if not mask_path or not Path(mask_path).is_file():
            raise RuntimeError("Could not find the returned mask. The public Space API may have changed.")
        with Image.open(source) as image:
            original = image.convert("RGBA")
        with Image.open(mask_path) as image:
            mask = image.convert("L")
        if mask.size != original.size:
            mask = mask.resize(original.size, Image.Resampling.LANCZOS)
        original.putalpha(mask)
        background = Image.new("RGBA", original.size, GREEN)
        background.alpha_composite(original)
        background.save(output, format="PNG", optimize=True)
        print(f"Done: {output}", flush=True)
        return

    output = (
        Path(args.output).expanduser().resolve()
        if args.output
        else Path("output") / f"{source.stem}_green.mp4"
    )
    output.parent.mkdir(parents=True, exist_ok=True)

    work = Path("frames") / source.stem
    original_frames = work / "source"
    green_frames = work / "green"
    if work.exists():
        shutil.rmtree(work)
    original_frames.mkdir(parents=True)
    green_frames.mkdir(parents=True)

    fps = probe_fps(source)
    print(f"Input: {source}", flush=True)
    print(f"FPS: {fps}", flush=True)
    print("Extracting frames...", flush=True)

    # Avoid deprecated/removed FFmpeg options such as -vsync on newer builds.
    run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(source), "-map", "0:v:0",
        str(original_frames / "%06d.jpg"),
    ])

    frames = sorted(original_frames.glob("*.jpg"))
    if not frames:
        raise RuntimeError("FFmpeg did not extract any video frames.")

    print(f"Found {len(frames)} frames.", flush=True)
    print("Connecting to the public anime segmentation demo...", flush=True)
    print("Note: each frame is uploaded to the hosted service.", flush=True)

    client = Client(SPACE_ID, verbose=False)

    for number, frame_path in enumerate(frames, start=1):
        print(f"Processing frame {number}/{len(frames)}", flush=True)

        result = client.predict(
            handle_file(str(frame_path)),
            api_name="/rmbg_fn",
        )
        if not isinstance(result, (list, tuple)) or len(result) < 1:
            raise RuntimeError(
                "Unexpected model response. The public Space API may have changed."
            )

        mask_path = result_path(result[0])
        if not mask_path or not Path(mask_path).is_file():
            raise RuntimeError(
                "Could not find the returned mask. The public Space API may have changed."
            )

        with Image.open(frame_path) as image:
            original = image.convert("RGBA")
        with Image.open(mask_path) as image:
            mask = image.convert("L")

        if mask.size != original.size:
            mask = mask.resize(original.size, Image.Resampling.LANCZOS)

        original.putalpha(mask)
        background = Image.new("RGBA", original.size, GREEN)
        background.alpha_composite(original)
        destination = green_frames / f"{number:06d}.png"
        background.convert("RGB").save(destination, optimize=False)

    print("Encoding output video...", flush=True)
    run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-framerate", fps, "-i", str(green_frames / "%06d.png"),
        "-i", str(source),
        "-map", "0:v:0", "-map", "1:a?",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest",
        str(output),
    ])

    print(f"Done: {output}", flush=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped by user.", file=sys.stderr)
        sys.exit(130)
    except subprocess.CalledProcessError as error:
        print(f"Command failed with exit code {error.returncode}.", file=sys.stderr)
        sys.exit(error.returncode or 1)
    except Exception as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)
