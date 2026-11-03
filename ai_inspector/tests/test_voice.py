import io
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch
import httpx
from fastapi.testclient import TestClient
from ai_inspector.api import create_app
from ai_inspector.config import Settings
from ai_inspector.errors import APIError
from ai_inspector.voice import transcribe

class VoiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.settings=Settings(Path(self.tmp.name),'operator-test','device-test',voice_enabled=True,meta_api_key='test-key')
    def tearDown(self):self.tmp.cleanup()
    def wav(self):
        out=io.BytesIO()
        with wave.open(out,'wb') as w:
            w.setnchannels(1);w.setsampwidth(2);w.setframerate(24000);w.writeframes(b'\0\0'*2400)
        return out.getvalue()
    async def test_invalid_audio_never_sent(self):
        with patch('ai_inspector.voice.httpx.AsyncClient') as client:
            with self.assertRaises(APIError):await transcribe(self.settings,b'not audio')
            client.assert_not_called()
    async def test_transcript_and_contract(self):
        def handler(request):
            self.assertEqual(request.url.path,'/v1/asr/transcribe')
            self.assertIn(b'muse-voice-transcribe-1.0',request.content)
            self.assertIn(b'PUSH_TO_TALK',request.content)
            return httpx.Response(200,json={'transcript':' Check the window. '})
        real=httpx.AsyncClient
        with patch('ai_inspector.voice.httpx.AsyncClient',side_effect=lambda **kw:real(transport=httpx.MockTransport(handler),**kw)):
            self.assertEqual(await transcribe(self.settings,self.wav()),'Check the window.')
    async def test_error_does_not_expose_provider_body(self):
        real=httpx.AsyncClient
        with patch('ai_inspector.voice.httpx.AsyncClient',side_effect=lambda **kw:real(transport=httpx.MockTransport(lambda r:httpx.Response(401,text='private provider details')),**kw)):
            with self.assertRaises(APIError) as error:await transcribe(self.settings,self.wav())
            self.assertNotIn('private provider',str(error.exception.body))
    def test_authentication(self):
        client=TestClient(create_app(self.settings,background_worker=False))
        self.assertEqual(client.post('/v1/voice/transcribe',content=self.wav()).status_code,401)
        self.assertEqual(client.get('/v1/voice/config',headers={'Authorization':'Bearer device-test'}).status_code,403)
        self.assertTrue(client.get('/v1/voice/config',headers={'Authorization':'Bearer operator-test'}).json()['enabled'])
