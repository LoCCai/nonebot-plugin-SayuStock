"""Host-aware post theme bridge for every SayuStock HTML card.

The QWeb/Qiqi host exposes its day/night theme through
``src.utils.report_theme``:

* ``post_theme_context()`` returns ``post_theme``, ``post_mode`` and
  ``post_theme_css``;
* ``graph_palette(context)`` returns the eight graph colours.

The plugin is also installed standalone.  When that host module is absent (or
returns an unusable value), the local dark palette is used instead.  The host
is consulted on every render: the theme is deliberately not cached, because a
day/night switch must take effect on the next card or image.
"""

from __future__ import annotations

import re
import html as html_lib
import inspect
import logging
import importlib
from typing import Any, Mapping
from dataclasses import dataclass

from .card_frame import apply_frame

LOGGER = logging.getLogger(__name__)

HOST_THEME_MODULE = "src.utils.report_theme"
PALETTE_KEYS = (
    "background",
    "surface",
    "text",
    "muted",
    "accent",
    "secondary",
    "pink",
    "border",
)
DEFAULT_POST_THEME = "sayustock-github-dark"
DEFAULT_POST_MODE = "dark"

FALLBACK_PALETTE: dict[str, str] = {
    "background": "#0b0e13",
    "surface": "#161b22",
    "text": "#e6edf3",
    "muted": "#8b949e",
    "accent": "#58a6ff",
    "secondary": "#d29922",
    "pink": "#db61a2",
    "border": "#30363d",
}

_HEX3 = re.compile(r"^#([0-9a-fA-F]{3})$")
_HEX6 = re.compile(r"^#([0-9a-fA-F]{6})$")
_HEX8 = re.compile(r"^#([0-9a-fA-F]{8})$")
_CSS_COLOR = re.compile(r"^(?:#[0-9a-fA-F]{3,8}|(?:rgb|rgba|hsl|hsla)\([0-9.,% +/\-]+\)|[a-zA-Z]{3,20})$")
_HEX_TOKEN = re.compile(r"#[0-9a-fA-F]{6}")
_STYLE_BLOCK = re.compile(r"(?is)^\s*<style[^>]*>(.*?)</style>\s*$")
_OWN_STYLE = re.compile(r"(?is)<style\s+id=[\"']sayustock-post-theme[\"'][^>]*>.*?</style>")
_HTML_TAG = re.compile(r"(?is)<html([^>]*)>")
_HEAD_CLOSE = re.compile(r"(?is)</head\s*>")
_BODY_OPEN = re.compile(r"(?is)<body([^>]*)>")


@dataclass(frozen=True, slots=True)
class ThemePalette:
    """Extended palette used by the HTML builders and SVG charts."""

    background: str
    surface: str
    text: str
    muted: str
    accent: str
    secondary: str
    pink: str
    border: str
    up: str
    down: str
    flat: str
    up_text: str
    down_text: str
    page_top: str
    page_bottom: str
    surface_alt: str
    grid: str
    grid_soft: str
    divider: str
    text_soft: str
    dim: str
    accent_soft: str
    warning: str
    series: tuple[str, ...]

    def graph(self) -> dict[str, str]:
        """The eight colours promised by the host ``graph_palette``."""
        return {key: getattr(self, key) for key in PALETTE_KEYS}


class ThemeContext(dict[str, Any]):
    """Small mapping with attribute access for template/tool ergonomics."""

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError as error:
            raise AttributeError(name) from error


def _text(value: Any, fallback: str = "") -> str:
    if value is None:
        return fallback
    result = str(value).strip()
    return result or fallback


def _normalize_color(value: Any, fallback: str) -> str:
    raw = _text(value)
    if raw and "<" not in raw and ">" not in raw and ";" not in raw and _CSS_COLOR.fullmatch(raw):
        return raw
    return fallback


