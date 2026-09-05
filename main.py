from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from typing import Optional
from pathlib import Path
import edge_tts
import asyncio
import subprocess
import tempfile
import shutil
import uuid
import os
import zipfile
import time
import re


# ============================================================
# CONFIGURATION
# ============================================================

APP_NAME = "Edge TTS Audio Processing Server"

BASE_DIR = Path(__file__).resolve().parent

OUTPUT_DIR = BASE_DIR / "outputs"
TEMP_DIR = BASE_DIR / "temp"

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
TEMP_DIR.mkdir(parents=True, exist_ok=True)

# الاحتفاظ بالملفات الناتجة لمدة 6 ساعات
FILE_LIFETIME = 6 * 60 * 60


# ============================================================
# FASTAPI
# ============================================================

app = FastAPI(
    title=APP_NAME,
    version="2.0.0"
)


# ============================================================
# JOB STORAGE
# ============================================================

jobs = {}


# ============================================================
# REQUEST MODELS
# ============================================================

class GenerateRequest(BaseModel):

    text: str = Field(..., min_length=1)

    voice: str = "ar-SA-ShakirNeural"

    rate: str = "+0%"

    pitch: str = "+0Hz"

    output_format: str = "mp3"

    bitrate: int = 128

    sample_rate: int = 44100

    channels: int = 2

    # التقسيم بالدقائق
    split_minutes: Optional[int] = None

    # أو التقسيم بعدد الأحرف
    split_chars: Optional[int] = None

    # إنشاء ZIP
    create_zip: bool = False


# ============================================================
# UTILITIES
# ============================================================

def sanitize_filename(name: str) -> str:

    name = re.sub(r"[^\w\-]+", "_", name)

    if not name:
        name = "audio"

    return name[:80]


def ffmpeg_exists():

    try:

        subprocess.run(
            ["ffmpeg", "-version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True
        )

        return True

    except Exception:

        return False


def update_job(job_id, **kwargs):

    if job_id not in jobs:
        return

    jobs[job_id].update(kwargs)


# ============================================================
# EDGE TTS
# ============================================================

async def generate_edge_tts(
    text: str,
    voice: str,
    rate: str,
    pitch: str,
    output_file: Path,
):

    communicate = edge_tts.Communicate(
        text=text,
        voice=voice,
        rate=rate,
        pitch=pitch,
    )

    with open(output_file, "wb") as f:

        async for chunk in communicate.stream():

            if chunk["type"] == "audio":

                f.write(chunk["data"])


# ============================================================
# FFMPEG PROCESSING
# ============================================================

def process_audio(
    input_file: Path,
    output_file: Path,
    output_format: str,
    bitrate: int,
    sample_rate: int,
    channels: int,
):

    if output_format == "mp3":

        command = [
            "ffmpeg",
            "-y",
            "-i",
            str(input_file),

            "-vn",

            "-ac",
            str(channels),

            "-ar",
            str(sample_rate),

            "-b:a",
            f"{bitrate}k",

            "-codec:a",
            "libmp3lame",

            str(output_file)
        ]

    elif output_format == "wav":

        command = [
            "ffmpeg",
            "-y",
            "-i",
            str(input_file),

            "-vn",

            "-ac",
            str(channels),

            "-ar",
            str(sample_rate),

            "-codec:a",
            "pcm_s16le",

            str(output_file)
        ]

    else:

        raise ValueError(
            "Unsupported output format"
        )

    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    if result.returncode != 0:

        raise RuntimeError(
            result.stderr[-4000:]
        )


# ============================================================
# AUDIO DURATION
# ============================================================

def get_audio_duration(file_path: Path):

    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(file_path)
    ]

    try:

        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

        return float(result.stdout.strip())

    except Exception:

        return 0


# ============================================================
# SPLIT AUDIO BY TIME
# ============================================================

