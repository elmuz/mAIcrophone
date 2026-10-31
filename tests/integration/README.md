# Live compositor smoke tests

`compositor_smoke.py` starts real compositor sessions, creates two GTK windows,
and exercises the same backend methods the daemon uses: focused window/output,
window existence, current focus, and refocus.

## Local usage

Run from the repository root on a machine with Sway, Niri, GTK4, and PyGObject:

```bash
python tests/integration/compositor_smoke.py sway
python tests/integration/compositor_smoke.py niri
python tests/integration/compositor_smoke.py sway niri
```

`all` is equivalent to `sway niri`.

Sway runs directly with its headless backend. Niri is started as a nested
compositor inside a headless Sway session. Temporary configs, sockets, and
processes are isolated in `/tmp` and removed afterwards.

## Hyprland

Run the Hyprland smoke test from inside a real Hyprland session:

```bash
python tests/integration/compositor_smoke.py hyprland
```

Hyprland 0.57 requires newer Wayland protocol versions than the headless
Sway/Niri parents used by this harness advertise, so it cannot be nested in the
same way without a DRM renderer or a self-hosted desktop runner. The Hyprland
mode therefore uses the existing Hyprland session and its real IPC socket.

## CI

The `compositor-smoke` GitHub Actions job runs the Sway and Niri tests inside an
Arch Linux container. Hyprland is covered by unit tests in CI and can be run
live on a self-hosted Hyprland runner with the command above.
