<p align="center">
  <img src="docs/assets/logo.jpg" alt="mAIcrophone logo" width="360">
</p>

<h1 align="center">mAIcrophone</h1>

<p align="center">
  <em>Local, focusless dictation for Niri, Sway, and Hyprland, toggled from a<br>
  global shortcut and backed by <code>nemo-speech serve</code> and the Nemotron 3.5
  streaming ASR model.</em>
</p>

<p align="center">
  <a href="https://github.com/elmuz/mAIcrophone/releases/latest"><img src="https://img.shields.io/github/v/release/elmuz/mAIcrophone?label=release&color=88C0D0" alt="Latest release"></a>
  <a href="https://github.com/elmuz/mAIcrophone/actions/workflows/ci.yml"><img src="https://github.com/elmuz/mAIcrophone/actions/workflows/ci.yml/badge.svg?branch=master" alt="CI"></a>
  <a href="https://github.com/astral-sh/uv"><img src="https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json" alt="uv"></a>
  <a href="https://github.com/astral-sh/ruff"><img src="https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json" alt="Ruff"></a>
  <a href="https://github.com/astral-sh/ty"><img src="https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ty/main/assets/badge/v0.json" alt="ty"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-88C0D0.svg" alt="License: MIT"></a>
</p>

<p align="center">
  <img src="docs/assets/overlay.png" alt="mAIcrophone focusless dictation overlay showing a live transcript" width="900">
</p>

mAIcrophone runs as a small background daemon. A global shortcut starts and
stops recording; live text appears in an overlay that never steals keyboard
focus, and the final transcript is typed into the window that was focused when
recording started.

**Status:** current release (`0.3.0`). The recording → ASR → overlay → injection
flow works end to end, with native Niri, Sway, and Hyprland compositor
backends. Missing features are tracked in [docs/backlog.md](docs/backlog.md).

## Main features

- **Focusless overlay** on the focused compositor output. The editor or prompt keeps
  keyboard focus while partial and final transcripts are displayed.
- **Streaming local ASR** through a separate `nemo-speech` WebSocket server,
  with live partials and an authoritative final transcript.
- **Pluggable backends** for compositor, audio capture, text injection, ASR,
  and overlay. Batch ASR servers such as whisper.cpp and OpenAI-compatible
  endpoints are also supported.
- **Per-session language switching** (`en-US`, `it-IT`, `auto`, …) from the CLI.
- **Safe text injection** with `wtype`: the original target window is restored
  if focus moved during dictation, and the transcript can also be copied to the
  clipboard and PRIMARY selection.
- **Manual `.` / `?` while dictating**: press a global shortcut when a sentence
  is visible in the overlay and the mark is inserted at that point in the final
  transcript. The shortcut is a compositor bind by design: Wayland does not let
  an unfocused client read keys, and a bare `.`/`?` bind would swallow those
  keys for every application.
- **Local-first**: audio and transcripts stay on the machine unless you
  explicitly configure a remote ASR endpoint.

## Getting started

### 1. Install the runtime dependencies

Required on Arch Linux / Wayland (Niri, Sway, or Hyprland):

- Python 3.11+
- `wtype`
- `libwayland-client`
- `libpulse` and a PulseAudio-compatible server (usually `pipewire-pulse`)
- `nemo-speech` with the `nemotron-3.5` model

The GTK4 overlay is optional:

```bash
sudo pacman -S --needed gtk4 gtk4-layer-shell python-gobject python-cairo
```

See [docs/development.md](docs/development.md) for a complete setup, including
the ASR server and the optional ITN grammars.

### 2. Install mAIcrophone

```bash
python -m venv --system-site-packages .venv
. .venv/bin/activate
pip install -e .
```

For the overlay, install the Python extra:

```bash
pip install -e '.[overlay]'
```

### 3. Configure and run

```bash
cp config/config.toml ~/.config/maic/config.toml
nemo-speech serve --asr-model nemotron-3.5 --host 127.0.0.1 --port 8080
maic daemon
```

In another shell:

```bash
maic toggle             # start recording; call again to stop and type
maic cancel             # discard the current recording
maic status             # show daemon state
maic punctuate period   # add . at the visible sentence while recording
maic punctuate question # add ? at the visible sentence while recording
```

Add the shortcuts to your compositor configuration. Niri:

```kdl
Mod+Alt+V            { spawn "maic" "toggle"; }
Mod+Alt+Shift+V      { spawn "maic" "cancel"; }
Mod+Alt+Period       { spawn "maic" "punctuate" "period"; }
Mod+Alt+Shift+Period { spawn "maic" "punctuate" "question"; }
```

Sway:

```ini
bindsym $mod+Mod1+v exec maic toggle
bindsym $mod+Mod1+Shift+v exec maic cancel
bindsym $mod+Mod1+period exec maic punctuate period
bindsym $mod+Mod1+Shift+period exec maic punctuate question
```

Hyprland:

```ini
bind = $mainMod ALT, V, exec, maic toggle
bind = $mainMod ALT SHIFT, V, exec, maic cancel
bind = $mainMod ALT, period, exec, maic punctuate period
bind = $mainMod ALT SHIFT, period, exec, maic punctuate question
```

For systemd user services, headless operation, overlay launching, and all
configuration options, see [docs/configuration.md](docs/configuration.md).

## Documentation

| Document | Contents |
| --- | --- |
| [Architecture](docs/architecture.md) | Process model, session state machine, ASR transport, backends, overlay, injection, and design decisions. |
| [Development](docs/development.md) | Repository layout, local setup, running tests, backend development, debugging, and libraries used. |
| [Configuration](docs/configuration.md) | Config file reference, backends, services, compositor bindings, and troubleshooting. |
| [Contributing](docs/contributing.md) | Workflow, coding conventions, testing expectations, and good first contributions. |
| [IPC protocol](docs/ipc.md) | Versioned daemon/CLI Unix-socket protocol. |
| [Roadmap](docs/roadmap.md) | Architectural sequence and decision gates. |
| [Releases](https://github.com/elmuz/mAIcrophone/releases) | Versioned releases and changelogs. |
| [Backlog](docs/backlog.md) | User-facing features, known gaps, and explicitly skipped work. |

## Current limitations

- Compositor focus integration is available for Niri, Sway, and Hyprland. Other
  Wayland compositors need a compositor backend or a generic fallback.
- The overlay does not accept input; language and cancel actions are global
  shortcuts.
- The overlay keeps a fixed transcript font size, grows from a compact initial
  height up to a line cap, then auto-scrolls long transcripts; there is no
  manual scrolling.
- No endpointing, transcript history, or spoken punctuation. Manual `.` and
  `?` insertion is available through the compositor shortcuts above.
- Partial text is best-effort; the final transcript is authoritative.

## License

mAIcrophone is released under the [MIT License](LICENSE). Third-party
dependencies keep their own licenses.
