# mAIcrophone

Local, focusless push-to-talk dictation for Niri/Wayland, backed by
`nemo-speech serve` and the Nemotron 3.5 streaming ASR model.

Status: first vertical slice. It records, streams to the ASR server, shows a
focusless overlay, and types the final transcript into the window that was
focused when recording started.

## Requirements

Core daemon and CLI (headless install):

- Arch Linux / Wayland / Niri
- Python 3.11+ and the Python package `websockets` (installed by `pip`)
- system libraries: `libwayland-client` and `libpulse`/`libpulse-simple`
- PipeWire with `pipewire-pulse` (native capture through `libpulse-simple`)
  or the optional `pw-record` fallback
- `wtype` (still required for text injection)
- `nemo-speech` with `nemotron-3.5` (the ASR server is a separate process)
- optional: `wl-copy` as a clipboard fallback; the default clipboard path is
  native Wayland `ext-data-control-v1`
- optional: `notify-send` for desktop error notifications
- optional: cased ITN grammars for inverse text normalization

The Niri backend talks to `$NIRI_SOCKET` directly, so the `niri` executable is
not required at runtime. When the daemon is started by systemd without
`NIRI_SOCKET` in its environment, it falls back to the newest `niri*.sock` in
`$XDG_RUNTIME_DIR`.

The normal headless dependency set is therefore Python + the `websockets`
package + `wtype` + `libwayland-client` + a PulseAudio-compatible server
(usually `pipewire-pulse`) + the external ASR server. The GTK overlay stack and
`wl-copy`/`pw-record` are optional. If `libwayland-client` or
`ext-data-control-v1` is unavailable, clipboard writes fall back to `wl-copy`
when it is installed.

Overlay (optional):

- `gtk4`, `gtk4-layer-shell`, `python-gobject`, `python-cairo`

Install the core package without GTK:

```bash
pip install -e .
```

Install the overlay dependency extra, then the system GTK packages:

```bash
pip install -e '.[overlay]'
sudo pacman -S gtk4 gtk4-layer-shell python-gobject python-cairo
```

On Arch, `gtk4-layer-shell` may be linked after `libwayland-client`, which makes
GTK fall back to a normal centred window. Layer-shell preloading is a
launcher/systemd concern, not application logic. Use the shipped launcher for
manual overlay runs:

```bash
scripts/maic-layer-shell daemon
```

The systemd unit sets `LD_PRELOAD=/usr/lib/libgtk4-layer-shell.so` in its
service environment. Headless runs (`maic daemon --no-overlay`) need neither the
launcher nor the GTK stack.

## Development setup

```bash
cd ~/projects/mAIcrophone
python -m venv --system-site-packages .venv
. .venv/bin/activate
pip install -e .
```

Run the test suite (uses only the standard library):

```bash
python -m unittest discover -s tests -v
```

The CLI and `maic daemon --no-overlay` do not import GTK; only the overlay
path requires `python-gobject`, `gtk4`, and `gtk4-layer-shell`.

## Inverse text normalization

Install the cased Sparrowhawk ITN grammars once. The archive is small and
includes English and several other languages. Grammars are loaded lazily.
Note: in NeMo-Speech 0.2.0 the Italian (`it`) grammar directory is present but
empty, so Italian ITN is not available yet.

```bash
mkdir -p ~/.local/share/nemo-speech/itn
curl -fL \
  https://github.com/NVIDIA/NeMo-Speech.cpp/releases/download/v0.2.0/itn_configs_cased.tar.bz2 \
  -o /tmp/itn_configs_cased.tar.bz2
tar -xjf /tmp/itn_configs_cased.tar.bz2 \
  -C ~/.local/share/nemo-speech/itn \
  --strip-components=1
```

## Run

Start the ASR server:

```bash
nemo-speech serve \
  --asr-model nemotron-3.5 \
  --host 127.0.0.1 --port 8080 \
  --asr.streaming.rnnt_right_context 6 \
  --itn-model-dir ~/.local/share/nemo-speech/itn
```

Start the daemon:

```bash
maic daemon
```

In another shell:

```bash
maic status
maic toggle     # start; call again to stop and type
maic cancel     # discard current recording
maic language   # show the current ASR language
maic language en-US
maic language it-IT
maic language auto
maic type "hello"   # diagnostic: type directly via the daemon
```

For testing without the overlay:

```bash
maic daemon --no-overlay
```

The CLI/daemon Unix-socket protocol is documented in [docs/ipc.md](docs/ipc.md).
It is versioned but currently private to the bundled CLI.

## Niri bindings

Add to `~/.config/niri/config.kdl`, choosing keys that do not conflict:

```kdl
Mod+Alt+V       { spawn "maic" "toggle"; }
Mod+Alt+Shift+V { spawn "maic" "cancel"; }
```

## Config

Copy `config/config.toml` to `$XDG_CONFIG_HOME/maic/config.toml`
(usually `~/.config/maic/config.toml`).

The daemon connects to the server URL from config. It checks `/ready` before
starting a recording, so an offline server fails immediately instead of after
you finish speaking.

Concrete implementations live under `maic/backends/`. The `[backend]` section
selects the compositor, audio, injection, ASR, and overlay providers; current
names are `niri`, `pulse`/`pw-record`, `wtype`, `nemo-speech`, and `gtk`/`null`.
Unknown names are rejected when the config is loaded.

`backend.audio = "pulse"` uses the PulseAudio simple API through ctypes, which
PipeWire's `pipewire-pulse` server implements. Set it to `pw-record` if the
Pulse compatibility server is unavailable.

For a non-streaming server (whisper.cpp or any OpenAI-compatible
faster-whisper server), set `backend.asr` to `whisper-cpp`, `openai-http`,
`faster-whisper`, or the generic `batch-http`, then point `[asr] batch_url` at
the transcription endpoint. Batch providers buffer audio while you speak and
transcribe once on stop, so they do not show live partials.

The default language is `en-US`. Language is a per-session setting: changing it
with `maic language` affects the next dictation without restarting the ASR
server. The overlay shows `EN`, `IT`, or `AUTO` in its status row.

The overlay uses a compact Nord-themed status row with state, coloured VU meter,
language, and ASR server status. It grows up to 30% of the monitor height, keeps
about 10 lines visible, and automatically scrolls to the newest text. Font size
is reduced only when doing so makes the whole transcript fit; otherwise the
newest text remains visible. Geometry and typography are configurable under
`[overlay]`.

By default, the final transcript is copied to both the regular clipboard and
the PRIMARY selection (middle-click paste). Set `primary_selection = false` if
you do not want that. The daemon owns both selections itself through a small
native `ext-data-control-v1` client, in both GTK and headless modes. If that
protocol, `libwayland-client`, or the Wayland connection is unavailable,
`wl-copy` is used as an optional fallback. `Gdk.Clipboard` remains available as
an optional provider but is not on the default daemon path.

## Systemd

```bash
mkdir -p ~/.config/systemd/user
cp systemd/maic.service ~/.config/systemd/user/
cp systemd/maic-server.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now maic-server.service
systemctl --user enable --now maic.service
```

## Current limitations

- Niri only.
- Overlay is capped at 30% of the monitor height; long text auto-scrolls to the
  newest lines, with no manual scrolling.
- Newlines are flattened to spaces by default.
- No history and no endpointing yet.
- Language switching is CLI-only; there is no overlay selector yet.
- Partial text is best-effort; the final transcript is authoritative.
- Deterministic/manual punctuation insertion is not implemented yet.
