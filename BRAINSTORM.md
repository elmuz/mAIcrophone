# mAIcrophone — brainstorming notes

Date: 2026-10-27
Status: research / one-day spike. Project name is provisional; we may end up
integrating with an existing frontend instead of building a standalone app.

## Goal

Local, low-friction voice dictation for Linux/Wayland (Niri), aimed primarily
at chatting with the `pi` coding harness, but usable in any focused text field.

Desired flow:

1. Press a shortcut while in the target app.
2. Toggle recording (not hold — see decisions).
3. Audio goes to a local streaming ASR server.
4. Partial transcript is shown in a small overlay, so mistakes can be spotted
   before committing.
5. On stop, the final transcript is typed into the focused app, or copied to
   the clipboard as a fallback.

Secondary goals: VU/wave feedback, Italian + English technical speech, custom
vocabulary for code identifiers, no cloud.

## Decisions so far

- **Toggle, not push-to-talk.** A stop keybind is fine. This also avoids
  Wayland/held-modifier issues with synthetic key events.
- **Overlay shows partials; only finals are typed.** Do not type revisions into
  the target app by default. The overlay can be replaced/re-rendered freely.
- **Each dictation is one independent ASR session.** Start WS session, stream,
  commit on stop, type the final, close. No long-lived transcript state needed.
- **Target app is the currently focused window, not hard-coded `pi`.** No `pi`
  plugin coupling. Use `wtype` (or clipboard fallback) for generic injection.
- **Focus-change safety:** record the focused window at start via niri IPC, and
  re-check before typing. If focus changed, abort typing and/or use clipboard.
- **Abort path:** user can cancel before commit if the partial overlay looks
  wrong. Then send `input_audio_buffer.clear` and close without typing.
- **Clipboard is an acceptable fallback**, especially for long text or apps
  where `wtype` fails. Preview overlay still lets the user abort.
- **Backend:** CPU Q8_0 first. AMD 760M Vulkan is worth one benchmark, but CPU
  is the safe default. OpenVINO is not relevant on AMD. No CUDA.
- **ASR tuning from the spike:** `language=auto`, `rnnt_right_context=6`,
  no aggressive speech-context boosting. Word boosting at 5 was harmful; 13
  was not better than 6 for mixed Italian/English technical speech. Handle code
  terms with a post-ASR replacement/glossary.
- **Single user / local only.** Model license (OpenMDW 1.1) is acceptable for
  this use.
- **Overlay interaction:** a focused floating TUI is simplest if we want
  cancel/language keys. A focusless layer-shell overlay is better for not
  stealing focus, but then cancel/language need separate global shortcuts.

## Research findings

### Server: already mostly solved by NeMo-Speech.cpp

`nemo-speech serve` provides:

- `WS /v1/audio/transcriptions/realtime`
- streaming little-endian PCM16 input
- `session.update` before audio
- partial `.delta` and final `.completed` events
- endpointing/VAD, punctuation/capitalization, ITN, word boosting
- model loaded once, independent state per WebSocket connection
- CPU / CUDA / Metal / Vulkan backends, GGUF quantization

Model: `nvidia/nemotron-3.5-asr-streaming-0.6b`

- 0.6B multilingual streaming FastConformer/RNNT, 33 languages incl. Italian
- Q8_0 GGUF is ~742 MB; original safetensors ~2.55 GB
- 160 ms streaming chunks
- NVIDIA CPU benchmark: ~6x realtime, ~27 ms per 160 ms chunk
- Must benchmark on Ryzen 5 7640U; should still be comfortably realtime

So the server is a systemd user unit around `nemo-speech serve`, not a project
we need to write.

### Verified realtime protocol details

From `server/http/http_server.cpp`:

- On connect, server sends `session.created`.
- Client may send one `session.update` before audio starts.
- Audio can be raw binary PCM16 frames or base64 in
  `input_audio_buffer.append`.
