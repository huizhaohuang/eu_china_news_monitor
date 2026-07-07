"""
💬 定制 tab — 访谈式监测配置生成(Claude API)
================================================
记者用对话描述自己的条线,Claude 访谈澄清后生成一份档案(profiles/<名>/
sources.json + taxonomy.json),应用实测每个源,一键在 GitHub 开 PR 交 Evan 审核。

设计原则(全部来自本仓库踩坑经验):
- LLM 只在配置生成时用,监测循环零 API 成本。
- 模型严禁发明 RSS URL:只能从"已验证目录"(根目录 sources.json)里选,
  或组装 Google News 查询(gnews 查询永远合法,不存在死链)。
- 生成的配置先实测(逐源真抓)再交付;失败源喂回模型修一轮。
- 两种模式:无档案 = 初次访谈建新档案;带档案(?profile=x)= 增量模式,
  聊"要新增什么",输出完整更新后的文件(PR diff 即审核视图)。
- 交付走 GitHub PR(代码硬性限定只写 profiles/、只开 PR、不碰 main);
  无 token 时回退为下载按钮。

所需 Secrets(Streamlit Cloud → App settings → Secrets,或环境变量):
  ANTHROPIC_API_KEY      必需,Claude API 密钥
  INTERVIEW_ACCESS_CODE  强烈建议,访谈访问口令(公开链接防蹭用)
  GITHUB_TOKEN           可选,细粒度 PAT(仅本仓库,Contents+PR 权限)→ 启用一键 PR
  GITHUB_REPO            可选,默认 huizhaohuang/eu_china_news_monitor
"""

from __future__ import annotations

import base64
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import quote_plus

import requests
import streamlit as st

REPO_DIR = Path(__file__).resolve().parent
DEFAULT_REPO = "huizhaohuang/eu_china_news_monitor"
MODEL = "claude-opus-4-8"
MAX_TURNS = 40                      # 每会话轮数上限(token 护栏)
CHAT_MAX_TOKENS = 1500
CONFIG_MAX_TOKENS = 16000
_GNEWS = {
    "en": "https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en",
    "de": "https://news.google.com/rss/search?q={q}&hl=de&gl=DE&ceid=DE:de",
    "fr": "https://news.google.com/rss/search?q={q}&hl=fr&gl=FR&ceid=FR:fr",
    "zh": "https://news.google.com/rss/search?q={q}&hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
}
_UA = {"User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36")}

# 结构化输出 schema:模型最终产出的档案包(strict JSON,SDK 层校验)
PROFILE_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "profile_name": {"type": "string", "description": "小写 slug,如 xiaofei、marco-cee"},
        "display_name": {"type": "string"},
        "summary": {"type": "string", "description": "配置概要(给人看的,进 PR 描述)"},
        "sources": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "name": {"type": "string"},
                "type": {"type": "string", "enum": ["rss", "gnews"]},
                "value": {"type": "string"},
                "lane": {"type": "string"},
                "lang": {"type": "string", "enum": ["en", "de", "fr", "zh"]},
                "filter": {"type": "string", "enum": ["china", "europe", "none"]},
                "gnews_locale": {"type": "string", "enum": ["en", "de", "fr", "zh"]},
                "enabled": {"type": "boolean"},
            },
            "required": ["name", "type", "value", "lane", "lang", "filter", "enabled"],
        }},
        "taxonomy": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "categories": {"type": "array", "items": {
                    "type": "object", "additionalProperties": False,
                    "properties": {
                        "id": {"type": "string"}, "emoji": {"type": "string"},
                        "zh": {"type": "string"}, "en": {"type": "string"},
                        "kw": {"type": "array", "items": {"type": "string"}},
                        "entities": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["id", "emoji", "zh", "en", "kw", "entities"],
                }},
                "specificity": {"type": "array", "items": {"type": "string"}},
                "breaking_keywords": {"type": "array", "items": {"type": "string"}},
                "priority_pairs": {"type": "array", "items": {
                    "type": "object", "additionalProperties": False,
                    "properties": {
                        "entity": {"type": "string"},
                        "contexts": {"anyOf": [{"type": "array", "items": {"type": "string"}},
                                               {"type": "null"}]},
                    },
                    "required": ["entity", "contexts"],
                }},
                "china_gate_extra": {"type": "array", "items": {"type": "string"}},
                "europe_gate_extra": {"type": "array", "items": {"type": "string"}},
                "title_blocklist_extra": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["categories", "breaking_keywords", "priority_pairs"],
        },
    },
    "required": ["profile_name", "display_name", "summary", "sources", "taxonomy"],
}


