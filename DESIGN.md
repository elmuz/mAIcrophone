# mAIcrophone — software design

Status: draft. Decisions marked **[proposed]** need confirmation.

## 1. Product shape

Single-user, Niri-first, local dictation helper for any focused text field,
primarily the `pi` prompt.

Components:

```text
niri hotkey
    |
    v
maic CLI  --Unix socket-->  maic daemon
                               |- state machine
                               |- audio capture
                               |- ASR WebSocket client
                               |- layer-shell overlay
                               |- text injection
                               |- focus guard
                               |
                               v
                        nemo-speech serve
                        (separate systemd user service)
```

- **`maic` daemon**: long-running, one instance, owns all state.
- **`maic` CLI**: thin client for `toggle`, `stop`, `cancel`, `status`.
- **`nemo-speech serve`**: separate process/service, not embedded.
- **No settings GUI in v1**: one TOML config file.
- **No transcript history in v1** (optional later).

## 2. MVP behaviour

- `Mod+V` starts recording.
- Same shortcut stops recording and commits.
- A separate shortcut cancels without typing.
- While recording:
  - focusless overlay at bottom centre,
  - VU meter,
  - evolving partial transcript.
- Before starting capture, verify the ASR server is ready. If it is offline,
  show the error immediately and do not record.
- On stop:
  - terminate audio capture,
  - send `input_audio_buffer.commit`,
  - wait for final + `input_audio_buffer.committed`,
  - verify focused window has not changed,
  - type the final with `wtype`,
  - close overlay.
- Partial text is **not** typed into the target app.
- One final transcript per session. No endpointing/multiple finals in v1.
- `language=en-US` by default; per-session switching via `maic language`;
  overlay shows an `EN`/`IT`/`AUTO` badge.
- Clipboard fallback if injection fails or focus changed.
- Overlay appears on the **currently focused monitor** at recording start.
- Overlay grows to show the whole transcript where practical.

### Shortcuts

Shortcuts are deliberately **not hard-coded**. The daemon exposes commands and
the user chooses niri bindings that do not conflict with existing ones:

```kdl
Mod+Alt+V       { spawn "maic" "toggle"; }
Mod+Alt+Shift+V { spawn "maic" "cancel"; }
```

The exact keys are decided later based on the current niri config.

## 3. Process model and IPC

**[proposed]** Daemon + CLI, not one process per dictation.

- `maic daemon` runs as a systemd user service.
- It listens on `$XDG_RUNTIME_DIR/maic.sock`.
- CLI commands connect, send one line of JSON, wait for an ack, exit.
- Only one daemon instance; socket lock prevents duplicates.
- If the daemon is not running, `maic toggle` fails with a clear message.
- Logs go to stderr/journald.

Why: hotkey commands stay short and stateless; recording/overlay/WS state
survives across key presses; systemd can restart the daemon.

## 4. Session state machine

```text
Idle
  | toggle
  v
Starting ── error ──> Idle
  |
  v
Recording
  | stop                 | cancel
  v                      v
Stopping                Cancelling
  | final + committed     | clear + close
  v                      v
Injecting                Idle
  | done/fallback
  v
Idle
```

Rules:

- Only one session at a time.
- `toggle` while recording = stop-and-commit.
- `cancel` while recording/stopping = discard audio and close.
- `stop` while idle = no-op.
- Overlay shows `REC`, `STOP`, `CANCEL`, `ERR`.
- On error, overlay shows a short message for ~2 s, then closes.

## 5. ASR transport

- WebSocket: `ws://127.0.0.1:8080/v1/audio/transcriptions/realtime`
- One WS connection per dictation session.
- Audio format: raw little-endian **PCM16, 16 kHz, mono**.
- Send 100 ms frames (3200 bytes).
- `session.update` before the first audio frame:
  - `sample_rate: 16000`
  - `language: en-US` (or the current `maic language` value)
  - `automatic_punctuation: true`
- On stop:
  1. stop capture,
  2. send `input_audio_buffer.commit`,
  3. wait for final `.completed`,
  4. wait for `input_audio_buffer.committed`,
  5. close socket.
- On cancel: send `input_audio_buffer.clear`, close socket, never inject.
- Partials: reconcile non-empty `.delta` values into the overlay text.
  `delta` is usually a suffix of the new partial, but it can be a full
  replacement when the server revises earlier words. Use longest-common-prefix
  reconciliation, not blind append.
- Final `transcript` is authoritative and replaces the partial display.
- Empty deltas are ignored.

Server-side settings (not client config):

```text
asr.streaming.rnnt_right_context = 6
asr.postproc.itn_model_dir = ~/.local/share/nemo-speech/itn
speech_contexts / word boosting = off in v1
```

## 6. Audio capture

**[proposed]** Start with `pw-record` subprocess:

```text
pw-record --raw --format s16 --rate 16000 --channels 1 -
```