def _parse_rgb(value: str) -> tuple[int, int, int] | None:
    raw = value.strip()
    match = _HEX3.fullmatch(raw)
    if match:
        chars = match.group(1)
        return int(chars[0] * 2, 16), int(chars[1] * 2, 16), int(chars[2] * 2, 16)
    match = _HEX6.fullmatch(raw)
    if match:
        chars = match.group(1)
        return int(chars[0:2], 16), int(chars[2:4], 16), int(chars[4:6], 16)
    match = _HEX8.fullmatch(raw)
    if match:
        chars = match.group(1)
        return int(chars[0:2], 16), int(chars[2:4], 16), int(chars[4:6], 16)
    match = re.fullmatch(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)", raw)
    if match:
        return min(255, int(match.group(1))), min(255, int(match.group(2))), min(255, int(match.group(3)))
    return None


def _hex(value: tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*(max(0, min(255, channel)) for channel in value))


def mix(color: str, other: str, share: float) -> str:
    """Blend ``share`` of ``color`` over ``other``, like the upstream palette."""
    left = _parse_rgb(color)
    right = _parse_rgb(other)
    if left is None or right is None:
        return color
    ratio = max(0.0, min(1.0, float(share)))
    return _hex(tuple(round(a * ratio + b * (1 - ratio)) for a, b in zip(left, right)))


def rgba(color: str, alpha: float) -> str:
    rgb = _parse_rgb(color)
    if rgb is None:
        rgb = (128, 128, 128)
    return f"rgba({rgb[0]},{rgb[1]},{rgb[2]},{max(0.0, min(1.0, alpha)):.2f})"


def _luminance(color: str) -> float:
    rgb = _parse_rgb(color)
    if rgb is None:
        return 0.0
    channels = []
    for raw in rgb:
        value = raw / 255.0
        channels.append(value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4)
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def _is_dark_mode(mode: str, background: str) -> bool:
    normalized = (mode or "").strip().lower()
    if normalized in {"light", "day", "日", "日间", "白天", "明亮"}:
        return False
    if normalized in {"dark", "night", "夜", "夜间", "暗", "深色"}:
        return True
    return _luminance(background) < 0.45


def _mapping_value(raw: Any, *names: str) -> Any:
    if isinstance(raw, Mapping):
        for name in names:
            if name in raw and raw[name] is not None:
                return raw[name]
        return None
    for name in names:
        value = getattr(raw, name, None)
        if value is not None:
            return value
    return None


def _call_host(module: Any, name: str, *args: Any) -> Any:
    function = getattr(module, name, None)
    if not callable(function):
        return None
    try:
        result = function(*args)
    except Exception as error:  # host code is an integration boundary
        LOGGER.debug("[SayuStock] 宿主主题 %s 调用失败: %s", name, error)
        return None
    if inspect.isawaitable(result):
        close = getattr(result, "close", None)
        if callable(close):
            close()
        LOGGER.debug("[SayuStock] 宿主主题 %s 返回异步结果，当前渲染路径使用回退主题", name)
        return None
    return result


def _host_module() -> Any | None:
    try:
        return importlib.import_module(HOST_THEME_MODULE)
    except Exception:
        return None


def _graph_from_value(value: Any) -> dict[str, str] | None:
    if value is None:
        return None
    found: dict[str, str] = {}
    for key in PALETTE_KEYS:
        raw = value.get(key) if isinstance(value, Mapping) else getattr(value, key, None)
        if raw is not None:
            found[key] = _normalize_color(raw, FALLBACK_PALETTE[key])
    return found or None


def _extract_palette(raw: Any) -> dict[str, str]:
    if isinstance(raw, Mapping):
        nested = raw.get("palette")
        direct = _graph_from_value(raw)
        graph = _graph_from_value(nested)
        return {**FALLBACK_PALETTE, **(direct or {}), **(graph or {})}
    nested = getattr(raw, "palette", None)
    direct = _graph_from_value(nested)
    return {**FALLBACK_PALETTE, **(direct or {})}


def _host_palette(module: Any | None, raw: Any) -> dict[str, str]:
    graph: dict[str, str] = {}
    if module is not None:
        value = _call_host(module, "graph_palette", raw)
        found = _graph_from_value(value)
        if found:
            graph.update(found)
    extracted = _extract_palette(raw)
    for key in PALETTE_KEYS:
        graph.setdefault(key, extracted[key])
    return graph