# ---------------------------------------------------------------------------
# secrets / 系统提示词
# ---------------------------------------------------------------------------

def _secret(name: str) -> str | None:
    try:
        if name in st.secrets:
            return str(st.secrets[name])
    except Exception:
        pass
    return os.environ.get(name)


def _compact(srcs: list[dict]) -> str:
    keep = ("name", "type", "value", "lang", "filter", "gnews_locale", "tier")
    return json.dumps([{k: s[k] for k in keep if s.get(k)} for s in srcs],
                      ensure_ascii=False, separators=(",", ":"))


def _catalog_json() -> str:
    """已验证信源目录 = 根目录 sources.json(默认档案),压缩后注入系统提示词。"""
    try:
        srcs = json.loads((REPO_DIR / "sources.json").read_text(encoding="utf-8"))
    except Exception:
        return "[]"
    compact = [{k: s[k] for k in ("name", "type", "value", "lane", "lang", "filter")
                if k in s} | ({"gnews_locale": s["gnews_locale"]} if s.get("gnews_locale") else {})
               for s in srcs]
    return json.dumps(compact, ensure_ascii=False, separators=(",", ":"))


def _library_md() -> str:
    """领域扩展库(source_library.json)→ 按领域包分组的紧凑文本,注入系统提示词。"""
    try:
        lib = json.loads((REPO_DIR / "source_library.json").read_text(encoding="utf-8"))
    except Exception:
        return "(领域扩展库缺失,仅可使用上方核心目录与 gnews 查询)"
    parts = []
    for pid, pack in lib.get("packs", {}).items():
        parts.append(f"### {pack.get('zh', pid)} [{pid}]\n{_compact(pack.get('sources', []))}")
    return "\n".join(parts)


def _profile_context(profile: str | None) -> str:
    """增量模式:把现有档案文件放进上下文。"""
    if not profile:
        return ""
    pdir = REPO_DIR / "profiles" / profile
    parts = []
    for fn in ("sources.json", "taxonomy.json"):
        f = pdir / fn
        if f.exists():
            parts.append(f"### 现有 {fn}\n```json\n{f.read_text(encoding='utf-8')}\n```")
    if not parts:
        return ""
    return (f"\n\n## 增量模式\n当前正在更新已有档案 `{profile}`,现有配置如下。"
            "访谈聚焦于用户想**新增或调整**的监控范围;最终输出**完整的更新后文件**"
            "(保留全部原有内容,除非用户明确要求删除;PR 的 diff 就是审核视图)。\n\n"
            + "\n\n".join(parts))


