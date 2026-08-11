"""p3 配置码 —— 记者个人监控配置的 URL 编码(encode/decode)。

个人配置(勾选的区域/条线/信源类型、自定义关键词、关注实体)只活在
URL 配置码/书签/下载备份里,绝不写入仓库(公私边界见 DESIGN.md)。
格式: "p3." + base64url(zlib(紧凑 JSON)),版本前缀在压缩层外,
解码器不解压即可路由版本;旧书签在未来版本下永远可读。

紧凑 JSON 键:
  v   schema 版本(冗余自校验)
  r   区域档 id 列表          d   条线 id 列表
  s   专线信源类型 id 列表     bg  背景快讯层开关(主流+通讯社)
  kw  自定义关键词 ≤12        ent 关注实体 ≤20
"""

from __future__ import annotations

import base64
import json
import zlib

SCHEMA_VERSION = 3
PREFIX = f"p{SCHEMA_VERSION}."

MAX_KEYWORDS = 12
MAX_ENTITIES = 20
MAX_DECOMPRESSED = 64 * 1024        # zlib 炸弹护栏
MAX_TERM_LEN = 80                   # 单个关键词/实体长度护栏

# 三个大档 + 细分档(中欧→中德/中法;中国→香港)。选大档自动含其细分
# (展开逻辑在 beat_packs._selected_regions);细分档可单选做更窄的监控。
REGIONS = {"cn", "cn-eu", "cn-us", "cn-de", "cn-fr", "cn-hk"}

# 条线 id 从第一天冻结;UI 显示名可改,id 不可改(改名会撕掉所有旧配置码)
DOMAINS = {
    # P0
    "macro", "central-bank", "trade-tariff", "export-control",
    "semiconductor", "ai", "nev", "battery", "solar",
    "health", "education", "shipping-logistics", "diplomacy",
    # P0 补充(2026-08-08 用户点名:金融/财政;消费→互联网)
    "finance-markets", "fiscal", "platforms",
    # P1 预留
    "real-estate", "commodities", "defense", "climate", "oil-power",
}

SOURCE_TYPES = {"gov", "wechat-mirror", "industry", "thinktank"}

# 条线的历史沿革:旧配置码里的 id → 新 id;None = 已移除。
# 解码时静默换新并给出人话提示,绝不因死 id 整码作废。
ALIASES: dict[str, str | None] = {
    "espionage": None,          # 间谍线 2026-08-08 按用户裁定移除
    "consumer": "platforms",    # 消费线 2026-08-08 按用户裁定并入互联网
}

# 压缩层内的短 token(仅为码长服务;对外 API 始终用完整 id)。
# token 一经发布同样冻结 —— 改它等于撕掉旧配置码。
_REGION_TOKENS = {"cn": "c", "cn-eu": "e", "cn-us": "u",
                  "cn-de": "d", "cn-fr": "f", "cn-hk": "h"}
_DOMAIN_TOKENS = {
    "macro": "ma", "central-bank": "cb", "trade-tariff": "tt",
    "export-control": "ec", "semiconductor": "se", "ai": "ai",
    "nev": "nv", "battery": "ba", "solar": "so", "health": "he",
    "education": "ed", "consumer": "co", "shipping-logistics": "sl",
    "diplomacy": "di", "finance-markets": "fm", "fiscal": "fs", "real-estate": "re",
    "commodities": "cm", "defense": "df", "platforms": "pl",
    "climate": "cl", "oil-power": "op",
}
_TYPE_TOKENS = {"gov": "g", "wechat-mirror": "w", "industry": "i", "thinktank": "t"}
_REGION_BACK = {v: k for k, v in _REGION_TOKENS.items()}
_DOMAIN_BACK = {v: k for k, v in _DOMAIN_TOKENS.items()}
_TYPE_BACK = {v: k for k, v in _TYPE_TOKENS.items()}


def _clean_terms(raw, cap: int) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for t in raw if isinstance(raw, list) else []:
        if not isinstance(t, str):
            continue
        t = t.strip()[:MAX_TERM_LEN]
        if t and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
        if len(out) >= cap:
            break
    return out


