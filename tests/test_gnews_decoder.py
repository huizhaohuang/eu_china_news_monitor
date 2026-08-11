"""gnews 解码器:纯离线单测(mock 网络)——预算/负缓存/熔断/降级/可达性。"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import gnews_decoder as gd

G = "https://news.google.com/rss/articles/"


@pytest.fixture(autouse=True)
def _reset():
    gd._cache.clear()
    gd._breaker.update(open_until=0.0, trips=0, clean_rounds=0)
    yield
    gd._cache.clear()
    gd._breaker.update(open_until=0.0, trips=0, clean_rounds=0)


def test_extract_b64_and_non_google():
    assert gd._extract_b64(G + "CBMiABC") == "CBMiABC"
    assert gd._extract_b64("https://www.reuters.com/x") is None


def test_non_google_urls_map_none():
    out = gd.resolve_batch(["https://reuters.com/a", "https://caixin.com/b"])
    assert out == {"https://reuters.com/a": None, "https://caixin.com/b": None}


def test_success_and_cache_hit(monkeypatch):
    calls = {"n": 0}

    def fake_decode(urls):
        calls["n"] += 1
        return {u: "https://real.example/" + gd._extract_b64(u) for u in urls}, False

    monkeypatch.setattr(gd, "_decode", fake_decode)
    r1 = gd.resolve_batch([G + "AAA", G + "BBB"])
    assert r1[G + "AAA"] == "https://real.example/AAA"
    assert calls["n"] == 1
    r2 = gd.resolve_batch([G + "AAA", G + "BBB"])   # 全命中缓存
    assert r2 == r1 and calls["n"] == 1             # 无二次网络调用


def test_budget_caps_and_no_negative_cache_for_over_budget(monkeypatch):
    seen = {}

    def fake_decode(urls):
        seen["batch"] = list(urls)
        return {u: "https://r/" + gd._extract_b64(u) for u in urls}, False

    monkeypatch.setattr(gd, "_decode", fake_decode)
    urls = [G + f"U{i:02d}" for i in range(10)]
    out = gd.resolve_batch(urls, budget=3)
    assert len(seen["batch"]) == 3                  # 只解 3 条
    over = urls[3:]
    assert all(out[u] is None for u in over)        # 超预算返回 None
    assert all(u not in gd._cache for u in over)    # 但【不】负缓存,留待下轮


def test_failed_decode_is_negative_cached(monkeypatch):
    monkeypatch.setattr(gd, "_decode", lambda urls: ({u: None for u in urls}, False))
    u = G + "FAIL"
    assert gd.resolve_batch([u])[u] is None
    assert gd._cache[u] is None                     # 真失败 → 负缓存
    # 第二次:命中负缓存,零网络
    monkeypatch.setattr(gd, "_decode", lambda urls: (_ for _ in ()).throw(AssertionError("不该再调")))
    assert gd.resolve_batch([u])[u] is None


def test_rate_limit_trips_breaker_and_no_negative_cache(monkeypatch):
    monkeypatch.setattr(gd, "_decode", lambda urls: ({u: None for u in urls}, True))
    u = G + "RL"
    assert gd.resolve_batch([u])[u] is None
    assert gd._breaker["trips"] == 1                # 限速 → 跳闸
    assert u not in gd._cache                       # 限速失败不负缓存(退避后重试)
    # 熔断期间:零请求
    monkeypatch.setattr(gd, "_decode", lambda urls: (_ for _ in ()).throw(AssertionError("熔断期不该调")))
    assert gd.resolve_batch([G + "OTHER"])[G + "OTHER"] is None


def test_exception_in_decode_is_swallowed(monkeypatch):
    monkeypatch.setattr(gd, "_decode", lambda urls: (_ for _ in ()).throw(RuntimeError("boom")))
    out = gd.resolve_batch([G + "X"])               # 不抛
    assert out[G + "X"] is None


def test_mainland_reachable():
    assert gd.mainland_reachable("https://www.caixin.com/x") is True
    assert gd.mainland_reachable("https://finance.sina.com.cn/y") is True
    assert gd.mainland_reachable("https://www.reuters.com/z") is False
    assert gd.mainland_reachable("https://news.google.com/rss/a") is False
    assert gd.mainland_reachable("https://udn.com/x") is False       # 台媒
    assert gd.mainland_reachable("") is True                          # 未知默认可达,不误标


def test_fetch_params_falls_back_past_429(monkeypatch):
    # 回归:/rss/articles 429 但 /articles 200 时,必须回落到能用的路径,不短路
    class Resp:
        def __init__(self, code, text=""):
            self.status_code, self.text = code, text
    seq = iter([Resp(429), Resp(200, 'data-n-a-sg="SIG" data-n-a-ts="123"')])
    sess = type("S", (), {"get": lambda self, url, timeout: next(seq)})()
    rl = [False]
    assert gd._fetch_params(sess, "B64", rl) == ("SIG", "123")
    assert rl[0] is False                           # 一路 429 一路成功 → 不算限速


def test_fetch_params_all_429_marks_ratelimit(monkeypatch):
    class Resp:
        status_code, text = 429, ""
    sess = type("S", (), {"get": lambda self, url, timeout: Resp()})()
    rl = [False]
    assert gd._fetch_params(sess, "B64", rl) is None
    assert rl[0] is True                            # 全 429 → 标限速


def test_parse_batch_response_shapes():
    good = '[["wrb.fr","Fbv4je","[\\"garturlres\\",\\"https://real/x\\",1]",null,null,null,"1"]]'
    assert gd._parse_batch_response(good)["1"] == "https://real/x"
    bad = '[["wrb.fr","Fbv4je",null,null,null,null,"2"]]'
    assert gd._parse_batch_response(bad).get("2") is None
