# mAIcrophone

Local, focusless push-to-talk dictation for Niri/Wayland, backed by
`nemo-speech serve` and the Nemotron 3.5 streaming ASR model.

Status: first vertical slice. It records, streams to the ASR server, shows a
focusless overlay, and types the final transcript into the window that was
focused when recording started.

## Requirements

- Arch Linux / Wayland / Niri
- Python 3.11+
- `gtk4`, `gtk4-layer-shell`, `python-gobject`
- `pipewire` (`pw-record`)
- `wtype`, `wl-copy`
- `nemo-speech` with `nemotron-3.5`
- optional: cased ITN grammars for inverse text normalization

Install the GTK layer-shell package:

```bash
sudo pacman -S gtk4-layer-shell
```

On Arch, `gtk4-layer-shell` may be linked after `libwayland-client`, which makes
GTK fall back to a normal centred window. `maic daemon` automatically re-execs
itself with the documented workaround:

```bash
LD_PRELOAD=/usr/lib/libgtk4-layer-shell.so
```

The systemd unit sets the same environment variable.

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
you do not want that.

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
