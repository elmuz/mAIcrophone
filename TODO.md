# TODO

## Backlog

- Manual punctuation insertion via a shortcut. The model punctuation is often
  weak in practice, especially for questions. Consider a post-injection or
  pre-injection command that appends/edits `.`, `,`, `?`, or `!` without
  re-recording.
- Optional scrolling waveform in the overlay status row. Keep the current
  coloured VU meter as the default.
- Replace external binary calls (`niri`, `wtype`, `pw-record`, clipboard tools)
  with native libraries/APIs where practical, behind backend interfaces.
- Optional server-status detail in the overlay error path.

## Explicitly skipped for now

- Spoken punctuation ("comma", "full stop", etc.).
- Transcript history.
- Endpointing / multiple finals per session.
- Non-Niri compositor support.
- Rust rewrite.
