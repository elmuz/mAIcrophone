"""Provider-independent transcript normalization."""

from __future__ import annotations

from .config import Config


def apply_replacements(text: str, cfg: Config) -> str:
    for source, target in cfg.replacements.items():
        text = text.replace(source, target)
    return text


def normalize_text(text: str, cfg: Config) -> str:
    text = apply_replacements(text, cfg)
    if cfg.multiline == "space":
        text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", " ")
    text = " ".join(text.split()) if cfg.multiline == "space" else text.strip()
    return text