def split_audio_by_time(
    input_file: Path,
    output_dir: Path,
    minutes: int,
    output_format: str,
    bitrate: int,
    sample_rate: int,
    channels: int,
):

    output_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    duration = get_audio_duration(
        input_file
    )

    if duration <= 0:

        raise RuntimeError(
            "Unable to determine audio duration"
        )

    segment_seconds = minutes * 60

    parts = []

    start = 0
    index = 1

    while start < duration:

        output_file = (
            output_dir /
            f"part_{index:03d}.{output_format}"
        )

        command = [
            "ffmpeg",
            "-y",

            "-ss",
            str(start),

            "-i",
            str(input_file),

            "-t",
            str(segment_seconds),

            "-vn",

            "-ac",
            str(channels),

            "-ar",
            str(sample_rate),
        ]

        if output_format == "mp3":

            command += [
                "-codec:a",
                "libmp3lame",

                "-b:a",
                f"{bitrate}k",
            ]

        elif output_format == "wav":

            command += [
                "-codec:a",
                "pcm_s16le",
            ]

        command.append(
            str(output_file)
        )

        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

        if result.returncode != 0:

            raise RuntimeError(
                result.stderr[-4000:]
            )

        parts.append(output_file)

        start += segment_seconds
        index += 1

    return parts


# ============================================================
# ZIP
# ============================================================

def create_zip(
    files,
    zip_file: Path
):

    with zipfile.ZipFile(
        zip_file,
        "w",
        compression=zipfile.ZIP_DEFLATED
    ) as z:

        for file in files:

            z.write(
                file,
                arcname=file.name
            )

    return zip_file


# ============================================================
# CLEAN OLD FILES
# ============================================================

def cleanup_old_files():

    now = time.time()

    for directory in [
        OUTPUT_DIR,
        TEMP_DIR
    ]:

        if not directory.exists():
            continue

        for item in directory.iterdir():

            try:

                if now - item.stat().st_mtime > FILE_LIFETIME:

                    if item.is_dir():

                        shutil.rmtree(
                            item,
                            ignore_errors=True
                        )

                    else:

                        item.unlink(
                            missing_ok=True
                        )

            except Exception:

                pass


# ============================================================
# MAIN BACKGROUND JOB
# ============================================================

async def run_job(
    job_id: str,
    request: GenerateRequest
):

    work_dir = (
        TEMP_DIR /
        job_id
    )

    output_dir = (
        OUTPUT_DIR /
        job_id
    )

    work_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    output_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    try:

        update_job(
            job_id,
            status="generating",
            progress=5
        )

        # ----------------------------------------------------
        # Validate
        # ----------------------------------------------------

        if not ffmpeg_exists():

            raise RuntimeError(
                "FFmpeg is not installed or not available in PATH."
            )

        if request.output_format not in [
            "mp3",
            "wav"
        ]:

            raise ValueError(
                "output_format must be mp3 or wav"
            )

        if request.output_format == "mp3":

            if request.bitrate < 56:

                request.bitrate = 56

            if request.bitrate > 320:

                request.bitrate = 320

        if request.channels not in [
            1,
            2
        ]:

            request.channels = 2

        # ----------------------------------------------------
        # Generate Edge-TTS
        # ----------------------------------------------------

        raw_file = (
            work_dir /
            "edge_output.mp3"
        )

        await generate_edge_tts(
            text=request.text,
            voice=request.voice,
            rate=request.rate,
            pitch=request.pitch,
            output_file=raw_file
        )

        update_job(
            job_id,
            status="processing",
            progress=55
        )

        # ----------------------------------------------------
        # First conversion
        # ----------------------------------------------------

        processed_file = (
            work_dir /
            f"processed.{request.output_format}"
        )

        process_audio(
            input_file=raw_file,
            output_file=processed_file,
            output_format=request.output_format,
            bitrate=request.bitrate,
            sample_rate=request.sample_rate,
            channels=request.channels
        )

        # ----------------------------------------------------
        # SPLITTING
        # ----------------------------------------------------

        files = []

        if request.split_minutes:

            update_job(
                job_id,
                status="splitting",
                progress=70
            )

            files = split_audio_by_time(
                input_file=processed_file,
                output_dir=output_dir,
                minutes=request.split_minutes,
                output_format=request.output_format,
                bitrate=request.bitrate,
                sample_rate=request.sample_rate,
                channels=request.channels
            )

        else:

            final_file = (
                output_dir /
                f"audio.{request.output_format}"
            )

            shutil.copy2(
                processed_file,
                final_file
            )

            files = [
                final_file
            ]

        # ----------------------------------------------------
        # ZIP
        # ----------------------------------------------------

        zip_file = None

        if request.create_zip:

            update_job(
                job_id,
                status="creating_zip",
                progress=90
            )

            zip_file = (
                output_dir /
                "audio_files.zip"
            )

            create_zip(
                files,
                zip_file
            )

        # ----------------------------------------------------
        # DONE
        # ----------------------------------------------------

        update_job(
            job_id,
            status="completed",
            progress=100,
            files=[
                str(
                    file.relative_to(OUTPUT_DIR)
                )
                for file in files
            ],
            zip_file=(
                str(
                    zip_file.relative_to(
                        OUTPUT_DIR
                    )
                )
                if zip_file
                else None
            ),
            finished_at=time.time()
        )

    except Exception as e:

        update_job(
            job_id,
            status="failed",
            progress=0,
            error=str(e)
        )

    finally:

        shutil.rmtree(
            work_dir,
            ignore_errors=True
        )


