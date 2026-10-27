# One-day spike plan

Goal: answer the risky questions before committing to an implementation.

## 1. Install and run the server

```bash
cd ~/projects/mAIcrophone

# CPU build; installer auto-detects, but force CPU to keep it simple.
curl -fsSL https://github.com/NVIDIA/NeMo-Speech.cpp/raw/main/scripts/install.sh \
  | sh -s -- --backend cpu

export PATH="$HOME/.local/bin:$PATH"
nemo-speech doctor
nemo-speech pull nemotron-3.5
nemo-speech model list

nemo-speech serve \
  --asr-model nemotron-3.5 \
  --host 127.0.0.1 --port 8080
```

In another shell:

```bash
curl -s http://127.0.0.1:8080/ready | jq .
curl -s http://127.0.0.1:8080/v1/models | jq .
```

Quick sanity check without writing a client: `nemo-speech serve --open` and use
the browser playground microphone. It shows partials + finals and proves the
model/audio path works.

## 2. Measure CPU and latency

While the server is running:

```bash
# Watch server CPU/RAM during dictation
top -p "$(pgrep -f 'nemo-speech serve' | head -1)"

# Optional: check Vulkan availability, then compare one run with
# --asr.backend.gpu 0 (or --device vulkan:0 if supported).
nemo-speech doctor
```

What to record:

- time from speaking to first partial
- time from stop/commit to final
- partial churn (does text get rewritten a lot?)
- CPU % during speech
- RAM after model load
- whether Italian/English mixed technical phrases are usable
- quality with a few code identifiers (e.g. `systemd`, `PipeWire`, `wtype`,
  `nemo-speech`, `input_audio_buffer.commit`)

## 3. Test raw PipeWire capture

Record a raw 16 kHz mono s16 stream:

```bash
# 5 seconds, raw PCM to a file
pw-record --raw --format s16 --rate 16000 --channels 1 \
  --sample-count $((16000 * 5)) /tmp/maic.raw

# Inspect it
ls -l /tmp/maic.raw
# 5 s * 16000 samples/s * 2 bytes = 160000 bytes
```

`pw-record --raw ... -` writes raw PCM to stdout; that is what the daemon will
consume. `parec --raw --rate=16000 --channels=1 --format=s16le` is a fallback.

## 4. Test WebSocket streaming with the script

```bash
cd ~/projects/mAIcrophone
python -m venv .venv
. .venv/bin/activate
pip install websockets
python spike_ws_asr.py 6
```

Speak for ~6 seconds. Expected output: `session.created`, `session.updated`,
some `PARTIAL` lines, then a `FINAL`. The script sends `input_audio_buffer.commit`
on stop and waits for `input_audio_buffer.committed`.

Things to observe:

- Are `PARTIAL` deltas usually suffixes, or do they sometimes replace the whole
  partial? (Both are possible; the server source confirms this.)
- Does `FINAL` arrive promptly after commit?
- Does the final include punctuation/capitalization?
- What happens if you send `input_audio_buffer.clear` instead of commit?

## 5. Test text injection with `wtype`

Focus a scratch terminal and run:

```bash
wtype 'hello from mAIcrophone'
wtype 'perché è così — test àèìòù'
wtype -k BackSpace -k BackSpace -k BackSpace
printf 'typed from stdin\n' | wtype -
```

Test in the real target apps:

- Kitty / foot / Alacritty
- Neovim
- Firefox textarea
- VSCode / Electron
- `pi` prompt

Check:

- Unicode/Italian characters survive
- keyboard layout is respected
- text goes to the focused window, not the window that was focused at start
- backspace works for cancel/rewind
- long text does not drop characters; try `wtype -d 1 '...'` if needed
- clipboard fallback: `wl-copy 'text'` and paste manually

## 6. Test Niri shortcut + focus behavior

Add a harmless test binding to `~/.config/niri/config.kdl`:

```kdl
Mod+V { spawn "sh" "-c" "sleep 1; wtype 'niri binding works'"; }
```

Reload niri, focus the editor, press `Mod+V`. This tests:

- whether niri is still holding modifiers when text arrives
- whether the target app receives the text
- whether `wtype` from a niri-spawned process has the right environment

Then test focus change:

```bash
niri msg --json focused-window
```

Start recording in Kitty, focus Firefox before stopping, and see where the text
lands. If you want a focus guard, capture the window ID/title/PID at start and
compare with `niri msg --json focused-window` before injection. Abort or use the
clipboard if it changed.