- Read raw PCM from stdout.
- Compute RMS for the VU meter client-side.
- On stop: terminate child, wait briefly, then commit.
- Device selection: `--target <name>` from config; default source otherwise.
- Native PipeWire bindings are a later optimization, not MVP.

Reasons: no bindings build step; easy to debug; PipeWire handles device
selection/resampling. The process overhead is negligible.

## 7. Overlay

**[proposed]** Focusless layer-shell surface, not a normal window.

Properties:

- anchored bottom centre of the **focused output** at recording start,
- output selected with `niri msg --json focused-output` (`name` + `logical`
  width/height),
- width: min(75% of output width, ~2000 px),
- height: dynamic, grows upward from the bottom margin,
- max height: ~30% of the output's logical height,
- bottom margin around 56 px,
- `keyboard-interactivity = none`,
- empty input region, so clicks pass through,
- monospace / TUI-like styling with Nord colours,
- rounded corners, thin border, slightly transparent background,
- status row: state, coloured VU meter, language, ASR server status,
- partial text wraps; the entire partial is shown, not a subtitle-style tail.

Long-text policy:

- Try to fit all text by growing the surface up to the max height (30%).
- Show up to about 10 lines at the base font size.
- If reducing font size by 1–3 steps makes the transcript fit, do that.
- Otherwise keep the newest lines visible and auto-scroll to the bottom.
- No manual scrolling.
- Do not reserve space for silence/pauses; only actual transcript text is shown.

The overlay stays on the output where recording started, permanently for that
session.

Why focusless: the editor/`pi` prompt must keep keyboard focus. If the overlay
takes focus, `wtype` would type into the overlay instead of the target.

Consequence: cancel/language actions must be **global shortcuts**, not keys
handled by the overlay.

Candidate implementations:

- **Python + GTK4 + gtk4-layer-shell**: current implementation; Pango text,
  CSS styling, no custom renderer. GTK is imported only on the overlay path.
- **Rust + smithay-client-toolkit + cosmic-text + tiny-skia**: single binary,
  full control, more code.
- **Rust + gtk4-layer-shell**: Rust logic with GTK rendering.
- **Rust + raclettui/ratatui**: TUI-like layer-shell, smaller ecosystem.

Decision: stay on Python + GTK4 for now. Re-evaluate under the gates in
`PLAN.md` step 5; introduce backend seams before any port.

## 8. Text injection

**[proposed]** `wtype` subprocess first.

- Final text only.
- Target locking:
  - capture `niri msg --json focused-window` at record start,
  - the target is the window `id`, not the title,
  - the user may focus another window while dictating; that does not abort,
  - before injection, if the target is no longer focused, call
    `niri msg action focus-window --id <id>` to return to the original target,
  - if the original target no longer exists, do not type; copy transcript to
    clipboard and show a notification,
  - title changes are ignored; `id`/`pid` identify the target.
- Wait for modifier release before typing if the shortcut used modifiers.
- Unicode/Italian: `wtype` tested OK.
- Clipboard:
  - optional `copy_to_clipboard` on every successful session,
  - `wl-copy` for the regular clipboard,
  - optional primary-selection mode (`wl-copy --primary`) for middle-click paste,
  - clipboard fallback when injection is impossible.
  - Overwriting the clipboard is acceptable by user preference; the only real
    costs are losing previous clipboard contents and clipboard-history/privacy.
- Multiline:
  - configurable newline policy: `space` (v1 default), `shift-enter`, `clipboard`,
  - `shift-enter` is the intended long-term default for chat prompts,
  - implement after the basic vertical slice if it adds too much risk.
- Long-text threshold: configurable; initially always try `wtype`.
- Post-ASR replacement table for code terms:
  - `Pipewire` -> `PipeWire`
  - `System D` -> `systemd`
  - `Enemmotron` -> `Nemotron`
  - etc.

Non-Niri focus detection is out of scope for v1.

## 9. Configuration

**[proposed]** XDG paths:

```text
$XDG_CONFIG_HOME/maic/config.toml     # config
$XDG_STATE_HOME/maic/                 # optional logs/state later
$XDG_RUNTIME_DIR/maic.sock            # daemon IPC
```

`$XDG_CONFIG_HOME` defaults to `~/.config`, `$XDG_STATE_HOME` to
`~/.local/state`, `$XDG_RUNTIME_DIR` is provided by the session.

Config file:

```toml
[server]
url = "ws://127.0.0.1:8080/v1/audio/transcriptions/realtime"

[audio]
source = ""              # empty = default PipeWire source
sample_rate = 16000
chunk_ms = 100

[asr]
language = "en-US"     # en-US | it-IT | auto
punctuation = true
context = []             # word boosting disabled by default in v1

[overlay]
width_percent = 75
max_width_px = 2000
max_height_percent = 30
margin_px = 56
font = "FiraCode Nerd Font Mono 13"
max_lines = 10
min_font_size = 11
font_steps = 3
corner_radius = 10
border_width = 1
background_opacity = 0.94

[injection]
method = "wtype"         # wtype | clipboard | both
copy_to_clipboard = true # user preference: keep transcript on clipboard
primary_selection = true
return_to_target = true
multiline = "space"      # space | shift-enter | clipboard
long_text_threshold = 400

[replacements]
"Pipewire" = "PipeWire"
"System D" = "systemd"
"Enemmotron" = "Nemotron"
```

