"""🎯 三层筛选流 —— 记者自助定制(区域 → 条线 → 信源口径)。

数据资产:
  source_registry.json  源注册表(region × source_type × domains 三维打标)
  packs.json            14 条 P0 条线的词表模块(id 冻结,见 profile_code.DOMAINS)

原则(DESIGN.md):
  - 个人配置只活在 URL 配置码里(profile_code.encode),零落盘、零 PR、零维护者;
  - 定制页(?setup=1)不触发任何抓取;
  - 供给徽章诚实:某条线在所选区域没有专线源,就明说"兜底",不假装。
"""

from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

import i18n
import profile_code

REPO_DIR = Path(__file__).resolve().parent

# 专线四类(逐项勾选) vs 背景层(主流+通讯社,总开关)——依据:实测 97% 的
# 主流/通讯社源是泛源,与领域专线并列勾选会制造大片假空格
SPECIALIST_TYPES = ("gov", "wechat-mirror", "industry", "thinktank")
TYPE_LABELS = {"gov": "🏛️ 官方网站", "wechat-mirror": "📱 公众号镜像",
               "industry": "🏭 行业媒体", "thinktank": "🎓 智库"}
REGION_LABELS = {"cn": "🇨🇳 中国国内", "cn-eu": "🤝 中欧", "cn-us": "🇺🇸 中美"}
SUB_REGION_LABELS = {"cn-de": "🇩🇪 中德", "cn-fr": "🇫🇷 中法", "cn-hk": "🇭🇰 香港"}
# 大档 → 含的细分档;选「中欧」自动包含中德/中法的源,选「中德」则只看德方源
_REGION_EXPAND = {"cn-eu": {"cn-eu", "cn-de", "cn-fr"}, "cn": {"cn", "cn-hk"},
                  "cn-us": {"cn-us"}}


def _selected_regions(regions) -> set[str]:
    out: set[str] = set()
    for r in regions or []:
        out |= _REGION_EXPAND.get(r, {r})
    return out

# 注册表 source_type → 应用现有 lane(必须落在 LANE_LABELS 里,
# 否则条目抓到了、归类了、就是不显示且不报错 —— 已知陷阱)
_TYPE_TO_LANE = {"gov": "gov", "wires": "wires", "thinktank": "thinktank",
                 "industry": "industry", "wechat-mirror": "cn-media"}


@st.cache_data(ttl=600)
def _load_json(path: str, mtime: float) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_registry() -> dict:
    p = REPO_DIR / "source_registry.json"
    return _load_json(str(p), p.stat().st_mtime)


def load_packs() -> dict:
    p = REPO_DIR / "packs.json"
    return _load_json(str(p), p.stat().st_mtime)


def _lane_for(src: dict) -> str:
    if src["source_type"] == "mainstream":
        return {"de": "de-media", "fr": "fr-media", "zh": "cn-media"}.get(src["lang"], "wires")
    return _TYPE_TO_LANE.get(src["source_type"], "custom")


def _region_ok(src: dict, regions: set[str]) -> bool:
    """regions 为已展开的档位集合(_selected_regions 的输出)。"""
    return src["region"] == "cross" or not regions or src["region"] in regions


