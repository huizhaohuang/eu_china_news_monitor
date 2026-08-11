"""Google News 跳转链 → 真实出版商 URL(服务器端批量解码)+ 大陆可达性标注。

**为什么需要**:gnews 源的条目链接是 news.google.com/rss/articles/CBMi… 跳转链,
中国大陆用户点击 = 打开 Google = 被墙。境外用户无感。解码成真实出版商 URL
后,大陆同事可直达(仍受出版商自身是否被墙限制,见 mainland_reachable)。

**协议**(逆向自 SSujitX/google-news-url-decoder + 自测,2024-07 后唯一可行路径):
  1. 逐条 GET 文章页,抓 data-n-a-sg(签名)+ data-n-a-ts(时间戳)——签名逐篇不同
  2. 批量 POST /_/DotsSplashUi/data/batchexecute,一次可带多条,按信封 id 对应

**生产保护**(云端机房 IP 对 Google 限速阈值低,故):
  - 进程内缓存(解码结果永久稳定,可无限缓存;含负缓存防重试坏条目)
  - 每轮预算封顶 + 墙钟硬停(首屏不被拖死;热门条目跨轮补齐)
  - 熔断:见 429/403 立即跳闸,指数退避;熔断期间零请求,全部回退原链
  - 全局串行(多用户 fetch_all 并发时防叠加请求)
  - 任何异常都被吞掉,调用方永远拿到可用链接(成功=真实URL,否则=原 google 链)
"""

from __future__ import annotations

import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote, urlparse

import requests

# --- 生产参数(见 gnews-link-decoder 终审规格) ---
MAX_WORKERS = 3          # 机房 IP 阈值低于住宅,3 并发(原型 8 降下来)
BATCH_SIZE = 20          # 实测单 POST 带 30/50 均可,20 留余量
BUDGET_DEFAULT = 40      # 每轮新解码上限;稳态增量 10-30 条全覆盖,冷启动分轮补齐
WALL_CLOCK_CAP = 30.0    # 每轮墙钟硬停(秒),超时返回已完成部分
TIMEOUT_GET = 8.0
TIMEOUT_POST = 15.0
CACHE_MAX = 5000         # FIFO;含负缓存;进程休眠丢失无所谓
_COOLDOWNS = (15, 30, 60, 120)  # 熔断退避分钟数(指数,封顶 120)

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36")
# 绕过 EU IP 的 consent.google.com 拦截页(美国机房不触发,带上无害)
_COOKIES = {"SOCS": "CAESHAgBEhJnd3NfMjAyMzA4MTAtMF9SQzIaAmVuIAEaBgiA_LyaBg"}
_BATCH_URL = "https://news.google.com/_/DotsSplashUi/data/batchexecute"
_SG_RE = re.compile(r'data-n-a-sg="([^"]+)"')
_TS_RE = re.compile(r'data-n-a-ts="([^"]+)"')

# 缓存:google_url -> real_url | None(None = 尝试过但解不出,负缓存防重试)
_cache: dict[str, str | None] = {}
_cache_lock = threading.Lock()
_gate = threading.Lock()  # 全局串行:同一时刻只有一次解码在打接口
_breaker = {"open_until": 0.0, "trips": 0, "clean_rounds": 0}


# --- 协议(逆向,原样保留原型实现) ---

def _extract_b64(url: str) -> str | None:
    try:
        p = urlparse(url)
        parts = p.path.split("/")
        if p.hostname == "news.google.com" and len(parts) > 1 and parts[-2] in ("articles", "read"):
            return parts[-1]
    except Exception:
        pass
    return None


