import asyncio
import io
import os
import re
import tempfile
import uuid
from typing import List, Optional

import av
import edge_tts

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response, JSONResponse
from pydantic import BaseModel, Field


app = FastAPI(
    title="Edge TTS Audio Processing Server",
    description="Edge-TTS + PyAV Audio Processing Server",
    version="1.0.0"
)


# =========================================================
# CONFIG
# =========================================================

SUPPORTED_FORMATS = ["mp3", "wav"]

SUPPORTED_BITRATES = [
    56,
    64,
    96,
    128,
    192,
    256,
    320
]

SUPPORTED_SAMPLE_RATES = [
    8000,
    16000,
    22050,
    24000,
    32000,
    44100,
    48000
]


# =========================================================
# MODELS
# =========================================================

class TTSRequest(BaseModel):

    text: str = Field(
        ...,
        min_length=1,
        description="Text to convert to speech"
    )

    voice: str = "ar-EG-SalmaNeural"

    rate: str = "+0%"

    pitch: str = "+0Hz"

    output_format: str = "mp3"

    bitrate: int = 128

    sample_rate: int = 24000

    channels: int = 1


class PodcastSegment(BaseModel):

    text: str = Field(
        ...,
        min_length=1
    )

    voice: str = "ar-EG-SalmaNeural"

    rate: str = "+0%"

    pitch: str = "+0Hz"


class PodcastRequest(BaseModel):

    segments: List[PodcastSegment]

    output_format: str = "mp3"

    bitrate: int = 128

    sample_rate: int = 24000

    channels: int = 1


# =========================================================
# TEXT HELPERS
# =========================================================

def clean_text(text: str) -> str:

    if not text:
        return ""

    text = text.strip()

    text = re.sub(
        r"\s+",
        " ",
        text
    )

    return text


def validate_audio_settings(
    output_format: str,
    bitrate: int,
    sample_rate: int,
    channels: int
):

    output_format = output_format.lower()

    if output_format not in SUPPORTED_FORMATS:

        raise HTTPException(
            status_code=400,
            detail=f"Unsupported format. Supported formats: {SUPPORTED_FORMATS}"
        )

    if bitrate not in SUPPORTED_BITRATES:

        raise HTTPException(
            status_code=400,
            detail=f"Unsupported bitrate. Supported bitrates: {SUPPORTED_BITRATES}"
        )

    if sample_rate not in SUPPORTED_SAMPLE_RATES:

        raise HTTPException(
            status_code=400,
            detail=f"Unsupported sample rate. Supported sample rates: {SUPPORTED_SAMPLE_RATES}"
        )

    if channels not in [1, 2]:

        raise HTTPException(
            status_code=400,
            detail="Channels must be 1 (Mono) or 2 (Stereo)"
        )


# =========================================================
# EDGE TTS
# =========================================================

async def generate_tts_audio(
    text: str,
    voice: str,
    rate: str,
    pitch: str
) -> bytes:

    text = clean_text(text)

    if not text:

        raise ValueError(
            "Text is empty"
        )

    communicate = edge_tts.Communicate(
        text=text,
        voice=voice,
        rate=rate,
        pitch=pitch
    )

    audio_buffer = io.BytesIO()

    async for chunk in communicate.stream():

        if chunk["type"] == "audio":

            audio_buffer.write(
                chunk["data"]
            )

    audio_data = audio_buffer.getvalue()

    if not audio_data:

        raise RuntimeError(
            "Edge-TTS returned empty audio"
        )

    return audio_data


# =========================================================
# PYAV AUDIO CONVERSION
# =========================================================