def resolve(cfg: dict) -> tuple[list[dict], list[dict], dict]:
    """配置 → (应用源列表, 原始词表模块列表, 统计)。纯选择逻辑,不做规范化
    (词表规范化 _ntl 由主脚本在换词表时执行)。"""
    registry, packs = load_registry(), load_packs()
    domains = set(cfg.get("domains", []))
    regions = _selected_regions(cfg.get("regions", []))
    types = set(cfg.get("source_types", [])) & set(SPECIALIST_TYPES) or set(SPECIALIST_TYPES)

    specialist: list[dict] = []
    background: list[dict] = []
    for s in registry["sources"]:
        if not s.get("enabled", True) or not _region_ok(s, regions):
            continue
        doms = set(s.get("domains", []))
        is_general = "general" in doms
        if doms & domains:
            # 领域专线:所选四类,外加主流/通讯社里的领域分版面(如 NYT·Education)
            if s["source_type"] in types or (s["source_type"] in ("mainstream", "wires")
                                             and not is_general):
                specialist.append(s)
                continue
        if cfg.get("background", True) and is_general \
                and s["source_type"] in ("mainstream", "wires"):
            background.append(s)

    seen: set[str] = set()
    sources: list[dict] = []
    for s in specialist + background:
        if s["value"] in seen:
            continue
        seen.add(s["value"])
        entry = {"name": s["name"], "type": s["type"], "value": s["value"],
                 "lane": _lane_for(s), "lang": s.get("lang", "en"),
                 "filter": s.get("filter", "none"), "enabled": True,
                 "stype": s["source_type"]}  # 口径(tab 内二级筛选用)
        if s["type"] == "gnews" and s.get("gnews_locale"):
            entry["gnews_locale"] = s["gnews_locale"]
        sources.append(entry)

    by_id = {p["id"]: p for p in packs["packs"]}
    cats_raw: list[dict] = []
    if cfg.get("keywords") or cfg.get("entities"):
        cats_raw.append({"id": "watch", "emoji": "🎯", "zh": "我的关注", "en": "My Watchlist",
                        "kw": cfg.get("keywords", []), "entities": cfg.get("entities", [])})
    for d in cfg.get("domains", []):
        if d in by_id:
            p = by_id[d]
            cats_raw.append({"id": p["id"], "emoji": p["emoji"], "zh": p["zh"],
                             "en": p.get("en", p["id"]), "kw": p["kw"],
                             "entities": p.get("entities", [])})

    stats = {"specialist": len(specialist), "background": len(background),
             "total": len(sources)}
    return sources, cats_raw, stats


def domain_supply(regions: set[str]) -> dict[str, int]:
    """每条线在所选区域下的专线源数(供徽章;背景泛源不计,诚实原则)。"""
    registry = load_registry()
    regions = _selected_regions(regions)
    counts: dict[str, int] = {}
    for s in registry["sources"]:
        if not s.get("enabled", True) or not _region_ok(s, regions):
            continue
        doms = set(s.get("domains", []))
        if "general" in doms and s["source_type"] in ("mainstream", "wires"):
            continue
        for d in doms:
            counts[d] = counts.get(d, 0) + 1
    return counts


# ---------------------------------------------------------------------------
# 定制页(?setup=1)
# ---------------------------------------------------------------------------

def _group_of(packs: dict) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = {}
    for p in packs["packs"]:
        groups.setdefault(p.get("group", "其他"), []).append(p)
    return groups


def _apply_prefill(pf: dict, packs: dict) -> None:
    """把一份配置写进各控件的 session key(必须在控件实例化之前调用)。"""
    regs = pf.get("regions", ["cn-eu"])
    st.session_state["bp_regions"] = [r for r in regs if r in REGION_LABELS]
    st.session_state["bp_regions_sub"] = [r for r in regs if r in SUB_REGION_LABELS]
    doms = set(pf.get("domains", []))
    for gname, plist in _group_of(packs).items():
        st.session_state[f"bp_dom_{gname}"] = [p["id"] for p in plist if p["id"] in doms]
    st.session_state["bp_types"] = [t for t in pf.get("source_types", list(SPECIALIST_TYPES))
                                    if t in SPECIALIST_TYPES]
    st.session_state["bp_bg"] = bool(pf.get("background", True))
    st.session_state["bp_kw"] = ", ".join(pf.get("keywords", []))
    st.session_state["bp_ent"] = ", ".join(pf.get("entities", []))


def _ensure(key: str, value) -> None:
    if key not in st.session_state:
        st.session_state[key] = value