def _fetch_params(session: requests.Session, b64: str, rl: list) -> tuple[str, str] | None:
    # 两条路径都试完再判:实测 /articles 常 429 而 /rss/articles 200 —— 见 429 就短路
    # 会跳过能用的回落路径并误判限速。只有全部路径都被限速才标 rl。
    saw_rl = False
    for path in (f"https://news.google.com/rss/articles/{b64}",
                 f"https://news.google.com/articles/{b64}"):
        try:
            r = session.get(path, timeout=TIMEOUT_GET)
            if r.status_code in (429, 403):
                saw_rl = True
                continue
            if r.status_code != 200:
                continue
            sg, ts = _SG_RE.search(r.text), _TS_RE.search(r.text)
            if sg and ts:
                return sg.group(1), ts.group(1)
        except Exception:
            continue
    if saw_rl:
        rl[0] = True
    return None


def _payload(b64: str, ts: str, sg: str) -> str:
    return ('["garturlreq",[["X","X",["X","X"],null,null,1,1,"US:en",null,1,null,null,'
            'null,null,null,0,1],"X","X",1,[1,1,1],1,1,null,0,0,null,0],'
            f'"{b64}",{ts},"{sg}"]')


def _parse_batch_response(text: str) -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    dec = json.JSONDecoder()
    pos = text.find("[")
    while 0 <= pos < len(text):
        try:
            obj, end = dec.raw_decode(text, pos)
        except json.JSONDecodeError:
            nxt = text.find("[", pos + 1)
            if nxt <= pos:
                break
            pos = nxt
            continue
        if isinstance(obj, list):
            for el in obj:
                if (isinstance(el, list) and len(el) >= 7 and el[0] == "wrb.fr"
                        and el[1] == "Fbv4je" and isinstance(el[6], str)):
                    url = None
                    if isinstance(el[2], str):
                        try:
                            inner = json.loads(el[2])
                            if isinstance(inner, list) and len(inner) > 1 and isinstance(inner[1], str):
                                url = inner[1]
                        except Exception:
                            url = None
                    out[el[6]] = url
        pos = text.find("[", end)
    return out


def _post_batch(session: requests.Session, envelopes: list, rl: list) -> dict[str, str | None]:
    freq = [[["Fbv4je", _payload(b, ts, sg), None, eid] for eid, b, ts, sg in envelopes]]
    body = "f.req=" + quote(json.dumps(freq))
    try:
        r = session.post(_BATCH_URL,
                         headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
                         data=body, timeout=TIMEOUT_POST)
        if r.status_code in (429, 403):
            rl[0] = True
            return {}
        if r.status_code != 200:
            return {}
        return _parse_batch_response(r.text)
    except Exception:
        return {}


def _decode(urls: list[str]) -> tuple[dict[str, str | None], bool]:
    """网络解码一批(已去缓存/超预算)。返回 ({url: real|None}, 是否遇到限速)。"""
    rl = [False]
    deadline = time.monotonic() + WALL_CLOCK_CAP
    session = requests.Session()
    session.headers["User-Agent"] = _UA
    session.cookies.update(_COOKIES)

    b64_of = {u: _extract_b64(u) for u in urls}
    b64_list = list({b for b in b64_of.values() if b})
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        params = list(ex.map(lambda b: _fetch_params(session, b, rl), b64_list))

    envelopes, decoded = [], {}
    for b, pr in zip(b64_list, params):
        if pr is None:
            decoded[b] = None
        else:
            eid = str(len(envelopes) + 1)
            envelopes.append((eid, b, pr[1], pr[0]))
    for i in range(0, len(envelopes), BATCH_SIZE):
        if time.monotonic() > deadline:
            break
        chunk = envelopes[i:i + BATCH_SIZE]
        resp = _post_batch(session, chunk, rl)
        for eid, b, _ts, _sg in chunk:
            decoded[b] = resp.get(eid)

    return {u: decoded.get(b) for u, b in b64_of.items()}, rl[0]


def _put(url: str, real: str | None) -> None:
    if url not in _cache and len(_cache) >= CACHE_MAX:
        _cache.pop(next(iter(_cache)))  # FIFO
    _cache[url] = real


