"""HTML -> image bridge backed by the official ``nonebot-plugin-htmlrender``.

SayuStock's Jinja/SVG pages are self-contained HTML (CSS, SVG and textures
are inlined), so the bridge only needs htmlrender's public ``html_to_pic``
entry point.  The renderer is loaded lazily: importing the plugin never starts
Chromium.
"""

from __future__ import annotations

from typing import Literal
from pathlib import Path


def _file_url() -> str:
    cwd = Path.cwd().resolve().as_posix()
    return f"file:///{cwd.lstrip('/')}" if not cwd.startswith("/") else f"file://{cwd}"


async def render_html_to_bytes(
    html: str,
    *,
    max_width: float,
    dpi: float = 144,
    root_max_width: float | None = None,
    allow_refit: bool = True,
    default_font_size: float = 0,
    image_format: Literal["png", "jpeg"] = "png",
    jpeg_quality: int | None = None,
    lang: str = "zh-CN",
) -> bytes:
    """Render a self-contained HTML document into a PNG/JPEG byte string.

    ``max_width`` is the desired pixel width and ``root_max_width`` the CSS
    viewport width; their ratio becomes htmlrender's device scale factor.
    """
    del allow_refit, default_font_size, lang  # contract-only parameters

    try:
        from nonebot_plugin_htmlrender import html_to_pic
    except Exception as error:  # pragma: no cover - dependency/runtime issue
        raise RuntimeError(
            "缺少 HTML 渲染依赖：请安装 nonebot-plugin-htmlrender，并执行 `playwright install chromium`"
        ) from error

    root_width = int(root_max_width or max_width)
    scale = float(dpi) / 96.0
    if root_width <= 0 or scale <= 0:
        raise ValueError("root_max_width and dpi must be positive")

    viewport_width = max(320, min(root_width, 4096))
    quality = jpeg_quality if image_format == "jpeg" else None
    if quality is not None:
        quality = max(1, min(int(quality), 100))

    return await html_to_pic(
        html,
        wait=0,
        template_path=_file_url(),
        type=image_format,
        quality=quality,
        device_scale_factor=max(1.0, min(scale, 3.0)),
        viewport={"width": viewport_width, "height": 1000},
        screenshot_timeout=45_000,
        full_page=True,
    )


__all__ = ["render_html_to_bytes"]
