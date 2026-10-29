from __future__ import annotations

import asyncio
import io
import unittest
import wave

from maic.backends import build_backends
from maic.backends.batch import (
    BatchHttpProvider,
    BatchHttpSession,
    _extract_text,
    _multipart,
    pcm_to_wav,
)
from maic.backends.protocols import AsrEvent
from maic.config import Config


class WavTests(unittest.TestCase):
    def test_pcm_is_wrapped_in_a_valid_wav(self):
        pcm = b"\x01\x00\x02\x00" * 8
        data = pcm_to_wav(pcm, sample_rate=16000)
        with wave.open(io.BytesIO(data), "rb") as wav:
            self.assertEqual(wav.getnchannels(), 1)
            self.assertEqual(wav.getsampwidth(), 2)
            self.assertEqual(wav.getframerate(), 16000)
            self.assertEqual(wav.readframes(wav.getnframes()), pcm)


class MultipartTests(unittest.TestCase):
    def test_file_and_fields_are_encoded(self):
        body, content_type = _multipart(
            [("model", "whisper-1")],
            file_field="file",
            filename="audio.wav",
            content=b"RIFF",
        )
        self.assertTrue(content_type.startswith("multipart/form-data; boundary="))
        self.assertIn(b'name="model"', body)
        self.assertIn(b'name="file"; filename="audio.wav"', body)
        self.assertIn(b"RIFF", body)


class ResponseTests(unittest.TestCase):
    def test_json_text_is_extracted(self):
        self.assertEqual(_extract_text(b'{"text": "hello"}'), "hello")

    def test_json_transcript_is_extracted(self):
        self.assertEqual(
            _extract_text(b'{"transcript": "ciao"}'),
            "ciao",
        )

    def test_plain_text_is_returned(self):
        self.assertEqual(_extract_text(b"plain transcript\n"), "plain transcript")

    def test_empty_response_is_empty(self):
        self.assertEqual(_extract_text(b""), "")


class BatchProviderTests(unittest.TestCase):
    def test_capabilities_are_non_streaming(self):
        provider = BatchHttpProvider(Config())
        self.assertFalse(provider.capabilities.supports_streaming)
        self.assertFalse(provider.capabilities.supports_partials)
        self.assertTrue(provider.capabilities.supports_commit)

    def test_factory_selects_non_streaming_providers(self):
        cfg = Config(backend_asr="openai-http")
        provider = build_backends(cfg).asr
        self.assertIsInstance(provider, BatchHttpProvider)
        self.assertFalse(provider.capabilities.supports_streaming)

    def test_server_url_can_be_an_http_endpoint(self):
        cfg = Config(
            backend_asr="openai-http",
            server_url="http://127.0.0.1:9000/v1/audio/transcriptions",
        )
        provider = BatchHttpProvider(cfg, "openai")
        self.assertEqual(
            provider.endpoint,
            "http://127.0.0.1:9000/v1/audio/transcriptions",
        )
        self.assertEqual(provider.ready_url, "http://127.0.0.1:9000/")


class BatchSessionTests(unittest.TestCase):
    def test_audio_is_buffered_until_commit(self):
        provider = BatchHttpProvider(Config(sample_rate=16000))
        uploaded: list[bytes] = []
        events: list[AsrEvent] = []

        async def fake_transcribe(pcm: bytes, timeout: float) -> str:
            uploaded.append(pcm)
            return "hello batch"

        provider.transcribe_pcm = fake_transcribe  # type: ignore[method-assign]

        async def scenario():
            session = provider.create_session(on_event=events.append)
            await session.connect()
            await session.send_audio(b"\x01\x00")
            await session.send_audio(b"\x02\x00")
            self.assertEqual(uploaded, [])
            final = await session.commit()
            await session.close()
            return final

        final = asyncio.run(scenario())
        self.assertEqual(final, "hello batch")
        self.assertEqual(uploaded, [b"\x01\x00\x02\x00"])
        self.assertEqual(
            [event.kind for event in events],
            ["final", "committed"],
        )

    def test_empty_batch_produces_empty_final(self):
        provider = BatchHttpProvider(Config())
        events: list[AsrEvent] = []
        session = BatchHttpSession(provider, events.append)

        async def scenario():
            await session.connect()
            return await session.commit()

        self.assertEqual(asyncio.run(scenario()), "")
        self.assertEqual([event.kind for event in events], ["final", "committed"])

    def test_cancel_discards_buffered_audio(self):
        provider = BatchHttpProvider(Config())
        uploaded: list[bytes] = []
        events: list[AsrEvent] = []

        async def fake_transcribe(pcm: bytes, timeout: float) -> str:
            uploaded.append(pcm)
            return "should not happen"

        provider.transcribe_pcm = fake_transcribe  # type: ignore[method-assign]

        async def scenario():
            session = provider.create_session(on_event=events.append)
            await session.connect()
            await session.send_audio(b"\x01\x00")
            await session.cancel()
            await session.close()

        asyncio.run(scenario())
        self.assertEqual(uploaded, [])
        self.assertEqual(events, [])


class ConfigTests(unittest.TestCase):
    def test_batch_protocol_is_validated(self):
        with self.assertRaises(ValueError):
            from maic.config import load_config
            import tempfile
            from pathlib import Path

            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "config.toml"
                path.write_text(
                    '[asr]\nbatch_protocol = "nope"\n',
                    encoding="utf-8",
                )
                load_config(path)


if __name__ == "__main__":
    unittest.main()
