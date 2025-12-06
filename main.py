import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse

# -------------------------------------------------------------------
# Logging setup
# -------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s:%(message)s",
)
logger = logging.getLogger("overlay-service")

# -------------------------------------------------------------------
# Constants
# -------------------------------------------------------------------

FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

# -------------------------------------------------------------------
# Helper functions
# -------------------------------------------------------------------


def prepare_overlay_text(raw: str, max_chars_per_line: int = 32) -> str:
    """
    Prepare overlay text for ffmpeg drawtext:

    - Word-wraps long text into multiple lines based on character count.
    - Escapes characters that break drawtext filter syntax.
    - Returns a string safe to insert into: text='...'
    """
    # 1) Simple word-wrapping
    words = raw.split()
    lines = []
    current = ""

    for word in words:
        candidate = (current + " " + word).strip() if current else word
        if len(candidate) <= max_chars_per_line:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word

    if current:
        lines.append(current)

    # Use *real* newline characters between lines
    wrapped = "\n".join(lines)

    # 2) Escape problematic characters for drawtext
    escaped = (
        wrapped
        .replace("\\", r"\\")   # backslash
        .replace(":", r"\:")    # colon
        .replace("'", r"\'")    # single quote
        .replace("[", r"\[")    # brackets
        .replace("]", r"\]")
    )

    return escaped


def run_ffmpeg(input_path: str, output_path: str, overlay_text: str) -> None:
    """
    Run ffmpeg to overlay text onto a video.

    Raises RuntimeError if ffmpeg fails.
    """
    safe_text = prepare_overlay_text(overlay_text)

    # Centered horizontally and vertically
    drawtext = (
        f"drawtext=fontfile='{FONT_PATH}':"
        f"text='{safe_text}':"
        "fontcolor=white:fontsize=32:box=1:boxcolor=black@0.5:boxborderw=5:"
        "x=(w-text_w)/2:y=(h-text_h)/2"
    )

    cmd = [
        "ffmpeg",
        "-y",               # overwrite output
        "-i",
        input_path,
        "-vf",
        drawtext,
        "-codec:a",
        "copy",
        output_path,
    ]

    logger.info("FFMPEG CMD: %s", " ".join(cmd))

    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    logger.info("FFMPEG return code: %s", proc.returncode)

    if proc.returncode != 0:
        logger.error(
            "FFMPEG FAILED. First 4000 chars of stderr:\n%s",
            proc.stderr[:4000],
        )
        raise RuntimeError(f"ffmpeg failed with code {proc.returncode}")

    logger.info("FFMPEG SUCCESS")


# -------------------------------------------------------------------
# FastAPI app
# -------------------------------------------------------------------

app = FastAPI(
    title="Overlay Service",
    version="1.0.0",
    description="Simple service to add text overlays to MP4 videos.",
)


@app.get("/", response_class=PlainTextResponse, summary="Health")
async def health() -> str:
    """Basic health check endpoint."""
    return "OK"


# -------------------------------------------------------------------
# Debug endpoint - does NOT run ffmpeg
# -------------------------------------------------------------------

@app.post("/overlay-debug", summary="Debug upload handling")
async def overlay_debug(
    video: UploadFile = File(...),
    overlay_text: str = Form(...),
):
    """
    Debug endpoint:

    - Saves the uploaded file to a temp dir
    - Logs details
    - Returns metadata (no ffmpeg processing)
    """
    workdir = Path(tempfile.mkdtemp(prefix="overlay-"))
    logger.info(
        "overlay-debug: filename=%s content_type=%s workdir=%s",
        video.filename,
        video.content_type,
        workdir,
    )

    input_path = workdir / "input.mp4"

    contents = await video.read()
    with input_path.open("wb") as f:
        f.write(contents)

    size = input_path.stat().st_size
    logger.info("overlay-debug: saved input.mp4 size=%d bytes", size)

    return {
        "workdir": str(workdir),
        "input_size": size,
        "overlay_text": overlay_text,
    }


# -------------------------------------------------------------------
# Main overlay endpoint (used by n8n + Swagger)
# -------------------------------------------------------------------

@app.post("/overlay", summary="Overlay text onto video")
async def overlay(
    video: UploadFile = File(...),
    overlay_text: str = Form(...),
):
    """
    Accepts:
      - video: uploaded MP4 file (binary)
      - overlay_text: text to overlay (can contain newlines)

    Returns:
      - processed MP4 with centered text overlay.
    """
    workdir = Path(tempfile.mkdtemp(prefix="overlay-"))
    logger.info("overlay: START workdir=%s", workdir)

    input_path = workdir / "input.mp4"
    output_path = workdir / "output.mp4"

    try:
        logger.info(
            "overlay: upload filename=%s content_type=%s",
            video.filename,
            video.content_type,
        )

        # Save uploaded video
        contents = await video.read()
        with input_path.open("wb") as f:
            f.write(contents)

        size = input_path.stat().st_size
        logger.info("overlay: saved input.mp4 size=%d bytes", size)

        # Run ffmpeg
        run_ffmpeg(str(input_path), str(output_path), overlay_text)

        if not output_path.exists():
            logger.error("overlay: output.mp4 not found after ffmpeg success")
            raise RuntimeError("output file missing")

        out_size = output_path.stat().st_size
        logger.info("overlay: SUCCESS output.mp4 size=%d bytes", out_size)

        # Return file (we intentionally do NOT delete workdir on success to
        # avoid race conditions with FileResponse; temp dirs are ephemeral)
        return FileResponse(
            path=str(output_path),
            media_type="video/mp4",
            filename="output.mp4",
        )

    except Exception as exc:
        logger.exception("overlay: ERROR: %s", exc)

        # Best-effort cleanup on error only
        try:
            shutil.rmtree(workdir, ignore_errors=True)
        except Exception:
            pass

        raise HTTPException(status_code=500, detail="Internal Server Error")


# For local debugging (not used on Render, which runs via start command)
if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)