Environment overrides for development:

```text
MAIC_CONFIG
MAIC_SERVER_URL
MAIC_LOG
```

## 10. Packaging and services

- `maic.service` — user daemon, `WantedBy=default.target`.
- `maic-server.service` — `nemo-speech serve` with RC=6.
- Config example + niri keybinding snippet.
- AUR package later; manual `cargo install`/venv initially.
- Optional `maic doctor` for server/socket/mic/wtype checks.

## 11. Server readiness contract

If the server is offline, the user must find out **immediately**, not after
speaking for 30 seconds.

**[proposed]**:

- On daemon startup, probe `GET /ready` and cache the result.
- Before every recording, probe `/ready` with a short timeout (~300 ms).
- If not ready:
  - show overlay error immediately,
  - send an optional desktop notification (Mako already installed),
  - do not spawn `pw-record` or open a WebSocket.
- If the WebSocket fails to connect or drops mid-session:
  - stop capture,
  - show the error overlay,
  - never inject partial text.
- If `commit` is sent but no final arrives within a timeout (~5 s after
  capture stops), abort injection, copy nothing, and show an error.
- No automatic reconnect within a session; the next session starts fresh.

Mako/`notify-send` is optional and used only for errors; the overlay remains the
primary feedback surface.

## 12. Decisions

1. **Implementation stack** — resolved for now.
   - Current: Python + GTK4 layer-shell; GTK is lazy-loaded on the overlay path.
   - Deferred: Rust + smithay/cosmic-text, Rust + gtk4-layer-shell. See
     `PLAN.md` step 5 for the gates that would trigger a port.
2. **Overlay interaction model**
   - focusless layer-shell + global cancel shortcut (recommended), or
   - focused floating TUI overlay (easier UI, steals focus, complicates `wtype`).
3. **Stop/cancel semantics**
   - toggle = stop-and-type; separate cancel shortcut (recommended).
4. **Focus-change behaviour**
   - abort + clipboard (recommended), or type anyway, or ask via overlay.
5. **MVP scope**
   - Niri only, one monitor, explicit language by default, one final per session,
     no history.
6. **Fallback policy**
   - wtype primary; clipboard on failure/focus change (recommended).
7. **Server lifecycle**
   - external systemd service (recommended), or daemon starts/stops it.

## 13. Suggested first vertical slice

1. `maic daemon` + Unix socket + `maic toggle`.
2. `pw-record` -> WS -> final transcript printed to journal.
3. Focusless GTK4 layer-shell overlay with status + VU + partial text.
4. `wtype` final into the focused window.
5. Niri focus guard + clipboard fallback.
6. Systemd units + config + niri bind snippet.

Do not add endpointing, history, non-Niri support, or native PipeWire bindings
until this slice works end to end.

## 14. Contracts that deserve deeper definition

These are the places where vague behaviour will cause bugs. The first four need
a real contract before or during the first vertical slice.

1. **Overlay long-text contract**
   - max height, minimum font size, wrapping, overflow behaviour,
   - whether the overlay ever scrolls or offers a full-text mode,
   - what happens on multi-monitor/output hotplug.

2. **Session failure contract**
   - exact behaviour for server offline, WS connect failure, WS drop mid-session,
   - missing final after commit, daemon shutdown mid-recording,
   - what is typed, what is copied, what is discarded.

3. **Focus guard contract**
   - target identity = niri window `id` + `pid` (not title),
   - what if focus changes to a child dialog or another window of the same app,
   - abort vs clipboard vs "type anyway" policy,
   - what if the focused output disappears.

4. **Injection contract**
   - success/failure detection for `wtype` (exit status may not be enough),
   - newline/tab handling, very long text, Unicode,
   - clipboard fallback and notification,
   - whether replacement rules run before or after focus guard,
   - how modifier keys are released before typing.

5. **ASR partial contract**
   - partial display is best-effort, final is authoritative,
   - deltas are suffixes or full replacements; reconcile with a common prefix,
   - how revisions are displayed without cursor churn,
   - empty deltas ignored.

6. **IPC contract**
   - command JSON schema, ack/error format, status snapshot,
   - socket ownership/permissions, stale socket handling,
   - daemon/CLI version compatibility.

7. **Configuration contract**
   - defaults < config file < env < CLI precedence,
   - schema validation and clear error messages,
   - server URL, language, audio source, overlay geometry, replacements.

8. **Privacy/logging contract**
   - never persist raw audio by default,
   - whether transcripts are logged,
   - journald verbosity.
