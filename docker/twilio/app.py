"""
Twilio Integration Service

Handles TwiML webhooks, Media Streams, and orchestrates the voice pipeline:
STT (Whisper) → Agent (Parlant) → TTS (ChatTTS) → Twilio
"""

import asyncio
import base64
import io
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from typing import Dict, Optional

import httpx
import numpy as np
import redis.asyncio as redis
from fastapi import FastAPI, Form, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import PlainTextResponse, Response
from pydantic import BaseModel, Field
from twilio.rest import Client as TwilioClient
from twilio.twiml.voice_response import Connect, VoiceResponse

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration from environment
TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "")
TWILIO_PHONE_NUMBER = os.environ.get("TWILIO_PHONE_NUMBER", "")
PARLANT_URL = os.environ.get("PARLANT_URL", "http://parlant-api:8800")
PARLANT_AGENT_ID = os.environ.get("PARLANT_AGENT_ID", "")  # Required: Parlant agent ID
TTS_URL = os.environ.get("TTS_URL", "http://chattts:8001")
STT_URL = os.environ.get("STT_URL", "http://whisper-stt:8002")
REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379")
PUBLIC_URL = os.environ.get("PUBLIC_URL", "http://localhost:8003")
PARLANT_RESPONSE_TIMEOUT = int(os.environ.get("PARLANT_RESPONSE_TIMEOUT", "30"))  # seconds

# Global clients
twilio_client: Optional[TwilioClient] = None
redis_client: Optional[redis.Redis] = None
http_client: Optional[httpx.AsyncClient] = None

# Active call sessions
call_sessions: Dict[str, dict] = {}

# Parlant session tracking (call_sid -> parlant_session_id)
parlant_sessions: Dict[str, dict] = {}


class OutboundCallRequest(BaseModel):
    """Request model for initiating outbound calls."""
    to_number: str = Field(..., description="Phone number to call")
    agent_id: Optional[str] = Field(None, description="Parlant agent ID to use")
    initial_message: Optional[str] = Field(None, description="Initial message to speak")


class CallStatusResponse(BaseModel):
    """Response model for call status."""
    call_sid: str
    status: str
    from_number: str
    to_number: str
    direction: str
    duration: Optional[int] = None


class HealthResponse(BaseModel):
    """Response model for health check."""
    status: str
    twilio_configured: bool
    parlant_url: str
    tts_url: str
    stt_url: str
    redis_connected: bool


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize clients on startup."""
    global twilio_client, redis_client, http_client

    logger.info("Starting Twilio Integration service...")

    # Initialize Twilio client
    if TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN:
        twilio_client = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        logger.info("Twilio client initialized")
    else:
        logger.warning("Twilio credentials not configured")

    # Initialize Redis client
    try:
        redis_client = redis.from_url(REDIS_URL, decode_responses=True)
        await redis_client.ping()
        logger.info("Redis connected")
    except Exception as e:
        logger.warning(f"Redis connection failed: {e}")
        redis_client = None

    # Initialize HTTP client for internal service calls
    http_client = httpx.AsyncClient(timeout=30.0)
    logger.info("HTTP client initialized")

    yield

    # Cleanup
    logger.info("Shutting down Twilio Integration service...")
    if http_client:
        await http_client.aclose()
    if redis_client:
        await redis_client.close()


app = FastAPI(
    title="Twilio Integration Service",
    description="Voice call integration with Twilio Media Streams",
    version="1.0.0",
    lifespan=lifespan
)


@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Check service health and dependencies."""
    redis_connected = False
    if redis_client:
        try:
            await redis_client.ping()
            redis_connected = True
        except Exception:
            pass

    return HealthResponse(
        status="healthy",
        twilio_configured=bool(TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN),
        parlant_url=PARLANT_URL,
        tts_url=TTS_URL,
        stt_url=STT_URL,
        redis_connected=redis_connected
    )


