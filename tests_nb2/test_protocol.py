from __future__ import annotations

from dataclasses import field, dataclass

from nonebot_plugin_sayustock.platform import from_event


@dataclass
class FakeAdapter:
    name: str

    def get_name(self) -> str:
        return self.name


@dataclass
class FakeBot:
    adapter: FakeAdapter
    self_id: str = "10000"


@dataclass
class FakeSender:
    card: str = "测试用户"
    role: str = "member"


@dataclass
class FakeSegment:
    type: str
    data: dict[str, str] = field(default_factory=dict)


@dataclass
class FakeEvent:
    user_id: str = "42"
    group_id: str | None = "99"
    message_id: str = "mid"
    sender: FakeSender = field(default_factory=FakeSender)
    segments: list[FakeSegment] = field(default_factory=list)
    plain: str = "大盘概览"

    def get_plaintext(self) -> str:
        return self.plain

    def get_message(self):
        return self.segments


def test_onebot_v11_group_event():
    bot = FakeBot(FakeAdapter("OneBot V11"))
    event = FakeEvent(segments=[FakeSegment("at", {"qq": "7"})])
    unified = from_event(bot, event)
    assert unified.platform == "onebot11"
    assert unified.user_id == "42"
    assert unified.group_id == "99"
    assert unified.at == "7"
    assert unified.session_id == "onebot11:10000:99"
    assert unified.is_group is True


def test_onebot_v12_channel_event():
    bot = FakeBot(FakeAdapter("OneBot V12"))
    event = FakeEvent(group_id=None)
    event.channel_id = "c1"  # type: ignore[attr-defined]
    unified = from_event(bot, event, text="个股 600519")
    assert unified.platform == "onebot12"
    assert unified.channel_id == "c1"
    assert unified.text == "个股 600519"
    assert unified.session_id == "onebot12:10000:c1"


def test_qq_official_event():
    bot = FakeBot(FakeAdapter("QQ"))
    event = FakeEvent(group_id=None)
    event.group_openid = "g1"  # type: ignore[attr-defined]
    unified = from_event(bot, event)
    assert unified.platform == "qq"
    assert unified.group_id == "g1"