def resolve_batch(urls: list[str], budget: int = BUDGET_DEFAULT) -> dict[str, str | None]:
    """gnews 跳转链 → {url: 真实URL | None}。永不抛异常。None = 未解出(调用方保留原链)。

    - 命中缓存(含负缓存)不占预算;超预算的条目返回 None 且【不】负缓存(下轮再解)
    - 尝试过且失败(非限速)→ 负缓存,防每轮重试坏条目
    - 熔断期间:直接全部 None,零请求
    - 输入应按时间倒序(最新条目优先在预算内解出)
    """
    result: dict[str, str | None] = {}
    todo: list[str] = []
    for u in urls:
        with _cache_lock:
            if u in _cache:
                result[u] = _cache[u]
                continue
        if _extract_b64(u) is None:
            result[u] = None
        else:
            todo.append(u)
    if not todo:
        return result

    if time.monotonic() < _breaker["open_until"]:  # 熔断中
        for u in todo:
            result[u] = None
        return result

    try:
        with _gate:
            fresh = [u for u in todo if u not in _cache]  # 锁内复查(别的线程可能已填)
            batch = fresh[:budget]
            for u in fresh[budget:]:
                result[u] = None                          # 超预算:不负缓存
            if not batch:
                return result
            decoded, rate_limited = _decode(batch)
            ok = 0
            for u in batch:
                real = decoded.get(u)
                result[u] = real
                if real:
                    _put(u, real); ok += 1
                elif not rate_limited:
                    _put(u, None)                         # 真失败才负缓存;限速失败留待重试
            _update_breaker(rate_limited, attempted=len(batch), ok=ok)
    except Exception:
        for u in todo:
            result.setdefault(u, None)
    return result


def _update_breaker(rate_limited: bool, attempted: int, ok: int) -> None:
    trip = rate_limited or (attempted >= 10 and ok / attempted < 0.5)
    if trip:
        t = min(_breaker["trips"], len(_COOLDOWNS) - 1)
        _breaker["open_until"] = time.monotonic() + _COOLDOWNS[t] * 60
        _breaker["trips"] += 1
        _breaker["clean_rounds"] = 0
    elif ok > 0:
        _breaker["clean_rounds"] += 1
        if _breaker["clean_rounds"] >= 2:  # 连续 2 轮干净 → 退避归零
            _breaker["trips"] = 0


def stats() -> dict:
    """给侧栏 caption 用:缓存量、熔断状态。"""
    open_for = max(0, _breaker["open_until"] - time.monotonic())
    return {"cached": len(_cache), "trips": _breaker["trips"],
            "cooldown_min": round(open_for / 60, 1)}


# --- 大陆可达性标注(正交增强,零网络成本) ---
# 解码 ≠ 可达:纽时/彭博/路透等出版商本身在大陆被墙。维护一份 GFW 常见被墙域名后缀,
# 让大陆同事一眼知道哪条点了有用。宁缺毋滥:不确定的默认按可达(不误标)。
_GFW_BLOCKED = (
    "google.com", "google.co", "youtube.com", "twitter.com", "x.com", "facebook.com",
    "instagram.com", "nytimes.com", "wsj.com", "bloomberg.com", "reuters.com", "ft.com",
    "economist.com", "theguardian.com", "bbc.com", "bbc.co.uk", "politico.eu", "apnews.com",
    "rfi.fr", "dw.com", "voanews.com", "rfa.org", "wikipedia.org", "medium.com", "substack.com",
    # 台湾媒体
    "udn.com", "chinatimes.com", "ltn.com.tw", "cna.com.tw", "storm.mg", "setn.com",
    # 部分半墙(从严标)
    "zaobao.com", "zaobao.com.sg",
)


def mainland_reachable(url: str) -> bool:
    """该链接从中国大陆是否可直接打开(粗判,基于出版商域名)。"""
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return True
    if not host:
        return True
    return not any(host == d or host.endswith("." + d) for d in _GFW_BLOCKED)
