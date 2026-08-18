"""🌐 UI 双语(中/英)。只翻界面文案,不碰新闻内容。

用法:
    import i18n
    i18n.init()                 # 每次 rerun 早期调用:从 ?lang= 读语言进 session
    i18n.t("refresh")           # 取当前语言文案
    i18n.t("n_groups", n=5)     # 带参数的用 .format
    i18n.lang_suffix()          # 内部链接补 "&lang=en"(中文为空串,URL 保持干净)

默认中文(不带 ?lang= 的一切行为与旧版逐字相同;黄金基线依赖这一点)。
"""

from __future__ import annotations

import streamlit as st

_TR: dict[str, tuple[str, str]] = {
    # --- 侧栏 ---
    "settings": ("设置", "Settings"),
    "profile": ("档案", "Profile"),
    "profile_default": ("默认(中欧监测)", "Default (China–EU)"),
    "profile_help": ("每个档案 = 一套专属信源与分类词表;也可直接用 ?profile=名字 的链接",
                     "Each profile = its own sources & taxonomy; direct link: ?profile=<name>"),
    "customize_entry": ("[🎯 定制我的监控](?setup=1{ls}) — 选条线,60 秒生成专属监控页",
                        "[🎯 Customize my monitor](?setup=1{ls}) — pick your beats, ready in 60s"),
    "adjust_entry": ("[🎯 调整我的监控](?setup=1&profile={code}{ls})",
                     "[🎯 Adjust my monitor](?setup=1&profile={code}{ls})"),
    "hours": ("时间窗(小时)", "Time window (hours)"),
    "lanes": ("来源类型", "Source lanes"),
    "search": ("🔍 搜索(标题/摘要)", "🔍 Search (title/summary)"),
    "refresh": ("🔄 刷新", "🔄 Refresh"),
    "digest_btn": ("📋 早报摘要", "📋 Digest"),
    "customize_hint": ("定制/调整自己的监控 → 上方 🎯 入口",
                       "Customize your monitor → 🎯 entry above"),
    "fetch_status_ok": ("📡 抓取状态 🟢 全部正常", "📡 Fetch status 🟢 all OK"),
    "fetch_status_bad": ("📡 抓取状态 🔴 {n} 个源失败", "📡 Fetch status 🔴 {n} source(s) failing"),
    "decode_stats": ("🔗 链接解码缓存 {cached} 条 · 熔断 {trips} 次{cd}",
                     "🔗 Link-decode cache {cached} · breaker trips {trips}{cd}"),
    "decode_cooldown": (" · 熔断冷却 {m}min", " · cooling down {m}min"),
    "stale_stats": ("🕰 拦截陈年新闻 {n} 条 · 已核验 {v} 链接",
                    "🕰 stale-news blocked {n} · {v} links verified"),

    # --- 主区 ---
    "summary_caption": ("{groups} 组报道({items} 条)· 过去 {hours}h · 更新于 {ts} Berlin · 缓存 10 分钟",
                        "{groups} story groups ({items} items) · last {hours}h · updated {ts} Berlin · 10-min cache"),
    "summary_profile": (" · 档案 {p}", " · profile {p}"),
    "digest_expander": ("📋 早报摘要(Markdown,可直接粘贴)", "📋 Digest (Markdown, copy-ready)"),
    "digest_download": ("⬇️ 下载 .md", "⬇️ Download .md"),
    "digest_collapse": ("收起摘要", "Collapse"),
    "tab_hot": ("⚡ 重点", "⚡ Top"),
    "tab_all": ("📋 全部", "📋 All"),
    "hot_empty": ("过去 {h}h 内没有触发突发信号或多源交叉的报道。",
                  "No breaking-signal or multi-source stories in the last {h}h."),
    "tab_empty": ("该类别暂无报道。可放宽时间窗或检查来源类型筛选。",
                  "No stories in this category. Try a wider time window or check lane filters."),
    "hot_caption": ("突发信号词或 ≥2 家独立媒体同题报道 · 最近 {h}h",
                    "Breaking keywords or ≥2 independent outlets on the same story · last {h}h"),
    "stype_all": ("全部 {n}", "All {n}"),
    "outlets_n": ("🌐 {n} 家独立报道", "🌐 {n} independent outlets"),
    "cross_lang": ("跨语言", "cross-language"),
    "related": ("相关报道 ({n}) — 首发: {outlet} {time}", "Related ({n}) — first: {outlet} {time}"),

    # --- 定制页 ---
    "setup_title": ("🎯 定制我的监控", "🎯 Customize My Monitor"),
    "setup_caption": ("三步:选区域 → 选条线 → 选口径。本页不抓取;你的配置只存在生成的链接里,"
                      "**不写入任何服务器文件** —— 把链接加书签就是保存,发给同事就是分享。",
                      "Three steps: regions → beats → source types. Nothing is fetched here; your "
                      "config lives **only in the generated link** — bookmark it to save, share it to share."),
    "setup_back": ("[✕ 返回监测台](./{ls0})", "[✕ Back to monitor](./{ls0})"),
    "step_region": ("① 区域", "① Regions"),
    "region_multi": ("区域(可多选)", "Regions (multi-select)"),
    "region_sub": ("细分(可选;选大档已自动包含,单选细分 = 只看该方向)",
                   "Sub-regions (optional; parent already includes them — select alone to narrow)"),
    "region_note": ("其余区域档(欧洲内部/中国×全球…)供给矩阵尚未实测,后续版本开放。",
                    "Other region tiers (intra-EU / China×Global…) ship after supply testing."),
    "step_beats": ("② 领域", "② Beats"),
    "fallback_badge": ("0→兜底", "0→fallback"),
    "fallback_note": ("⚠️ 标「0→兜底」的条线在所选区域暂无专线源,内容将来自综合源按词表筛出,"
                      "命中偏少属正常 —— 不是坏了,是诚实。",
                      "⚠️ Beats marked “0→fallback” have no dedicated sources for the selected "
                      "regions; items come from general feeds via keyword matching. Fewer hits is "
                      "expected — honest, not broken."),
    "step_types": ("③ 口径", "③ Source types"),
    "types_label": ("领域专线(可多选)", "Beat-dedicated (multi-select)"),
    "bg_toggle": ("背景快讯(主流媒体 + 通讯社)", "Background wires (mainstream + newswires)"),
    "bg_help": ("背景层**跟随你选的区域**,不跟条线:勾了「中欧」就会包含德法/欧盟媒体的综合报道;"
                "只勾「中国国内」则是财新/财联社/央视等中文快讯。只想看条线专线就关掉本开关。",
                "The background layer **follows your regions**, not beats: with China–EU selected it "
                "includes German/French/EU outlets; China-domestic only gives Caixin/CLS/CCTV wires. "
                "Turn it off to see dedicated beat sources only."),
    "kw_label": ("一定不能漏的关键词(逗号分隔,≤12,选填)",
                 "Must-not-miss keywords (comma-separated, ≤12, optional)"),
    "kw_ph": ("稀土, Nexperia, 中欧班列, 集采",
              "rare earths, Nexperia, China-Europe Express, VBP"),
    "ent_label": ("关注实体:公司/机构/人物(逗号分隔,≤20,选填;命中即进 ⚡重点)",
                  "Watchlist entities: companies/orgs/people (comma-separated, ≤20; hits go to ⚡Top)"),
    "ent_ph": ("宁德时代, 万科, BioNTech, 商务部",
               "CATL, Vanke, BioNTech, MOFCOM"),
    "need_beat": ("至少勾选一个领域。", "Pick at least one beat."),
    "your_config": ("**📋 你的配置:{r} 区域 × {b} 条线 × {t} 类口径 → 专线 {sp} 源 · 背景 {bg} 源**",
                    "**📋 Your config: {r} regions × {b} beats × {t} source types → {sp} dedicated · {bg} background**"),
    "open_monitor": ("### [🚀 打开我的监控页](?profile={code}{ls})",
                     "### [🚀 Open my monitor](?profile={code}{ls})"),
    "bookmark_hint": ("↑ 打开后**把地址栏链接加书签** —— 书签就是你的配置。换电脑用下面的备份恢复。",
                      "↑ After opening, **bookmark the URL** — the bookmark IS your config. "
                      "Restore on a new machine with the backup below."),
    "code_expander": ("🔗 配置码 / 备份", "🔗 Config code / backup"),
    "backup_btn": ("⬇️ 下载配置备份", "⬇️ Download config backup"),
    "modify_hint": ("要修改:回到本页(监控页侧边栏有入口),当前选择会自动带入。",
                    "To modify: return here (entry in the monitor sidebar); current choices prefill."),

    # --- p3 配置码模式(无档案名,用「我的条线」作显示标签) ---
    "my_beats": ("我的条线", "My beats"),
    "p3_msg": ("我的条线:{n} 个板块 · 专线 {sp} 源 · 背景 {bg} 源",
               "My beats: {n} sections · {sp} dedicated · {bg} background"),

    # --- 源管理(侧栏) ---
    "sources_header": ("监控源", "Sources"),
    "add_source": ("➕ 添加监控源", "➕ Add source"),
    "src_name": ("名称", "Name"),
    "src_type": ("类型", "Type"),
    "src_type_help": ("rss = 原生 feed 地址;gnews = Google News 查询语句(适合无 RSS 或被 Cloudflare 挡的网站)",
                      "rss = native feed URL; gnews = Google News query (for sites without RSS or behind Cloudflare)"),
    "src_query": ("查询语句", "Query"),
    "src_lane": ("分组", "Lane"),
    "src_filter": ("相关性过滤", "Relevance filter"),
    "src_filter_china": ("china — 综合流,须提到中国", "china — general feed, must mention China"),
    "src_filter_europe": ("europe — 中方源,须提到欧洲", "europe — Chinese source, must mention Europe"),
    "src_filter_none": ("none — 已自带限定,不过滤", "none — query already scoped, no filter"),
    "src_lang": ("语言", "Language"),
    "src_lang_help": ("gnews 查询会按语言选 Google News 区域(法语查询在英文区搜不到)",
                      "gnews queries use the matching Google News locale (French queries return 0 in the EN locale)"),
    "src_added": ("已添加", "Added"),
    "src_test_add": ("测试并添加", "Test & add"),
    "src_need_both": ("名称和地址/查询语句都要填", "Both name and URL/query are required"),
    "src_dup": ("该地址/查询已在监控列表中", "This URL/query is already being monitored"),
    "src_del_help": ("删除该源", "Remove this source"),

    # --- 反馈 ---
    "fb_expander": ("📮 反馈 / 报告问题 —— 源不对?太吵?想加条线或公众号?",
                    "📮 Feedback — wrong source? too noisy? want a beat or account added?"),
    "fb_who": ("你是谁(选填,方便回复)", "Who are you (optional, for replies)"),
    "fb_who_ph": ("如 Revy / 汽车线", "e.g. Revy / autos beat"),
    "fb_msg": ("反馈内容", "Your feedback"),
    "fb_msg_ph": ("哪个源太吵?想加哪个号/条线?哪个 tag 不准?看到明显跑错的稿子,贴标题最有用。",
                  "Which source is noisy? What beat/account to add? Mis-filed stories — paste the title."),
    "fb_submit": ("提交反馈", "Submit"),
    "fb_empty": ("写点内容再提交～", "Write something first :)"),
    "fb_ok": ("✅ 已提交,谢谢!", "✅ Submitted, thanks!"),
    "fb_fail": ("⚠️ 反馈通道暂时不通,已本地留存。请稍后再试或直接联系维护者。",
                "⚠️ Feedback channel unavailable; saved locally. Try later or contact the maintainer."),
    "fb_unconfigured": ("✅ 已记录(本地)。⚠️ 管理员尚未配置反馈通道,云端不会送达 —— 见 feedback.py 顶部说明。",
                        "✅ Logged locally. ⚠️ No feedback sink configured — it will NOT reach the "
                        "maintainer on cloud. See feedback.py header."),
}

