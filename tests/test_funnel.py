"""三层筛选流端到端:?setup=1 定制页 / ?profile=p3.* 个性化监控页。

关键断言:p3 模式全程零落盘 —— 跑完后 sources.json 与 monitor_state.json
逐字节不变(个人配置只活在 URL 里)。
"""

import json
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import profile_code as pc  # noqa: E402

APP = str(REPO / "china_europe_monitor.py")


def test_setup_page_renders_without_fetch():
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(APP, default_timeout=60)
    at.query_params["setup"] = "1"
    at.run()
    assert not at.exception, f"定制页抛异常: {at.exception}"
    assert at.title and "定制我的监控" in at.title[0].value


@pytest.mark.skipif(os.environ.get("SKIP_E2E") == "1", reason="跳过联网 e2e")
def test_p3_profile_view_and_zero_disk_writes():
    from streamlit.testing.v1 import AppTest
    cfg = {"regions": ["cn-eu"], "domains": ["trade-tariff", "diplomacy"],
           "source_types": ["gov", "industry", "thinktank", "wechat-mirror"],
           "background": True, "keywords": ["稀土"], "entities": ["宁德时代"]}
    code = pc.encode(cfg)

    before_sources = (REPO / "sources.json").read_bytes()
    state_p = REPO / "monitor_state.json"
    before_state = state_p.read_bytes() if state_p.exists() else None

    at = AppTest.from_file(APP, default_timeout=180)
    at.query_params["profile"] = code
    at.run()
    assert not at.exception, f"p3 监控页抛异常: {at.exception}"
    assert at.title and "我的条线" in at.title[0].value
    labels = " | ".join(t.label for t in at.tabs)
    assert "贸易关税" in labels and "外交" in labels and "我的关注" in labels, labels

    assert (REPO / "sources.json").read_bytes() == before_sources, \
        "p3 模式改写了共享 sources.json —— 零落盘被破坏"
    after_state = state_p.read_bytes() if state_p.exists() else None
    assert after_state == before_state, \
        "p3 模式改写了 monitor_state.json —— 零落盘被破坏"


@pytest.mark.skipif(os.environ.get("SKIP_E2E") == "1", reason="跳过联网 e2e")
def test_english_ui_via_lang_param():
    """?lang=en:标签页/界面转英文;默认(无参数)仍是中文(黄金基线另测)。"""
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(APP, default_timeout=180)
    at.query_params["lang"] = "en"
    at.run()
    assert not at.exception
    labels = [t.label for t in at.tabs]
    assert labels[0].startswith("⚡ Top") and labels[-1].startswith("📋 All"), labels
    assert any("Germany-China" in l for l in labels), labels  # 分类用词表自带 en 名


def test_setup_page_english():
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(APP, default_timeout=60)
    at.query_params["setup"] = "1"
    at.query_params["lang"] = "en"
    at.run()
    assert not at.exception
    assert at.title and "Customize" in at.title[0].value


@pytest.mark.skipif(os.environ.get("SKIP_E2E") == "1", reason="跳过联网 e2e")
def test_english_p3_title_has_no_chinese():
    """回归:p3 模式标题曾硬编码「我的条线」,英文界面下会中英混排。"""
    import re as _re
    from streamlit.testing.v1 import AppTest
    code = pc.encode({"regions": ["cn-eu"], "domains": ["trade-tariff"],
                      "source_types": ["gov"], "background": False})
    at = AppTest.from_file(APP, default_timeout=180)
    at.query_params["profile"] = code
    at.query_params["lang"] = "en"
    at.run()
    assert not at.exception
    title = at.title[0].value
    assert "My beats" in title
    assert not _re.search(r"[一-鿿]", title), f"英文标题里仍有中文: {title}"


@pytest.mark.skipif(os.environ.get("SKIP_E2E") == "1", reason="跳过联网 e2e(坏码回退到默认监测台=全量抓取)")
def test_bad_p3_code_falls_back_gracefully():
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(APP, default_timeout=180)
    at.query_params["profile"] = "p3.THIS_IS_GARBAGE"
    at.run()
    assert not at.exception
    # 落回默认监测台(标题无"我的条线"),错误横幅可见
    assert at.title and at.title[0].value == "📰 News Monitor!"
    assert any("配置码" in e.value for e in at.error), "应显示配置码无效提示"
