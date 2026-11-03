"""Bounded, authenticated dictation through Meta. Audio is never persisted."""
import io
import json
import wave
import httpx
from .errors import APIError

MAX_AUDIO_BYTES = 24_000 * 2 * 60 + 4096

async def transcribe(settings, audio: bytes) -> str:
    if not settings.voice_enabled or not settings.meta_api_key:
        raise APIError(503, 'voice_unavailable', 'Voice input is not enabled')
    if len(audio) > MAX_AUDIO_BYTES:
        raise APIError(413, 'audio_too_large', 'Record up to 60 seconds')
    try:
        with wave.open(io.BytesIO(audio), 'rb') as wav:
            valid = (wav.getnchannels() == 1 and wav.getsampwidth() == 2
                     and wav.getframerate() in (16000, 24000)
                     and 0 < wav.getnframes() <= wav.getframerate() * 60
                     and len(wav.readframes(wav.getnframes())) == wav.getnframes() * 2)
        if not valid:
            raise ValueError()
    except (wave.Error, EOFError, ValueError):
        raise APIError(422, 'invalid_audio', 'Expected mono PCM WAV at 16 or 24 kHz, up to 60 seconds') from None
    try:
        async with httpx.AsyncClient(timeout=55, follow_redirects=False) as client:
            response = await client.post(settings.meta_base_url.rstrip('/') + '/asr/transcribe',
                headers={'Authorization': 'Bearer ' + settings.meta_api_key, 'Accept': 'application/json'},
                files={'request': (None, json.dumps({'model': 'muse-voice-transcribe-1.0', 'audioEncoding': 'WAV', 'mode': 'PUSH_TO_TALK'}), 'application/json'),
                       'audio': ('dictation.wav', audio, 'audio/wav')})
        if response.status_code == 429:
            raise APIError(429, 'voice_busy', 'Voice input is busy. Please try again shortly')
        response.raise_for_status()
        text = response.json().get('transcript')
        if not isinstance(text, str) or len(text) > 8000:
            raise ValueError()
        return text.strip()
    except (httpx.HTTPError, ValueError, AttributeError):
        raise APIError(502, 'transcription_failed', 'Could not transcribe the recording. Please try again') from None