def _extended_palette(graph: Mapping[str, str]) -> ThemePalette:
    background = _normalize_color(graph.get("background"), FALLBACK_PALETTE["background"])
    surface = _normalize_color(graph.get("surface"), FALLBACK_PALETTE["surface"])
    text = _normalize_color(graph.get("text"), FALLBACK_PALETTE["text"])
    muted = _normalize_color(graph.get("muted"), FALLBACK_PALETTE["muted"])
    accent = _normalize_color(graph.get("accent"), FALLBACK_PALETTE["accent"])
    secondary = _normalize_color(graph.get("secondary"), FALLBACK_PALETTE["secondary"])
    pink = _normalize_color(graph.get("pink"), FALLBACK_PALETTE["pink"])
    border = _normalize_color(graph.get("border"), FALLBACK_PALETTE["border"])
    dark = _luminance(background) < 0.35

    up = "#f6465d" if dark else "#c62828"
    down = "#2ebd85" if dark else "#0a7a4b"
    flat = muted
    up_text = mix(up, "#ffffff", 0.36) if dark else mix(up, "#000000", 0.12)
    down_text = mix(down, "#ffffff", 0.36) if dark else mix(down, "#000000", 0.12)
    page_top = mix(surface, background, 0.24 if dark else 0.14)
    page_bottom = mix(background, "#000000" if dark else "#ffffff", 0.08)
    surface_alt = mix(surface, background, 0.44)
    grid = mix(border, background, 0.55)
    grid_soft = mix(border, background, 0.24)
    divider = mix(border, text, 0.36)
    text_soft = mix(text, background, 0.82)
    dim = mix(muted, background, 0.65)
    accent_soft = mix(accent, surface, 0.26)
    warning = secondary if secondary != accent else up
    series = (
        accent,
        secondary,
        pink,
        mix(accent, text, 0.28),
        mix(secondary, text, 0.28),
        mix(pink, accent, 0.5),
        mix(accent, pink, 0.55),
        mix(secondary, accent, 0.45),
        mix(text, accent, 0.55),
        mix(muted, secondary, 0.45),
    )
    return ThemePalette(
        background=background,
        surface=surface,
        text=text,
        muted=muted,
        accent=accent,
        secondary=secondary,
        pink=pink,
        border=border,
        up=up,
        down=down,
        flat=flat,
        up_text=up_text,
        down_text=down_text,
        page_top=page_top,
        page_bottom=page_bottom,
        surface_alt=surface_alt,
        grid=grid,
        grid_soft=grid_soft,
        divider=divider,
        text_soft=text_soft,
        dim=dim,
        accent_soft=accent_soft,
        warning=warning,
        series=series,
    )


def resolve_theme() -> ThemeContext:
    """Read the host theme, falling back to the bundled dark palette.

    The host is called on every render so a day/night switch is picked up
    immediately.  Returned mappings are copied and never mutated.
    """
    module = _host_module()
    raw = _call_host(module, "post_theme_context") if module is not None else None
    graph = _host_palette(module, raw)
    post_theme = _text(_mapping_value(raw, "post_theme", "theme", "name"), DEFAULT_POST_THEME)
    post_mode = _text(_mapping_value(raw, "post_mode", "mode", "day_night"), "")
    raw_css = _mapping_value(raw, "post_theme_css", "theme_css", "css")
    post_theme_css = "" if raw_css is None else str(raw_css)
    if not post_mode:
        post_mode = DEFAULT_POST_MODE if _is_dark_mode("", graph["background"]) else "light"
    mode = "dark" if _is_dark_mode(post_mode, graph["background"]) else "light"
    return ThemeContext(
        post_theme=post_theme,
        post_mode=post_mode,
        post_theme_css=post_theme_css,
        palette=dict(graph),
        mode=mode,
        host=module is not None,
    )


def post_theme_context() -> ThemeContext:
    """Return the normalized context used by the plugin renderers."""
    return resolve_theme()


