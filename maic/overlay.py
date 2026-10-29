from __future__ import annotations

import math
from html import escape

import cairo
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gdk, GLib, Gtk, Pango  # noqa: E402

from .config import Config  # noqa: E402
from .niri import OutputInfo  # noqa: E402
from .overlay_null import NullOverlay  # noqa: E402,F401

NORD_BG = "#2E3440"
NORD_BORDER = "#4C566A"
NORD_TEXT = "#ECEFF4"
NORD_MUTED = "#D8DEE9"
NORD_ACCENT = "#88C0D0"
NORD_GREEN = "#A3BE8C"
NORD_YELLOW = "#EBCB8B"
NORD_ORANGE = "#D08770"
NORD_RED = "#BF616A"

STATE_COLORS = {
    "REC": NORD_GREEN,
    "STOP": NORD_YELLOW,
    "COMMIT": NORD_ACCENT,
    "TYPE": NORD_ACCENT,
    "CANCEL": NORD_ORANGE,
    "ERR": NORD_RED,
}


def _load_layer_shell():
    try:
        gi.require_version("Gtk4LayerShell", "1.0")
        from gi.repository import Gtk4LayerShell as layer_shell
    except (ValueError, ImportError) as exc:
        raise RuntimeError(
            "gtk4-layer-shell is missing; install the Arch package "
            "'gtk4-layer-shell' (or set overlay.enabled = false)"
        ) from exc
    return layer_shell


def meter_level(value: float) -> float:
    """Map a PCM peak sample to a 0..1 meter using dBFS."""
    if value <= 0:
        return 0.0
    dbfs = 20.0 * math.log10(min(value, 32768.0) / 32768.0)
    return min(1.0, max(0.0, (dbfs + 60.0) / 60.0))


def vu_bar(value: float, width: int = 14) -> str:
    level = meter_level(value)
    if level <= 0:
        return " " * width
    filled = round(level * width)
    return "█" * filled + " " * (width - filled)


def vu_bar_markup(value: float, width: int = 14) -> str:
    level = meter_level(value)
    if level <= 0:
        return " " * width
    filled = round(level * width)
    green_end = int(width * 0.65)
    yellow_end = int(width * 0.85)
    chars = []
    for i in range(width):
        if i >= filled:
            chars.append(" ")
        elif i < green_end:
            chars.append(f'<span foreground="{NORD_GREEN}">█</span>')
        elif i < yellow_end:
            chars.append(f'<span foreground="{NORD_YELLOW}">█</span>')
        else:
            chars.append(f'<span foreground="{NORD_RED}">█</span>')
    return "".join(chars)


def language_label(code: str) -> str:
    code = (code or "auto").strip()
    if code.lower() == "auto":
        return "AUTO"
    return code.split("-")[0].upper()


def _font_parts(font: str) -> tuple[str, int]:
    fd = Pango.FontDescription.from_string(font or "monospace 14")
    family = fd.get_family() or "monospace"
    size = fd.get_size() / Pango.SCALE if fd.get_size() > 0 else 14
    return family, max(8, round(size))


def _font_steps(base: int, minimum: int, count: int) -> list[int]:
    steps = [base]
    for offset in range(1, count + 1):
        size = max(minimum, base - offset)
        if size < base and size not in steps:
            steps.append(size)
    return steps


