# Backlog

Architecture work is sequenced in `roadmap.md`. This file tracks user-facing
features and known gaps.

## Next up

- Replace remaining external binary calls (`wtype`; optional fallbacks such as
  `pw-record` and `wl-copy`) with native libraries/APIs where practical, behind
  the backend interfaces from `roadmap.md` step 2. Niri/Sway/Hyprland IPC,
  audio capture (`pulse`), and the default clipboard path are now native.
- Keep the native Niri/clipboard/audio implementations covered by protocol and
  lifecycle unit tests; add real-session smoke tests when a disposable Niri or
  PipeWire session is available.
- Manual punctuation insertion is implemented for `.` and `?` (see below).
  Still open: a comma/exclamation mark, capitalization of the following word,
  and reliable placement when the ASR revises the anchored sentence.

## Recently landed

- Manual `.` / `?` insertion via `maic punctuate` and a global shortcut.
  While recording, the mark is anchored to the partial transcript visible in
  the overlay and inserted into the final at that sentence boundary. When idle
  it is typed at the cursor only if the last target window still has focus.
  Marks are placed with a longest-common-prefix heuristic, so a late ASR
  revision of the anchored sentence can move a mark to the end of the
  previously matching word.

## Later

- Optional scrolling waveform in the overlay status row. Keep the current
  coloured VU meter as the default.
- Optional server-status detail in the overlay error path.
- Improve partial revision detection if nemo-speech exposes fuller partial
  events; the current LCP heuristic cannot reliably detect a revision that
  changes only the first characters.
- Endpointing, multiple finals per session, transcript history.
- Language selector in the overlay; language switching is currently CLI-only.

## Explicitly skipped for now

- Spoken punctuation ("comma", "full stop", etc.).
- Additional compositor backends beyond Niri, Sway, and Hyprland. A generic
  foreign-toplevel backend is a possible later addition.
- Rust rewrite. Re-evaluate only under the gates in `roadmap.md` step 5.
