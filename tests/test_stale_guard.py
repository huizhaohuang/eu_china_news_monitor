"""陈年新闻防御:URL 日期矛盾 / 发布时间核验 / 预算与异常安全。"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import stale_guard as sg

NOW = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)
CUTOFF = NOW - timedelta(hours=24)


def _item(link, verify=False):
    return {"link": link, "verify_date": verify, "title": "t"}


def test_url_claimed_date():
    assert sg.url_claimed_date("https://a.com/2020/03/tesla-x") == datetime(2020, 3, 1, tzinfo=timezone.utc)
    assert sg.url_claimed_date("https://a.com/news/2023-11-05/x").year == 2023
    assert sg.url_claimed_date("https://a.com/article/tesla-image") is None
    assert sg.url_claimed_date("https://a.com/2026/x") is None      # 只有年没有月,不算
    assert sg.url_claimed_date("https://a.com/p/12345/6789") is None  # 编号不误配


def test_parse_dt_variants():
    assert sg._parse_dt("2020-03-27T15:22:00Z").year == 2020
    assert sg._parse_dt("2026-08-18T10:00:00+02:00").tzinfo is not None
    assert sg._parse_dt("2020-03-27") .month == 3
    assert sg._parse_dt("garbage") is None


def test_l1_url_date_drops_old(monkeypatch):
    old = _item("https://a.com/2020/03/old-story")
    fresh = _item("https://a.com/2026/08/new-story")
    nodate = _item("https://a.com/article/whatever")
    kept, dropped = sg.filter_stale([old, fresh, nodate], CUTOFF)
    assert dropped == 1 and old not in kept and fresh in kept and nodate in kept


def test_l2_verifies_only_flagged_and_respects_budget(monkeypatch):
    calls = []
    monkeypatch.setattr(sg, "fetch_published",
                        lambda url, s: calls.append(url) or datetime(2020, 1, 1, tzinfo=timezone.utc))
    sg._cache.clear()
    flagged = [_item(f"https://a.com/art{i}", verify=True) for i in range(20)]
    unflagged = [_item("https://b.com/artx", verify=False)]
    kept, dropped = sg.filter_stale(flagged + unflagged, CUTOFF)
    assert len(calls) == sg.VERIFY_BUDGET          # 预算封顶
    assert dropped == sg.VERIFY_BUDGET             # 核验过的全是 2020 → 丢
    assert unflagged[0] in kept                    # 未标记的不核验


def test_l2_no_evidence_keeps_item(monkeypatch):
    monkeypatch.setattr(sg, "fetch_published", lambda url, s: None)
    sg._cache.clear()
    it = _item("https://a.com/art-nodate", verify=True)
    kept, dropped = sg.filter_stale([it], CUTOFF)
    assert it in kept and dropped == 0             # 拿不到证据 = 放行(宁多勿漏)


def test_google_links_skipped(monkeypatch):
    monkeypatch.setattr(sg, "fetch_published",
                        lambda url, s: (_ for _ in ()).throw(AssertionError("不该核验 google 链")))
    sg._cache.clear()
    it = _item("https://news.google.com/rss/articles/XX", verify=True)
    kept, _ = sg.filter_stale([it], CUTOFF)
    assert it in kept


def test_exception_safety(monkeypatch):
    monkeypatch.setattr(sg, "url_claimed_date",
                        lambda u: (_ for _ in ()).throw(RuntimeError("boom")))
    items = [_item("https://a.com/x")]
    kept, dropped = sg.filter_stale(items, CUTOFF)
    assert kept == items and dropped == 0          # 防御自身崩溃绝不影响抓取