def convert_audio_with_pyav(
    input_audio: bytes,
    output_format: str,
    bitrate: int,
    sample_rate: int,
    channels: int
) -> bytes:

    output_format = output_format.lower()

    input_buffer = io.BytesIO(
        input_audio
    )

    output_buffer = io.BytesIO()

    input_container = None
    output_container = None

    try:

        # ================================================
        # OPEN INPUT
        # ================================================

        input_container = av.open(
            input_buffer,
            mode="r"
        )

        # ================================================
        # OPEN OUTPUT
        # ================================================

        output_container = av.open(
            output_buffer,
            mode="w",
            format=output_format
        )

        # ================================================
        # FIND AUDIO STREAM
        # ================================================

        input_stream = None

        for stream in input_container.streams:

            if stream.type == "audio":

                input_stream = stream
                break

        if input_stream is None:

            raise RuntimeError(
                "No audio stream found"
            )

        # ================================================
        # CODEC
        # ================================================

        if output_format == "mp3":

            codec_name = "mp3"

        elif output_format == "wav":

            codec_name = "pcm_s16le"

        else:

            raise RuntimeError(
                "Unsupported output format"
            )

        # ================================================
        # OUTPUT STREAM
        # ================================================

        output_stream = output_container.add_stream(
            codec_name,
            rate=sample_rate
        )

        # ================================================
        # CHANNEL LAYOUT
        # ================================================

        if channels == 1:

            output_stream.layout = "mono"

        else:

            output_stream.layout = "stereo"

        # ================================================
        # BITRATE
        # ================================================

        if output_format == "mp3":

            output_stream.bit_rate = (
                bitrate * 1000
            )

        # ================================================
        # RESAMPLER
        # ================================================

        target_layout = (
            "mono"
            if channels == 1
            else "stereo"
        )

        resampler = av.audio.resampler.AudioResampler(
            format="s16",
            layout=target_layout,
            rate=sample_rate
        )

        # ================================================
        # DECODE
        # ================================================

        for frame in input_container.decode(
            input_stream
        ):

            resampled_frames = resampler.resample(
                frame
            )

            if resampled_frames is None:

                continue

            if not isinstance(
                resampled_frames,
                list
            ):

                resampled_frames = [
                    resampled_frames
                ]

            for resampled_frame in resampled_frames:

                packets = output_stream.encode(
                    resampled_frame
                )

                for packet in packets:

                    output_container.mux(
                        packet
                    )

        # ================================================
        # FLUSH RESAMPLER
        # ================================================

        flushed_frames = resampler.resample(
            None
        )

        if flushed_frames:

            if not isinstance(
                flushed_frames,
                list
            ):

                flushed_frames = [
                    flushed_frames
                ]

            for frame in flushed_frames:

                packets = output_stream.encode(
                    frame
                )

                for packet in packets:

                    output_container.mux(
                        packet
                    )

        # ================================================
        # FLUSH ENCODER
        # ================================================

        packets = output_stream.encode(
            None
        )

        for packet in packets:

            output_container.mux(
                packet
            )

        # ================================================
        # CLOSE OUTPUT
        # ================================================

        output_container.close()

        output_container = None

        return output_buffer.getvalue()

    finally:

        if input_container:

            try:

                input_container.close()

            except Exception:

                pass

        if output_container:

            try:

                output_container.close()

            except Exception:

                pass


# =========================================================
# MULTIPLE AUDIO CONCATENATION
# =========================================================

def merge_audio_files(
    audio_files: List[bytes],
    output_format: str,
    bitrate: int,
    sample_rate: int,
    channels: int
) -> bytes:

    if not audio_files:

        raise RuntimeError(
            "No audio files to merge"
        )

    output_format = output_format.lower()

    output_buffer = io.BytesIO()

    output_container = None

    try:

        # ================================================
        # OPEN OUTPUT
        # ================================================

        output_container = av.open(
            output_buffer,
            mode="w",
            format=output_format
        )

        # ================================================
        # SELECT CODEC
        # ================================================

        if output_format == "mp3":

            codec_name = "mp3"

        elif output_format == "wav":

            codec_name = "pcm_s16le"

        else:

            raise RuntimeError(
                "Unsupported output format"
            )

        # ================================================
        # CREATE OUTPUT STREAM
        # ================================================

        output_stream = output_container.add_stream(
            codec_name,
            rate=sample_rate
        )

        if channels == 1:

            output_stream.layout = "mono"

        else:

            output_stream.layout = "stereo"

        if output_format == "mp3":

            output_stream.bit_rate = (
                bitrate * 1000
            )

        target_layout = (
            "mono"
            if channels == 1
            else "stereo"
        )

        # ================================================
        # PROCESS EVERY AUDIO FILE
        # ================================================

        for audio_data in audio_files:

            input_buffer = io.BytesIO(
                audio_data
            )

            input_container = None

            try:

                input_container = av.open(
                    input_buffer,
                    mode="r"
                )

                input_stream = None

                for stream in input_container.streams:

                    if stream.type == "audio":

                        input_stream = stream
                        break

                if input_stream is None:

                    continue

                # ========================================
                # RESAMPLER
                # ========================================

                resampler = av.audio.resampler.AudioResampler(
                    format="s16",
                    layout=target_layout,
                    rate=sample_rate
                )

                # ========================================
                # DECODE AUDIO
                # ========================================

                for frame in input_container.decode(
                    input_stream
                ):

                    frames = resampler.resample(
                        frame
                    )

                    if frames is None:

                        continue

                    if not isinstance(
                        frames,
                        list
                    ):

                        frames = [frames]

                    for processed_frame in frames:

                        packets = output_stream.encode(
                            processed_frame
                        )

                        for packet in packets:

                            output_container.mux(
                                packet
                            )

                # ========================================
                # FLUSH RESAMPLER
                # ========================================

                frames = resampler.resample(
                    None
                )

                if frames:

                    if not isinstance(
                        frames,
                        list
                    ):

                        frames = [frames]

                    for processed_frame in frames:

                        packets = output_stream.encode(
                            processed_frame
                        )

                        for packet in packets:

                            output_container.mux(
                                packet
                            )

            finally:

                if input_container:

                    try:

                        input_container.close()

                    except Exception:

                        pass

        # ================================================
        # FLUSH ENCODER
        # ================================================

        packets = output_stream.encode(
            None
        )

        for packet in packets:

            output_container.mux(
                packet
            )

        output_container.close()

        output_container = None

        final_audio = output_buffer.getvalue()

        if not final_audio:

            raise RuntimeError(
                "Final merged audio is empty"
            )

        return final_audio

    finally:

        if output_container:

            try:

                output_container.close()

            except Exception:

                pass