@app.post("/webhook/voice", response_class=PlainTextResponse)
async def handle_voice_webhook(
    request: Request,
    CallSid: str = Form(...),
    From: str = Form(...),
    To: str = Form(...),
    Direction: str = Form(default="inbound"),
    CallStatus: str = Form(default="ringing")
):
    """
    Handle incoming voice webhook from Twilio.
    Returns TwiML to connect to Media Streams.
    """
    logger.info(f"Voice webhook: CallSid={CallSid}, From={From}, To={To}, Direction={Direction}")

    # Store call session
    call_sessions[CallSid] = {
        "call_sid": CallSid,
        "from": From,
        "to": To,
        "direction": Direction,
        "status": CallStatus,
        "start_time": time.time()
    }

    # Save to Redis if available
    if redis_client:
        await redis_client.hset(f"call:{CallSid}", mapping=call_sessions[CallSid])
        await redis_client.expire(f"call:{CallSid}", 3600)  # 1 hour TTL

    # Create TwiML response with Media Streams
    response = VoiceResponse()

    # Optional: Play a greeting first
    response.say("Hello! Please wait while I connect you to our AI assistant.", voice="alice")

    # Connect to Media Streams
    connect = Connect()
    stream_url = f"{PUBLIC_URL.replace('http', 'ws')}/media/stream"
    connect.stream(url=stream_url, name=f"stream_{CallSid}")
    response.append(connect)

    return str(response)


@app.post("/webhook/status")
async def handle_status_webhook(
    CallSid: str = Form(...),
    CallStatus: str = Form(...),
    CallDuration: Optional[int] = Form(None)
):
    """Handle call status callback from Twilio."""
    logger.info(f"Status webhook: CallSid={CallSid}, Status={CallStatus}, Duration={CallDuration}")

    # Update call session
    if CallSid in call_sessions:
        call_sessions[CallSid]["status"] = CallStatus
        if CallDuration:
            call_sessions[CallSid]["duration"] = CallDuration

    # Update in Redis if available
    if redis_client:
        await redis_client.hset(f"call:{CallSid}", "status", CallStatus)
        if CallDuration:
            await redis_client.hset(f"call:{CallSid}", "duration", str(CallDuration))

    # Clean up completed calls
    if CallStatus in ["completed", "failed", "busy", "no-answer", "canceled"]:
        if CallSid in call_sessions:
            del call_sessions[CallSid]

        # Clean up Parlant session
        if CallSid in parlant_sessions:
            del parlant_sessions[CallSid]
        if redis_client:
            try:
                await redis_client.delete(f"parlant_session:{CallSid}")
            except Exception:
                pass

    return {"status": "ok"}


@app.websocket("/media/stream")
async def handle_media_stream(websocket: WebSocket):
    """
    Handle Twilio Media Streams WebSocket connection.

    Protocol:
    - Twilio sends 'start' event with stream metadata
    - Twilio sends 'media' events with base64-encoded audio
    - We process audio through STT → Parlant → TTS pipeline
    - We send audio back via 'media' events
    - Twilio sends 'stop' event when call ends
    """
    await websocket.accept()
    logger.info("Media Stream WebSocket connected")

    stream_sid: Optional[str] = None
    call_sid: Optional[str] = None
    audio_buffer = io.BytesIO()
    last_process_time = time.time()

    try:
        while True:
            message = await websocket.receive_text()
            data = json.loads(message)
            event = data.get("event")

            if event == "start":
                # Stream started
                stream_sid = data.get("streamSid")
                call_sid = data.get("start", {}).get("callSid")
                logger.info(f"Stream started: streamSid={stream_sid}, callSid={call_sid}")

            elif event == "media":
                # Received audio data
                payload = data.get("media", {}).get("payload", "")
                if payload:
                    # Decode base64 audio (mulaw 8kHz)
                    audio_bytes = base64.b64decode(payload)
                    audio_buffer.write(audio_bytes)

                    # Process audio every 2 seconds
                    current_time = time.time()
                    if current_time - last_process_time >= 2.0 and audio_buffer.tell() > 0:
                        await process_audio_chunk(
                            websocket,
                            stream_sid,
                            audio_buffer,
                            call_sid
                        )
                        audio_buffer = io.BytesIO()  # Reset buffer
                        last_process_time = current_time

            elif event == "stop":
                # Stream ended - process remaining audio
                logger.info(f"Stream stopped: streamSid={stream_sid}")
                if audio_buffer.tell() > 0:
                    await process_audio_chunk(
                        websocket,
                        stream_sid,
                        audio_buffer,
                        call_sid,
                        is_final=True
                    )
                break

            elif event == "mark":
                # Audio playback completed marker
                mark_name = data.get("mark", {}).get("name", "")
                logger.debug(f"Mark received: {mark_name}")

    except WebSocketDisconnect:
        logger.info("Media Stream WebSocket disconnected")
    except Exception as e:
        logger.error(f"Media Stream error: {e}")


