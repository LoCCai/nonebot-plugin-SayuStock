"""HTML -> image bridge for SayuStock's existing Jinja/SVG render pipeline.

GsCore exposes ``render_html_to_bytes`` backed by a shared PyTakumi renderer.
The NoneBot2 branch uses ``nonebot-plugin-htmlrender`` (Playwright/Chromium)
instead.  Callers only depend on the public keyword contract, so swapping the
backend does not touch the business render modules.
"""

from __future__ import annotations

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
    viewport width; their ratio becomes the device scale factor.
    """
    del allow_refit, default_font_size, lang  # contract-only parameters

    try:
        from nonebot_plugin_htmlrender import html_to_pic
    except Exception as error:  # pragma: no cover - optional dependency
        raise RuntimeError(
            "缺少 HTML 渲染依赖：请安装 nonebot-plugin-htmlrender 并执行 `playwright install chromium`"
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
        type=image_format,
        quality=quality,
        device_scale_factor=max(1.0, min(scale, 4.0)),
        viewport={"width": viewport_width, "height": 900},
        full_page=True,
    )


__all__ = ["render_html_to_bytes"]