def _build_system(profile: str | None) -> str:
    return f"""你是「中欧新闻监测平台」的配置顾问。用户是记者,你通过简短访谈弄清 TA 的条线,然后生成一份监测档案(信源列表 + 分类词表)。全程用用户的语言(通常是中文)。

## 访谈规则
- 每轮最多问 2-3 个问题,别问卷式轰炸。需要弄清:条线主题与地域、涉及语言(决定选德/法/英/中文源)、重点行业、绝不能漏的事件类型(决定突发词表)、大致每天想看多少条(决定源的数量与过滤松紧)。
- 信息足够时,先用几句话给出配置概要(几个源、几个分类)征求确认,提示用户点「⚙️ 生成配置」按钮。不要在聊天里输出完整 JSON。

## 平台机制(生成配置时必须遵守)
- 信源字段:type=rss(原生 feed)/gnews(Google News 查询);filter=china(欧洲综合流须提中国)/europe(中方宽流须提欧洲)/none(查询已自带限定);gnews 非英语查询必须设 gnews_locale(法语站在英文区返回 0 条,实测)。lane 用于侧边栏分组,可用: de-media/fr-media/eu-brussels/wires/cn-media/industry/gov/thinktank/custom。
- 词表计分:关键词命中 1 分、实体 3 分、标题命中 ×2,总分 ≥2 归入该类;多标签(一篇可进多个 tab)。词表匹配在规范化文本上(小写、标点与连字符转空格、首尾补空格):短词/缩写必须用空格包裹做词边界(如 " ev "、" ai "),德语复合词可用词根(如 "zoll" 匹配 Strafzölle 场景要写 "zölle"/"strafzoll" 两个)。可含中英德法四语词。
- breaking_keywords:标题命中即标 🔴 突发;priority_pairs:实体+语境词同现即突发(contexts 为 null 表示实体单独出现即突发)。
- china_gate_extra/europe_gate_extra:在默认闸门上追加(比如用户条线涉及新地区的公司名)。

## 硬规则:绝不发明 RSS URL
你只能通过两种方式给出信源:
1. 从下方「已验证目录」中原样选用(可改 name/enabled,不可改 value);
2. 组装 Google News 查询:`site:域名 (关键词) when:Nd` 或全网关键词查询,非英语站配 locale。
目录外的原生 RSS 一律不写——猜测的 feed 路径大概率是死链(本平台实测过)。用户想监控目录外的网站时,用 site: 查询替代,并在 name 里注明 (via GNews)。

## 已验证信源目录 A:中欧核心(可直接选用)
{_catalog_json()}

## 已验证信源目录 B:领域扩展库(按领域包分组,均经实测;按记者条线从相关包挑选)
{_library_md()}

## 渠道经验(组装查询时参考)
- Politico.eu/Euractiv/consilium 原生 RSS 被 Cloudflare 挡,必须 gnews;Reuters/AP/Bloomberg/WSJ 无公开 RSS,必须 gnews site: 查询;FT 例外:分版面 `ft.com/<版面>?format=rss` 可用。
- **中文查询必须 gnews_locale=zh**(英文区返回 0 条,实测),法语=fr、德语=de 同理;低频官方源(部委公告)用 when:7d~14d,高频媒体 when:1d~2d。
- 大陆媒体原生 RSS 大多已死或冻结(人民网/中国日报/环球),活的例外:中新社 chinanews.com.cn/rss/<版面>.xml、CGTN cgtn.com/subscribe/rss/section/<版面>.xml、界面快报;其余大陆站一律走 zh 区 gnews site: 查询。南方周末无法监测(无 RSS 且 Google 不收录)。
- 国际主流分版面 RSS 模式(均已验证):BBC feeds.bbci.co.uk/news/<版面>/rss.xml;Guardian 任意版面/标签页 URL 加 /rss;NYT rss.nytimes.com/services/xml/rss/nyt/<版面驼峰>.xml;Economist economist.com/<版面>/rss.xml;NPR feeds.npr.org/<数字ID>/rss.xml;CNBC search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=<版面ID>。
- 全网兜底查询(不限站点)是防漏的安全网,给每个档案配 1-2 条(中英条线各一条,注意 locale)。
- 会议/展会雷达(📅 活动 tab)是全组共享的,不进个人档案,不用生成。
{_profile_context(profile)}"""


# ---------------------------------------------------------------------------
# Claude 调用
# ---------------------------------------------------------------------------

def _resolve_api_key() -> str | None:
    """服务端 Secrets 优先;否则用记者在页面里粘贴的会话级 key(不落盘,刷新即失)。"""
    return _secret("ANTHROPIC_API_KEY") or st.session_state.get("iv_user_key")


def _client():
    import anthropic  # 懒加载:缺包只影响本 tab,不影响主平台/推送
    return anthropic.Anthropic(api_key=_resolve_api_key())


def _chat_turn(system: str, history: list[dict]) -> str:
    resp = _client().messages.create(
        model=MODEL, max_tokens=CHAT_MAX_TOKENS,
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        messages=history,
    )
    if resp.stop_reason == "refusal":
        return "(请求被安全策略拒绝,请换个说法。)"
    return next((b.text for b in resp.content if b.type == "text"), "")


def _generate_bundle(system: str, messages: list[dict]) -> dict:
    resp = _client().messages.create(
        model=MODEL, max_tokens=CONFIG_MAX_TOKENS,
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        messages=messages,
        output_config={"format": {"type": "json_schema", "schema": PROFILE_SCHEMA}},
    )
    if resp.stop_reason == "refusal":
        raise RuntimeError("配置生成被安全策略拒绝")
    text = next(b.text for b in resp.content if b.type == "text")
    return json.loads(text)


# ---------------------------------------------------------------------------
# 实测验证 + 文件物化
# ---------------------------------------------------------------------------