# 来源类型分组(lane)与口径(stype)的英文名
LANE_EN = {
    "de-media": "German media", "fr-media": "French media", "eu-brussels": "EU/Brussels",
    "gov": "Official", "wires": "Wires/Intl", "cn-media": "Chinese media",
    "industry": "Industry", "thinktank": "Think tanks", "custom": "Custom",
}
STYPE_EN = {
    "gov": "🏛️ Official", "mainstream": "📰 Mainstream", "industry": "🏭 Trade media",
    "wechat-mirror": "📱 WeChat mirrors", "wires": "🌐 Newswires", "thinktank": "🎓 Think tanks",
}
REGION_EN = {"cn": "🇨🇳 China domestic", "cn-eu": "🤝 China–EU", "cn-us": "🇺🇸 China–US"}
GROUP_EN = {"宏观与金融": "Macro & Finance", "地缘与安全": "Geopolitics & Security",
            "产业与科技": "Industry & Tech", "民生与社会": "Society & Livelihood",
            "其他": "Other"}
SUB_REGION_EN = {"cn-de": "🇩🇪 China–DE", "cn-fr": "🇫🇷 China–FR", "cn-hk": "🇭🇰 Hong Kong"}


def init() -> None:
    """每次 rerun 早期调用:URL ?lang= 优先,其次沿用本 session 已选。"""
    q = st.query_params.get("lang")
    if q in ("zh", "en"):
        st.session_state["ui_lang"] = q
    st.session_state.setdefault("ui_lang", "zh")