## 7. Decide

At the end of the day, answer:

1. Is CPU latency good enough on the 7640U?
2. Is `wtype` reliable enough for the target apps?
3. Is partial churn acceptable for an overlay-only preview?
4. Does the niri binding + focus story work without hacks?
5. Is the next step a Voxtype NeMo engine, or a standalone mAIcrophone?

## First-run results (2026-10-27)

- `nemo-speech serve` works on CPU with Nemotron 3.5 Q8_0.
- WebSocket session works: `session.created` -> `session.updated` -> partials
  -> final -> `input_audio_buffer.committed`.
- Test phrase produced `Hello, my name is Alessio` with correct punctuation and
  capitalization.
- Empty `.delta` events are normal: the server emits an event per chunk even
  when the partial has not changed. Clients should ignore empty deltas.
- Observed deltas were suffix chunks (`Hello`, `, my`, ` name`, ...). For an
  overlay, append non-empty deltas. If a model revision appears, the server can
  send a full replacement instead, so a production client should reconcile.
- Initial empty final was caused by low microphone gain (27% / -34 dB), not by
  the server. Raising the default source volume fixed it.
- `wtype` works, including Italian accented characters.
- Italian test with `language=auto` produced Italian text but `Ciao` was
  transcribed as `Chao`, and `Nemotron PipeWire` as `Nemotrone Pipewire`.
  Automatic language detection probably worked: the output is Italian, not
  English. In `auto` mode the model appends an `<it-IT>` tag; NeMo-Speech strips
  it and records it as detected language. The tag is not part of the transcript.
- To verify detected language, request `word_timestamps` and inspect
  `words[].language_code` in the final event. `spike_ws_asr.py` now prints this
  as `LANGS`.
- To improve orthography, test an explicit `language=it-IT` prompt and word
  boosting for code terms (`PipeWire`, `Nemotron`, `systemd`, `wtype`).
- Niri focus detection works: `niri msg --json focused-window` returns `id`,
  `title`, `app_id`, `pid`, `is_focused`, and `focus_timestamp`.
- Follow-up: `auto` produced `Čau` (Czech orthography for "ciao"), suggesting
  auto language ID was fooled by the first short word. Explicit `it-IT` produced
  `Charles`, which is not obviously an Italian-prompt improvement. Both runs
  also mangled `PipeWire` / `Nemotron` / `systemd`.
- Hypothesis to test next: the default streaming right context is 1 (160 ms).
  The model card's WER numbers use a 1.12 s frame size, so 160 ms is the
  lowest-latency / lowest-accuracy operating point. Test
  `--asr.streaming.rnnt_right_context 3` (320 ms) and `6` (560 ms), with and
  without word boosting.
- The realtime final event does not expose the detected language tag; NeMo
  strips it. To inspect language detection, use offline/CLI JSON output instead.
- Right context 6 (560 ms) clearly improved the short-word problem: `Ciao` was
  recognized correctly in the `it-IT`, `it`, and `auto` runs.
- Remaining errors are mostly technical-term transliteration, not language
  detection:
  - `auto`: `Ciao sto testando ... Pipewire, System D e Nemotron` (best overall)
  - `it-IT`: `Ciao, sto destando ... Pipewire System D enemmotron`
  - `it`: `Ciao sto testando ... Pipewire System di Enemmotron`
- Word boosting at boost=5 made things worse (`Chao`, `Pypuaier`, `Vtype`).
  Do not use aggressive boosting for mixed Italian/English technical terms.
- Candidate default: `language=auto`, `rnnt_right_context=6` (or 13), no
  aggressive boosting, plus a small post-ASR replacement table for known code
  terms (`System D` -> `systemd`, `Enemmotron` -> `Nemotron`, etc.).
- Next: test `rnnt_right_context=13` (1.12 s, the model-card accuracy point) and
  low boost (~2) with a single phrase. Compare latency vs accuracy.
- Right context 13 (1.12 s) was not better for this mixed Italian/English
  sentence: `Papeware System DNOtron`. It adds latency and can merge adjacent
  English terms. Discard 13; 6 is the best tested point.
- Low boost (2) for `systemd` did not fix it (`System DN`). Drop word boosting
  for mixed-language code terms; use post-ASR replacement/fuzzy normalization.
- Final candidate for the spike: `language=auto`,
  `asr.streaming.rnnt_right_context=6`, no speech contexts.
