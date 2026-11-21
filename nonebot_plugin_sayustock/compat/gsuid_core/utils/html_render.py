"""HTML -> image bridge backed by the official ``nonebot-plugin-htmlrender``.

SayuStock's Jinja/SVG pages are self-contained HTML (CSS, SVG and textures
are inlined), so the bridge only needs htmlrender's public ``render_html``
entry point.  The renderer is loaded lazily: importing the plugin never starts
Chromium.
"""

from __future__ import annotations

import re
from typing import Literal


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
        import nonebot_plugin_htmlrender as renderer
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

    # Charts contain inline SVG/CSS and data images; remote Chromium needs no
    # directory navigation, external stylesheets, scripts or network resources.
    policy = (
        '<meta http-equiv="Content-Security-Policy" content="'
        "default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:"
        '">'
    )
    html = re.sub(r"(<head\b[^>]*>)", lambda match: match[0] + policy, html, count=1, flags=re.I)
    render = getattr(renderer, "render_html", None)
    format_arg = {"image_type": image_format} if render is not None else {"type": image_format}
    return await (render or renderer.html_to_pic)(
        html,
        wait=0,
        **format_arg,
        quality=quality,
        device_scale_factor=max(1.0, min(scale, 3.0)),
        viewport={"width": viewport_width, "height": 64},
        screenshot_timeout=45_000,
        full_page=True,
    )


__all__ = ["render_html_to_bytes"]
