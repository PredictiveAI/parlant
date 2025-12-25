"""
Whisper STT FastAPI Wrapper Service

Provides REST and WebSocket endpoints for speech-to-text transcription using OpenAI Whisper.
"""

import asyncio
import io
import logging
import os
import tempfile
import time
from contextlib import asynccontextmanager
from typing import Optional

import numpy as np
import torch
import whisper
from fastapi import FastAPI, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Global Whisper model instance
whisper_model = None
model_loaded = False
model_name: str = "base"
load_time: Optional[float] = None


class TranscriptionResponse(BaseModel):
    """Response model for transcription."""
    text: str
    language: Optional[str] = None
    confidence: Optional[float] = None
    duration_seconds: Optional[float] = None
    processing_time_seconds: Optional[float] = None


class HealthResponse(BaseModel):
    """Response model for health check."""
    status: str
    model_loaded: bool
    model_name: str
    gpu_available: bool
    gpu_name: Optional[str] = None
    load_time_seconds: Optional[float] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load Whisper model on startup."""
    global whisper_model, model_loaded, model_name, load_time

    model_name = os.environ.get("WHISPER_MODEL", "base")
    logger.info(f"Starting Whisper STT service with model: {model_name}")
    start_time = time.time()

    try:
        # Determine device
        if torch.cuda.is_available():
            device = "cuda"
            logger.info(f"CUDA available: {torch.cuda.get_device_name(0)}")
        else:
            device = "cpu"
            logger.warning("CUDA not available, using CPU (will be slower)")

        # Load model
        whisper_model = whisper.load_model(model_name, device=device)
        model_loaded = True
        load_time = time.time() - start_time
        logger.info(f"Whisper model '{model_name}' loaded successfully in {load_time:.2f}s")

    except Exception as e:
        logger.error(f"Failed to load Whisper model: {e}")
        model_loaded = False

    yield

    # Cleanup on shutdown
    logger.info("Shutting down Whisper STT service...")
    whisper_model = None


app = FastAPI(
    title="Whisper STT Service",
    description="Speech-to-Text API using OpenAI Whisper",
    version="1.0.0",
    lifespan=lifespan
)


@app.get("/v1/health", response_model=HealthResponse)
async def health_check():
    """Check service health and GPU status."""
    gpu_available = torch.cuda.is_available()
    gpu_name = torch.cuda.get_device_name(0) if gpu_available else None

    return HealthResponse(
        status="healthy" if model_loaded else "degraded",
        model_loaded=model_loaded,
        model_name=model_name,
        gpu_available=gpu_available,
        gpu_name=gpu_name,
        load_time_seconds=load_time
    )


@app.post("/v1/stt", response_model=TranscriptionResponse)
async def transcribe_audio(
    file: UploadFile = File(..., description="Audio file to transcribe"),
    language: Optional[str] = None
):
    """
    Transcribe audio file to text.

    Accepts WAV, MP3, M4A, FLAC, and other common audio formats.
    """
    if not model_loaded or whisper_model is None:
        raise HTTPException(
            status_code=503,
            detail="Whisper model not loaded. Service is starting up."
        )

    try:
        logger.info(f"Transcribing file: {file.filename}")
        start_time = time.time()

        # Read audio file
        audio_bytes = await file.read()

        # Save to temporary file (Whisper requires file path)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_file:
            tmp_file.write(audio_bytes)
            tmp_path = tmp_file.name

        try:
            # Transcribe
            options = {}
            if language:
                options["language"] = language

            result = whisper_model.transcribe(tmp_path, **options)

            processing_time = time.time() - start_time
            logger.info(f"Transcription completed in {processing_time:.2f}s")

            # Calculate audio duration from segments if available
            audio_duration = None
            if result.get("segments"):
                audio_duration = result["segments"][-1].get("end", 0)

            return TranscriptionResponse(
                text=result["text"].strip(),
                language=result.get("language"),
                confidence=None,  # Whisper doesn't provide overall confidence
                duration_seconds=audio_duration,
                processing_time_seconds=processing_time
            )

        finally:
            # Cleanup temporary file
            os.unlink(tmp_path)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Transcription failed: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Transcription failed: {str(e)}"
        )


@app.websocket("/v1/stt/stream")
async def websocket_transcribe(websocket: WebSocket):
    """
    WebSocket endpoint for streaming audio transcription.

    Protocol:
    - Client sends binary audio chunks (PCM 16-bit, 16kHz mono recommended)
    - Server sends JSON transcription results: {"text": "...", "is_final": true/false}
    - Client sends {"action": "end"} to finish the stream
    """
    if not model_loaded or whisper_model is None:
        await websocket.close(code=1013, reason="Service not ready")
        return

    await websocket.accept()
    logger.info("WebSocket connection established for streaming STT")

    audio_buffer = io.BytesIO()
    sample_rate = 16000  # Expected sample rate

    try:
        while True:
            try:
                # Receive data (can be binary audio or JSON control message)
                data = await asyncio.wait_for(websocket.receive(), timeout=30.0)

                if "bytes" in data:
                    # Binary audio data
                    audio_buffer.write(data["bytes"])

                    # Process when we have enough audio (e.g., 2 seconds worth)
                    buffer_duration = audio_buffer.tell() / (sample_rate * 2)  # 16-bit = 2 bytes

                    if buffer_duration >= 2.0:
                        # Process accumulated audio
                        audio_buffer.seek(0)
                        audio_bytes = audio_buffer.read()

                        # Convert to numpy array
                        audio_np = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0

                        # Transcribe
                        result = whisper_model.transcribe(
                            audio_np,
                            fp16=torch.cuda.is_available()
                        )

                        await websocket.send_json({
                            "text": result["text"].strip(),
                            "is_final": False,
                            "language": result.get("language")
                        })

                        # Reset buffer but keep last 0.5s for context overlap
                        overlap_bytes = int(0.5 * sample_rate * 2)
                        audio_buffer = io.BytesIO()
                        if len(audio_bytes) > overlap_bytes:
                            audio_buffer.write(audio_bytes[-overlap_bytes:])

                elif "text" in data:
                    # JSON control message
                    import json
                    try:
                        message = json.loads(data["text"])
                        if message.get("action") == "end":
                            # Process remaining audio
                            if audio_buffer.tell() > 0:
                                audio_buffer.seek(0)
                                audio_bytes = audio_buffer.read()
                                audio_np = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0

                                result = whisper_model.transcribe(
                                    audio_np,
                                    fp16=torch.cuda.is_available()
                                )

                                await websocket.send_json({
                                    "text": result["text"].strip(),
                                    "is_final": True,
                                    "language": result.get("language")
                                })

                            await websocket.close()
                            break
                    except json.JSONDecodeError:
                        pass

            except asyncio.TimeoutError:
                # Send keepalive
                await websocket.send_json({"type": "keepalive"})

    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        try:
            await websocket.close(code=1011, reason=str(e))
        except Exception:
            pass


@app.get("/")
async def root():
    """Root endpoint with API info."""
    return {
        "service": "Whisper STT",
        "version": "1.0.0",
        "model": model_name,
        "endpoints": {
            "health": "/v1/health",
            "stt": "/v1/stt",
            "stt_stream": "/v1/stt/stream (WebSocket)"
        }
    }
