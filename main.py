from fastapi import FastAPI
from fastapi.responses import JSONResponse
import edge_tts
import os
import shutil
import tempfile
import traceback

# ============================================================
# APP
# ============================================================

app = FastAPI(
    title="Edge TTS Audio Processing Server",
    version="2.0.0"
)


# ============================================================
# PYAV TEST
# ============================================================

def check_pyav():
    """
    اختبار حقيقي لتحميل PyAV.
    لا يعتمد على وجود أمر ffmpeg في النظام.
    """
    try:
        import av

        return {
            "installed": True,
            "version": av.__version__,
            "status": "working"
        }

    except Exception as e:
        return {
            "installed": False,
            "version": None,
            "status": "error",
            "error": str(e)
        }


# ============================================================
# EDGE TTS TEST
# ============================================================

def check_edge_tts():
    try:
        version = getattr(edge_tts, "__version__", "unknown")

        return {
            "installed": True,
            "version": version,
            "status": "working"
        }

    except Exception as e:
        return {
            "installed": False,
            "version": None,
            "status": "error",
            "error": str(e)
        }


# ============================================================
# FFMPEG COMMAND TEST
# ============================================================

def check_ffmpeg_command():
    """
    هذا الاختبار يبحث عن برنامج ffmpeg كأمر نظام.
    
    ملاحظة:
    وجود PyAV لا يعني أن أمر ffmpeg نفسه موجود.
    """

    path = shutil.which("ffmpeg")

    return {
        "installed": path is not None,
        "path": path
    }


# ============================================================
# ROOT
# ============================================================

