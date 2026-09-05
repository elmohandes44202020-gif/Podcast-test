from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from pathlib import Path
import edge_tts
import asyncio
import subprocess
import tempfile
import uuid
import os
import shutil
import re

app = FastAPI(
    title="Edge TTS Audio Processing Server",
    version="2.0.0"
)

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "outputs"
TEMP_DIR = BASE_DIR / "temp"

OUTPUT_DIR.mkdir(exist_ok=True)
TEMP_DIR.mkdir(exist_ok=True)


# ============================================================
# Models
# ============================================================

class GenerateRequest(BaseModel):
    text: str = Field(..., min_length=1)
    voice: str = "ar-SA-HamedNeural"
    rate: str = "+0%"
    pitch: str = "+0Hz"
    output_format: str = "mp3"
    bitrate: str = "192k"
    sample_rate: int = 44100
    channels: int = 2


class PodcastSegment(BaseModel):
    speaker: str
    text: str = Field(..., min_length=1)


class PodcastRequest(BaseModel):
    segments: list[PodcastSegment]

    speaker_voices: dict[str, str] = {
        "speaker1": "ar-SA-HamedNeural",
        "speaker2": "ar-SA-ZariyahNeural"
    }

    speaker_rates: dict[str, str] = {}
    speaker_pitches: dict[str, str] = {}

    output_format: str = "mp3"
    bitrate: str = "192k"
    sample_rate: int = 44100
    channels: int = 2

    silence_ms: int = 300


# ============================================================
# Helpers
# ============================================================

def ffmpeg_path():
    return shutil.which("ffmpeg")


def validate_format(fmt: str):
    fmt = fmt.lower()

    if fmt not in ("mp3", "wav"):
        raise HTTPException(
            status_code=400,
            detail="output_format must be mp3 or wav"
        )

    return fmt


def validate_audio_settings(sample_rate: int, channels: int):
    allowed_rates = {
        8000,
        16000,
        22050,
        24000,
        32000,
        44100,
        48000
    }

    if sample_rate not in allowed_rates:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported sample_rate. Allowed: {sorted(allowed_rates)}"
        )

    if channels not in (1, 2):
        raise HTTPException(
            status_code=400,
            detail="channels must be 1 or 2"
        )


def safe_filename(name: str):
    name = re.sub(r'[\\/:*?"<>|]+', "_", name)
    name = name.strip()

    if not name:
        name = "audio"

    return name


# ============================================================
# Edge-TTS
# ============================================================

async def generate_tts(
    text: str,
    voice: str,
    rate: str,
    pitch: str,
    output_file: Path
):
    communicate = edge_tts.Communicate(
        text=text,
        voice=voice,
        rate=rate,
        pitch=pitch
    )

    with open(output_file, "wb") as f:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])


# ============================================================
# FFmpeg processing
# ============================================================

def convert_audio(
    input_file: Path,
    output_file: Path,
    output_format: str,
    bitrate: str,
    sample_rate: int,
    channels: int
):
    if not ffmpeg_path():
        raise RuntimeError("FFmpeg is not installed on the server.")

    command = [
        ffmpeg_path(),
        "-y",
        "-i",
        str(input_file),
        "-ar",
        str(sample_rate),
        "-ac",
        str(channels)
    ]

    if output_format == "mp3":
        command += [
            "-codec:a",
            "libmp3lame",
            "-b:a",
            bitrate
        ]

    elif output_format == "wav":
        command += [
            "-codec:a",
            "pcm_s16le"
        ]

    command.append(str(output_file))

    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    if result.returncode != 0:
        raise RuntimeError(
            "FFmpeg conversion failed:\n" + result.stderr[-4000:]
        )