# =========================================================
# HOME
# =========================================================

@app.get("/")
async def home():

    return {
        "name": "Edge TTS Audio Processing Server",

        "status": "online",

        "edge_tts": True,

        "pyav": True,

        "ffmpeg_command": False,

        "formats": SUPPORTED_FORMATS,

        "bitrates": SUPPORTED_BITRATES,

        "sample_rates": SUPPORTED_SAMPLE_RATES,

        "channels": {
            "1": "Mono",
            "2": "Stereo"
        },

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
        ],

        "endpoints": {

            "tts": "/tts",

            "podcast": "/podcast",

            "health": "/health"
        }
    }


# =========================================================
# HEALTH
# =========================================================

@app.get("/health")
async def health():

    return {
        "status": "ok",

        "edge_tts": True,

        "pyav": True
    }


# =========================================================
# TTS ENDPOINT
# =========================================================

@app.post("/tts")
async def tts(
    request: TTSRequest
):

    validate_audio_settings(
        request.output_format,
        request.bitrate,
        request.sample_rate,
        request.channels
    )

    try:

        # ================================================
        # GENERATE EDGE AUDIO
        # ================================================

        original_audio = await generate_tts_audio(

            text=request.text,

            voice=request.voice,

            rate=request.rate,

            pitch=request.pitch
        )

        # ================================================
        # CONVERT USING PYAV
        # ================================================

        final_audio = convert_audio_with_pyav(

            input_audio=original_audio,

            output_format=request.output_format,

            bitrate=request.bitrate,

            sample_rate=request.sample_rate,

            channels=request.channels
        )

        # ================================================
        # RESPONSE
        # ================================================

        if request.output_format == "mp3":

            media_type = "audio/mpeg"

        else:

            media_type = "audio/wav"

        filename = (
            f"edge_tts_{uuid.uuid4().hex[:8]}"
            f".{request.output_format}"
        )

        return Response(

            content=final_audio,

            media_type=media_type,

            headers={

                "Content-Disposition":
                    f'attachment; filename="{filename}"',

                "X-Audio-Format":
                    request.output_format,

                "X-Bitrate":
                    str(request.bitrate),

                "X-Sample-Rate":
                    str(request.sample_rate),

                "X-Channels":
                    str(request.channels)
            }
        )

    except Exception as error:

        raise HTTPException(

            status_code=500,

            detail=str(error)
        )


# =========================================================
# PODCAST ENDPOINT
# =========================================================

@app.post("/podcast")
async def podcast(
    request: PodcastRequest
):

    if not request.segments:

        raise HTTPException(

            status_code=400,

            detail="Podcast must contain at least one segment"
        )

    validate_audio_settings(

        request.output_format,

        request.bitrate,

        request.sample_rate,

        request.channels
    )

    try:

        audio_files = []

        # ================================================
        # GENERATE EVERY SEGMENT
        # ================================================

        for index, segment in enumerate(
            request.segments
        ):

            audio_data = await generate_tts_audio(

                text=segment.text,

                voice=segment.voice,

                rate=segment.rate,

                pitch=segment.pitch
            )

            audio_files.append(
                audio_data
            )

        # ================================================
        # MERGE ALL SEGMENTS
        # ================================================

        final_audio = merge_audio_files(

            audio_files=audio_files,

            output_format=request.output_format,

            bitrate=request.bitrate,

            sample_rate=request.sample_rate,

            channels=request.channels
        )

        # ================================================
        # RESPONSE
        # ================================================

        if request.output_format == "mp3":

            media_type = "audio/mpeg"

        else:

            media_type = "audio/wav"

        filename = (
            f"podcast_{uuid.uuid4().hex[:8]}"
            f".{request.output_format}"
        )

        return Response(

            content=final_audio,

            media_type=media_type,

            headers={

                "Content-Disposition":
                    f'attachment; filename="{filename}"',

                "X-Segments":
                    str(len(request.segments)),

                "X-Audio-Format":
                    request.output_format,

                "X-Bitrate":
                    str(request.bitrate),

                "X-Sample-Rate":
                    str(request.sample_rate),

                "X-Channels":
                    str(request.channels)
            }
        )

    except Exception as error:

        raise HTTPException(

            status_code=500,

            detail=str(error)
        )


# =========================================================
# SERVER INFO
# =========================================================

@app.get("/info")
async def info():

    return {

        "server": "Edge TTS Audio Processing Server",

        "edge_tts": True,

        "pyav": True,

        "ffmpeg_command": False,

        "audio_processing": True,

        "podcast_support": True,

        "multi_speaker": True,

        "formats": SUPPORTED_FORMATS,

        "bitrates": SUPPORTED_BITRATES,

        "sample_rates": SUPPORTED_SAMPLE_RATES
    }
