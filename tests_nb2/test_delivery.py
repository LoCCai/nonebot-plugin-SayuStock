from __future__ import annotations

import pytest

from tests_nb2.test_protocol import FakeBot, FakeEvent, FakeAdapter
from nonebot_plugin_sayustock.platform import from_event, send_image
from nonebot_plugin_sayustock.platform.event import UnifiedEvent

PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


class SendingBot(FakeBot):
    def __init__(self, name: str):
        super().__init__(FakeAdapter(name))
        self.sent = []

    async def send(self, event, message):
        self.sent.append(message)
        return {"message_id": "1"}

    async def call_api(self, api, **data):
        assert api == "upload_file"
        return {"file_id": "uploaded-1"}


@pytest.mark.asyncio
async def test_onebot11_image_uses_base64():
    bot = SendingBot("OneBot V11")
    ue = from_event(bot, FakeEvent())
    assert ue.platform == "onebot11"
    assert await send_image(ue, PNG) is True
    assert bot.sent
    assert bot.sent[0].type == "image"


@pytest.mark.asyncio
async def test_onebot12_image_uploads_first():
    bot = SendingBot("OneBot V12")
    ue = from_event(bot, FakeEvent())
    assert ue.platform == "onebot12"
    assert await send_image(ue, PNG) is True
    assert bot.sent
    assert bot.sent[0].type == "image"
    assert bot.sent[0].data["file_id"] == "uploaded-1"


@pytest.mark.asyncio
async def test_qq_image_uses_local_attachment():
    bot = SendingBot("QQ")
    ue = UnifiedEvent(platform="qq", bot_id="1", user_id="u", text="")
    ue.bot = bot
    ue.raw_event = FakeEvent(group_id=None)
    assert await send_image(ue, PNG) is True
    assert bot.sent
    assert bot.sent[0].type == "file_image"


@pytest.mark.asyncio
@pytest.mark.parametrize(("text","images","types"), [
    ("行情", [], ["text"]),
    ("日k\n贵州茅台: +2.52%", [PNG], ["image", "text"]),
    ("", [PNG, PNG], ["image", "image"]),
])
async def test_group_forward_one_envelope(text, images, types):
    from nonebot.adapters.onebot.v11 import Message

    from nonebot_plugin_sayustock.commands import CommandResult, deliver

    class GroupBot(SendingBot):
        async def call_api(self, api, **data):
            assert api == "send_group_forward_msg" and data["group_id"] == 99
            self.sent.append(data["messages"])
            return {"message_id": 1}

    bot = GroupBot("OneBot V11")
    result = CommandResult(text, next(iter(images), None), extra_images=images[1:])
    await deliver(from_event(bot, FakeEvent()), result)
    assert len(bot.sent) == 1
    nodes = bot.sent[0]
    assert [Message(node["data"]["content"])[0].type for node in nodes] == types
    if text:
        assert nodes[-1]["data"]["content"][0].data["text"] == text


@pytest.mark.asyncio
async def test_news_card_sent_once_without_fallback_text():
    from nonebot_plugin_sayustock.commands import CommandResult, deliver

    bot = SendingBot("OneBot V11")
    await deliver(from_event(bot, FakeEvent()), CommandResult("财经快讯", PNG, image_only=True))
    assert len(bot.sent) == 1 and bot.sent[0].type == "image"
