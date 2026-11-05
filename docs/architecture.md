# mAIcrophone — software design

Status: current design. The implementation sequence and open contracts are
noted where they still matter.

## 1. Product shape

Single-user, local dictation helper for any focused text field,
primarily the `pi` prompt.

Components:

```text
compositor hotkey
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
- **`maic` CLI**: thin client for `toggle`, `stop`, `cancel`, `status`,
  `language`, `type`, and `punctuate`.
- **`nemo-speech serve`**: separate process/service, not embedded.
- **No settings GUI in v1**: one TOML config file.
- **No transcript history in v1** (optional later).

## 2. MVP behaviour

- `Mod+Alt+V` starts recording.
- Same shortcut stops recording and commits.
- A separate shortcut cancels without typing.
- Optional shortcuts insert `.` or `?` at the sentence visible in the overlay.
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
  `maic language next` cycles the configured favourite languages. The overlay
  shows the language for the next session. The value is daemon-wide, not
  per-window.
- Clipboard fallback if injection fails or focus changed.
- Overlay appears on the **currently focused monitor** at recording start.
- Overlay starts compact, grows as the transcript wraps, then caps its height
  and auto-scrolls to keep the newest lines visible.

### Shortcuts

Shortcuts are deliberately **not hard-coded**. The daemon exposes commands and
the user chooses compositor bindings that do not conflict with existing ones:

```kdl
Mod+Alt+V            { spawn "maic" "toggle"; }
Mod+Alt+Shift+V      { spawn "maic" "cancel"; }
Mod+Alt+L            { spawn "maic" "language" "next"; }
Mod+Alt+Period       { spawn "maic" "punctuate" "period"; }
Mod+Alt+Shift+Period { spawn "maic" "punctuate" "question"; }
```

These are examples; choose keys that do not conflict with your compositor.
Niri, Sway, and Hyprland examples are in `configuration.md`.

## 3. Process model and IPC

Daemon + CLI, not one process per dictation.

- `maic daemon` runs as a systemd user service.
- It listens on `$XDG_RUNTIME_DIR/maic.sock`.
- CLI commands connect, send one line of JSON, wait for an ack, exit.
- Only one daemon instance; socket lock prevents duplicates.
- If the daemon is not running, `maic toggle` fails with a clear message.
- Logs go to stderr/journald.
- The protocol is versioned and documented in `docs/ipc.md`. It is currently
  private to the bundled CLI, not a supported third-party client API.

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

Current default: the PulseAudio simple API via ctypes
(`maic/backends/pulse.py`), which PipeWire implements through
`pipewire-pulse`.

- Capture PCM16/16 kHz/mono in a dedicated reader thread.
- Read in 10 ms frames, aggregate to the configured chunk size, and compute
  RMS/peak for the VU meter client-side.
- Device selection uses the configured Pulse source name.
- Stop the reader before freeing the Pulse stream; this avoids aborting
  libpulse when a blocking read is still in flight.

`pw-record` remains available as `backend.audio = "pw-record"`. It is a
subprocess with no binding concerns, but adds a runtime executable dependency.
Native PipeWire stream bindings are still a later optimization: the C API uses
SPA pod construction and realtime callbacks, which is disproportionate to the
current requirement.

## 7. Overlay

Focusless layer-shell surface, not a normal window.

Properties:

- anchored bottom centre of the **focused output** at recording start,
- output selected through the compositor backend (native Niri/Sway/Hyprland
  IPC, with output name and logical width/height),
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

- Keep one fixed transcript font size; never shrink text to fit more lines.
- Start at `initial_lines` (default 2) and grow one wrapped line at a time as
  text is added.
- Stop growing at `max_lines` (default 8) or when the surface reaches 30% of the
  output height, whichever comes first.
- Keep the newest lines visible and auto-scroll to the bottom once capped.
- No manual scrolling.
- Do not reserve space for silence/pauses; only actual transcript text is shown.

The overlay stays on the output where recording started, permanently for that
session.

Why focusless: the editor/`pi` prompt must keep keyboard focus. If the overlay
takes focus, `wtype` would type into the overlay instead of the target.

Consequence: cancel and language actions must be **global shortcuts**, not keys
handled by the overlay. Language cycling changes the next session; it is not
sent as a mid-session `session.update`.

Candidate implementations:

- **Python + GTK4 + gtk4-layer-shell**: current implementation; Pango text,
  CSS styling, no custom renderer. GTK is imported only on the overlay path.
- **Rust + smithay-client-toolkit + cosmic-text + tiny-skia**: single binary,
  full control, more code.
- **Rust + gtk4-layer-shell**: Rust logic with GTK rendering.
- **Rust + raclettui/ratatui**: TUI-like layer-shell, smaller ecosystem.

Decision: stay on Python + GTK4 for now. Re-evaluate under the gates in
`roadmap.md` step 5; introduce backend seams before any port.

## 8. Text injection

`wtype` subprocess first, with clipboard fallback.

- Final text only.
- Target locking:
  - capture the focused window through the compositor IPC at record start,
  - the target is the window `id`, not the title,
  - the user may focus another window while dictating; that does not abort,
  - before injection, if the target is no longer focused, ask the compositor
    backend to refocus the original target,
  - if the original target no longer exists, do not type; copy transcript to
    clipboard and show a notification,
  - title changes are ignored; `id`/`pid` identify the target.
- Wait for modifier release before typing if the shortcut used modifiers.
- Unicode/Italian: `wtype` tested OK.
- Clipboard:
  - optional `copy_to_clipboard` on every successful session,
  - native Wayland `ext-data-control-v1` client for both GTK and headless
    daemons, so regular and PRIMARY selections share one implementation,
  - `Gdk.Clipboard` remains an optional provider for GTK callers,
  - `wl-copy` only as an optional failure fallback,
  - optional primary-selection mode for middle-click paste,
  - clipboard fallback when injection is impossible.
  - Overwriting the clipboard is acceptable by user preference; the only real
    costs are losing previous clipboard contents and clipboard-history/privacy.
- Multiline:
  - configurable newline policy: `space` (default) or `shift-enter`,
  - `shift-enter` is the intended long-term default for chat prompts.
- No length-based clipboard threshold yet; the final text always goes through
  `wtype` first, with clipboard fallback when injection is impossible.
- Manual punctuation (`maic punctuate period|question`): while recording, a
  mark is anchored to the partial transcript visible when the shortcut was
  pressed and inserted into the final at that boundary using a
  longest-common-prefix heuristic. Only `.` and `?` are supported; there is no
  automatic capitalization. When idle, the mark is typed at the cursor only if
  the last target window still has focus.
- The shortcut must be a compositor bind, not a key handled by mAIcrophone.
  Wayland routes keys only to the focused surface; the overlay is intentionally
  focusless and the target app keeps focus during dictation. A bare `.`/`?`
  global bind is not viable because it would consume those keys in every
  application. Layer-shell keyboard grabs would defeat the focusless design.
- Post-ASR replacement table for code terms:
  - `Pipewire` -> `PipeWire`
  - `System D` -> `systemd`
  - `Enemmotron` -> `Nemotron`
  - etc.

Focus detection and refocusing are implemented by the Niri, Sway, and
Hyprland compositor backends. Other compositors require a new backend or a
generic fallback.

## 9. Configuration

XDG paths:

```text
$XDG_CONFIG_HOME/maic/config.toml     # config
$XDG_STATE_HOME/maic/                 # optional logs/state later
$XDG_RUNTIME_DIR/maic.sock            # daemon IPC
```

`$XDG_CONFIG_HOME` defaults to `~/.config`, `$XDG_STATE_HOME` to
`~/.local/state`, `$XDG_RUNTIME_DIR` is provided by the session.

The canonical example, precedence rules, and per-key reference live in
[configuration.md](configuration.md). Environment overrides:

```text
MAIC_CONFIG
MAIC_SERVER_URL
MAIC_LANGUAGE
MAIC_LOG
```

## 10. Packaging and services

- `maic.service` — user daemon; sets the layer-shell `LD_PRELOAD` in the
  service environment.
- `maic-server.service` — `nemo-speech serve` with RC=6.
- Core package has no GTK dependency; `.[overlay]` adds the Python GTK extras,
  while gtk4/gtk4-layer-shell/python-gobject remain distro packages.
- Manual overlay launches use `scripts/maic-layer-shell` for the preload
  workaround; headless runs need neither the wrapper nor GTK.
- Config example + niri keybinding snippet.
- AUR package later; manual venv initially.
- Optional `maic doctor` for server/socket/mic/wtype checks.

## 11. Server readiness contract

If the server is offline, the user must find out **immediately**, not after
speaking for 30 seconds.

- On daemon startup, probe `GET /ready` and cache the result.
- Before every recording, probe `/ready` with a short timeout (~300 ms).
- If not ready:
  - show overlay error immediately,
  - send an optional desktop notification (Mako already installed),
  - do not start audio capture or open a WebSocket.
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
     `roadmap.md` step 5 for the gates that would trigger a port.
2. **Overlay interaction model**
   - focusless layer-shell + global cancel shortcut (recommended), or
   - focused floating TUI overlay (easier UI, steals focus, complicates `wtype`).
3. **Stop/cancel semantics**
   - toggle = stop-and-type; separate cancel shortcut (recommended).
4. **Focus-change behaviour**
   - abort + clipboard (recommended), or type anyway, or ask via overlay.
5. **MVP scope**
   - Niri, Sway, and Hyprland; one monitor, explicit language by default with
     a favourite-language cycle, one final per session, no history.
6. **Fallback policy**
   - wtype primary; clipboard on failure/focus change (recommended).
7. **Server lifecycle**
   - external systemd service (recommended), or daemon starts/stops it.

## 13. Implementation sequence

The first vertical slice landed in this order and remains the base of the
current code:

1. `maic daemon` + Unix socket + `maic toggle`.
2. native audio capture -> WS -> final transcript printed to journal.
3. Focusless GTK4 layer-shell overlay with status + VU + partial text.
4. `wtype` final into the focused window.
5. Compositor focus guard + clipboard fallback.
6. Systemd units + config + compositor bind snippets.

Broader work such as endpointing, history, and native PipeWire bindings stays
in `backlog.md` and `roadmap.md`.

## 14. Contracts that deserve deeper definition

These are the places where vague behaviour causes bugs. They are implemented,
but keep them explicit when changing the code.

1. **Overlay long-text contract**
   - fixed font size, initial/max visible lines, wrapping, max height,
   - auto-scroll behaviour and whether a full-text mode exists,
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
