"""V6 聚类规则:关键判例回归 + 162 对评测集基准闸门。

判例均来自 2026-08-19 真实数据校准(tests/data/eval_pairs.json 为金标全集)。
"""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import event_merge

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _no_real_llm(monkeypatch):
    monkeypatch.setattr(event_merge, "_api_key", lambda: None)
    event_merge._cache.clear()
    yield
    event_merge._cache.clear()


def _item(ns, title, outlet, lang="zh", minutes=0):
    return {"source": outlet, "lane": "industry", "stype": "", "lang": lang,
            "outlet": outlet, "title": title, "summary": "",
            "link": f"https://x.com/{abs(hash(title)) % 10**8}",
            "time": datetime(2026, 8, 19, 8, 0, tzinfo=timezone.utc) + timedelta(minutes=minutes),
            "cats": [], "primary": None, "breaking": False, "tokens": ns["_tokens"](title)}


def _fillers(ns, n=24):
    return [_item(ns, f"unrelated filler story topic{i} sector{i} theme{i}", f"F{i}", "en", 500 + i)
            for i in range(n)]


def _pairtest(ns, a, b, oa, ob, la="zh", lb="zh"):
    """返回 (是否合并, 是否进灰带)。灰带经 mock 记录。"""
    asked = []
    orig = event_merge.adjudicate
    event_merge.adjudicate = lambda pairs: (asked.extend(pairs),
                                            {frozenset(x): False for x in pairs})[1]
    try:
        items = [_item(ns, a, oa, la), _item(ns, b, ob, lb, 9)] + _fillers(ns)
        cls = ns["cluster_items"](items)
        ab = [c for c in cls if any(i["title"] in (a, b) for i in c["items"])]
        merged = len(ab) == 1 and ab[0]["n"] >= 2
    finally:
        event_merge.adjudicate = orig
    return merged, bool(asked)


def test_date_guard_different_editions_never_merge_nor_gray(head_ns):
    m, g = _pairtest(head_ns, "财联社汽车早报【8月19日】", "财联社汽车早报【8月18日】",
                     "财联社", "Futubull")
    assert not m and not g       # 期号不同:连灰带都不进(省 LLM)


def test_number_guard_different_tenors(head_ns):
    m, _ = _pairtest(head_ns, "日本5年期国债收益率上升1.5个基点,达到2.100%",
                     "日本2年期国债收益率上升3.0个基点", "财联社", "第一财经")
    assert not m                 # 数字全异,一级封锁(评测实锤误合案例)


def test_unit_number_guard_trillion_milestones(head_ns):
    m, _ = _pairtest(head_ns, "沪深两市成交额突破2万亿 较上一日此时放量超500亿",
                     "10:13:56【沪深两市成交额突破1万亿 较上一日此时放量超40亿】",
                     "新浪财经", "api3.cls.cn")
    assert not m                 # 2万亿 vs 1万亿:数字+单位复合是判别信号


def test_shared_number_still_merges(head_ns):
    m, _ = _pairtest(head_ns, "中汽协：7月汽车国内销量154.1万辆 同比下降23.6%",
                     "中汽协：7月汽车国内销量环比下降13.1% 同比下降23.6%",
                     "观点网", "Sohu")
    assert m                     # 共享 23.6%:同一发布的转写变体


def test_roundup_containment_blocked(head_ns):
    m, _ = _pairtest(head_ns,
                     "能源内参｜李书福辞去吉利汽车董事长上市公司称释放去家族化信号；宁德时代拓展新业务5个月内入股三家AIDC相关公司；隆基绿能上半年组件出货全球第一；国家能源局部署迎峰度夏",
                     "宁德时代拓展新业务 5个月内入股三家AIDC相关公司", "财新", "财新网")
    assert not m                 # 集锦稿包含子标题,ratio>2 不得借道包含关系吸稿


def test_same_outlet_catalog_goes_gray_not_merge(head_ns):
    m, g = _pairtest(head_ns, "2026年08月19日医疗器械批准证明文件送达信息",
                     "2026年08月19日药品批准证明文件送达信息", "NMPA", "NMPA")
    assert not m and g           # 同媒体高相似:算法不合并,交灰带 LLM 分辨


def test_cross_lingual_entity_anchor_goes_gray(head_ns, monkeypatch):
    a = "宇树科技开盘暴涨629%，市值超4449亿元"
    b = "Chinese Robot-Maker Unitree Soars 629% in Shanghai Debut"
    m, g = _pairtest(head_ns, a, b, "芯智讯", "Caixin Global", "zh", "en")
    assert g                     # 跨语言共享 §unitree+#629:进灰带
    # LLM 判同 → 合并
    monkeypatch.setattr(event_merge, "_call_llm", lambda pairs: [True] * len(pairs))
    event_merge._cache.clear()
    items = [_item(head_ns, a, "芯智讯", "zh"), _item(head_ns, b, "Caixin Global", "en", 9)] + _fillers(head_ns)
    cls = head_ns["cluster_items"](items)
    ab = [c for c in cls if any(i["title"] in (a, b) for i in c["items"])]
    assert len(ab) == 1 and ab[0]["n"] == 2


def test_alias_normalization_full_vs_abbrev(head_ns):
    tk = head_ns["_tokens"]("国家发展和改革委员会发布新政策")
    tk2 = head_ns["_tokens"]("发改委发布新政策")
    assert "§ndrc" in tk and "§ndrc" in tk2   # 全称/简称归一到同一 token


def test_benchmark_eval_pairs(head_ns):
    """基准闸门:162 对金标上,生产决策 P≥0.90 且 R+灰带≥0.78。
    任何聚类改动把这里搞红 = 引入了回归。"""
    pairs = json.loads((REPO / "tests/data/eval_pairs.json").read_text(encoding="utf-8"))["pairs"]
    asked_log = []
    orig = event_merge.adjudicate
    event_merge.adjudicate = lambda ps: (asked_log.extend(ps),
                                         {frozenset(x): False for x in ps})[1]
    try:
        tp = fp = fn = gray_same = 0
        n_same = 0
        for p in pairs:
            asked_log.clear()
            m, _ = None, None
            items = [_item(head_ns, p["title_a"], p["outlet_a"], p["lang_a"]),
                     _item(head_ns, p["title_b"], p["outlet_b"], p["lang_b"], 9)] + _fillers(head_ns)
            cls = head_ns["cluster_items"](items)
            ab = [c for c in cls if any(i["title"] in (p["title_a"], p["title_b"]) for i in c["items"])]
            merged = len(ab) == 1 and ab[0]["n"] >= 2
            gray = bool(asked_log)
            if p["label"] == "same":
                n_same += 1
                if merged:
                    tp += 1
                elif gray:
                    gray_same += 1
                else:
                    fn += 1
            elif merged:
                fp += 1
        precision = tp / max(tp + fp, 1)
        recall_llm = (tp + gray_same) / max(n_same, 1)
        assert precision >= 0.90, f"P={precision:.2f} (tp={tp} fp={fp})"
        assert recall_llm >= 0.78, f"R+灰带={recall_llm:.2f} (tp={tp} gray={gray_same} fn={fn})"
    finally:
        event_merge.adjudicate = orig