@app.get("/")
async def root():

    pyav = check_pyav()
    edge = check_edge_tts()
    ffmpeg = check_ffmpeg_command()

    return {
        "name": "Edge TTS Audio Processing Server",

        "status": "online",

        "edge_tts": edge["installed"],

        "edge_tts_version": edge["version"],

        "pyav": pyav["installed"],

        "pyav_version": pyav["version"],

        "pyav_status": pyav["status"],

        "ffmpeg_command": ffmpeg["installed"],

        "ffmpeg_path": ffmpeg["path"],

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
        ],

        "features": [
            "Edge-TTS",
            "PyAV",
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
# PYAV TEST ENDPOINT
# ============================================================

@app.get("/pyav-test")
async def pyav_test():

    result = check_pyav()

    if not result["installed"]:

        return JSONResponse(
            status_code=500,
            content={
                "pyav": False,
                "status": "failed",
                "error": result.get("error")
            }
        )

    return {
        "pyav": True,
        "version": result["version"],
        "status": "PyAV is working correctly"
    }


# ============================================================
# EDGE TTS TEST ENDPOINT
# ============================================================

@app.get("/edge-test")
async def edge_test():

    result = check_edge_tts()

    return {
        "edge_tts": result["installed"],
        "version": result["version"],
        "status": result["status"]
    }


# ============================================================
# FFMPEG COMMAND TEST
# ============================================================

@app.get("/ffmpeg-test")
async def ffmpeg_test():

    result = check_ffmpeg_command()

    return {
        "ffmpeg_command": result["installed"],
        "path": result["path"],
        "note": (
            "This only checks for the ffmpeg executable. "
            "PyAV does not require the executable."
        )
    }


# ============================================================
# PYAV AUDIO CREATION TEST
# ============================================================

@app.get("/pyav-audio-test")
async def pyav_audio_test():

    """
    اختبار أقوى من مجرد import av.

    يقوم بإنشاء WAV حقيقي باستخدام PyAV
    داخل ملف مؤقت.

    لا يستخدم ffmpeg command.
    """

    try:

        import av
        import math
        import struct

        sample_rate = 44100
        duration = 1.0
        frequency = 440.0

        samples = int(sample_rate * duration)

        temp_dir = tempfile.mkdtemp(prefix="pyav_test_")

        wav_path = os.path.join(
            temp_dir,
            "pyav_test.wav"
        )

        # ----------------------------------------------------
        # إنشاء ملف WAV
        # ----------------------------------------------------

        container = av.open(
            wav_path,
            mode="w",
            format="wav"
        )

        stream = container.add_stream(
            "pcm_s16le",
            rate=sample_rate
        )

        stream.layout = "mono"

        # ----------------------------------------------------
        # إنشاء صوت Sine Wave
        # ----------------------------------------------------

        pcm_data = bytearray()

        for i in range(samples):

            value = int(
                16000 *
                math.sin(
                    2.0 *
                    math.pi *
                    frequency *
                    i /
                    sample_rate
                )
            )

            pcm_data.extend(
                struct.pack(
                    "<h",
                    value
                )
            )

        # ----------------------------------------------------
        # تحويل البيانات إلى AudioFrame
        # ----------------------------------------------------

        frame = av.AudioFrame(
            format="s16",
            layout="mono",
            samples=samples
        )

        frame.sample_rate = sample_rate

        frame.planes[0].update(
            bytes(pcm_data)
        )

        # ----------------------------------------------------
        # Encoding
        # ----------------------------------------------------

        for packet in stream.encode(frame):

            container.mux(packet)

        # Flush

        for packet in stream.encode():

            container.mux(packet)

        container.close()

        # ----------------------------------------------------
        # التحقق من الملف
        # ----------------------------------------------------

        file_exists = os.path.exists(wav_path)

        file_size = (
            os.path.getsize(wav_path)
            if file_exists
            else 0
        )

        # ----------------------------------------------------
        # محاولة فتح الملف مرة أخرى
        # ----------------------------------------------------

        verify = None

        if file_exists:

            verify_container = av.open(
                wav_path,
                mode="r"
            )

            verify_stream = (
                verify_container.streams.audio[0]
                if verify_container.streams.audio
                else None
            )

            verify = {
                "audio_stream_found": verify_stream is not None,
                "format": (
                    str(verify_container.format.name)
                    if verify_container.format
                    else None
                ),
                "sample_rate": (
                    verify_stream.rate
                    if verify_stream
                    else None
                )
            }

            verify_container.close()

        # ----------------------------------------------------
        # Cleanup
        # ----------------------------------------------------

        shutil.rmtree(
            temp_dir,
            ignore_errors=True
        )

        return {
            "success": True,

            "pyav": True,

            "pyav_version": av.__version__,

            "test": "WAV creation",

            "format": "wav",

            "sample_rate": sample_rate,

            "channels": 1,

            "duration_seconds": duration,

            "file_created": file_exists,

            "file_size_bytes": file_size,

            "verification": verify,

            "message": (
                "PyAV successfully created and "
                "re-opened a WAV audio file."
            )
        }

    except Exception as e:

        traceback.print_exc()

        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "pyav": False,
                "test": "WAV creation",
                "error": str(e),
                "traceback": traceback.format_exc()
            }
        )


# ============================================================
# HEALTH CHECK
# ============================================================

@app.get("/health")
async def health():

    pyav = check_pyav()
    edge = check_edge_tts()

    return {
        "status": "healthy",

        "services": {
            "fastapi": True,

            "edge_tts": edge["installed"],

            "pyav": pyav["installed"]
        },

        "versions": {
            "edge_tts": edge["version"],

            "pyav": pyav["version"]
        }
    }


# ============================================================
# STARTUP
# ============================================================

@app.on_event("startup")
async def startup_event():

    print("=" * 60)
    print("Edge TTS Audio Processing Server")
    print("=" * 60)

    # Edge TTS
    edge = check_edge_tts()

    print(
        "Edge-TTS:",
        edge["installed"],
        edge["version"]
    )

    # PyAV
    pyav = check_pyav()

    print(
        "PyAV:",
        pyav["installed"],
        pyav["version"]
    )

    # ffmpeg executable
    ffmpeg = check_ffmpeg_command()

    print(
        "FFmpeg executable:",
        ffmpeg["installed"],
        ffmpeg["path"]
    )

    print("=" * 60)


# ============================================================
# MAIN
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