def create_silence(
    output_file: Path,
    milliseconds: int,
    sample_rate: int,
    channels: int
):
    if milliseconds <= 0:
        return

    duration = milliseconds / 1000

    command = [
        ffmpeg_path(),
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"anullsrc=r={sample_rate}:cl={'mono' if channels == 1 else 'stereo'}",
        "-t",
        str(duration),
        "-c:a",
        "pcm_s16le",
        str(output_file)
    ]

    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    if result.returncode != 0:
        raise RuntimeError(
            "Could not create silence:\n" + result.stderr[-4000:]
        )


def concat_audio(
    files: list[Path],
    output_file: Path
):
    if not files:
        raise RuntimeError("No audio files to concatenate.")

    list_file = output_file.parent / f"{uuid.uuid4().hex}_concat.txt"

    try:
        with open(list_file, "w", encoding="utf-8") as f:
            for file in files:
                path = str(file.resolve())
                path = path.replace("'", "'\\''")
                f.write(f"file '{path}'\n")

        command = [
            ffmpeg_path(),
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_file),
            "-c",
            "copy",
            str(output_file)
        ]

        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

        if result.returncode != 0:
            raise RuntimeError(
                "FFmpeg concat failed:\n" + result.stderr[-4000:]
            )

    finally:
        list_file.unlink(missing_ok=True)


# ============================================================
# Health / Root
# ============================================================

@app.get("/")
async def root():
    return {
        "name": "Edge TTS Audio Processing Server",
        "status": "online",
        "edge_tts": True,
        "ffmpeg": ffmpeg_path() is not None,
        "formats": ["mp3", "wav"],
        "bitrates": [56, 64, 96, 128, 192, 256, 320],
        "features": [
            "Edge-TTS",
            "MP3",
            "WAV",
            "Bitrate",
            "Sample Rate",
            "Mono/Stereo",
            "Podcast",
            "Multi Speaker",
            "Audio Concatenation"
        ]
    }


# ============================================================
# FFmpeg status
# ============================================================

