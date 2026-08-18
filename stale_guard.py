"""陈年新闻防御:识破 Google News 给旧文章盖的新时间戳。

实锤机制(2026-08-17 抓到现行):开放式 gnews 查询(不带 site:)里,Google 重新
索引的旧内容会带**伪造的当日 pubDate**(2020 年 Mashable 旧文标着今天 20:36)。
时间窗过滤信了 feed 日期就放行 → 考古新闻混进监测台。

两层防御(都只在有确凿"旧"证据时才丢,宁多勿漏):
  L1 URL 日期矛盾:很多媒体 URL 自带 /2020/03/ 式路径,与声称日期矛盾即丢。零成本。
  L2 发布日期核验:对开放查询的条目,拉真实文章页读 article:published_time /
     JSON-LD datePublished / <time datetime>。带预算(15 条/轮)+ 永久缓存 +
     失败不丢(拿不到证据就放行)。

不适用防御的:site: 定向查询与原生 RSS(它们的日期可信度高,且量大核验不起)。
"""

from __future__ import annotations

import json
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import requests

VERIFY_BUDGET = 15          # 每轮最多核验条数(缓存命中不计)
MARGIN = timedelta(hours=48)  # 只丢比窗口起点还早 48h 以上的(防时区/边界误伤)
_TIMEOUT = 6.0
_MAX_BYTES = 120_000        # 只读页面前 120KB(日期 meta 都在 head 里)
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36")

# URL 路径里的日期:/2020/03/、/2020-03-15/、_20200315 等;年份限定 2015-2029 防误配编号
_URL_DATE_RE = re.compile(r"[/_-](20[12]\d)[/_-](0?[1-9]|1[0-2])(?:[/_-]|$)")

_META_RES = (
    re.compile(r'property=["\']article:published_time["\'][^>]*content=["\']([^"\']+)', re.I),
    re.compile(r'content=["\']([^"\']+)["\'][^>]*property=["\']article:published_time', re.I),
    re.compile(r'"datePublished"\s*:\s*"([^"]+)"'),
    re.compile(r'<time[^>]+datetime=["\']([^"\']+)', re.I),
    re.compile(r'name=["\'](?:pubdate|publish-date|publication_date|date)["\'][^>]*content=["\']([^"\']+)', re.I),
)

_cache: dict[str, datetime | None] = {}   # url -> 已核验的发布时间(None=核验过但拿不到)
_cache_lock = threading.Lock()
_last_dropped = 0                          # 最近一轮丢弃数(侧栏可观测)


def url_claimed_date(url: str) -> datetime | None:
    """URL 路径自带的年月(仅当同时有年+月才算,单独 /2026 不算)。"""
    try:
        m = _URL_DATE_RE.search(urlparse(url).path)
        if m:
            return datetime(int(m.group(1)), int(m.group(2)), 1, tzinfo=timezone.utc)
    except Exception:
        pass
    return None


def _parse_dt(raw: str) -> datetime | None:
    raw = raw.strip()[:32]
    for cut in (len(raw), 19, 10):
        try:
            dt = datetime.fromisoformat(raw[:cut].replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except Exception:
            continue
    return None


def fetch_published(url: str, session: requests.Session) -> datetime | None:
    """拉文章页抽发布时间;任何失败返回 None(= 无证据,放行)。结果永久缓存。"""
    with _cache_lock:
        if url in _cache:
            return _cache[url]
    dt = None
    try:
        r = session.get(url, timeout=_TIMEOUT, stream=True)
        head = r.raw.read(_MAX_BYTES, decode_content=True).decode("utf-8", "ignore")
        r.close()
        for rx in _META_RES:
            m = rx.search(head)
            if m and (dt := _parse_dt(m.group(1))):
                break
    except Exception:
        dt = None
    with _cache_lock:
        if len(_cache) > 5000:
            _cache.pop(next(iter(_cache)))
        _cache[url] = dt
    return dt


def filter_stale(items: list[dict], cutoff: datetime) -> tuple[list[dict], int]:
    """两层防御过滤。items 需带 link;开放查询条目带 verify_date=True(fetch_one 标记)。
    返回 (保留的条目, 丢弃数)。绝不抛异常。"""
    try:
        hard_cutoff = cutoff - MARGIN
        kept: list[dict] = []
        dropped = 0
        budget = VERIFY_BUDGET
        session = None
        for it in items:
            link = it.get("link", "")
            # L1:URL 自带日期与声称日期矛盾(对所有条目,零成本)
            ud = url_claimed_date(link)
            if ud is not None and ud < datetime(hard_cutoff.year, hard_cutoff.month, 1,
                                                tzinfo=timezone.utc):
                dropped += 1
                continue
            # L2:开放查询条目核验真实发布时间(带预算;google 跳转链无法核验,跳过)
            if it.get("verify_date") and not link.startswith("https://news.google.com"):
                with _cache_lock:
                    cached = link in _cache
                if cached or budget > 0:
                    if not cached:
                        budget -= 1
                    if session is None:
                        session = requests.Session()
                        session.headers["User-Agent"] = _UA
                    pub = fetch_published(link, session)
                    if pub is not None and pub < hard_cutoff:
                        dropped += 1
                        continue
            kept.append(it)
        global _last_dropped
        _last_dropped = dropped
        return kept, dropped
    except Exception:
        return items, 0


def stats() -> dict:
    return {"verified_cached": len(_cache), "last_dropped": _last_dropped}
