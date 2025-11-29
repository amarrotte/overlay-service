import os
import uuid
import tempfile
import subprocess
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import FileResponse

app = FastAPI(title="Overlay Service", version="1.0.0")

# Path to ffmpeg; for most Linux containers it's just "ffmpeg".
# On Windows local dev, you can set FFMPEG_BIN to the full path if needed.
FFMPEG_BIN = os.environ.get("FFMPEG_BIN", "ffmpeg")

# Default font – adjust if you want a different one
DEFAULT_FONT = os.environ.get(
    "OVERLAY_FONT_FILE",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
)


def _escape_for_ffmpeg(text: str) -> str:
    """
    Escape characters so ffmpeg drawtext can handle the string.
    """
    out = text.replace("\\", "\\\\")
    out = out.replace(":", r"\:")
    out = out.replace("'", r"\'")
    out = out.replace("\n", r"\n")
    return out


@app.get("/")
def health():
    return {"status": "ok"}


@app.post("/overlay")
async def overlay(
    video: UploadFile = File(...),
    overlay_text: str = Form(...),
):
    """
    Accepts:
      - video: uploaded MP4 file (binary)
      - overlay_text: text to overlay (can contain newlines)

    Returns:
      - processed MP4 with text overlayed near the bottom.
    """

    if not video.filename:
        raise HTTPException(status_code=400, detail="Video file is required")

    if len(overlay_text) > 300:
        raise HTTPException(status_code=400, detail="overlay_text too long")

    # Make sure font exists (inside the container this will be installed by Dockerfile)
    if not Path(DEFAULT_FONT).exists():
        raise HTTPException(
            status_code=500,
            detail=f"Font file not found at {DEFAULT_FONT}",
        )

    # Temp files
    tmp_dir = Path(tempfile.gettempdir())
    work_dir = tmp_dir / f"overlay-{uuid.uuid4().hex}"
    work_dir.mkdir(parents=True, exist_ok=True)

    input_path = work_dir / "input.mp4"
    output_path = work_dir / "output.mp4"

    try:
        # Save uploaded video to disk
        with input_path.open("wb") as f:
            f.write(await video.read())

        # Prepare drawtext filter
        text = _escape_for_ffmpeg(overlay_text)

        drawtext_filter = (
            f"drawtext=fontfile='{DEFAULT_FONT}':"
            f"text='{text}':"
            "fontcolor=white:fontsize=48:borderw=2:"
            "box=1:boxcolor=black@0.5:boxborderw=10:"
            "x=(w-text_w)/2:"
            "y=h-text_h-120"
        )

        cmd = [
            FFMPEG_BIN,
            "-y",
            "-i",
            str(input_path),
            "-vf",
            drawtext_filter,
            "-c:a",
            "copy",
            str(output_path),
        ]

        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        if proc.returncode != 0:
            raise HTTPException(
                status_code=500,
                detail=f"ffmpeg failed: {proc.stderr.decode('utf-8', 'ignore')}",
            )

        # Send back the file
        return FileResponse(
            path=str(output_path),
            media_type="video/mp4",
            filename=video.filename or "overlay_output.mp4",
        )

    finally:
        # Clean up temp files
        if work_dir.exists():
            for p in work_dir.iterdir():
                try:
                    p.unlink()
                except Exception:
                    pass
            try:
                work_dir.rmdir()
            except Exception:
                pass

