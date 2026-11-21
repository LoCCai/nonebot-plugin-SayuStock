from __future__ import annotations

from nonebot_plugin_sayustock.menu import MENU_DATA, menu_extra


def test_menu_data_matches_picmenu_contract():
    assert len(MENU_DATA) >= 8
    required = {"func", "trigger_method", "trigger_condition", "brief_des", "detail_des"}
    for item in MENU_DATA:
        assert required <= set(item)
        assert item["func"]
        assert item["trigger_condition"]
        assert isinstance(item.get("llm_intents", []), list)


def test_menu_extra_has_version_and_pmn():
    extra = menu_extra()
    assert extra["version"]
    assert extra["author"]
    assert extra["pmn"]["inherit_func_template"] is True
    assert extra["menu_data"] is MENU_DATA


def test_menu_does_not_advertise_plugin_owned_web_ai():
    joined = " ".join(str(item) for item in MENU_DATA)
    assert "SAYUSTOCK_WEB_TOKEN" not in joined
    assert "访问令牌" not in joined
    assert any(item["func"] == "QWeb 股票页" for item in MENU_DATA)
