import logging
import shutil
import subprocess
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse, JSONResponse

# -----------------------------------------------------------------------------
# App setup
# -----------------------------------------------------------------------------
app = FastAPI(title="Overlay Service", version="1.0.0")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("overlay-service")

BASE_TMP = Path("/tmp")


# -----------------------------------------------------------------------------
# Health
# -----------------------------------------------------------------------------
@app.get("/", response_class=PlainTextResponse, summary="Health")
async def health() -> str:
    """
    Simple health endpoint.
    Render hits this with GET and HEAD; 405 on HEAD is okay but noisy.
    """
    return "OK"


# -----------------------------------------------------------------------------
# Helper: run ffmpeg
# -----------------------------------------------------------------------------
def run_ffmpeg(input_path: Path, output_path: Path, overlay_text: str) -> None:
    font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

    drawtext = (
        f"drawtext=fontfile='{font_path}':"
        f"text='{overlay_text}':"
        "fontcolor=white:fontsize=28:"
        "box=1:boxcolor=black@0.5:boxborderw=5:"
        "x=(w-text_w)/2:y=h-(text_h*2)"
    )

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_path),
        "-vf",
        drawtext,
        "-codec:a",
        "copy",
        str(output_path),
    ]

    logger.info("FFMPEG CMD: %s", " ".join(cmd))
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    logger.info("FFMPEG return code: %s", proc.returncode)

    if proc.returncode != 0:
        stderr_text = proc.stderr.decode(errors="ignore")
        logger.error("FFMPEG FAILED. First 4000 chars of stderr:\n%s", stderr_text[:4000])
        raise RuntimeError(f"ffmpeg failed with code {proc.returncode}")


# -----------------------------------------------------------------------------
# DEBUG ENDPOINT (no ffmpeg)
# -----------------------------------------------------------------------------
@app.post("/overlay-debug", summary="Debug upload only (no ffmpeg)")
async def overlay_debug(
    video: UploadFile = File(...),
    overlay_text: str = Form(...),
):
    """
    Debug helper: just saves the uploaded video to /tmp,
    logs a bunch of info, and returns JSON. No ffmpeg.
    """
    workdir = BASE_TMP / f"overlay-{uuid.uuid4().hex}"
    workdir.mkdir(parents=True, exist_ok=True)

    input_path = workdir / "input.mp4"

    logger.info(
        "overlay-debug: filename=%s content_type=%s workdir=%s",
        video.filename,
        video.content_type,
        workdir,
    )

    try:
        with input_path.open("wb") as f:
            shutil.copyfileobj(video.file, f)

        size = input_path.stat().st_size
        logger.info("overlay-debug: saved input.mp4 size=%s bytes", size)

        return {
            "workdir": str(workdir),
            "input_path": str(input_path),
            "input_size": size,
            "overlay_text": overlay_text,
        }
    finally:
        video.file.close()


# -----------------------------------------------------------------------------
# MAIN OVERLAY ENDPOINT
# -----------------------------------------------------------------------------
@app.post("/overlay", summary="Create overlayed video")
async def overlay(
    video: UploadFile = File(...),
    overlay_text: str = Form(...),
):
    """
    Accepts an MP4 and overlay text, runs ffmpeg, and returns the MP4.
    Loudly logs any errors and never tries to send a non-existent file.
    """
    workdir = BASE_TMP / f"overlay-{uuid.uuid4().hex}"
    workdir.mkdir(parents=True, exist_ok=True)

    input_path = workdir / "input.mp4"
    output_path = workdir / "output.mp4"

    logger.info("overlay: START workdir=%s", workdir)
    logger.info(
        "overlay: upload filename=%s content_type=%s",
        video.filename,
        video.content_type,
    )

    try:
        # Save uploaded file
        with input_path.open("wb") as f:
            shutil.copyfileobj(video.file, f)

        size = input_path.stat().st_size
        logger.info("overlay: saved input.mp4 size=%s bytes", size)

        # Run ffmpeg
        try:
            run_ffmpeg(input_path, output_path, overlay_text)
        except Exception as e:
            logger.exception("overlay: ffmpeg phase failed")
            return JSONResponse(
                status_code=500,
                content={"detail": f"ffmpeg error: {str(e)}"},
            )

        # Check output exists
        if not output_path.exists():
            logger.error(
                "overlay: output.mp4 missing after ffmpeg. Dir contents: %s",
                [p.name for p in workdir.iterdir()],
            )
            return JSONResponse(
                status_code=500,
                content={"detail": "output video was not created"},
            )

        logger.info(
            "overlay: SUCCESS output.mp4 size=%s bytes",
            output_path.stat().st_size,
        )

        # IMPORTANT: do NOT delete workdir yet; FileResponse streams lazily
        return FileResponse(
            path=str(output_path),
            media_type="video/mp4",
            filename="output.mp4",
        )

    finally:
        video.file.close()

