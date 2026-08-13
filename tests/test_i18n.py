"""i18n 完整性:翻译表无缺口、每个中文标签字典都有等价英文表。

这类"某处标签忘了走 i18n"的 bug 只在英文界面下可见(中文界面一切正常),
人眼很难扫全,故用不变量把它钉住。
"""

import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import i18n  # noqa: E402


def test_every_entry_has_both_languages():
    bad = [k for k, v in i18n._TR.items()
           if len(v) != 2 or not v[0].strip() or not v[1].strip()]
    assert not bad, f"翻译表条目缺中文或英文: {bad}"


def test_english_entries_have_no_chinese():
    import re
    bad = [k for k, (_zh, en) in i18n._TR.items() if re.search(r"[一-鿿]", en)]
    assert not bad, f"英文文案里混入中文: {bad}"


def test_placeholders_match_between_languages():
    """{name} 占位符必须两语一致,否则切语言时 .format 抛 KeyError。"""
    import re
    bad = {}
    for k, (zh, en) in i18n._TR.items():
        pz = set(re.findall(r"\{(\w+)", zh))
        pe = set(re.findall(r"\{(\w+)", en))
        if pz != pe:
            bad[k] = (sorted(pz), sorted(pe))
    assert not bad, f"占位符不一致: {bad}"


def test_label_dicts_have_english_counterparts(head_ns):
    """每个中文标签字典的 key 都要在对应英文表里有值 —— 漏一个就会中英混排。"""
    import beat_packs
    pairs = [
        ("STYPE_LABELS", head_ns["STYPE_LABELS"], i18n.STYPE_EN),
        ("LANE_LABELS", head_ns["LANE_LABELS"], i18n.LANE_EN),
        ("TYPE_LABELS", beat_packs.TYPE_LABELS, i18n.STYPE_EN),
        ("REGION_LABELS", beat_packs.REGION_LABELS, i18n.REGION_EN),
        ("SUB_REGION_LABELS", beat_packs.SUB_REGION_LABELS, i18n.SUB_REGION_EN),
    ]
    missing = {name: sorted(set(zh) - set(en)) for name, zh, en in pairs if set(zh) - set(en)}
    assert not missing, f"缺英文对照: {missing}"


@pytest.mark.skipif(os.environ.get("SKIP_E2E") == "1", reason="跳过联网 e2e")
def test_english_page_has_no_chinese_ui_labels(head_ns):
    """回归:卡片元信息里的口径标签曾直接读中文字典,英文界面中英混排。"""
    from streamlit.testing.v1 import AppTest
    import profile_code as pc

    code = pc.encode({"regions": ["cn-eu", "cn"],
                      "domains": ["trade-tariff", "semiconductor", "ai"],
                      "source_types": ["gov", "industry", "thinktank"], "background": True})
    at = AppTest.from_file(str(REPO / "china_europe_monitor.py"), default_timeout=180)
    at.query_params["profile"] = code
    at.query_params["lang"] = "en"
    at.run()
    assert not at.exception

    # 新闻标题/媒体名可以是中文;但界面标签(口径/分组)不该出现
    ui_labels = list(head_ns["STYPE_LABELS"].values()) + list(head_ns["LANE_LABELS"].values())
    texts = [c.value for c in at.caption] + [t.label for t in at.tabs]
    leaked = {lab for lab in ui_labels for txt in texts if lab in txt}
    assert not leaked, f"英文界面出现中文 UI 标签: {leaked}"