def probe_source(src: dict) -> dict:
    """真抓一个源:HTTP 状态、条目数、带时间戳条目数。"""
    if src["type"] == "gnews":
        url = _GNEWS.get(src.get("gnews_locale", "en"), _GNEWS["en"]).format(q=quote_plus(src["value"]))
    else:
        url = src["value"]
    out = {"name": src["name"], "ok": False, "detail": ""}
    try:
        import feedparser
        r = requests.get(url, headers=_UA, timeout=(6, 12))
        if r.status_code == 403 and src["type"] == "rss":
            r = requests.get(url, headers={"User-Agent": "ChinaEUMonitor/1.0 (RSS reader)"}, timeout=(6, 12))
        p = feedparser.parse(r.content)
        dated = sum(1 for e in p.entries if getattr(e, "published_parsed", None)
                    or getattr(e, "updated_parsed", None))
        out["ok"] = r.status_code == 200 and len(p.entries) > 0 and dated > 0
        out["detail"] = f"http={r.status_code} 条目={len(p.entries)} 带时间戳={dated}"
    except Exception as exc:  # noqa: BLE001
        out["detail"] = f"{type(exc).__name__}: {str(exc)[:90]}"
    return out


def bundle_to_files(bundle: dict, existing_tax: dict | None = None) -> dict[str, str]:
    """档案包 → 两个文件文本。priority_pairs 对象转平台的 [实体, 语境] 对;
    增量模式下,现有 taxonomy 中模型 schema 无法表达/未输出的键原样保留。"""
    tax = dict(bundle["taxonomy"])
    tax["priority_pairs"] = [[p["entity"], p["contexts"]] for p in tax.get("priority_pairs", [])]
    for k, v in (existing_tax or {}).items():
        tax.setdefault(k, v)
    tax["schema_version"] = 1
    name = bundle["profile_name"]
    return {
        f"profiles/{name}/sources.json": json.dumps(bundle["sources"], ensure_ascii=False, indent=2),
        f"profiles/{name}/taxonomy.json": json.dumps(tax, ensure_ascii=False, indent=2),
    }


def _slug_ok(name: str) -> bool:
    return bool(re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,39}", name or ""))


# ---------------------------------------------------------------------------
# GitHub PR(硬性限定:只写 profiles/,只开 PR,不碰 main)
# ---------------------------------------------------------------------------

def create_profile_pr(files: dict[str, str], title: str, body: str) -> tuple[bool, str]:
    # 路径护栏放在一切之前:无论配置如何,本函数物理上只可能写 profiles/
    if any(not p.startswith("profiles/") or ".." in p for p in files):
        return False, "安全限制:只允许写 profiles/ 目录"
    token = _secret("GITHUB_TOKEN")
    repo = _secret("GITHUB_REPO") or DEFAULT_REPO
    if not token:
        return False, "未配置 GITHUB_TOKEN"
    api = f"https://api.github.com/repos/{repo}"
    h = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
         "X-GitHub-Api-Version": "2022-11-28"}
    try:
        r = requests.get(f"{api}/git/ref/heads/main", headers=h, timeout=15)
        r.raise_for_status()
        base_sha = r.json()["object"]["sha"]
        branch = f"profile/{int(time.time())}"
        r = requests.post(f"{api}/git/refs", headers=h, timeout=15,
                          json={"ref": f"refs/heads/{branch}", "sha": base_sha})
        r.raise_for_status()
        for path, content in files.items():
            payload = {"message": f"profile: {path}", "branch": branch,
                       "content": base64.b64encode(content.encode()).decode()}
            r0 = requests.get(f"{api}/contents/{path}?ref={branch}", headers=h, timeout=15)
            if r0.status_code == 200:
                payload["sha"] = r0.json()["sha"]  # 更新已有文件(增量模式)
            r = requests.put(f"{api}/contents/{path}", headers=h, timeout=15, json=payload)
            r.raise_for_status()
        r = requests.post(f"{api}/pulls", headers=h, timeout=15,
                          json={"title": title, "head": branch, "base": "main", "body": body})
        r.raise_for_status()
        return True, r.json()["html_url"]
    except requests.HTTPError as exc:
        return False, f"GitHub API 错误: {exc.response.status_code} {exc.response.text[:200]}"
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