def lang() -> str:
    return st.session_state.get("ui_lang", "zh")


def is_en() -> bool:
    return lang() == "en"


def t(key: str, **kw) -> str:
    pair = _TR[key]
    s = pair[1] if is_en() else pair[0]
    return s.format(**kw) if kw else s


def lang_suffix() -> str:
    """内部链接的语言参数:英文补 &lang=en;中文返回空串(URL 与旧版一致)。"""
    return "&lang=en" if is_en() else ""


def cat_label(cat: dict) -> str:
    """分类/条线的显示名:英文界面用词表包自带的 en 字段,缺则回落 zh。"""
    return (cat.get("en") or cat.get("zh", cat.get("id", ""))) if is_en() else cat.get("zh", cat.get("id", ""))


def render_toggle() -> None:
    """侧栏语言切换。写 ?lang= 进 URL,书签/分享自带语言。"""
    cur = lang()
    pick = st.pills("🌐", options=["zh", "en"], selection_mode="single",
                    default=cur, format_func=lambda v: {"zh": "中文", "en": "EN"}[v],
                    label_visibility="collapsed", key="ui_lang_pick")
    if pick and pick != cur:
        st.session_state["ui_lang"] = pick
        if pick == "en":
            st.query_params["lang"] = "en"
        else:
            st.query_params.pop("lang", None)
        st.rerun()