# ============================================================
# CREATE JOB
# ============================================================

@app.post("/generate")
async def generate(
    request: GenerateRequest
):

    cleanup_old_files()

    if not request.text.strip():

        raise HTTPException(
            status_code=400,
            detail="Text cannot be empty."
        )

    job_id = str(
        uuid.uuid4()
    )

    jobs[job_id] = {

        "job_id": job_id,

        "status": "queued",

        "progress": 0,

        "created_at": time.time(),

        "finished_at": None,

        "error": None,

        "files": [],

        "zip_file": None
    }

    asyncio.create_task(
        run_job(
            job_id,
            request
        )
    )

    return {

        "success": True,

        "job_id": job_id,

        "status": "queued"
    }


# ============================================================
# JOB STATUS
# ============================================================

@app.get("/status/{job_id}")
async def status(
    job_id: str
):

    if job_id not in jobs:

        raise HTTPException(
            status_code=404,
            detail="Job not found."
        )

    return jobs[job_id]


# ============================================================
# DOWNLOAD SINGLE FILE
# ============================================================

@app.get("/download/{job_id}/{filename}")
async def download_file(
    job_id: str,
    filename: str
):

    file_path = (
        OUTPUT_DIR /
        job_id /
        filename
    )

    if not file_path.exists():

        raise HTTPException(
            status_code=404,
            detail="File not found."
        )

    return FileResponse(
        path=str(file_path),
        filename=file_path.name
    )


# ============================================================
# DOWNLOAD ZIP
# ============================================================

@app.get("/download-zip/{job_id}")
async def download_zip(
    job_id: str
):

    if job_id not in jobs:

        raise HTTPException(
            status_code=404,
            detail="Job not found."
        )

    zip_file = jobs[job_id].get(
        "zip_file"
    )

    if not zip_file:

        raise HTTPException(
            status_code=404,
            detail="ZIP file was not created."
        )

    file_path = (
        OUTPUT_DIR /
        zip_file
    )

    if not file_path.exists():

        raise HTTPException(
            status_code=404,
            detail="ZIP file not found."
        )

    return FileResponse(
        path=str(file_path),
        filename="audio_files.zip",
        media_type="application/zip"
    )


# ============================================================
# HEALTH CHECK
# ============================================================

@app.get("/")
async def root():

    return {

        "name": APP_NAME,

        "status": "online",

        "edge_tts": True,

        "ffmpeg": ffmpeg_exists(),

        "formats": [
            "mp3",
            "wav"
        ],

        "bitrates": [
            56,
            64,
            96,
            128,
            192,
            256,
            320
        ]
    }


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(
            os.environ.get(
                "PORT",
                8000
            )
        )
    )
