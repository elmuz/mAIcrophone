from __future__ import annotations

import math
from collections import deque
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

SERVER_LABELS = {
    "online": "live",
    "offline": "offline",
    "checking": "checking",
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


def language_label(code: str) -> str:
    code = (code or "auto").strip()
    if code.lower() == "auto":
        return "AUTO"
    return code.upper()


def _hex_rgb(color: str) -> tuple[int, int, int]:
    digits = color.lstrip("#")
    if len(digits) in {3, 4}:
        digits = "".join(char * 2 for char in digits[:3])
    digits = digits[:6]
    return int(digits[0:2], 16), int(digits[2:4], 16), int(digits[4:6], 16)


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
        self.status_dot: Gtk.DrawingArea | None = None
        self.status_label: Gtk.Label | None = None
        self.waveform: Gtk.DrawingArea | None = None
        self.language_pill: Gtk.Box | None = None
        self.language_label: Gtk.Label | None = None
        self.server_dot: Gtk.Label | None = None
        self.server_label: Gtk.Label | None = None
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
        self._text_final = False
        self._stable_prefix = ""
        self._previous_partial = ""
        self._levels: deque[float] = deque([0.0] * 15, maxlen=15)
        self._wave_current = [0.0] * 15
        self._wave_tick_id: int | None = None
        self._wave_last_frame_us: int = 0
        self._level_queue: deque[float] = deque(maxlen=240)
        self._level_timer_id: int | None = None
        self._level_interval_ms = 16
        self._state = "idle"
        self.language = language_label(cfg.language)
        self.server_status = "checking"
        self._font_family, self._font_base = _font_parts(cfg.overlay_font)
        self._font_steps = _font_steps(
            self._font_base, cfg.overlay_min_font_size, cfg.overlay_font_steps
        )
        self.color_background = cfg.overlay_background_color
        self.color_border = cfg.overlay_border_color
        self.color_text = cfg.overlay_text_color
        self.color_partial = cfg.overlay_partial_color
        self.color_accent = cfg.overlay_accent_color
        self.color_divider = cfg.overlay_divider_color
        self.color_success = cfg.overlay_success_color
        self.color_warning = cfg.overlay_warning_color
        self.color_error = cfg.overlay_error_color
        self.color_cancel = cfg.overlay_cancel_color
        self._build()

    def _build_css(self) -> bytes:
        family = self._font_family.replace('"', '\\"')
        small_size = max(9, self._font_base - 4)
        step_rules = "\n".join(
            f".maic-text.font-step-{i} {{ font-size: {size}pt; }}"
            for i, size in enumerate(self._font_steps[1:], start=1)
        )
        opacity = self.cfg.overlay_background_opacity
        radius = self.cfg.overlay_corner_radius
        border = self.cfg.overlay_border_width
        bg_red, bg_green, bg_blue = _hex_rgb(self.color_background)
        return f"""
window.maic-overlay,
window.maic-overlay.background,
window.maic-overlay.csd,
window.maic-overlay:backdrop,
window.maic-overlay.csd:backdrop {{
    background: none;
    background-color: transparent;
    border: none;
    border-radius: 0;
    box-shadow: none;
}}
.maic-box {{
    background-color: rgba({bg_red}, {bg_green}, {bg_blue}, {opacity});
    border: {border}px solid {self.color_border};
    border-radius: {radius}px;
    box-shadow: none;
}}
.maic-state, .maic-dot {{
    font-family: "{family}";
    font-size: {small_size}pt;
    font-weight: 700;
}}
.maic-pill {{
    background-color: transparent;
    border: 1px solid {self.color_border};
    border-radius: 18px;
}}
.maic-pill-label {{
    font-family: "{family}";
    font-size: {small_size}pt;
    color: {self.color_accent};
}}
.maic-server {{
    font-family: "{family}";
    font-size: {small_size}pt;
}}
.maic-divider {{
    background-color: {self.color_divider};
    min-height: 1px;
}}
.maic-text {{
    font-family: "{family}";
    font-size: {self._font_base}pt;
    color: {self.color_text};
}}
{step_rules}
.maic-error {{
    font-family: "{family}";
    font-size: {max(self._font_base - 2, self.cfg.overlay_min_font_size)}pt;
    color: {self.color_error};
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
        # Replace the theme's automatic window classes (background/csd). They
        # paint a rectangular surface behind the rounded box, which shows up as
        # opaque corners on a layer-shell overlay.
        self.window.set_css_classes(["maic-overlay"])
        self.window.set_decorated(False)
        self.window.set_resizable(False)
        self.window.set_can_focus(False)
        self.window.set_can_target(False)
        self.window.connect("realize", self._on_realize)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        outer.add_css_class("maic-box")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        header.set_margin_start(22)
        header.set_margin_end(22)
        header.set_margin_top(14)
        header.set_margin_bottom(12)

        status_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=7)
        status_box.set_valign(Gtk.Align.CENTER)
        self.status_dot = Gtk.DrawingArea()
        self.status_dot.set_content_width(20)
        self.status_dot.set_content_height(20)
        self.status_dot.set_valign(Gtk.Align.CENTER)
        self.status_dot.set_draw_func(self._draw_status_dot)
        self.status_label = Gtk.Label(xalign=0)
        self.status_label.add_css_class("maic-state")
        status_box.append(self.status_dot)
        status_box.append(self.status_label)
        header.append(status_box)

        self.waveform = Gtk.DrawingArea()
        self.waveform.set_content_width(154)
        self.waveform.set_content_height(36)
        self.waveform.set_hexpand(True)
        self.waveform.set_halign(Gtk.Align.CENTER)
        self.waveform.set_valign(Gtk.Align.CENTER)
        self.waveform.set_draw_func(self._draw_waveform)
        header.append(self.waveform)

        right = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        right.set_valign(Gtk.Align.CENTER)
        self.language_pill = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.language_pill.add_css_class("maic-pill")
        self.language_pill.set_valign(Gtk.Align.CENTER)
        self.language_label = Gtk.Label()
        self.language_label.add_css_class("maic-pill-label")
        self.language_label.set_margin_start(16)
        self.language_label.set_margin_end(16)
        self.language_label.set_margin_top(5)
        self.language_label.set_margin_bottom(5)
        self.language_pill.append(self.language_label)
        server_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        server_box.set_valign(Gtk.Align.CENTER)
        self.server_dot = Gtk.Label()
        self.server_dot.add_css_class("maic-dot")
        self.server_label = Gtk.Label(xalign=0)
        self.server_label.add_css_class("maic-server")
        server_box.append(self.server_dot)
        server_box.append(self.server_label)
        right.append(self.language_pill)
        right.append(server_box)
        header.append(right)

        footer = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        footer.add_css_class("maic-divider")
        footer.set_margin_start(22)
        footer.set_margin_end(22)

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
        self.scroll.set_margin_start(22)
        self.scroll.set_margin_end(22)
        self.scroll.set_margin_top(14)
        self.scroll.set_margin_bottom(16)
        self.scroll.set_child(self.text)
        adjustment = self.scroll.get_vadjustment()
        adjustment.connect("changed", self._on_adjustment_changed)

        box.append(header)
        box.append(footer)
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
        chrome_height = 96
        available_lines = max(1, (self.max_height - chrome_height) // line_height)
        visible_lines = max(1, min(lines, self.cfg.overlay_max_lines, available_lines))
        text_height = visible_lines * line_height
        total_height = min(self.max_height, chrome_height + text_height)

        if self._text_final:
            markup = f'<span foreground="{self.color_text}">{escape(text)}</span>' if text else ""
        else:
            stable = self._stable_prefix_for(text)
            if stable and text.startswith(stable):
                rest = text[len(stable) :]
                markup = (
                    f'<span foreground="{self.color_text}">{escape(stable)}</span>'
                    f'<span foreground="{self.color_partial}">{escape(rest)}</span>'
                )
            else:
                markup = f'<span foreground="{self.color_partial}">{escape(text)}</span>'
            markup += f'<span foreground="{self.color_accent}">▌</span>'
        self.text.set_markup(markup)
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

    def _stable_prefix_for(self, text: str) -> str:
        limit = min(len(self._previous_partial), len(text))
        common = 0
        while common < limit and self._previous_partial[common] == text[common]:
            common += 1
        self._previous_partial = text
        candidate = text[:common]
        boundary = max(candidate.rfind(" "), candidate.rfind("\n"), candidate.rfind("\t"))
        candidate = candidate[: boundary + 1] if boundary >= 0 else ""
        # Keep the last word provisional even when the ASR repeats a partial.
        if candidate and candidate == text:
            trimmed = candidate.rstrip()
            boundary = max(trimmed.rfind(" "), trimmed.rfind("\n"), trimmed.rfind("\t"))
            candidate = candidate[: boundary + 1] if boundary >= 0 else ""
        self._stable_prefix = candidate
        return candidate

    def _state_color(self, state: str) -> str:
        if state in {"REC", "ERR"}:
            return self.color_error
        if state in {"STOP", "CHECK"}:
            return self.color_warning
        if state == "CANCEL":
            return self.color_cancel
        return self.color_accent

    def _server_color(self, status: str) -> str:
        if status == "online":
            return self.color_success
        if status == "offline":
            return self.color_error
        return self.color_warning

    def _draw_status_dot(self, area, cr, width: int, height: int) -> None:
        rgba = Gdk.RGBA()
        if not rgba.parse(self._state_color(self._state)):
            rgba.parse(self.color_text)
        cx, cy = width / 2, height / 2
        cr.set_source_rgba(rgba.red, rgba.green, rgba.blue, 0.22)
        cr.arc(cx, cy, 8, 0, 2 * math.pi)
        cr.fill()
        cr.set_source_rgba(rgba.red, rgba.green, rgba.blue, 1.0)
        cr.arc(cx, cy, 4.5, 0, 2 * math.pi)
        cr.fill()

    def _stop_wave_animation(self) -> None:
        if self._wave_tick_id is not None and self.waveform is not None:
            self.waveform.remove_tick_callback(self._wave_tick_id)
        self._wave_tick_id = None
        self._wave_last_frame_us = 0
        if self._level_timer_id is not None:
            GLib.source_remove(self._level_timer_id)
        self._level_timer_id = None
        self._level_queue.clear()

    def _animate_waveform(self, area, frame_clock) -> bool:
        """Frame-clock driven easing; called on every compositor frame."""
        now_us = frame_clock.get_frame_time()
        if self._wave_last_frame_us:
            # Clamp dt (ms) to survive long pauses (occlusion, suspend, ...).
            dt_ms = max(0.0, min(100.0, (now_us - self._wave_last_frame_us) / 1000.0))
        else:
            dt_ms = 16.7
        self._wave_last_frame_us = now_us
        if not self.window or not self.window.get_visible():
            self._wave_tick_id = None
            self._wave_last_frame_us = 0
            return GLib.SOURCE_REMOVE
        # Match the old per-16.7ms-tick factor of 0.45 with a time-based
        # exponential smoothing so speed is independent of frame rate.
        easing = 1.0 - math.exp(-dt_ms / 27.9)
        for i, target in enumerate(self._levels):
            current = self._wave_current[i]
            delta = target - current
            self._wave_current[i] = target if abs(delta) < 0.005 else current + delta * easing
        if self.waveform is not None:
            self.waveform.queue_draw()
        return GLib.SOURCE_CONTINUE

    def _draw_waveform(self, area, cr, width: int, height: int) -> None:
        bars = len(self._wave_current)
        bar_width = 4
        gap = 6
        total_width = bars * bar_width + (bars - 1) * gap
        x = (width - total_width) / 2
        mid = height / 2
        red, green, blue = _hex_rgb(self.color_accent)
        cr.set_source_rgba(red / 255, green / 255, blue / 255, 0.95)
        for level in self._wave_current:
            bar_height = 5 + level * 31
            y = mid - bar_height / 2
            radius = min(2.0, bar_width / 2, bar_height / 2)
            cr.new_sub_path()
            cr.arc(x + bar_width - radius, y + radius, radius, -math.pi / 2, 0)
            cr.arc(x + bar_width - radius, y + bar_height - radius, radius, 0, math.pi / 2)
            cr.arc(x + radius, y + bar_height - radius, radius, math.pi / 2, math.pi)
            cr.arc(x + radius, y + radius, radius, math.pi, 3 * math.pi / 2)
            cr.close_path()
            cr.fill()
            x += bar_width + gap

    def _refresh_status(self) -> None:
        state_color = self._state_color(self._state)
        if self.status_dot is not None:
            self.status_dot.queue_draw()
        if self.status_label is not None:
            self.status_label.set_markup(
                f'<span foreground="{state_color}">{escape(self._state)}</span>'
            )
        if self.language_label is not None:
            self.language_label.set_markup(
                f'<span foreground="{self.color_accent}">{escape(self.language)}</span>'
            )
        server_color = self._server_color(self.server_status)
        server_text = SERVER_LABELS.get(self.server_status, self.server_status)
        if self.server_dot is not None:
            self.server_dot.set_markup(f'<span foreground="{server_color}">●</span>')
        if self.server_label is not None:
            self.server_label.set_markup(
                f'<span foreground="{server_color}">{escape(server_text)}</span>'
            )
        if self.waveform is not None:
            self.waveform.queue_draw()

    def _start_ui(self, output: OutputInfo, state: str) -> bool:
        assert self.window is not None
        self.output = output
        self.width = min(
            self.cfg.overlay_max_width_px,
            max(320, int(output.width * self.cfg.overlay_width_percent / 100)),
        )
        self.content_width = self.width - 44
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
        self._text_final = False
        self._stable_prefix = ""
        self._previous_partial = ""
        self._levels = deque([0.0] * 15, maxlen=15)
        self._wave_current = [0.0] * 15
        if self._level_timer_id is not None:
            GLib.source_remove(self._level_timer_id)
            self._level_timer_id = None
        self._level_queue.clear()
        if self._wave_tick_id is None and self.waveform is not None:
            self._wave_tick_id = self.waveform.add_tick_callback(self._animate_waveform)
        self._refresh_status()
        self._apply_text("")
        self.window.set_visible(True)
        return False

    def start(self, output: OutputInfo) -> None:
        GLib.idle_add(self._start_ui, output, "REC")

    def set_partial(self, text: str) -> None:
        self._last_text = text
        GLib.idle_add(self._update_text, text)

    def set_final(self, text: str) -> None:
        self._last_text = text
        GLib.idle_add(self._update_final, text)

    def _update_text(self, text: str) -> bool:
        if not self.window or not self.window.get_visible():
            return False
        self._apply_text(text)
        return False

    def _update_final(self, text: str) -> bool:
        if not self.window or not self.window.get_visible():
            return False
        self._text_final = True
        self._apply_text(text)
        return False

    def set_level(self, rms: float) -> None:
        self._last_rms = rms
        self.set_levels([rms], interval_ms=16)

    def set_levels(self, levels: list[float], interval_ms: int = 25) -> None:
        """Queue meter samples and consume them at a steady display cadence.

        Audio capture can deliver samples in uneven bursts. A single paced
        queue prevents overlapping GLib timers from shifting several bars at
        once and then freezing until the next audio chunk arrives.
        """
        if levels:
            GLib.idle_add(self._enqueue_levels, list(levels), max(1, interval_ms))

    def _enqueue_levels(self, levels: list[float], interval_ms: int) -> bool:
        if not self.window or not self.window.get_visible():
            return False
        self._level_queue.extend(meter_level(level) for level in levels)
        self._level_interval_ms = interval_ms
        if self._level_timer_id is None:
            self._level_timer_id = GLib.timeout_add(
                self._level_interval_ms, self._drain_level_queue
            )
        return False

    def _drain_level_queue(self) -> bool:
        if not self.window or not self.window.get_visible():
            self._level_timer_id = None
            self._level_queue.clear()
            return GLib.SOURCE_REMOVE
        if self._level_queue:
            self._levels.append(self._level_queue.popleft())
        if self._level_queue:
            self._level_timer_id = GLib.timeout_add(
                self._level_interval_ms, self._drain_level_queue
            )
        else:
            self._level_timer_id = None
        return GLib.SOURCE_REMOVE

    def set_state(self, state: str) -> None:
        self._state = state
        GLib.idle_add(self._update_state, state)

    def _update_state(self, state: str) -> bool:
        self._state = state
        self._refresh_status()
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
        if not self.window or not self.text:
            return False
        self._state = "ERR"
        self._refresh_status()
        self.text.set_markup(f'<span foreground="{self.color_error}">{escape(message)}</span>')
        self.text.add_css_class("maic-error")
        self.window.set_size_request(self.width, 110)
        self.window.set_default_size(self.width, 110)
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
        self._stop_wave_animation()
        if self.window is not None:
            self.window.set_visible(False)
        return False

    def destroy(self) -> None:
        GLib.idle_add(self._destroy_ui)

    def _destroy_ui(self) -> bool:
        self._stop_wave_animation()
        if self.window is not None:
            self.window.destroy()
            self.window = None
        return False