@app.get("/ffmpeg")
async def ffmpeg_status():

    path = ffmpeg_path()

    if not path:
        return {
            "installed": False,
            "message": "FFmpeg is not installed."
        }

    try:
        result = subprocess.run(
            [path, "-version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

        first_line = result.stdout.splitlines()[0] \
            if result.stdout else "Unknown"

        return {
            "installed": True,
            "path": path,
            "version": first_line
        }

    except Exception as e:
        return {
            "installed": True,
            "error": str(e)
        }


# ============================================================
# Normal TTS
# ============================================================

@app.post("/generate")
async def generate_audio(request: GenerateRequest):

    validate_format(request.output_format)

    validate_audio_settings(
        request.sample_rate,
        request.channels
    )

    if not ffmpeg_path():
        raise HTTPException(
            status_code=500,
            detail="FFmpeg is not installed on the server."
        )

    job_id = uuid.uuid4().hex

    temp_mp3 = TEMP_DIR / f"{job_id}.mp3"

    filename = safe_filename(f"audio_{job_id}")

    final_file = OUTPUT_DIR / (
        f"{filename}.{request.output_format}"
    )

    try:

        await generate_tts(
            request.text,
            request.voice,
            request.rate,
            request.pitch,
            temp_mp3
        )

        convert_audio(
            temp_mp3,
            final_file,
            request.output_format,
            request.bitrate,
            request.sample_rate,
            request.channels
        )

        return {
            "success": True,
            "type": "single",
            "filename": final_file.name,
            "download_url": f"/download/{final_file.name}"
        }

    except Exception as e:

        raise HTTPException(
            status_code=500,
            detail=str(e)
        )

    finally:

        temp_mp3.unlink(missing_ok=True)


# ============================================================
# Podcast
# ============================================================

@app.post("/podcast")
async def generate_podcast(request: PodcastRequest):

    if not request.segments:
        raise HTTPException(
            status_code=400,
            detail="Podcast must contain at least one segment."
        )

    validate_format(request.output_format)

    validate_audio_settings(
        request.sample_rate,
        request.channels
    )

    if not ffmpeg_path():
        raise HTTPException(
            status_code=500,
            detail="FFmpeg is not installed on the server."
        )

    job_id = uuid.uuid4().hex

    work_dir = TEMP_DIR / f"podcast_{job_id}"
    work_dir.mkdir(parents=True, exist_ok=True)

    generated_files = []

    try:

        # ----------------------------------------------------
        # Generate every dialogue segment separately
        # ----------------------------------------------------

        for index, segment in enumerate(request.segments):

            speaker = segment.speaker

            voice = request.speaker_voices.get(
                speaker,
                "ar-SA-HamedNeural"
            )

            rate = request.speaker_rates.get(
                speaker,
                "+0%"
            )

            pitch = request.speaker_pitches.get(
                speaker,
                "+0Hz"
            )

            segment_mp3 = (
                work_dir /
                f"{index:06d}_{speaker}.mp3"
            )

            await generate_tts(
                segment.text,
                voice,
                rate,
                pitch,
                segment_mp3
            )

            generated_files.append(segment_mp3)

            # ------------------------------------------------
            # Add silence between dialogue segments
            # ------------------------------------------------

            if (
                request.silence_ms > 0
                and index < len(request.segments) - 1
            ):

                silence_file = (
                    work_dir /
                    f"{index:06d}_silence.wav"
                )

                create_silence(
                    silence_file,
                    request.silence_ms,
                    request.sample_rate,
                    request.channels
                )

                generated_files.append(silence_file)

        # ----------------------------------------------------
        # Normalize everything to same WAV format
        # ----------------------------------------------------

        normalized_files = []

        for index, file in enumerate(generated_files):

            normalized = (
                work_dir /
                f"normalized_{index:06d}.wav"
            )

            convert_audio(
                file,
                normalized,
                "wav",
                request.bitrate,
                request.sample_rate,
                request.channels
            )

            normalized_files.append(normalized)

        # ----------------------------------------------------
        # Concatenate
        # ----------------------------------------------------

        merged_wav = (
            work_dir /
            "podcast_merged.wav"
        )

        concat_audio(
            normalized_files,
            merged_wav
        )

        # ----------------------------------------------------
        # Final output
        # ----------------------------------------------------

        final_name = safe_filename(
            f"podcast_{job_id}"
        )

        final_file = OUTPUT_DIR / (
            f"{final_name}.{request.output_format}"
        )

        if request.output_format == "wav":

            convert_audio(
                merged_wav,
                final_file,
                "wav",
                request.bitrate,
                request.sample_rate,
                request.channels
            )

        else:

            convert_audio(
                merged_wav,
                final_file,
                "mp3",
                request.bitrate,
                request.sample_rate,
                request.channels
            )

        return {
            "success": True,
            "type": "podcast",
            "segments": len(request.segments),
            "filename": final_file.name,
            "download_url": f"/download/{final_file.name}"
        }

    except Exception as e:

        raise HTTPException(
            status_code=500,
            detail=str(e)
        )

    finally:

        shutil.rmtree(
            work_dir,
            ignore_errors=True
        )


# ============================================================
# Download
# ============================================================

@app.get("/download/{filename}")
async def download_file(filename: str):

    filename = Path(filename).name

    file_path = OUTPUT_DIR / filename

    if not file_path.exists():
        raise HTTPException(
            status_code=404,
            detail="File not found."
        )

    return FileResponse(
        path=str(file_path),
        filename=file_path.name,
        media_type="application/octet-stream"
    )


# ============================================================
# Cleanup old files
# ============================================================

@app.post("/cleanup")
async def cleanup():

    removed = 0

    for file in OUTPUT_DIR.iterdir():

        if not file.is_file():
            continue

        try:
            file.unlink()
            removed += 1

        except Exception:
            pass

    return {
        "success": True,
        "removed_files": removed
    }


# ============================================================
# Run
# ============================================================

if __name__ == "__main__":

    import uvicorn

    port = int(
        os.environ.get(
            "PORT",
            "8000"
        )
    )

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port
    )
