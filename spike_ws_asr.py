#!/usr/bin/env python3
"""Minimal NeMo-Speech.cpp realtime ASR spike.

Captures raw 16 kHz mono s16 PCM from PipeWire with pw-record, streams it to
nemo-speech serve over WebSocket, prints partials/finals, then commits.

Usage:
    python spike_ws_asr.py [seconds] [websocket-url]

Environment:
    MAIC_LANG=auto|it-IT|en-US|...   language prompt (default: auto)
    MAIC_CONTEXT="systemd,PipeWire,wtype"  optional word-boosting phrases
    MAIC_BOOST=3.0                  word-boosting strength

Requires:
    pip install websockets
    pw-record (pipewire)
    nemo-speech serve --asr-model nemotron-3.5 --host 127.0.0.1 --port 8080
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time

import websockets

RATE = 16_000
CHANNELS = 1
CHUNK_BYTES = RATE * 2 // 10  # 100 ms of PCM16 mono


async def read_events(ws, t0: float) -> None:
    partial = ""
    async for raw in ws:
        event = json.loads(raw)
        kind = event.get("type", "")
        elapsed = time.monotonic() - t0

        if kind.endswith("transcription.delta"):
            delta = event.get("delta") or ""
            if not delta:
                # The server emits an event per chunk even when the partial
                # has not changed. Empty deltas are no-ops.
                continue
            # For this streaming RNNT model, deltas observed so far are
            # suffixes. If revisions appear, the server sends the full new
            # partial instead, so a production client needs a reconciliation
            # rule; for the spike we just append.
            partial += delta
            print(f"+{elapsed:5.2f}s PARTIAL {partial!r}", flush=True)
        elif kind.endswith("transcription.completed"):
            print(f"+{elapsed:5.2f}s FINAL   {event.get('transcript')!r}", flush=True)
        elif kind == "input_audio_buffer.committed":
            print(f"+{elapsed:5.2f}s COMMITTED", flush=True)
            return
        else:
            print(f"+{elapsed:5.2f}s EVENT   {event}", flush=True)


async def main(seconds: float, url: str) -> None:
    proc = subprocess.Popen(
        [
            "pw-record",
            "--raw",
            "--format",
            "s16",
            "--rate",
            str(RATE),
            "--channels",
            str(CHANNELS),
            "-",
        ],
        stdout=subprocess.PIPE,
    )
    assert proc.stdout is not None

    try:
        async with websockets.connect(url, max_size=None) as ws:
            print("EVENT  ", await ws.recv(), flush=True)
            language = os.environ.get("MAIC_LANG", "auto")
            context = [
                phrase.strip()
                for phrase in os.environ.get("MAIC_CONTEXT", "").split(",")
                if phrase.strip()
            ]
            session = {
                "sample_rate": RATE,
                "language": language,
                "automatic_punctuation": True,
            }
            boost = float(os.environ.get("MAIC_BOOST", "3.0"))
            if context:
                session["speech_contexts"] = [{"phrases": context, "boost": boost}]
            print(
                f"CONFIG  language={language!r} context={context} boost={boost}",
                flush=True,
            )
            await ws.send(json.dumps({"type": "session.update", "session": session}))
            print("EVENT  ", await ws.recv(), flush=True)

            t0 = time.monotonic()
            reader = asyncio.create_task(read_events(ws, t0))
            loop = asyncio.get_running_loop()
            deadline = loop.time() + seconds
            sent_bytes = 0

            while loop.time() < deadline and not reader.done():
                # Reading the pipe is blocking; keep the event loop free.
                data = await asyncio.to_thread(proc.stdout.read, CHUNK_BYTES)
                if not data:
                    break
                await ws.send(data)
                sent_bytes += len(data)

            print(
                f"SENT    {sent_bytes} bytes "
                f"(~{sent_bytes / (RATE * 2):.1f}s of audio)",
                flush=True,
            )
            proc.terminate()
            await asyncio.to_thread(proc.wait, 2)
            await ws.send(json.dumps({"type": "input_audio_buffer.commit"}))
            await reader
    finally:
        if proc.poll() is None:
            proc.terminate()
            proc.wait()


if __name__ == "__main__":
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 5.0
    url = (
        sys.argv[2]
        if len(sys.argv) > 2
        else "ws://127.0.0.1:8080/v1/audio/transcriptions/realtime"
    )
    asyncio.run(main(seconds, url))