- Partials are `conversation.item.input_audio_transcription.delta`.
- Finals are `conversation.item.input_audio_transcription.completed` with a
  `transcript` field.
- On stop, send `input_audio_buffer.commit`; server emits the final, then
  `input_audio_buffer.committed`.
- `input_audio_buffer.clear` / `response.cancel` discards audio and sends
  `input_audio_buffer.cleared`.
- **Delta semantics are not always pure append.** If the new partial extends
  the previous partial as a prefix, `delta` is the new suffix. If the ASR
  revises earlier words, `delta` is the full new partial transcript. A client
  must do longest-common-prefix reconciliation, not blind `partial += delta`.
  For overlay-only partials this is easy: just replace the overlay text with
  the reconciled/current transcript.

### Existing OSS frontends

Most relevant: **Voxtype** (MIT, Rust, ~1.7k stars)

- Niri/Hyprland/Sway/River/GNOME/KDE support
- PipeWire capture, `wtype` with dotool/ydotool/clipboard fallbacks
- floating waveform OSD, systemd integration
- push-to-talk and toggle
- streaming transcription with commit-only typing, cancel-rewind
- explicitly recommends toggle for streaming because synthetic keys interfere
  with held physical keys on Wayland

Other projects: hyprwhspr, Handy, niri-transcribe, whisper_dictation,
nerd-dictation, Vocalinux, whisrs, awesome-voice-typing list.

The gap: no obvious first-class NeMo/Nemotron 3.5 streaming engine in those
frontends. Voxtype has Parakeet streaming, which is not the same model.

## Likely direction

1. Run a one-day spike on `nemo-speech serve` + WebSocket + `pw-record` +
   `wtype`.
2. If the protocol/latency/injection all feel good, prefer **adding a NeMo
   streaming engine to Voxtype** over building mAIcrophone from scratch.
3. Build a standalone mAIcrophone only if we need a materially different
   overlay/TUI or tighter control.

## Standalone architecture (if we go that way)

```
niri keybind -> maic toggle
                  |
                  v
          mAIcrophone daemon
          |- pw-record 16 kHz mono s16
          |- RMS VU meter
          |- WebSocket client to nemo-speech
          |- state: idle / recording / partial / final
          |- overlay: partial text + VU
          |- wtype or clipboard injection
                  |
                  v
          nemo-speech serve (systemd user unit)
```

Server unit sketch:

```ini
[Unit]
Description=mAIcrophone NeMo ASR server
After=pipewire.service wireplumber.service

[Service]
ExecStart=%h/.local/bin/nemo-speech serve \
  --asr-model nemotron-3.5 \
  --host 127.0.0.1 --port 8080
Restart=on-failure
Nice=5

[Install]
WantedBy=default.target
```

## Open questions / risks

1. Real latency and CPU on the 7640U, especially with punctuation/endpointing.
2. Reliable text injection in terminals, browsers, Electron, GTK/Qt, Neovim.
   `wtype` Unicode/Italian layout behavior must be tested.
3. Focus target: niri IPC can detect focus changes, but cannot guarantee the
   final injection target. Abort/clipboard fallback needed.
4. Overlay: focused TUI vs focusless layer-shell. Focusless is better UX but
   needs global cancel/language shortcuts.
5. Partial revision display: need LCP reconciliation if showing a single
   evolving overlay line.
6. Code dictation quality: identifiers, camelCase/snake_case, symbols, file
   paths. Use NeMo word boosting and possibly post-processing.
7. Language switching / Italian-English code-switching.
8. Packaging: AUR, systemd user units, model download, config, updates.
9. Battery/always-on model: acceptable at ~1-2 GB RAM and idle CPU; consider
   socket activation or idle unload later.

## Rough estimate

- Spike: 1-2 days
- MVP (toggle, record, final typing, simple overlay): 5-8 days
- Polished v1: 3-5 weeks
- Adding a NeMo engine to Voxtype: ~3-8 days plus upstream review/iteration
