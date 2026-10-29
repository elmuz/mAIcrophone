# TODO

Architecture work is sequenced in `PLAN.md`. This file tracks user-facing
features and known gaps.

## Next up

- Replace external binary calls (`niri`, `wtype`, `pw-record`, and the
  `wl-copy` clipboard fallback) with native libraries/APIs where practical,
  behind the backend interfaces from `PLAN.md` step 2.
- Manual punctuation insertion via a shortcut. The model punctuation is often
  weak in practice, especially for questions. Consider a post-injection or
  pre-injection command that appends/edits `.`, `,`, `?`, or `!` without
  re-recording.

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
- Non-Niri compositor support. Backend seams are being prepared, but no second
  compositor is planned yet.
- Rust rewrite. Re-evaluate only under the gates in `PLAN.md` step 5.