async def process_audio_chunk(
    websocket: WebSocket,
    stream_sid: str,
    audio_buffer: io.BytesIO,
    call_sid: Optional[str] = None,
    is_final: bool = False
):
    """Process audio chunk through STT → Parlant → TTS pipeline."""
    try:
        audio_buffer.seek(0)
        audio_bytes = audio_buffer.read()

        if len(audio_bytes) < 1000:  # Skip very short chunks
            return

        # Convert mulaw 8kHz to PCM 16kHz for Whisper
        audio_pcm = convert_mulaw_to_pcm(audio_bytes)

        # Step 1: Speech to Text
        transcription = await transcribe_audio(audio_pcm)
        if not transcription or len(transcription.strip()) < 2:
            return

        logger.info(f"STT result: {transcription}")

        # Step 2: Get response from Parlant
        response_text = await get_parlant_response(transcription, call_sid)
        if not response_text:
            return

        logger.info(f"Parlant response: {response_text}")

        # Step 3: Text to Speech
        audio_wav = await synthesize_speech(response_text)
        if not audio_wav:
            return

        # Step 4: Convert WAV to mulaw and send back
        audio_mulaw = convert_wav_to_mulaw(audio_wav)
        await send_audio_to_twilio(websocket, stream_sid, audio_mulaw)

    except Exception as e:
        logger.error(f"Audio processing error: {e}")


def convert_mulaw_to_pcm(mulaw_bytes: bytes) -> bytes:
    """Convert mulaw 8kHz to PCM 16-bit 16kHz."""
    import audioop

    # Decode mulaw to linear PCM
    pcm_8k = audioop.ulaw2lin(mulaw_bytes, 2)

    # Upsample from 8kHz to 16kHz
    pcm_16k = audioop.ratecv(pcm_8k, 2, 1, 8000, 16000, None)[0]

    return pcm_16k


def convert_wav_to_mulaw(wav_bytes: bytes) -> bytes:
    """Convert WAV audio to mulaw 8kHz for Twilio."""
    import audioop
    import wave

    # Read WAV
    with io.BytesIO(wav_bytes) as wav_buffer:
        with wave.open(wav_buffer, 'rb') as wav_file:
            sample_rate = wav_file.getframerate()
            pcm_data = wav_file.readframes(wav_file.getnframes())

    # Resample to 8kHz if needed
    if sample_rate != 8000:
        pcm_data = audioop.ratecv(pcm_data, 2, 1, sample_rate, 8000, None)[0]

    # Convert to mulaw
    mulaw_data = audioop.lin2ulaw(pcm_data, 2)

    return mulaw_data


async def transcribe_audio(audio_pcm: bytes) -> Optional[str]:
    """Send audio to Whisper STT service."""
    try:
        files = {"file": ("audio.wav", create_wav_from_pcm(audio_pcm), "audio/wav")}
        response = await http_client.post(f"{STT_URL}/v1/stt", files=files)
        response.raise_for_status()
        result = response.json()
        return result.get("text", "")
    except Exception as e:
        logger.error(f"STT request failed: {e}")
        return None


def create_wav_from_pcm(pcm_data: bytes) -> bytes:
    """Create WAV file from PCM data."""
    import wave

    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)  # 16-bit
        wav_file.setframerate(16000)
        wav_file.writeframes(pcm_data)
    buffer.seek(0)
    return buffer.read()