def encode(cfg: dict) -> str:
    """配置 dict → "p3.xxx" 配置码。入参键同 decode 返回;缺省键省略以缩短码长。"""
    compact: dict = {"v": SCHEMA_VERSION}
    if cfg.get("regions"):
        compact["r"] = sorted(_REGION_TOKENS[r] for r in set(cfg["regions"]) & REGIONS)
    if cfg.get("domains"):
        compact["d"] = sorted(_DOMAIN_TOKENS[d] for d in set(cfg["domains"]) & DOMAINS)
    if cfg.get("source_types"):
        compact["s"] = sorted(_TYPE_TOKENS[s] for s in set(cfg["source_types"]) & SOURCE_TYPES)
    if not cfg.get("background", True):
        compact["bg"] = 0               # 只有关掉背景层才占字节(默认开)
    kw = _clean_terms(cfg.get("keywords", []), MAX_KEYWORDS)
    ent = _clean_terms(cfg.get("entities", []), MAX_ENTITIES)
    if kw:
        compact["kw"] = kw
    if ent:
        compact["ent"] = ent
    raw = json.dumps(compact, ensure_ascii=False, separators=(",", ":")).encode()
    packed = base64.urlsafe_b64encode(zlib.compress(raw, 9)).decode().rstrip("=")
    return PREFIX + packed


def decode(code: str) -> tuple[dict | None, list[str]]:
    """"p3.xxx" → (配置 dict, 提示列表)。任何解不开的情况返回 (None, [原因]),
    调用方落回筛选流第一层,绝不抛异常、绝不崩页面。"""
    warnings: list[str] = []
    code = (code or "").strip()
    if "." not in code or not code.startswith("p"):
        return None, ["配置码格式不对(应以 p3. 开头)。请粘贴完整链接或重新配置。"]
    ver, _, payload = code.partition(".")
    if ver != f"p{SCHEMA_VERSION}":
        return None, [f"配置码版本 {ver} 不受当前应用支持。请重新配置一次(旧配置无法自动迁移)。"]
    try:
        packed = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
        raw = zlib.decompressobj().decompress(packed, MAX_DECOMPRESSED)
        obj = json.loads(raw)
        if not isinstance(obj, dict):
            raise ValueError("payload 不是对象")
    except Exception:
        return None, ["配置码无法解析(可能被截断或篡改)。请粘贴完整链接或重新配置。"]

    # 短 token → 完整 id(同时接受完整 id,便于手写/调试)
    regions = [_REGION_BACK.get(r, r) for r in obj.get("r", []) if isinstance(r, str)]
    bad_r = [r for r in regions if r not in REGIONS]
    if bad_r:
        warnings.append(f"配置里的区域档暂不支持,已忽略:{', '.join(bad_r)}")

    domains: list[str] = []
    for d in obj.get("d", []):
        if not isinstance(d, str):
            continue
        d = _DOMAIN_BACK.get(d, d)
        if d in ALIASES:
            new = ALIASES[d]
            if new is None:
                warnings.append(f"条线「{d}」已从系统中移除,已自动去掉。")
            else:
                warnings.append(f"条线「{d}」已并入「{new}」,已自动更新。")
                if new not in domains:
                    domains.append(new)
            continue
        if d not in DOMAINS:
            warnings.append(f"条线「{d}」不存在,已忽略。")
            continue
        if d not in domains:
            domains.append(d)

    source_types = [t for t in (_TYPE_BACK.get(s, s) for s in obj.get("s", [])
                                if isinstance(s, str)) if t in SOURCE_TYPES]

    cfg = {
        "regions": [r for r in regions if r in REGIONS],
        "domains": domains,
        "source_types": source_types,
        "background": bool(obj.get("bg", 1)),
        "keywords": _clean_terms(obj.get("kw", []), MAX_KEYWORDS),
        "entities": _clean_terms(obj.get("ent", []), MAX_ENTITIES),
    }
    return cfg, warnings