def graph_palette(context: Mapping[str, Any] | None = None) -> dict[str, str]:
    """Return exactly the eight host graph colours.

    ``context`` may be a :class:`ThemeContext`, a raw host context or a plain
    mapping that already contains the palette keys.
    """
    if context is None:
        return dict(resolve_theme()["palette"])
    if isinstance(context, Mapping):
        nested = context.get("palette")
        direct = _graph_from_value(context)
        graph = _graph_from_value(nested)
        if direct or graph:
            return {**FALLBACK_PALETTE, **(direct or {}), **(graph or {})}
    module = _host_module()
    found = _graph_from_value(_call_host(module, "graph_palette", context) if module is not None else None)
    if found:
        return {**FALLBACK_PALETTE, **found}
    return dict(FALLBACK_PALETTE)


def theme_palette(context: Mapping[str, Any] | None = None) -> ThemePalette:
    """Build the extended palette used by HTML and SVG output."""
    resolved = context if context is not None else resolve_theme()
    graph = graph_palette(resolved)
    return _extended_palette(graph)


def _style_inner(css: str) -> str:
    match = _STYLE_BLOCK.match(css or "")
    return match.group(1) if match else (css or "")


def _role_css(palette: ThemePalette, mode: str) -> str:
    scheme = "dark" if mode == "dark" else "light"
    series = palette.series
    return f""":root {{
  color-scheme: {scheme};
  --ss-background: {palette.background};
  --ss-bg: {palette.background};
  --ss-surface: {palette.surface};
  --ss-surface-alt: {palette.surface_alt};
  --ss-text: {palette.text};
  --ss-text-soft: {palette.text_soft};
  --ss-muted: {palette.muted};
  --ss-dim: {palette.dim};
  --ss-accent: {palette.accent};
  --ss-secondary: {palette.secondary};
  --ss-pink: {palette.pink};
  --ss-border: {palette.border};
  --ss-grid: {palette.grid};
  --ss-grid-soft: {palette.grid_soft};
  --ss-divider: {palette.divider};
  --ss-page-top: {palette.page_top};
  --ss-page-bottom: {palette.page_bottom};
  --ss-accent-soft: {palette.accent_soft};
  --ss-warning: {palette.warning};
  --ss-up: {palette.up};
  --ss-down: {palette.down};
  --ss-flat: {palette.flat};
  --ss-up-text: {palette.up_text};
  --ss-down-text: {palette.down_text};
  --ss-up-card: {mix(palette.up, palette.surface, 0.20)};
  --ss-down-card: {mix(palette.down, palette.surface, 0.20)};
  --ss-flat-card: {mix(palette.flat, palette.surface, 0.16)};
  --ss-series-1: {series[0]};
  --ss-series-2: {series[1]};
  --ss-series-3: {series[2]};
  --ss-series-4: {series[3]};
  --ss-series-5: {series[4]};
  --ss-series-6: {series[5]};
  --ss-series-7: {series[6]};
  --ss-series-8: {series[7]};
  --ss-series-9: {series[8]};
  --ss-series-10: {series[9]};
  --bg: {palette.background};
  --panel: {palette.surface};
  --panel2: {palette.surface_alt};
  --text: {palette.text};
  --muted: {palette.muted};
  --line: {palette.border};
  --gold: {palette.secondary};
  --blue: {palette.accent};
  --up: {palette.up};
  --down: {palette.down};
  --flat: {palette.flat};
}}"""