class Overlay:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.layer_shell = _load_layer_shell()
        self.window: Gtk.Window | None = None
        self.status: Gtk.Label | None = None
        self.text: Gtk.Label | None = None
        self.scroll: Gtk.ScrolledWindow | None = None
        self.width = 800
        self.content_width = 760
        self.max_height = 400
        self.output: OutputInfo | None = None
        self._hide_source: int | None = None
        self._scroll_source: int | None = None
        self._last_text = ""
        self._last_rms = 0.0
        self._state = "idle"
        self.language = language_label(cfg.language)
        self.server_status = "checking"
        self._font_family, self._font_base = _font_parts(cfg.overlay_font)
        self._font_steps = _font_steps(
            self._font_base, cfg.overlay_min_font_size, cfg.overlay_font_steps
        )
        self._build()

    def _build_css(self) -> bytes:
        family = self._font_family.replace('"', '\\"')
        status_size = max(9, self._font_base - 3)
        step_rules = "\n".join(
            f".maic-text.font-step-{i} {{ font-size: {size}pt; }}"
            for i, size in enumerate(self._font_steps[1:], start=1)
        )
        opacity = self.cfg.overlay_background_opacity
        radius = self.cfg.overlay_corner_radius
        border = self.cfg.overlay_border_width
        return f"""
window.maic-overlay {{
    background-color: transparent;
}}
.maic-box {{
    background-color: rgba(46, 52, 64, {opacity});
    border: {border}px solid {NORD_BORDER};
    border-radius: {radius}px;
}}
.maic-status {{
    font-family: "{family}";
    font-size: {status_size}pt;
    color: {NORD_MUTED};
}}
.maic-text {{
    font-family: "{family}";
    font-size: {self._font_base}pt;
    color: {NORD_TEXT};
}}
{step_rules}
.maic-error {{
    font-family: "{family}";
    font-size: {max(self._font_base - 2, self.cfg.overlay_min_font_size)}pt;
    color: {NORD_RED};
}}
scrolledwindow {{
    background: transparent;
}}
scrollbar {{
    opacity: 0;
    min-width: 0;
    min-height: 0;
}}
""".encode()

    def _build(self) -> None:
        self.window = Gtk.Window()
        self.window.add_css_class("maic-overlay")
        self.window.set_decorated(False)
        self.window.set_resizable(False)
        self.window.set_can_focus(False)
        self.window.set_can_target(False)
        self.window.connect("realize", self._on_realize)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        outer.add_css_class("maic-box")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.set_margin_start(20)
        box.set_margin_end(20)
        box.set_margin_top(12)
        box.set_margin_bottom(18)
        self.status = Gtk.Label(xalign=0)
        self.status.add_css_class("maic-status")
        self.text = Gtk.Label(xalign=0)
        self.text.add_css_class("maic-text")
        self.text.set_wrap(True)
        self.text.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.text.set_selectable(False)
        self.text.set_hexpand(True)

        self.scroll = Gtk.ScrolledWindow()
        self.scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scroll.set_has_frame(False)
        self.scroll.set_overlay_scrolling(True)
        self.scroll.set_propagate_natural_height(False)
        self.scroll.set_hexpand(True)
        self.scroll.set_child(self.text)
        adjustment = self.scroll.get_vadjustment()
        adjustment.connect("changed", self._on_adjustment_changed)

        box.append(self.status)
        box.append(self.scroll)
        outer.append(box)
        self.window.set_child(outer)

        display = Gdk.Display.get_default()
        if display is not None:
            provider = Gtk.CssProvider()
            provider.load_from_data(self._build_css())
            Gtk.StyleContext.add_provider_for_display(
                display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )

        self.layer_shell.init_for_window(self.window)
        self.layer_shell.set_layer(self.window, self.layer_shell.Layer.OVERLAY)
        self.layer_shell.set_namespace(self.window, "maic-overlay")
        self.layer_shell.set_anchor(self.window, self.layer_shell.Edge.BOTTOM, True)
        self.layer_shell.set_anchor(self.window, self.layer_shell.Edge.LEFT, True)
        self.layer_shell.set_anchor(self.window, self.layer_shell.Edge.RIGHT, False)
        self.layer_shell.set_keyboard_mode(self.window, self.layer_shell.KeyboardMode.NONE)
        self.window.set_visible(False)

    def _on_realize(self, window) -> None:
        surface = window.get_surface()
        if surface is not None:
            surface.set_input_region(cairo.Region())

    def _find_monitor(self, output_name: str):
        display = Gdk.Display.get_default()
        if display is None:
            return None
        monitors = display.get_monitors()
        for i in range(monitors.get_n_items()):
            monitor = monitors.get_item(i)
            try:
                connector = monitor.get_connector()
            except Exception:
                connector = None
            if connector == output_name:
                return monitor
        return None

    def _line_height(self, font_size: int) -> int:
        return max(12, round(font_size * 1.45))

    def _count_lines(self, text: str, font_size: int) -> int:
        assert self.text is not None
        layout = Pango.Layout(self.text.get_pango_context())
        layout.set_font_description(
            Pango.FontDescription.from_string(f"{self._font_family} {font_size}")
        )
        layout.set_width(self.content_width * Pango.SCALE)
        layout.set_wrap(Pango.WrapMode.WORD_CHAR)
        layout.set_text(text, -1)
        return max(1, layout.get_line_count())

    def _choose_font(self, text: str) -> tuple[int, int, int]:
        lines = self._count_lines(text, self._font_steps[0])
        if lines <= self.cfg.overlay_max_lines:
            return 0, self._font_steps[0], lines
        for step, size in enumerate(self._font_steps[1:], start=1):
            candidate = self._count_lines(text, size)
            if candidate <= self.cfg.overlay_max_lines:
                return step, size, candidate
        return 0, self._font_steps[0], lines

    def _apply_text(self, text: str) -> None:
        assert self.text is not None and self.window is not None
        assert self.scroll is not None
        step, font_size, lines = self._choose_font(text)
        for i in range(1, len(self._font_steps)):
            self.text.remove_css_class(f"font-step-{i}")
        if step:
            self.text.add_css_class(f"font-step-{step}")

        line_height = self._line_height(font_size)
        chrome_height = 56
        available_lines = max(1, (self.max_height - chrome_height) // line_height)
        visible_lines = max(1, min(lines, self.cfg.overlay_max_lines, available_lines))
        text_height = visible_lines * line_height
        total_height = min(self.max_height, chrome_height + text_height)

        self.text.set_text(text)
        self.text.set_size_request(self.content_width, -1)
        self.scroll.set_min_content_height(-1)
        self.scroll.set_max_content_height(-1)
        self.scroll.set_max_content_height(text_height)
        self.scroll.set_min_content_height(text_height)
        self.window.set_size_request(self.width, total_height)
        self.window.set_default_size(self.width, total_height)
        self.window.queue_resize()
        self._schedule_scroll_to_bottom()

    def _schedule_scroll_to_bottom(self) -> None:
        if self._scroll_source is not None:
            GLib.source_remove(self._scroll_source)
        self._scroll_source = GLib.timeout_add(20, self._scroll_to_bottom)

    def _on_adjustment_changed(self, adjustment) -> None:
        target = adjustment.get_upper() - adjustment.get_page_size()
        target = max(adjustment.get_lower(), target)
        if abs(adjustment.get_value() - target) > 0.5:
            adjustment.set_value(target)

    def _scroll_to_bottom(self) -> bool:
        self._scroll_source = None
        if self.scroll is None:
            return GLib.SOURCE_REMOVE
        adjustment = self.scroll.get_vadjustment()
        if adjustment is not None:
            self._on_adjustment_changed(adjustment)
        return GLib.SOURCE_REMOVE

    def _status_markup(self, state: str, rms: float) -> str:
        state_color = STATE_COLORS.get(state, NORD_MUTED)
        if self.server_status == "online":
            server_color = NORD_GREEN
        elif self.server_status == "offline":
            server_color = NORD_RED
        else:
            server_color = NORD_YELLOW
        rec_dot = f'<span foreground="{NORD_RED}">●</span> ' if state == "REC" else ""
        return (
            f"{rec_dot}"
            f'<span foreground="{state_color}" weight="bold">'
            f"{escape(state.ljust(6))}</span>"
            f" {vu_bar_markup(rms)}  "
            f'<span foreground="{NORD_ACCENT}">{escape(self.language)}</span>  '
            f'<span foreground="{server_color}">●</span> '
            f'<span foreground="{server_color}">{escape(self.server_status)}</span>'
        )

    def _refresh_status(self) -> None:
        if self.status is not None:
            self.status.set_markup(self._status_markup(self._state, self._last_rms))

    def _start_ui(self, output: OutputInfo, state: str) -> bool:
        assert self.window is not None and self.status is not None
        self.output = output
        self.width = min(
            self.cfg.overlay_max_width_px,
            max(320, int(output.width * self.cfg.overlay_width_percent / 100)),
        )
        self.content_width = self.width - 40
        self.max_height = max(120, int(output.height * self.cfg.overlay_max_height_percent / 100))
        monitor = self._find_monitor(output.name)
        if monitor is not None:
            self.layer_shell.set_monitor(self.window, monitor)
        left_margin = max(
            self.cfg.overlay_margin_px,
            (output.width - self.width) // 2,
        )
        self.layer_shell.set_margin(self.window, self.layer_shell.Edge.LEFT, left_margin)
        self.layer_shell.set_margin(
            self.window, self.layer_shell.Edge.BOTTOM, self.cfg.overlay_margin_px
        )
        self._state = state
        self._last_text = ""
        self._last_rms = 0.0
        self._refresh_status()
        self._apply_text("")
        self.window.set_visible(True)
        return False

    def start(self, output: OutputInfo) -> None:
        GLib.idle_add(self._start_ui, output, "REC")

    def set_partial(self, text: str) -> None:
        self._last_text = text
        GLib.idle_add(self._update_text, text)

    def _update_text(self, text: str) -> bool:
        if not self.window or not self.window.get_visible():
            return False
        self._apply_text(text)
        return False

    def set_level(self, rms: float) -> None:
        self._last_rms = rms
        GLib.idle_add(self._update_level, rms)

    def _update_level(self, rms: float) -> bool:
        if not self.window or not self.window.get_visible() or self.status is None:
            return False
        self.status.set_markup(self._status_markup(self._state, rms))
        return False

    def set_state(self, state: str) -> None:
        self._state = state
        GLib.idle_add(self._update_state, state)

    def _update_state(self, state: str) -> bool:
        if self.status is not None:
            self.status.set_markup(self._status_markup(state, self._last_rms))
        return False

    def set_language(self, code: str) -> None:
        self.language = language_label(code)
        GLib.idle_add(self._update_language)

    def _update_language(self) -> bool:
        self._refresh_status()
        return False

    def set_server_status(self, status: str) -> None:
        self.server_status = status
        GLib.idle_add(self._update_server_status)

    def _update_server_status(self) -> bool:
        self._refresh_status()
        return False

    def show_error(self, message: str, timeout_ms: int = 5000) -> None:
        GLib.idle_add(self._show_error_ui, message, timeout_ms)

    def _show_error_ui(self, message: str, timeout_ms: int) -> bool:
        if not self.window or not self.status or not self.text:
            return False
        self.status.set_markup(self._status_markup("ERR", self._last_rms))
        self.text.set_text(message)
        self.text.add_css_class("maic-error")
        self.window.set_size_request(self.width, 100)
        self.window.set_default_size(self.width, 100)
        self.window.set_visible(True)
        if self._hide_source is not None:
            GLib.source_remove(self._hide_source)
        self._hide_source = GLib.timeout_add(timeout_ms, self._hide_error)
        return False

    def _hide_error(self) -> bool:
        self._hide_source = None
        if self.text is not None:
            self.text.remove_css_class("maic-error")
        self.hide()
        return False

    def hide(self) -> None:
        GLib.idle_add(self._hide_ui)

    def _hide_ui(self) -> bool:
        if self.window is not None:
            self.window.set_visible(False)
        return False

    def destroy(self) -> None:
        GLib.idle_add(self._destroy_ui)

    def _destroy_ui(self) -> bool:
        if self.window is not None:
            self.window.destroy()
            self.window = None
        return False
