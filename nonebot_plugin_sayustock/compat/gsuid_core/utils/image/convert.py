"""PIL bytes -> ``base64://`` string, matching Core's image conversion contract."""

from __future__ import annotations

import base64
from io import BytesIO
from typing import Union

from PIL import Image

ImageInput = Union[bytes, Image.Image]


async def convert_img(img: ImageInput, *_args: object, **_kwargs: object) -> str:
    if isinstance(img, Image.Image):
        buffer = BytesIO()
        img.save(buffer, format="PNG")
        payload = buffer.getvalue()
    else:
        payload = bytes(img)
    return "base64://" + base64.b64encode(payload).decode("ascii")


__all__ = ["convert_img"]