async def get_or_create_parlant_session(call_sid: str, caller_number: str) -> Optional[dict]:
    """Get existing or create new Parlant session for a call."""
    # Check local cache first
    if call_sid in parlant_sessions:
        return parlant_sessions[call_sid]

    # Check Redis cache
    if redis_client:
        try:
            cached = await redis_client.hgetall(f"parlant_session:{call_sid}")
            if cached and cached.get("session_id"):
                parlant_sessions[call_sid] = {
                    "session_id": cached["session_id"],
                    "last_offset": int(cached.get("last_offset", 0))
                }
                return parlant_sessions[call_sid]
        except Exception as e:
            logger.warning(f"Redis cache read failed: {e}")

    # Create new Parlant session
    if not PARLANT_AGENT_ID:
        logger.error("PARLANT_AGENT_ID not configured")
        return None

    try:
        response = await http_client.post(
            f"{PARLANT_URL}/sessions",
            params={"allow_greeting": "true"},
            json={
                "agent_id": PARLANT_AGENT_ID,
                "title": f"Voice call: {call_sid}",
                "metadata": {
                    "channel": "voice",
                    "call_sid": call_sid,
                    "caller_number": caller_number,
                    "start_time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                }
            }
        )
        response.raise_for_status()
        session_data = response.json()

        session_info = {
            "session_id": session_data["id"],
            "last_offset": 0
        }

        # Cache locally and in Redis
        parlant_sessions[call_sid] = session_info
        if redis_client:
            try:
                await redis_client.hset(
                    f"parlant_session:{call_sid}",
                    mapping={"session_id": session_info["session_id"], "last_offset": "0"}
                )
                await redis_client.expire(f"parlant_session:{call_sid}", 3600)
            except Exception as e:
                logger.warning(f"Redis cache write failed: {e}")

        logger.info(f"Created Parlant session {session_info['session_id']} for call {call_sid}")
        return session_info

    except Exception as e:
        logger.error(f"Failed to create Parlant session: {e}")
        return None


async def send_customer_message(session_id: str, message: str, caller_number: str) -> Optional[dict]:
    """Send customer message to Parlant session."""
    try:
        response = await http_client.post(
            f"{PARLANT_URL}/sessions/{session_id}/events",
            json={
                "kind": "message",
                "source": "customer",
                "message": message,
                "participant": {
                    "id": caller_number,
                    "display_name": caller_number
                }
            }
        )
        response.raise_for_status()
        event = response.json()
        logger.debug(f"Sent customer message, event offset: {event.get('offset')}")
        return event

    except Exception as e:
        logger.error(f"Failed to send customer message: {e}")
        return None


async def poll_agent_response(session_id: str, min_offset: int) -> Optional[tuple[str, int]]:
    """Poll for agent response from Parlant using long polling."""
    try:
        response = await http_client.get(
            f"{PARLANT_URL}/sessions/{session_id}/events",
            params={
                "min_offset": min_offset,
                "source": "ai_agent",
                "kinds": "message",
                "wait_for_data": PARLANT_RESPONSE_TIMEOUT
            },
            timeout=PARLANT_RESPONSE_TIMEOUT + 5  # Add buffer for network
        )

        if response.status_code == 504:
            # Timeout waiting for response
            logger.warning(f"Timeout waiting for Parlant response (session: {session_id})")
            return None

        response.raise_for_status()
        events = response.json()

        if events and len(events) > 0:
            # Get the latest message event
            for event in reversed(events):
                if event.get("kind") == "message" and event.get("source") == "ai_agent":
                    message = event.get("data", {}).get("message", "")
                    new_offset = event.get("offset", min_offset) + 1
                    return (message, new_offset)

        return None

    except httpx.TimeoutException:
        logger.warning(f"HTTP timeout waiting for Parlant response (session: {session_id})")
        return None
    except Exception as e:
        logger.error(f"Failed to poll agent response: {e}")
        return None


async def get_parlant_response(user_message: str, call_sid: Optional[str] = None) -> Optional[str]:
    """Get response from Parlant agent for a voice call."""
    if not call_sid:
        logger.error("call_sid required for Parlant integration")
        return "I'm sorry, I encountered an error. Please try again."

    # Get caller info from call session
    caller_number = "unknown"
    if call_sid in call_sessions:
        caller_number = call_sessions[call_sid].get("from", "unknown")

    # Get or create Parlant session
    session_info = await get_or_create_parlant_session(call_sid, caller_number)
    if not session_info:
        logger.error(f"Failed to get Parlant session for call {call_sid}")
        return "I'm sorry, I'm having trouble connecting. Please try again later."

    session_id = session_info["session_id"]
    current_offset = session_info["last_offset"]

    try:
        # Send customer message
        event = await send_customer_message(session_id, user_message, caller_number)
        if not event:
            return "I'm sorry, I couldn't process your message. Please try again."

        # Poll for agent response
        result = await poll_agent_response(session_id, current_offset)
        if result:
            agent_message, new_offset = result

            # Update offset tracking
            parlant_sessions[call_sid]["last_offset"] = new_offset
            if redis_client:
                try:
                    await redis_client.hset(
                        f"parlant_session:{call_sid}",
                        "last_offset",
                        str(new_offset)
                    )
                except Exception:
                    pass

            logger.info(f"Parlant response for call {call_sid}: {agent_message[:50]}...")
            return agent_message

        # No response received
        return "I'm still thinking about that. Could you please repeat?"

    except Exception as e:
        logger.error(f"Parlant request failed for call {call_sid}: {e}")
        return "I'm sorry, I encountered an error. Please try again."


async def synthesize_speech(text: str) -> Optional[bytes]:
    """Send text to ChatTTS service for speech synthesis."""
    try:
        response = await http_client.post(
            f"{TTS_URL}/v1/tts",
            json={"text": text}
        )
        response.raise_for_status()
        return response.content
    except Exception as e:
        logger.error(f"TTS request failed: {e}")
        return None


async def send_audio_to_twilio(websocket: WebSocket, stream_sid: str, audio_mulaw: bytes):
    """Send audio back to Twilio via Media Streams."""
    try:
        # Split audio into chunks (Twilio expects 20ms chunks = 160 bytes at 8kHz mulaw)
        chunk_size = 160
        for i in range(0, len(audio_mulaw), chunk_size):
            chunk = audio_mulaw[i:i + chunk_size]
            payload = base64.b64encode(chunk).decode("ascii")

            message = {
                "event": "media",
                "streamSid": stream_sid,
                "media": {
                    "payload": payload
                }
            }
            await websocket.send_text(json.dumps(message))

        # Send mark to know when audio playback completes
        mark_message = {
            "event": "mark",
            "streamSid": stream_sid,
            "mark": {
                "name": f"response_{int(time.time())}"
            }
        }
        await websocket.send_text(json.dumps(mark_message))

    except Exception as e:
        logger.error(f"Error sending audio to Twilio: {e}")


@app.post("/api/calls/outbound")
async def initiate_outbound_call(request: OutboundCallRequest):
    """Initiate an outbound call."""
    if not twilio_client:
        raise HTTPException(status_code=503, detail="Twilio client not configured")

    try:
        call = twilio_client.calls.create(
            to=request.to_number,
            from_=TWILIO_PHONE_NUMBER,
            url=f"{PUBLIC_URL}/webhook/voice",
            status_callback=f"{PUBLIC_URL}/webhook/status",
            status_callback_event=["initiated", "ringing", "answered", "completed"]
        )

        return {
            "call_sid": call.sid,
            "status": call.status,
            "from": TWILIO_PHONE_NUMBER,
            "to": request.to_number
        }

    except Exception as e:
        logger.error(f"Failed to initiate call: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/calls/{call_sid}", response_model=CallStatusResponse)
async def get_call_status(call_sid: str):
    """Get status of a call."""
    # Check local cache first
    if call_sid in call_sessions:
        session = call_sessions[call_sid]
        return CallStatusResponse(
            call_sid=call_sid,
            status=session.get("status", "unknown"),
            from_number=session.get("from", ""),
            to_number=session.get("to", ""),
            direction=session.get("direction", "unknown"),
            duration=session.get("duration")
        )

    # Check Redis
    if redis_client:
        session = await redis_client.hgetall(f"call:{call_sid}")
        if session:
            return CallStatusResponse(
                call_sid=call_sid,
                status=session.get("status", "unknown"),
                from_number=session.get("from", ""),
                to_number=session.get("to", ""),
                direction=session.get("direction", "unknown"),
                duration=int(session["duration"]) if session.get("duration") else None
            )

    # Query Twilio directly
    if twilio_client:
        try:
            call = twilio_client.calls(call_sid).fetch()
            return CallStatusResponse(
                call_sid=call.sid,
                status=call.status,
                from_number=call.from_,
                to_number=call.to,
                direction=call.direction,
                duration=call.duration
            )
        except Exception as e:
            logger.error(f"Failed to fetch call from Twilio: {e}")

    raise HTTPException(status_code=404, detail="Call not found")


@app.get("/")
async def root():
    """Root endpoint with API info."""
    return {
        "service": "Twilio Integration",
        "version": "1.0.0",
        "endpoints": {
            "health": "/health",
            "voice_webhook": "/webhook/voice",
            "status_webhook": "/webhook/status",
            "media_stream": "/media/stream (WebSocket)",
            "outbound_call": "/api/calls/outbound",
            "call_status": "/api/calls/{call_sid}"
        }
    }
