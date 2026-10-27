from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Config:
    server_url: str = "ws://127.0.0.1:8080/v1/audio/transcriptions/realtime"
    language: str = "auto"
    punctuation: bool = True
    audio_source: str = ""
    sample_rate: int = 16000
    chunk_ms: int = 100

    overlay_enabled: bool = True
    overlay_width_percent: int = 70
    overlay_max_width_px: int = 1000
    overlay_max_height_percent: int = 30
    overlay_margin_px: int = 24
    overlay_font: str = "monospace 12"

    copy_to_clipboard: bool = True
    primary_selection: bool = True
    return_to_target: bool = True
    multiline: str = "space"  # space | shift-enter | clipboard
    long_text_threshold: int = 400
    replacements: dict[str, str] = field(default_factory=dict)

    @property
    def chunk_bytes(self) -> int:
        return int(self.sample_rate * 2 * self.chunk_ms / 1000)

    @property
    def ready_url(self) -> str:
        url = self.server_url
        if url.startswith("wss://"):
            url = "https://" + url[len("wss://") :]
        elif url.startswith("ws://"):
            url = "http://" + url[len("ws://") :]
        base, _, _ = url.partition("/v1/")
        return base + "/ready"


def config_path() -> Path:
    explicit = os.environ.get("MAIC_CONFIG")
    if explicit:
        return Path(explicit).expanduser()
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "maic" / "config.toml"


def _section(raw: dict, name: str) -> dict:
    value = raw.get(name, {})
    return value if isinstance(value, dict) else {}


def load_config(path: Path | None = None) -> Config:
    cfg = Config()
    path = path or config_path()
    if path.exists():
        with path.open("rb") as fh:
            raw = tomllib.load(fh)
    else:
        raw = {}

    server = _section(raw, "server")
    audio = _section(raw, "audio")
    asr = _section(raw, "asr")
    overlay = _section(raw, "overlay")
    injection = _section(raw, "injection")

    cfg.server_url = str(server.get("url", cfg.server_url))
    cfg.language = str(asr.get("language", cfg.language))
    cfg.punctuation = bool(asr.get("punctuation", cfg.punctuation))
    cfg.audio_source = str(audio.get("source", cfg.audio_source))
    cfg.sample_rate = int(audio.get("sample_rate", cfg.sample_rate))
    cfg.chunk_ms = int(audio.get("chunk_ms", cfg.chunk_ms))

    cfg.overlay_enabled = bool(overlay.get("enabled", cfg.overlay_enabled))
    cfg.overlay_width_percent = int(
        overlay.get("width_percent", cfg.overlay_width_percent)
    )
    cfg.overlay_max_width_px = int(
        overlay.get("max_width_px", cfg.overlay_max_width_px)
    )
    cfg.overlay_max_height_percent = int(
        overlay.get("max_height_percent", cfg.overlay_max_height_percent)
    )
    cfg.overlay_margin_px = int(overlay.get("margin_px", cfg.overlay_margin_px))
    cfg.overlay_font = str(overlay.get("font", cfg.overlay_font))

    cfg.copy_to_clipboard = bool(
        injection.get("copy_to_clipboard", cfg.copy_to_clipboard)
    )
    cfg.primary_selection = bool(
        injection.get("primary_selection", cfg.primary_selection)
    )
    cfg.return_to_target = bool(
        injection.get("return_to_target", cfg.return_to_target)
    )
    cfg.multiline = str(injection.get("multiline", cfg.multiline))
    cfg.long_text_threshold = int(
        injection.get("long_text_threshold", cfg.long_text_threshold)
    )
    replacements = raw.get("replacements", {})
    if isinstance(replacements, dict):
        cfg.replacements = {str(k): str(v) for k, v in replacements.items()}

    env_url = os.environ.get("MAIC_SERVER_URL")
    if env_url:
        cfg.server_url = env_url
    env_lang = os.environ.get("MAIC_LANGUAGE")
    if env_lang:
        cfg.language = env_lang

    if cfg.multiline not in {"space", "shift-enter", "clipboard"}:
        raise ValueError(f"invalid injection.multiline: {cfg.multiline!r}")
    if not 8_000 <= cfg.sample_rate <= 96_000:
        raise ValueError("audio.sample_rate must be between 8000 and 96000")
    if not 20 <= cfg.chunk_ms <= 1000:
        raise ValueError("audio.chunk_ms must be between 20 and 1000")

    return cfg
