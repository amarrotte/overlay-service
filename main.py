from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import httpx
import subprocess
import uuid
from pathlib import Path
import os
import shutil

app = FastAPI(title="Overlay Service", version="1.0.0")

# Path to ffmpeg; for most Linux containers it's just "ffmpeg".
# On Windows local dev, you can set FFMPEG_BIN to the full path if needed.
FFMPEG_BIN = os.environ.get("FFMPEG_BIN", "ffmpeg")

# Default font – adjust if you want a different one
DEFAULT_FONT = os.environ.get(
    "OVERLAY_FONT_FILE",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
)


class OverlayRequest(BaseModel):
    video_url: str        # public URL to mp4
    overlay_text: str     # overlay text, can contain \n


def _escape_for_ffmpeg(text: str) -> str:
    """
    Escape characters so ffmpeg drawtext can handle the string.
    """
    out = text.replace("\\", "\\\\")
    out = out.replace(":", r"\:")
    out = out.replace("'", r"\'")
    out = out.replace("\n", r"\n")
    return out


@app.post("/overlay")
async def overlay(req: OverlayRequest):
    # Basic validation
    if not req.video_url.startswith("http"):
        raise HTTPException(status_code=400, detail="video_url must be http(s)")

    if len(req.overlay_text) > 300:
        raise HTTPException(status_code=400, detail="overlay_text too long")

    tmp_root = Path("/tmp")
    work_dir = tmp_root / f"overlay-{uuid.uuid4().hex}"
    work_dir.mkdir(parents=True, exist_ok=True)

    input_path = work_dir / "input.mp4"
    output_path = work_dir / "output.mp4"

    try:
        # 1) Download the source video
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.get(req.video_url)
            r.raise_for_status()
            input_path.write_bytes(r.content)

        # 2) Prepare ffmpeg drawtext filter
        if not Path(DEFAULT_FONT).exists():
            # For Windows local dev, we override this later; for now just fail if missing.
            raise HTTPException(
                status_code=500,
                detail=f"Font file not found at {DEFAULT_FONT}"
            )

        text = _escape_for_ffmpeg(req.overlay_text)

        drawtext_filter = (
            f"drawtext=fontfile='{DEFAULT_FONT}':"
            f"text='{text}':"
            "fontcolor=white:fontsize=48:borderw=2:"
            "x=(w-text_w)/2:"
            "y=h-text_h-120"
        )

        cmd = [
            FFMPEG_BIN,
            "-y",
            "-i", str(input_path),
            "-vf", drawtext_filter,
            "-c:a", "copy",
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

        # 3) Stream back the result
        return StreamingResponse(
            output_path.open("rb"),
            media_type="video/mp4",
        )

    finally:
        if work_dir.exists():
            shutil.rmtree(work_dir, ignore_errors=True)