def _gate() -> bool:
    """访问口令闸(公开链接防蹭 API);未配置口令时放行但显示警告。"""
    code = _secret("INTERVIEW_ACCESS_CODE")
    if not code:
        st.caption("⚠️ 未设置 INTERVIEW_ACCESS_CODE,任何拿到链接的人都可使用本功能(消耗 API 额度)")
        return True
    if st.session_state.get("iv_gate_ok"):
        return True
    entered = st.text_input("访问口令", type="password", key="iv_code")
    if entered and entered == code:
        st.session_state["iv_gate_ok"] = True
        st.rerun()
    elif entered:
        st.error("口令不对")
    return False


def render_interview_tab(profile: str | None = None) -> None:
    st.caption("和 Claude 聊清楚你的条线,自动生成专属监测档案(信源+分类词表),"
               "实测验证后一键提交审核。" + (f" 当前为档案 **{profile}** 的增量模式。" if profile else ""))

    if not _resolve_api_key():
        st.info("**开始前需要一把 Anthropic API Key**(console.anthropic.com → API keys)。\n\n"
                "- 方式一(即刻可用):在下方粘贴,仅保存在当前浏览器会话,刷新即失效;\n"
                "- 方式二(团队推荐,一次配置):部署方在 Streamlit Cloud → App settings → "
                "Secrets 填 `ANTHROPIC_API_KEY`,之后记者只需访问口令,无需接触 key。")
        k = st.text_input("Anthropic API Key", type="password", key="iv_key_in",
                          placeholder="sk-ant-…")
        if k:
            st.session_state["iv_user_key"] = k.strip()
            st.rerun()
        return
    try:
        import anthropic  # noqa: F401
    except ImportError:
        st.error("缺少 anthropic 包:pip install anthropic(requirements.txt 已含,云端重新部署即可)")
        return
    if not _gate():
        return

    system = _build_system(profile)
    hist_key = f"iv_hist_{profile or 'new'}"
    if hist_key not in st.session_state:
        st.session_state[hist_key] = []
    history = st.session_state[hist_key]

    if history and st.button("🔄 重新定制(清空本次对话)", key=f"iv_reset_{profile or 'new'}"):
        for sk in (hist_key, f"iv_bundle_{profile or 'new'}", f"iv_probes_{profile or 'new'}"):
            st.session_state.pop(sk, None)
        st.rerun()

    opening = (f"你的档案 **{profile}** 已加载。想新增或调整什么监控范围?直接说,"
               "比如「我最近开始盯匈牙利的电池产业政策」。" if profile else
               "你好!我来帮你搭建专属新闻监测。先聊聊:你主要跑什么条线?"
               "(主题、地域、关注的行业都可以说)")
    with st.chat_message("assistant"):
        st.markdown(opening)
    for m in history:
        with st.chat_message(m["role"]):
            st.markdown(m["content"])

    n_user = sum(1 for m in history if m["role"] == "user")
    if n_user >= MAX_TURNS:
        st.warning(f"本会话已达 {MAX_TURNS} 轮上限。刷新页面可重新开始。")
    elif prompt := st.chat_input("描述你的监控需求 …", key=f"iv_in_{profile or 'new'}"):
        history.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)
        with st.chat_message("assistant"):
            with st.spinner("思考中 …"):
                try:
                    reply = _chat_turn(system, history)
                except Exception as exc:  # noqa: BLE001
                    reply = f"API 调用失败:{exc}"
            st.markdown(reply)
        history.append({"role": "assistant", "content": reply})

    # --- 生成配置 → 实测 → 修复 → 交付 ---
    if n_user >= 1:
        c1, c2 = st.columns([1, 3])
        with c1:
            gen = st.button("⚙️ 生成配置", key=f"iv_gen_{profile or 'new'}",
                            help="基于以上对话生成档案并逐源实测")
        if gen:
            try:
                ask = "请基于以上访谈输出最终档案配置。"
                if profile:
                    ask += f" profile_name 必须为 {profile};输出完整更新后的文件。"
                with st.spinner("生成配置中 …"):
                    bundle = _generate_bundle(system, history + [{"role": "user", "content": ask}])
                if profile:
                    bundle["profile_name"] = profile  # 硬性锁定,不依赖模型遵从
                with st.spinner(f"实测 {len(bundle['sources'])} 个源 …"):
                    probes = [probe_source(s) for s in bundle["sources"]]
                bad = [p for p in probes if not p["ok"]]
                if bad:
                    with st.spinner(f"{len(bad)} 个源失败,请 Claude 修复 …"):
                        # 修复轮必须带上首次产出(结构化输出不在对话历史里),
                        # 否则模型只知道失败源的名字、看不到自己写的查询,等于盲改
                        fb = ("以下源实测失败,请只修复这些源(换已验证目录内的源,或改写 gnews "
                              "查询),其余已通过的内容原样保留,重新输出完整配置:\n"
                              + "\n".join(f"- {p['name']}: {p['detail']}" for p in bad))
                        repair_msgs = history + [
                            {"role": "user", "content": ask},
                            {"role": "assistant", "content": json.dumps(bundle, ensure_ascii=False)},
                            {"role": "user", "content": fb},
                        ]
                        bundle = _generate_bundle(system, repair_msgs)
                        if profile:
                            bundle["profile_name"] = profile
                        probes = [probe_source(s) for s in bundle["sources"]]
                st.session_state[f"iv_bundle_{profile or 'new'}"] = bundle
                st.session_state[f"iv_probes_{profile or 'new'}"] = probes
            except Exception as exc:  # noqa: BLE001
                st.error(f"生成失败:{exc}")

    bundle = st.session_state.get(f"iv_bundle_{profile or 'new'}")
    probes = st.session_state.get(f"iv_probes_{profile or 'new'}", [])
    if not bundle:
        return

    if not _slug_ok(bundle["profile_name"]):
        st.error(f"档案名不合法:{bundle['profile_name']}(需小写字母/数字/连字符)")
        return
    # 新建模式撞名保护:防止静默覆盖别人的档案(增量模式本来就是更新自己的)
    if not profile and (REPO_DIR / "profiles" / bundle["profile_name"]).exists():
        st.error(f"档案名 **{bundle['profile_name']}** 已存在。请回到对话里说明换一个名字,"
                 "然后重新点「⚙️ 生成配置」。")
        return

    st.divider()
    st.subheader(f"📦 档案预览:{bundle['display_name']}({bundle['profile_name']})")
    st.markdown(bundle["summary"])
    n_bad = sum(1 for p in probes if not p["ok"])
    st.markdown(f"**信源实测:{len(probes) - n_bad}/{len(probes)} 通过**"
                + (" ⚠️ 有失败源,可继续对话调整后重新生成" if n_bad else " ✅"))
    with st.expander("信源明细", expanded=bool(n_bad)):
        for s, p in zip(bundle["sources"], probes):
            dot = "🟢" if p["ok"] else "🔴"
            st.caption(f"{dot} {s['name']} [{s['type']}] — {p['detail']}")
    with st.expander("分类词表"):
        for c in bundle["taxonomy"]["categories"]:
            st.caption(f"{c['emoji']} {c['zh']} ({c['id']}) — 关键词 {len(c['kw'])} · 实体 {len(c['entities'])}")

    existing_tax = None
    if profile:
        _tf = REPO_DIR / "profiles" / profile / "taxonomy.json"
        if _tf.exists():
            try:
                existing_tax = json.loads(_tf.read_text(encoding="utf-8"))
            except Exception:
                pass
    files = bundle_to_files(bundle, existing_tax)
    d1, d2, d3 = st.columns(3)
    with d1:
        if st.button("🚀 提交审核(开 PR)", key="iv_pr", type="primary"):
            title = f"新增/更新档案: {bundle['profile_name']}"
            body = (f"{bundle['summary']}\n\n实测:{len(probes) - n_bad}/{len(probes)} 源通过\n\n"
                    "由 💬 定制访谈自动生成,merge 后该档案链接即生效:\n"
                    f"`?profile={bundle['profile_name']}`\n\n"
                    "🤖 Generated with beat interview")
            ok, res = create_profile_pr(files, title, body)
            if ok:
                st.success(f"PR 已创建,等待审核:{res}")
            else:
                st.error(f"PR 创建失败({res}),请用下载按钮兜底。")
    with d2:
        st.download_button("⬇️ sources.json", files[f"profiles/{bundle['profile_name']}/sources.json"],
                           file_name="sources.json")
    with d3:
        st.download_button("⬇️ taxonomy.json", files[f"profiles/{bundle['profile_name']}/taxonomy.json"],
                           file_name="taxonomy.json")