def render_setup(prefill: dict | None = None, notices: list[str] | None = None) -> None:
    packs = load_packs()
    groups = _group_of(packs)
    en = i18n.is_en()
    st.title(i18n.t("setup_title"))
    st.caption(i18n.t("setup_caption"))
    st.markdown(i18n.t("setup_back", ls0="?lang=en" if en else ""))
    i18n.render_toggle()
    for n in notices or []:
        st.info(n)

    # 首次进入:带码则预填当前配置,否则给默认值
    if "bp_regions" not in st.session_state:
        _apply_prefill(prefill or {}, packs)

    st.subheader(i18n.t("step_region"))
    regions = st.pills(i18n.t("region_multi"), options=list(REGION_LABELS),
                       selection_mode="multi",
                       format_func=lambda r: (i18n.REGION_EN[r] if en else REGION_LABELS[r]),
                       label_visibility="collapsed", key="bp_regions")
    sub = st.pills(i18n.t("region_sub"),
                   options=list(SUB_REGION_LABELS), selection_mode="multi",
                   format_func=lambda r: (i18n.SUB_REGION_EN[r] if en else SUB_REGION_LABELS[r]),
                   key="bp_regions_sub")
    all_regions = list(regions or []) + list(sub or [])
    st.caption(i18n.t("region_note"))

    st.subheader(i18n.t("step_beats"))
    supply = domain_supply(set(all_regions))
    chosen: list[str] = []
    for gname, plist in groups.items():
        ids = [p["id"] for p in plist]
        names = {p["id"]: i18n.cat_label(p) for p in plist}
        emoji = {p["id"]: p.get("emoji", "") for p in plist}

        def _label(d, names=names, emoji=emoji):
            n = supply.get(d, 0)
            return f"{emoji[d]} {names[d]} ({n if n else i18n.t('fallback_badge')})"

        _ensure(f"bp_dom_{gname}", [])
        sel = st.pills(i18n.GROUP_EN.get(gname, gname) if en else gname,
                       options=ids, selection_mode="multi",
                       format_func=_label, key=f"bp_dom_{gname}")
        chosen.extend(sel or [])
    if any(supply.get(d, 0) == 0 for d in chosen):
        st.caption(i18n.t("fallback_note"))

    st.subheader(i18n.t("step_types"))
    types = st.pills(i18n.t("types_label"), options=list(SPECIALIST_TYPES),
                     selection_mode="multi",
                     format_func=lambda t: (i18n.STYPE_EN[t] if en else TYPE_LABELS[t]),
                     key="bp_types")
    background = st.toggle(i18n.t("bg_toggle"), key="bp_bg", help=i18n.t("bg_help"))
    kw_in = st.text_input(i18n.t("kw_label"), key="bp_kw", placeholder=i18n.t("kw_ph"))
    ent_in = st.text_input(i18n.t("ent_label"), key="bp_ent", placeholder=i18n.t("ent_ph"))

    cfg = {
        "regions": all_regions,
        "domains": chosen,
        "source_types": list(types or []),
        "background": bool(background),
        "keywords": [t.strip() for t in (kw_in or "").replace(",", ",").split(",") if t.strip()],
        "entities": [t.strip() for t in (ent_in or "").replace(",", ",").split(",") if t.strip()],
    }

    st.divider()
    if not chosen:
        st.warning(i18n.t("need_beat"))
        return
    _, _, stats = resolve(cfg)
    code = profile_code.encode(cfg)
    st.markdown(i18n.t("your_config", r=len(all_regions), b=len(chosen), t=len(types or []),
                       sp=stats["specialist"], bg=stats["background"]))
    st.markdown(i18n.t("open_monitor", code=code, ls=i18n.lang_suffix()))
    st.caption(i18n.t("bookmark_hint"))
    with st.expander(i18n.t("code_expander")):
        st.code(f"?profile={code}", language=None)
        st.download_button(i18n.t("backup_btn"), json.dumps(cfg, ensure_ascii=False, indent=2),
                           file_name="my_beats.profile.json")
        st.caption(i18n.t("modify_hint"))