def _compat_css() -> str:
    return """
html, body { color: var(--ss-text); background-color: var(--ss-bg); }
body { margin: 0; }
a { color: var(--ss-accent); }
html[data-card-theme] body, html[data-card-theme] .chart-page {
  background-color:var(--ss-bg);background-image:var(--background-image,none);
  font-family:var(--font-family-display,"Qiqi Sans","WenQuanYi Zen Hei",sans-serif);
}
html[data-card-theme] .wrap, html[data-card-theme] .chart-page {
  box-sizing:border-box;max-width:100%;margin-inline:auto;
}
html[data-card-theme] .wrap { width:min(100%,var(--stock-width,920px)); }
.stock-heading {
  display:flex;align-items:stretch;justify-content:space-between;gap:0;margin:0 0 18px;
  padding:0;background:var(--surface-accent,var(--ss-surface-alt));color:var(--ss-text);
  border:1px solid var(--ss-border);border-radius:var(--menu-radius,14px);overflow:hidden;
}
html[data-card-theme] body:has(.stock-upstream-content) { max-width:100%;margin-inline:auto; }
.stock-upstream-content { max-width:100%;min-width:0;overflow-x:auto; }
.stock-upstream-content .page { box-sizing:border-box;max-width:100%;margin-inline:auto; }
.stock-upstream-content .ex-table, .stock-upstream-content .matrix { overflow-x:auto; }
@media(max-width:640px) {
  .stock-upstream-content .page { padding:12px; }
  .stock-upstream-content .ex-grid:is(.three,.four,.five,.six) { grid-template-columns:repeat(2,minmax(0,1fr)); }
  .stock-upstream-content .ex-head { flex-wrap:wrap; }
}
.stock-heading-copy { flex:1;min-width:0;align-self:center;padding:16px clamp(12px,3vw,30px);overflow-wrap:anywhere; }
.stock-heading h1 { margin:0;font-size:clamp(22px,3.3vw,38px);line-height:1.25; }
.stock-heading-copy :is(.sub,.dash-sub,.news-source,.meta,.chart-subtitle,.news-count) {
  color:var(--ss-muted);margin:5px 0 0;
}
.stock-heading-copy .chart-kicker { margin:0 0 4px; }
.stock-heading-art {
  display:block;align-self:stretch;width:clamp(110px,32%,370px);height:auto;min-height:96px;
  max-height:166px;object-fit:cover;object-position:center;mask-image:linear-gradient(90deg,transparent,#000 15%);
}
html[data-card-theme] :is(.news-item,.panel,.dash-panel,.dash-card,.chart-card) {
  border-radius:var(--menu-radius,12px);
}
.report-footer {
  --card-bg:var(--ss-surface);--text-muted:var(--ss-muted);--primary-color:var(--ss-accent);
  --shadow-color:var(--card-shadow-color,transparent);--border-color:var(--ss-border);
  font-variant-numeric:tabular-nums;
}
.stock-panels { min-width:0;overflow-x:auto; }
.chart-page .stock-heading { flex-shrink:0; }
html[data-card-theme] .note {color:var(--ss-text);background:var(--surface-accent,var(--ss-surface-alt));}
@media(max-width:680px) {
  html[data-card-theme] :is(.wrap,.chart-page) { padding:12px; }
  .stock-heading-art { min-height:84px;max-height:132px; }
  .stock-heading-copy { padding:10px 12px; }
  .wrap table { overflow-wrap:anywhere; }
  .wrap :is(.tables,.stats,.grid,.dash-grid) {grid-template-columns:repeat(auto-fit,minmax(min(148px,100%),1fr));}
  .dash-panel,.panel { max-width:100%;overflow-x:auto; }
  .report-footer .report-footer-avatars {gap:8px;}
  .report-footer .report-footer-avatars img {width:48px;height:48px;}
}

.up, .is-up { color: var(--ss-up) !important; }
.down, .is-down { color: var(--ss-down) !important; }
.flat, .is-flat { color: var(--ss-flat) !important; }
.eyebrow, .chart-kicker { color: var(--ss-accent); }
.chart-panel, .chart-card, .panel, .card, .bar {
  border-color: var(--ss-border);
}
svg text { font-variant-numeric: tabular-nums; }
"""


_QIQI_FONT_SOURCES = {
    "sans-regular.otf": 'local("Noto Sans CJK SC"), local("WenQuanYi Zen Hei"), local("Microsoft YaHei")',
    "sans-bold.otf": 'local("Noto Sans CJK SC Bold"), local("Microsoft YaHei Bold"), local("WenQuanYi Zen Hei")',
    "serif-regular.otf": 'local("Noto Serif CJK SC"), local("Songti SC"), local("SimSun")',
    "serif-bold.otf": 'local("Noto Serif CJK SC Bold"), local("Songti SC Bold")',
    "emoji.ttf": 'local("Noto Color Emoji"), local("Apple Color Emoji"), local("Segoe UI Emoji")',
}
_FONT_URL = re.compile(r"""url\(\s*['"]?file:///[^)'"]*/qcard/assets/fonts/([\w.-]+)['"]?\s*\)""")


