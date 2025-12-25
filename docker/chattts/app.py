"""
ChatTTS FastAPI Wrapper Service

Provides REST API endpoints for text-to-speech synthesis using ChatTTS.
"""

import io
import logging
import time
from contextlib import asynccontextmanager
from typing import Optional

import numpy as np
import torch
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from scipy.io import wavfile

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Global ChatTTS instance
chat_model = None
model_loaded = False
load_time: Optional[float] = None


class TTSRequest(BaseModel):
    """Request model for TTS synthesis."""
    text: str = Field(..., description="Text to synthesize", min_length=1, max_length=5000)
    speaker_id: str = Field(default="default", description="Speaker ID for voice selection")
    temperature: float = Field(default=0.3, ge=0.0, le=1.0, description="Sampling temperature")
    top_p: float = Field(default=0.7, ge=0.0, le=1.0, description="Top-p sampling parameter")
    top_k: int = Field(default=20, ge=1, le=100, description="Top-k sampling parameter")
    speed: float = Field(default=1.0, ge=0.5, le=2.0, description="Speech speed multiplier")


class HealthResponse(BaseModel):
    """Response model for health check."""
    status: str
    model_loaded: bool
    gpu_available: bool
    gpu_name: Optional[str] = None
    load_time_seconds: Optional[float] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load ChatTTS model on startup."""
    global chat_model, model_loaded, load_time

    logger.info("Starting ChatTTS service...")
    start_time = time.time()

    try:
        import ChatTTS

        chat_model = ChatTTS.Chat()

        # Load models with GPU if available
        if torch.cuda.is_available():
            logger.info(f"CUDA available: {torch.cuda.get_device_name(0)}")
            chat_model.load(compile=False)  # compile=False for faster loading
        else:
            logger.warning("CUDA not available, using CPU (will be slower)")
            chat_model.load(compile=False, device="cpu")

        model_loaded = True
        load_time = time.time() - start_time
        logger.info(f"ChatTTS model loaded successfully in {load_time:.2f}s")

    except Exception as e:
        logger.error(f"Failed to load ChatTTS model: {e}")
        model_loaded = False

    yield

    # Cleanup on shutdown
    logger.info("Shutting down ChatTTS service...")
    chat_model = None


app = FastAPI(
    title="ChatTTS Service",
    description="Text-to-Speech API using ChatTTS",
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
        gpu_available=gpu_available,
        gpu_name=gpu_name,
        load_time_seconds=load_time
    )


@app.post("/v1/tts")
async def synthesize_speech(request: TTSRequest):
    """
    Synthesize speech from text.

    Returns audio as WAV format.
    """
    if not model_loaded or chat_model is None:
        raise HTTPException(
            status_code=503,
            detail="ChatTTS model not loaded. Service is starting up."
        )

    try:
        logger.info(f"Synthesizing text: {request.text[:50]}...")
        start_time = time.time()

        # Configure inference parameters
        params_infer_code = ChatTTS.Chat.InferCodeParams(
            temperature=request.temperature,
            top_P=request.top_p,
            top_K=request.top_k,
        )

        params_refine_text = ChatTTS.Chat.RefineTextParams(
            temperature=request.temperature,
            top_P=request.top_p,
            top_K=request.top_k,
        )

        # Generate speech
        wavs = chat_model.infer(
            [request.text],
            params_infer_code=params_infer_code,
            params_refine_text=params_refine_text,
        )

        if wavs is None or len(wavs) == 0:
            raise HTTPException(
                status_code=500,
                detail="Failed to generate audio"
            )

        # Get the first (and only) audio output
        audio_data = wavs[0]

        # Normalize and convert to int16
        audio_data = np.clip(audio_data, -1.0, 1.0)
        audio_int16 = (audio_data * 32767).astype(np.int16)

        # Apply speed adjustment if needed
        if request.speed != 1.0:
            # Simple resampling for speed adjustment
            original_len = len(audio_int16)
            new_len = int(original_len / request.speed)
            indices = np.linspace(0, original_len - 1, new_len).astype(int)
            audio_int16 = audio_int16[indices]

        # Write to WAV buffer
        buffer = io.BytesIO()
        sample_rate = 24000  # ChatTTS default sample rate
        wavfile.write(buffer, sample_rate, audio_int16)
        buffer.seek(0)

        synthesis_time = time.time() - start_time
        logger.info(f"Speech synthesized in {synthesis_time:.2f}s")

        return Response(
            content=buffer.read(),
            media_type="audio/wav",
            headers={
                "X-Synthesis-Time": str(synthesis_time),
                "X-Audio-Duration": str(len(audio_int16) / sample_rate)
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"TTS synthesis failed: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Speech synthesis failed: {str(e)}"
        )


@app.get("/")
async def root():
    """Root endpoint with API info."""
    return {
        "service": "ChatTTS",
        "version": "1.0.0",
        "endpoints": {
            "health": "/v1/health",
            "tts": "/v1/tts"
        }
    }
