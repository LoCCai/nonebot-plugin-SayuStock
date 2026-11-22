"""Use Qiqi's published artwork policy and report footer in portable stock cards."""

from __future__ import annotations

import io
import base64
import logging
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from functools import lru_cache

from PIL import Image, ImageOps

from SayuStock.render.templates import render_template

ART_SLOT = "<!--stock-heading-art-->"
FOOTER_SLOT = "<!--stock-report-footer-->"


@lru_cache(maxsize=32)
def _thumbnail(path: Path, size: tuple[int, int], stamp: int) -> str:
    with Image.open(path) as original:
        image = ImageOps.exif_transpose(original).convert("RGB")
        image.thumbnail(size, Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        image.save(buffer, format="WEBP", quality=88)
    return "data:image/webp;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def _image(path: Path, size: tuple[int, int]) -> str:
    return _thumbnail(path, size, path.stat().st_mtime_ns)


def _selected_image(selected: Path | None, default: Path, size: tuple[int, int]) -> str:
    if selected and selected != default:
        try:
            return _image(selected, size)
        except (ValueError, OSError):
            logging.getLogger(__name__).warning("七七装饰素材不可读，使用默认图：%s", selected.name)
    return _image(default, size)


def apply_frame(document: str) -> str:
    if ART_SLOT not in document or FOOTER_SLOT not in document:
        return document
    try:
        from src.utils import seasonal_artwork
        from src.utils.artwork_library import image_path, decorative_image
    except ImportError:  # Standalone installs have no Qiqi artwork library.
        return document.replace(ART_SLOT, "", 1).replace(FOOTER_SLOT, "")

    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    root = seasonal_artwork.ROOT
    chosen = decorative_image("shared", now=now)
    default_art = seasonal_artwork.asset_path(seasonal_artwork.day_selection(now)["hero_id"], "full")
    art = _selected_image(image_path(chosen, "full") if chosen else None, default_art, (1280, 680))
    portraits = seasonal_artwork.seasonal_portraits(now)
    footer_data = {}
    for actor, field in (("developer", "developer"), ("qiqi", "qiqi"), ("sayu", "zaoyou")):
        default = root / f"qcard/assets/footer-{actor}.jpg"
        footer_data[f"footer_{field}_avatar"] = _selected_image(portraits.get(actor), default, (144, 144))
    footer = render_template("stock_footer.html", **footer_data, now_time=now.strftime("%Y-%m-%d %H:%M:%S"))
    head, _, tail = document.rpartition(FOOTER_SLOT)
    return head.replace(ART_SLOT, f'<img class="stock-heading-art" src="{art}" alt="七七插图">', 1) + footer + tail