def portable_font_css(css: str) -> str:
    return _FONT_URL.sub(lambda match: _QIQI_FONT_SOURCES.get(match[1], 'local("sans-serif")'), css)


def theme_css(context: Mapping[str, Any] | None = None) -> str:
    """Return the CSS applied to a rendered page (host CSS first, then roles)."""
    resolved = context if context is not None else resolve_theme()
    palette = theme_palette(resolved)
    mode = _text(resolved.get("mode") if isinstance(resolved, Mapping) else "", "dark")
    host_css = _style_inner(_text(resolved.get("post_theme_css") if isinstance(resolved, Mapping) else ""))
    pieces: list[str] = []
    if host_css.strip():
        pieces.append(portable_font_css(host_css))
    pieces.extend([_role_css(palette, mode), _compat_css()])
    return "\n".join(pieces)


def theme_style(context: Mapping[str, Any] | None = None) -> str:
    """Return the full ``<style>`` block for a page."""
    return f'<style id="sayustock-post-theme">\n{theme_css(context)}\n</style>'


def _escape_attr(value: Any) -> str:
    return html_lib.escape(_text(value), quote=True)


def _apply_attrs(document: str, context: Mapping[str, Any]) -> str:
    post_theme = _escape_attr(context.get("post_theme"))
    post_mode = _escape_attr(context.get("post_mode"))
    mode = "dark" if _is_dark_mode(str(context.get("post_mode", "")), graph_palette(context)["background"]) else "light"
    attrs = (
        f' data-card-theme="{post_theme}" data-theme="{mode}" data-post-theme="{post_theme}"'
        f' data-post-mode="{post_mode}" data-sayustock-theme="1"'
    )
    match = _HTML_TAG.search(document)
    if match is None:
        return document
    existing = match.group(1)
    for name in ("data-card-theme", "data-theme", "data-post-theme", "data-post-mode", "data-sayustock-theme"):
        existing = re.sub(rf"\s{re.escape(name)}=[\"'][^\"']*[\"']", "", existing, flags=re.I)
    return document[: match.start()] + f"<html{existing}{attrs}>" + document[match.end() :]


def _recolor(document: str, palette: ThemePalette) -> str:
    mapping = {
        "#10151d": palette.page_top,
        "#0b0e13": palette.background,
        "#090c10": palette.page_bottom,
        "#0b1220": palette.background,
        "#131e32": palette.surface,
        "#182743": palette.surface_alt,
        "#161b22": palette.surface,
        "#1c2128": palette.grid_soft,
        "#21262d": palette.grid,
        "#22324f": palette.border,
        "#23304a": palette.surface_alt,
        "#30363d": palette.border,
        "#484f58": palette.divider,
        "#e8eefb": palette.text,
        "#e6edf3": palette.text,
        "#c9d1d9": palette.text_soft,
        "#ffffff": palette.text,
        "#8ea2c4": palette.muted,
        "#8b949e": palette.muted,
        "#6e7681": palette.dim,
        "#63769a": palette.dim,
        "#5d6b82": palette.flat,
        "#58a6ff": palette.accent,
        "#5aa9ff": palette.accent,
        "#79c0ff": palette.series[3],
        "#d29922": palette.secondary,
        "#ffc857": palette.secondary,
        "#f0883e": palette.series[6],
        "#e3b341": palette.series[1],
        "#a371f7": palette.series[5],
        "#56d4dd": palette.series[4],
        "#db61a2": palette.pink,
        "#d2a8ff": palette.series[7],
        "#ff7b72": palette.series[6],
        "#3fb950": palette.series[4],
        "#f6465d": palette.up,
        "#2ebd85": palette.down,
        "#aeb8c6": palette.flat,
    }
    lowered = {color.lower(): replacement for color, replacement in mapping.items()}

    def replace(match: re.Match[str]) -> str:
        return lowered.get(match.group(0).lower(), match.group(0))

    def recolor_css(css: re.Match[str]) -> str:
        recolored = _HEX_TOKEN.sub(replace, css.group(0))
        background = _parse_rgb(palette.background) or (0, 0, 0)
        text = _parse_rgb(palette.text) or (255, 255, 255)
        recolored = re.sub(
            r"rgba\(11,\s*14,\s*19,",
            f"rgba({background[0]},{background[1]},{background[2]},",
            recolored,
            flags=re.I,
        )
        recolored = re.sub(
            r"rgba\(255,\s*255,\s*255,",
            f"rgba({text[0]},{text[1]},{text[2]},",
            recolored,
            flags=re.I,
        )
        return recolored

    # Chart titles, labels, notes and table values can themselves contain hex codes.
    return re.sub(
        r"""<style\b[^>]*>.*?</style>|\b(?:style|fill|stroke|stop-color)=(?:"[^"]*"|'[^']*')""",
        recolor_css,
        document,
        flags=re.I | re.S,
    )


