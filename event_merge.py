"""同事件跨媒体合并的灰带裁决器(LLM,批量+缓存+彻底 fail-open)。

背景:各家独立撰写的同事件标题词集重叠低(实测 BYD 米兰设计中心三连仅 0.28-0.33),
纯阈值在 0.28-0.45 灰带真假混杂("智利外长访问"×"王毅访韩"0.42 是假对)。
≥0.45 由算法直接合并(实测零误判);灰带批量问一次小模型"是否同一事件"。

安全边界:
  - 无 API key / 调用失败 / 解析失败 → 全部判"不合并"(回到现状,绝不多合)
  - 结果按标题对永久缓存(进程内),稳态每轮新增仅几对,成本趋零
  - 单次调用,超时 12s,绝不重试,绝不抛异常到抓取层
"""

from __future__ import annotations

import json
import re
import threading

MODEL = "claude-haiku-4-5"      # 灰带裁决是二分类小任务,用最便宜档
MAX_PAIRS_PER_CALL = 24
_TIMEOUT = 12.0

_cache: dict[frozenset, bool] = {}   # {title_a, title_b} -> 同事件?
_lock = threading.Lock()


def _api_key() -> str | None:
    import os
    if k := os.environ.get("ANTHROPIC_API_KEY"):
        return k
    try:
        import streamlit as st
        return st.secrets.get("ANTHROPIC_API_KEY") or st.secrets.get("anthropic", {}).get("api_key")
    except Exception:
        return None


def adjudicate(pairs: list[tuple[str, str]]) -> dict[frozenset, bool]:
    """输入标题对列表,返回 {frozenset({a,b}): 是否同一事件}。缓存命中不调用;
    任何失败 → 未缓存的对全部 False。永不抛。"""
    out: dict[frozenset, bool] = {}
    todo: list[tuple[str, str]] = []
    with _lock:
        for a, b in pairs:
            k = frozenset((a, b))
            if k in _cache:
                out[k] = _cache[k]
            else:
                todo.append((a, b))
    if not todo:
        return out
    todo = todo[:MAX_PAIRS_PER_CALL]
    verdicts = _call_llm(todo)
    with _lock:
        for (a, b), v in zip(todo, verdicts):
            k = frozenset((a, b))
            _cache[k] = v
            out[k] = v
    for a, b in pairs:  # 超出单次上限的对:本轮按不合并,下轮再判
        out.setdefault(frozenset((a, b)), False)
    return out


def _call_llm(pairs: list[tuple[str, str]]) -> list[bool]:
    key = _api_key()
    if not key:
        return [False] * len(pairs)
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=key, timeout=_TIMEOUT, max_retries=0)
        lines = "\n".join(f"{i+1}. A:「{a[:120]}」 B:「{b[:120]}」" for i, (a, b) in enumerate(pairs))
        msg = client.messages.create(
            model=MODEL, max_tokens=200,
            system=("判断每对新闻标题是否报道**同一具体事件**(同一公司同一动作同一天,"
                    "仅措辞不同=是;同一主体的不同事件/不同数据点=否)。"
                    '只输出 JSON 数组,如 [1,0,1],1=同一事件,0=不是。'),
            messages=[{"role": "user", "content": lines}],
        )
        text = msg.content[0].text
        arr = json.loads(re.search(r"\[[\d,\s]*\]", text).group(0))
        if len(arr) != len(pairs):
            return [False] * len(pairs)
        return [bool(x) for x in arr]
    except Exception:
        return [False] * len(pairs)


def stats() -> dict:
    with _lock:
        return {"cached_pairs": len(_cache), "merged": sum(_cache.values())}
