"""Anchored punctuation marks for an in-flight transcript.

While recording, the user can attach ``.`` or ``?`` to the sentence that is
currently visible in the overlay. The ASR final is only available later and
may revise earlier text, so a mark stores a snapshot of the partial transcript
it was attached to. When the final (or a newer partial) arrives, the snapshot
is located with a longest-common-prefix heuristic and the mark is inserted at
the end of the last word that still matches.

This deliberately supports only the two marks that matter in practice. It is
not a general text editor and does not try to place marks mid-word.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

# Names accepted by the IPC/CLI plus their literal shortcuts.
_MARK_CHARS = {
    "period": ".",
    ".": ".",
    "question": "?",
    "?": "?",
}

# A mark is not inserted if the text already ends with terminal punctuation at
# the insertion point, so pressing the shortcut twice cannot produce ".?".
_TERMINAL_PUNCTUATION = ".?!"


def mark_char(name: str) -> str | None:
    """Return the character for a mark name, or ``None`` if unknown."""
    return _MARK_CHARS.get(name.strip().lower())


@dataclass(frozen=True)
class Mark:
    """A punctuation mark anchored to the text visible when it was pressed."""

    anchor: str
    char: str


def _anchor_position(text: str, anchor: str) -> int:
    """Where ``anchor`` ends inside ``text``, best effort.

    Returns ``len(text)`` when the anchor no longer exists. Trailing whitespace
    is skipped so the mark attaches to the preceding word rather than leaving a
    space before it (``"hello ."`` -> ``"hello."``).
    """
    if not anchor:
        return len(text)

    limit = min(len(text), len(anchor))
    matched = 0
    while matched < limit and text[matched] == anchor[matched]:
        matched += 1

    if matched == len(anchor):
        position = matched
    elif matched < len(anchor) // 2:
        # The final diverged too early to trust the anchor.
        return len(text)
    else:
        # Keep the mark after the last complete word that still matches,
        # instead of splitting a partially revised word.
        position = matched
        while position > 0 and not text[position - 1].isspace():
            position -= 1

    while position > 0 and text[position - 1].isspace():
        position -= 1
    return position


def apply_marks(text: str, marks: Iterable[Mark]) -> str:
    """Insert anchored marks into ``text``.

    Marks are inserted right-to-left so earlier positions stay valid. A mark is
    skipped when terminal punctuation already sits at the insertion point.
    """
    edits = [
        (_anchor_position(text, mark.anchor), index, mark.char) for index, mark in enumerate(marks)
    ]
    for position, _index, char in sorted(edits, key=lambda item: (item[0], item[1]), reverse=True):
        if position > 0 and text[position - 1] in _TERMINAL_PUNCTUATION:
            continue
        if position < len(text) and text[position] in _TERMINAL_PUNCTUATION:
            continue
        text = text[:position] + char + text[position:]
    return text
