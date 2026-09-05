from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
import edge_tts
import os
import tempfile
import subprocess


app = FastAPI(title="Edge TTS API")


# =========================================================
# Models
# =========================================================

class TTSRequest(BaseModel):
    text: str
    voice: str = "ar-EG-SalmaNeural"
    rate: str = "+0%"
    pitch: str = "+0Hz"


# =========================================================
# Home
# =========================================================

@app.get("/")
async def home():
    return {
        "status": "online",
        "service": "Edge TTS API",
        "podcast": True
    }


# =========================================================
# Existing TTS Endpoint
# =========================================================

@app.post("/tts")
async def tts(data: TTSRequest):

    if not data.text or not data.text.strip():
        raise HTTPException(
            status_code=400,
            detail="Text is empty"
        )

    temp_file = tempfile.NamedTemporaryFile(
        suffix=".mp3",
        delete=False
    )

    temp_file.close()

    try:
        communicate = edge_tts.Communicate(
            text=data.text,
            voice=data.voice,
            rate=data.rate,
            pitch=data.pitch
        )

        await communicate.save(temp_file.name)

        with open(temp_file.name, "rb") as f:
            audio = f.read()

        return Response(
            content=audio,
            media_type="audio/mpeg",
            headers={
                "Content-Disposition": 'attachment; filename="speech.mp3"'
            }
        )

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )

    finally:
        if os.path.exists(temp_file.name):
            os.remove(temp_file.name)


# =========================================================
# FFmpeg Check
# =========================================================

@app.get("/ffmpeg-check")
async def ffmpeg_check():

    try:

        result = subprocess.run(
            ["ffmpeg", "-version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=10
        )

        if result.returncode == 0:

            first_line = result.stdout.splitlines()[0]

            return {
                "ffmpeg": True,
                "status": "available",
                "version": first_line
            }

        return {
            "ffmpeg": False,
            "status": "error",
            "details": result.stderr
        }

    except FileNotFoundError:

        return {
            "ffmpeg": False,
            "status": "not_installed",
            "message": "FFmpeg is not installed on the server."
        }

    except Exception as e:

        return {
            "ffmpeg": False,
            "status": "error",
            "details": str(e)
        }


# =========================================================
# Generate Audio Segment
# =========================================================

async def generate_segment(
    text: str,
    voice: str,
    output_file: str
):

    communicate = edge_tts.Communicate(
        text=text,
        voice=voice
    )

    await communicate.save(output_file)

    if not os.path.exists(output_file):
        raise Exception(
            "Audio file was not created."
        )

    if os.path.getsize(output_file) == 0:
        raise Exception(
            "Audio file is empty."
        )


# =========================================================
# Podcast Test
# =========================================================

@app.get("/podcast-test")
async def podcast_test():

    if not os.path.exists("/tmp"):
        os.makedirs("/tmp", exist_ok=True)

    segment1 = tempfile.NamedTemporaryFile(
        suffix=".mp3",
        delete=False
    )

    segment2 = tempfile.NamedTemporaryFile(
        suffix=".mp3",
        delete=False
    )

    output = tempfile.NamedTemporaryFile(
        suffix=".mp3",
        delete=False
    )

    segment1.close()
    segment2.close()
    output.close()

    files = [
        segment1.name,
        segment2.name,
        output.name
    ]

    try:

        # -------------------------------------------------
        # Speaker 1 - Shakir
        # -------------------------------------------------

        await generate_segment(
            text=(
                "السلام عليكم ورحمة الله وبركاته، "
                "ومرحباً بكم في بودكاست بيان."
            ),
            voice="ar-EG-ShakirNeural",
            output_file=segment1.name
        )

        # -------------------------------------------------
        # Speaker 2 - Salma
        # -------------------------------------------------

        await generate_segment(
            text=(
                "وعليكم السلام ورحمة الله وبركاته، "
                "أهلاً بكم جميعاً في بودكاست بيان."
            ),
            voice="ar-EG-SalmaNeural",
            output_file=segment2.name
        )

        # -------------------------------------------------
        # Create FFmpeg concat list
        # -------------------------------------------------

        concat_file = tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".txt",
            delete=False,
            encoding="utf-8"
        )

        concat_file.write(
            "file '" + segment1.name.replace("'", "'\\''") + "'\n"
        )

        concat_file.write(
            "file '" + segment2.name.replace("'", "'\\''") + "'\n"
        )

        concat_file.close()

        # -------------------------------------------------
        # FFmpeg Merge
        # -------------------------------------------------

        command = [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            concat_file.name,
            "-c",
            "copy",
            output.name
        ]

        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=120
        )

        # -------------------------------------------------
        # Check FFmpeg
        # -------------------------------------------------

        if result.returncode != 0:

            raise Exception(
                "FFmpeg failed:\n\n" +
                result.stderr
            )

        # -------------------------------------------------
        # Check final file
        # -------------------------------------------------

        if not os.path.exists(output.name):

            raise Exception(
                "FFmpeg did not create the output file."
            )

        if os.path.getsize(output.name) == 0:

            raise Exception(
                "Final podcast file is empty."
            )

        # -------------------------------------------------
        # Read final MP3
        # -------------------------------------------------

        with open(output.name, "rb") as f:
            audio = f.read()

        return Response(
            content=audio,
            media_type="audio/mpeg",
            headers={
                "Content-Disposition":
                    'attachment; filename="Bayan_Podcast_Test.mp3"'
            }
        )

    except FileNotFoundError:

        raise HTTPException(
            status_code=500,
            detail=(
                "FFmpeg is not installed on the server. "
                "Install FFmpeg first."
            )
        )

    except subprocess.TimeoutExpired:

        raise HTTPException(
            status_code=500,
            detail="FFmpeg operation timed out."
        )

    except Exception as e:

        raise HTTPException(
            status_code=500,
            detail=str(e)
        )

    finally:

        # Remove generated temporary files

        for file in files:

            try:

                if os.path.exists(file):
                    os.remove(file)

            except:
                pass

        # Remove concat list

        try:

            if "concat_file" in locals():
                if os.path.exists(concat_file.name):
                    os.remove(concat_file.name)

        except:
            pass