def _svg_semantics(document: str) -> str:
    result = re.sub(r"<svg(?![^>]*\brole=)", '<svg role="img"', document, flags=re.I)
    return re.sub(r"<svg(?![^>]*\baria-label=)", '<svg aria-label="SayuStock chart"', result, flags=re.I)


def _insert_style(document: str, style: str) -> str:
    old = _OWN_STYLE.search(document)
    if old is not None:
        return document[: old.start()] + style + document[old.end() :]
    head = _HEAD_CLOSE.search(document)
    if head is not None:
        return document[: head.start()] + style + "\n" + document[head.start() :]
    body = _BODY_OPEN.search(document)
    if body is not None:
        return document[: body.start()] + style + document[body.start() :]
    html = _HTML_TAG.search(document)
    if html is not None:
        return document[: html.end()] + style + document[html.end() :]
    return style + document


def apply_theme(
    document: str,
    context: Mapping[str, Any] | None = None,
    *,
    recolor: bool = False,
) -> str:
    """Attach the host theme to a full HTML document.

    ``recolor`` also maps the upstream hard-coded SVG/CSS palette to the host
    colours.  Up/down stay red/green by A-share convention.  The input string
    and the supplied context are never modified.
    """
    if not document or not document.strip():
        return document
    resolved = context if context is not None else resolve_theme()
    if not isinstance(resolved, Mapping):
        resolved = resolve_theme()
    palette = theme_palette(resolved)
    result = _recolor(document, palette) if recolor else document
    result = _svg_semantics(result)
    result = _apply_attrs(result, resolved)
    return apply_frame(_insert_style(result, theme_style(resolved)))


def themed_upstream_page(document: str, title: str) -> str:
    from html import escape

    from .card_frame import ART_SLOT, FOOTER_SLOT

    if 'class="stock-heading"' in document and ART_SLOT not in document:
        # A page already themed by the chart builder carries its own footer.
        return document
    source = re.search(r"(<body\b[^>]*>)(.*?)(</body>)", document, re.I | re.S)
    if not source:
        return document
    content = source[2]
    heading = re.search(r'<header class="ex-head">(.*?)</header>', content, re.S)
    if heading:
        copy = heading[1]
        content = content[:heading.start()] + content[heading.end():]
    else:
        copy = f"<h1>{escape(title)}</h1>"
    # The upstream all-weather template embeds a legacy brand bitmap.
    content = re.sub(r'<img\b[^>]*class="footer"[^>]*alt="SayuStock"[^>]*>', "", content, flags=re.I)
    header = f'<header class="stock-heading"><div class="stock-heading-copy">{copy}</div>{ART_SLOT}</header>'
    wrap = f'{header}<div class="stock-upstream-content">{content}</div>{FOOTER_SLOT}'
    document = document[:source.start(2)] + wrap + document[source.end(2):]
    return apply_theme(document, recolor=True)


__all__ = [
    "DEFAULT_POST_MODE",
    "DEFAULT_POST_THEME",
    "FALLBACK_PALETTE",
    "HOST_THEME_MODULE",
    "PALETTE_KEYS",
    "ThemeContext",
    "ThemePalette",
    "apply_theme",
    "graph_palette",
    "mix",
    "post_theme_context",
    "resolve_theme",
    "rgba",
    "theme_css",
    "theme_palette",
    "theme_style",
]
