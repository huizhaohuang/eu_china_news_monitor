"""
中欧新闻监测 · China-Europe News Monitor (v3)
=============================================
为中欧地缘政治+产经条线记者定制的监测台。

设计要点
--------
- 71 个精选源(德媒/法媒/欧盟/国际媒体/中方/行业垂直/政府/智库),全部经过实测验证;
  sources.json 持久化,侧边栏可增删启停。
- 9 个主题标签页,多标签归类:一篇"欧盟对比亚迪反补贴调查"同时出现在
  「贸易防御」和「汽车」两个 tab——tab 是工作场景,不是档案柜。
- ⚡重点 tab:突发信号词(制裁/关税裁决/搜查/逮捕/国事访问…)+ 多源交叉报道
  (≥2 家独立媒体同题 = 重要性信号)。
- 跨源聚类去重:同题报道合并成一张卡,可展开看各家标题与首发时间。
- 并发抓取 + 每源超时保护;抓取健康状态在侧边栏可见。
- 「生成早报摘要」:一键导出 Markdown,按主题分组,直接粘给编辑。

Run:  streamlit run china_europe_monitor.py
Deps: pip install -r requirements.txt   (streamlit, feedparser, requests)
"""

from __future__ import annotations

import concurrent.futures as cf
import email.utils
import json
import math
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

import feedparser
import requests
import streamlit as st

import events_monitor  # 📅 活动 tab(独立模块,不与新闻逻辑耦合)
import feedback  # 📮 首页反馈渠道(送外部 sink,密钥走 st.secrets)

BERLIN = ZoneInfo("Europe/Berlin")
CONFIG_PATH = Path(__file__).with_name("sources.json")
STATE_PATH = Path(__file__).with_name("monitor_state.json")
GNEWS_BASE = "https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"
# 非英语 site:/关键词查询在英文区返回 0 条(法语、中文均实测),
# gnews 源用 "gnews_locale" 字段选区;zh 区是大陆媒体监测的唯一可靠通道
GNEWS_LOCALES = {
    "en": "https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en",
    "de": "https://news.google.com/rss/search?q={q}&hl=de&gl=DE&ceid=DE:de",
    "fr": "https://news.google.com/rss/search?q={q}&hl=fr&gl=FR&ceid=FR:fr",
    "zh": "https://news.google.com/rss/search?q={q}&hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
}
UA = {"User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                     "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36")}
FETCH_TIMEOUT = (6, 12)          # (connect, read) seconds per feed
MAX_WORKERS = 12
PER_SOURCE_CAP = 80              # max items kept per source per fetch

# ---------------------------------------------------------------------------
# 1. 源配置 —— 首次运行写入 sources.json,此后完全由侧边栏管理
#    lane: de-media | eu-brussels | wires | cn-media | industry | gov | thinktank | custom
#    filter: china(欧洲综合流,须提到中国) | europe(中方宽流,须提到欧洲) | none(已自带限定)
# ---------------------------------------------------------------------------

# 口径标签(p3 模式 tab 内二级筛选;顺序即显示顺序)
STYPE_LABELS = {
    "gov": "🏛️ 官方", "mainstream": "📰 主流媒体", "industry": "🏭 垂类媒体",
    "wechat-mirror": "📱 公众号镜像", "wires": "🌐 通讯社", "thinktank": "🎓 智库",
}

LANE_LABELS = {
    "de-media": "🇩🇪 德国媒体",
    "fr-media": "🇫🇷 法国媒体",
    "eu-brussels": "🇪🇺 欧盟/布鲁塞尔",
    "wires": "🌍 国际媒体/通讯社",
    "cn-media": "🇨🇳 中方媒体",
    "industry": "🏭 行业垂直",
    "gov": "🏛️ 政府/官方",
    "thinktank": "🎓 智库",
    "custom": "⭐ 自定义",
}

DEFAULT_SOURCES = [
    # --- 德国媒体 ---
    {"name": "Handelsblatt · Schlagzeilen", "type": "rss", "value": "https://feeds.cms.handelsblatt.com/schlagzeilen", "lane": "de-media", "lang": "de", "filter": "china", "enabled": True},
    {"name": "Handelsblatt · Politik", "type": "rss", "value": "https://feeds.cms.handelsblatt.com/politik", "lane": "de-media", "lang": "de", "filter": "china", "enabled": True},
    {"name": "Handelsblatt · Unternehmen", "type": "rss", "value": "https://feeds.cms.handelsblatt.com/unternehmen", "lane": "de-media", "lang": "de", "filter": "china", "enabled": True},
    {"name": "FAZ · Wirtschaft", "type": "rss", "value": "https://www.faz.net/rss/aktuell/wirtschaft/", "lane": "de-media", "lang": "de", "filter": "china", "enabled": True},
    {"name": "FAZ · Politik", "type": "rss", "value": "https://www.faz.net/rss/aktuell/politik/", "lane": "de-media", "lang": "de", "filter": "china", "enabled": True},
    {"name": "Spiegel · International (EN)", "type": "rss", "value": "https://www.spiegel.de/international/index.rss", "lane": "de-media", "lang": "en", "filter": "china", "enabled": True},
    {"name": "Spiegel · Wirtschaft", "type": "rss", "value": "https://www.spiegel.de/wirtschaft/index.rss", "lane": "de-media", "lang": "de", "filter": "china", "enabled": False},
    {"name": "SZ · Wirtschaft", "type": "rss", "value": "https://rss.sueddeutsche.de/rss/Wirtschaft", "lane": "de-media", "lang": "de", "filter": "china", "enabled": True},
    {"name": "Die Zeit · Wirtschaft", "type": "rss", "value": "https://newsfeed.zeit.de/wirtschaft/index", "lane": "de-media", "lang": "de", "filter": "china", "enabled": False},
    {"name": "WiWo · Unternehmen", "type": "rss", "value": "https://www.wiwo.de/contentexport/feed/rss/unternehmen", "lane": "de-media", "lang": "de", "filter": "china", "enabled": False},
    {"name": "Tagesschau · Wirtschaft", "type": "rss", "value": "https://www.tagesschau.de/wirtschaft/index~rss2.xml", "lane": "de-media", "lang": "de", "filter": "china", "enabled": True},
    {"name": "n-tv · Wirtschaft", "type": "rss", "value": "https://www.n-tv.de/wirtschaft/rss", "lane": "de-media", "lang": "de", "filter": "china", "enabled": False},
    {"name": "DW · Business (EN)", "type": "rss", "value": "https://rss.dw.com/xml/rss-en-bus", "lane": "de-media", "lang": "en", "filter": "china", "enabled": True},
    {"name": "DW · Asia (EN)", "type": "rss", "value": "https://rss.dw.com/rdf/rss-en-asia", "lane": "de-media", "lang": "en", "filter": "china", "enabled": True},
    {"name": "DW · 中文", "type": "rss", "value": "https://rss.dw.com/xml/rss-chi-all", "lane": "de-media", "lang": "zh", "filter": "china", "enabled": False},
    {"name": "China.Table (Table.Briefings)", "type": "gnews", "value": "site:table.media (China OR Peking) when:2d", "lane": "de-media", "lang": "de", "filter": "none", "enabled": True},
    # --- 法国媒体 (合作者条线;法语 gnews 源需 gnews_locale=fr) ---
    {"name": "Le Monde · International", "type": "rss", "value": "https://www.lemonde.fr/international/rss_full.xml", "lane": "fr-media", "lang": "fr", "filter": "china", "enabled": True},
    {"name": "Le Monde · Économie", "type": "rss", "value": "https://www.lemonde.fr/economie/rss_full.xml", "lane": "fr-media", "lang": "fr", "filter": "china", "enabled": True},
    {"name": "Le Figaro · Économie", "type": "rss", "value": "https://www.lefigaro.fr/rss/figaro_economie.xml", "lane": "fr-media", "lang": "fr", "filter": "china", "enabled": True},
    {"name": "Le Figaro · International", "type": "rss", "value": "https://www.lefigaro.fr/rss/figaro_international.xml", "lane": "fr-media", "lang": "fr", "filter": "china", "enabled": False},
    {"name": "France 24 (EN)", "type": "rss", "value": "https://www.france24.com/en/rss", "lane": "fr-media", "lang": "en", "filter": "china", "enabled": True},
    {"name": "France 24 (FR)", "type": "rss", "value": "https://www.france24.com/fr/rss", "lane": "fr-media", "lang": "fr", "filter": "china", "enabled": False},
    {"name": "RFI (EN)", "type": "rss", "value": "https://www.rfi.fr/en/rss", "lane": "fr-media", "lang": "en", "filter": "china", "enabled": True},
    {"name": "RFI · 中文", "type": "rss", "value": "https://www.rfi.fr/cn/rss", "lane": "fr-media", "lang": "zh", "filter": "china", "enabled": True},
    {"name": "Les Echos · Chine", "type": "gnews", "value": "site:lesechos.fr (Chine OR chinois OR Pékin) when:2d", "lane": "fr-media", "lang": "fr", "filter": "none", "gnews_locale": "fr", "enabled": True},
    {"name": "La Tribune · Chine", "type": "gnews", "value": "site:latribune.fr (Chine OR chinois) when:2d", "lane": "fr-media", "lang": "fr", "filter": "none", "gnews_locale": "fr", "enabled": True},
    {"name": "L'Usine Nouvelle · Chine", "type": "gnews", "value": "site:usinenouvelle.com (Chine OR chinois) when:7d", "lane": "fr-media", "lang": "fr", "filter": "none", "gnews_locale": "fr", "enabled": True},
    {"name": "兜底 · China×France (全网)", "type": "gnews", "value": "(China OR Chinese) (France OR Paris OR Macron) when:1d", "lane": "fr-media", "lang": "en", "filter": "china", "enabled": True},
    # --- 欧盟 / 布鲁塞尔 ---
    {"name": "Politico Europe · China", "type": "gnews", "value": "site:politico.eu (China OR Chinese OR Beijing) when:2d", "lane": "eu-brussels", "lang": "en", "filter": "none", "enabled": True},
    {"name": "Euractiv · China", "type": "gnews", "value": "site:euractiv.com (China OR Chinese OR Beijing) when:2d", "lane": "eu-brussels", "lang": "en", "filter": "none", "enabled": True},
    {"name": "EUobserver", "type": "rss", "value": "https://euobserver.com/rss", "lane": "eu-brussels", "lang": "en", "filter": "china", "enabled": True},
    {"name": "EU Commission · Press corner", "type": "rss", "value": "https://ec.europa.eu/commission/presscorner/api/rss?language=en", "lane": "gov", "lang": "en", "filter": "china", "enabled": True},
    {"name": "DG TRADE · 贸易与经济安全", "type": "rss", "value": "https://policy.trade.ec.europa.eu/node/2/rss_en", "lane": "gov", "lang": "en", "filter": "china", "enabled": True},
    {"name": "European Parliament · Press", "type": "rss", "value": "https://www.europarl.europa.eu/rss/doc/press-releases/en.xml", "lane": "gov", "lang": "en", "filter": "china", "enabled": True},
    {"name": "Council of the EU (via GNews)", "type": "gnews", "value": "site:consilium.europa.eu (China OR Chinese) when:2d", "lane": "gov", "lang": "en", "filter": "none", "enabled": True},
    {"name": "EEAS (via GNews)", "type": "gnews", "value": "site:eeas.europa.eu (China OR Chinese) when:7d", "lane": "gov", "lang": "en", "filter": "none", "enabled": False},
    # --- 国际媒体 / 通讯社 (无公开 RSS → Google News) ---
    # 注:gnews 按正文匹配查询词,标题可能与中国无关 → 加 china 闸门(gnews 无摘要,即标题闸门)
    {"name": "Reuters · China×Europe", "type": "gnews", "value": "site:reuters.com (China OR Chinese) (EU OR Europe OR Germany OR Brussels) when:1d", "lane": "wires", "lang": "en", "filter": "china", "enabled": True},
    {"name": "Bloomberg · China×Europe", "type": "gnews", "value": "site:bloomberg.com (China OR Chinese) (EU OR Europe OR Germany OR Brussels) when:1d", "lane": "wires", "lang": "en", "filter": "china", "enabled": True},
    {"name": "Financial Times · China×Europe", "type": "gnews", "value": "site:ft.com (China OR Chinese) (EU OR Europe OR Germany OR Brussels) when:1d", "lane": "wires", "lang": "en", "filter": "china", "enabled": True},
    {"name": "WSJ · China×Europe", "type": "gnews", "value": "site:wsj.com (China) (EU OR Germany OR Europe) when:1d", "lane": "wires", "lang": "en", "filter": "china", "enabled": True},
    {"name": "Nikkei Asia · China×Europe", "type": "gnews", "value": "site:asia.nikkei.com (China OR Chinese) (Europe OR EU OR Germany OR Netherlands) when:2d", "lane": "wires", "lang": "en", "filter": "china", "enabled": True},
    {"name": "兜底 · China×Germany (全网)", "type": "gnews", "value": "(China OR Chinese) (Germany OR Berlin OR Bundestag OR Merz) when:1d", "lane": "wires", "lang": "en", "filter": "china", "enabled": True},
    {"name": "兜底 · China×EU (全网)", "type": "gnews", "value": '(China OR Chinese) ("European Union" OR Brussels OR "European Commission") when:1d', "lane": "wires", "lang": "en", "filter": "china", "enabled": True},
    # --- 中方媒体 ---
    {"name": "SCMP · China Diplomacy", "type": "rss", "value": "https://www.scmp.com/rss/318199/feed", "lane": "cn-media", "lang": "en", "filter": "europe", "enabled": True},
    {"name": "SCMP · China Economy", "type": "rss", "value": "https://www.scmp.com/rss/318421/feed", "lane": "cn-media", "lang": "en", "filter": "europe", "enabled": True},
    {"name": "SCMP · Tech", "type": "rss", "value": "https://www.scmp.com/rss/36/feed", "lane": "cn-media", "lang": "en", "filter": "europe", "enabled": True},
    {"name": "Caixin Global (via GNews)", "type": "gnews", "value": "site:caixinglobal.com when:2d", "lane": "cn-media", "lang": "en", "filter": "none", "enabled": True},
    {"name": "Yicai Global (via GNews)", "type": "gnews", "value": "site:yicaiglobal.com when:2d", "lane": "cn-media", "lang": "en", "filter": "none", "enabled": True},
    # 官媒查询很宽(体育/文化通稿也会命中欧洲地名)→ 加 europe 闸门收紧到中欧交叉
    {"name": "Global Times · Europe", "type": "gnews", "value": "site:globaltimes.cn (Europe OR EU OR Germany OR Brussels) when:1d", "lane": "cn-media", "lang": "en", "filter": "europe", "enabled": True},
    {"name": "Xinhua EN · Europe", "type": "gnews", "value": "site:english.news.cn (China OR Chinese) (Europe OR EU OR Germany) when:1d", "lane": "cn-media", "lang": "en", "filter": "europe", "enabled": True},
    {"name": "MOFCOM / 外交部 公告", "type": "gnews", "value": "(site:english.mofcom.gov.cn OR site:mfa.gov.cn) when:7d", "lane": "gov", "lang": "en", "filter": "none", "enabled": True},
    {"name": "Sixth Tone", "type": "rss", "value": "https://www.sixthtone.com/rss", "lane": "cn-media", "lang": "en", "filter": "europe", "enabled": False},
    # --- 行业垂直 ---
    {"name": "CnEVPost", "type": "rss", "value": "https://cnevpost.com/feed/", "lane": "industry", "lang": "en", "filter": "none", "enabled": True},
    {"name": "electrive (EN)", "type": "rss", "value": "https://www.electrive.com/feed/", "lane": "industry", "lang": "en", "filter": "china", "enabled": True},
    {"name": "electrive.net (DE)", "type": "rss", "value": "https://www.electrive.net/feed/", "lane": "industry", "lang": "de", "filter": "china", "enabled": False},
    {"name": "Automobilwoche", "type": "rss", "value": "https://www.automobilwoche.de/arc/outboundfeeds/rss/?outputType=xml", "lane": "industry", "lang": "de", "filter": "china", "enabled": True},
    {"name": "pv magazine Global", "type": "rss", "value": "https://www.pv-magazine.com/feed/", "lane": "industry", "lang": "en", "filter": "china", "enabled": True},
    {"name": "CarNewsChina", "type": "rss", "value": "https://carnewschina.com/feed/", "lane": "industry", "lang": "en", "filter": "europe", "enabled": False},
    {"name": "Electrek · China", "type": "rss", "value": "https://electrek.co/guides/china/feed/", "lane": "industry", "lang": "en", "filter": "europe", "enabled": False},
    {"name": "芯片 · 中欧半导体 (全网)", "type": "gnews", "value": '(China) (ASML OR semiconductor OR chips OR "export controls") (Europe OR Netherlands OR EU OR Germany) when:1d', "lane": "industry", "lang": "en", "filter": "none", "enabled": True},
    {"name": "电池 · 中企在欧 (全网)", "type": "gnews", "value": '(CATL OR BYD OR Gotion OR "battery plant" OR gigafactory) (Europe OR Germany OR Hungary OR EU) when:2d', "lane": "industry", "lang": "en", "filter": "none", "enabled": True},
    # --- 德国政府 ---
    {"name": "Bundesregierung · Presse", "type": "rss", "value": "https://www.bundesregierung.de/service/rss/breg-de/1151244/feed.xml", "lane": "gov", "lang": "de", "filter": "china", "enabled": True},
    {"name": "Auswärtiges Amt · Press (EN)", "type": "rss", "value": "https://www.auswaertiges-amt.de/static/includes/rss_en/RSS_Pressemitteilungen_Reden.xml", "lane": "gov", "lang": "en", "filter": "china", "enabled": True},
    {"name": "BMWE · Presse", "type": "rss", "value": "https://www.bundeswirtschaftsministerium.de/SiteGlobals/BMWI/Functions/RSSFeed/DE/RSSFeed-Pressemitteilung.xml", "lane": "gov", "lang": "de", "filter": "china", "enabled": True},
    {"name": "BMWE · Außenwirtschaft", "type": "rss", "value": "https://www.bundeswirtschaftsministerium.de/SiteGlobals/BMWI/Functions/RSSFeed/DE/RSSFeed-Aussenwirtschaft.xml", "lane": "gov", "lang": "de", "filter": "china", "enabled": True},
    # --- 智库 ---
    {"name": "MERICS", "type": "rss", "value": "https://merics.org/en/rss", "lane": "thinktank", "lang": "en", "filter": "none", "enabled": True},
    {"name": "Bruegel", "type": "rss", "value": "https://www.bruegel.org/rss.xml", "lane": "thinktank", "lang": "en", "filter": "china", "enabled": True},
    {"name": "Rhodium Group", "type": "rss", "value": "https://rhg.com/feed/", "lane": "thinktank", "lang": "en", "filter": "china", "enabled": True},
    {"name": "ECFR", "type": "rss", "value": "https://ecfr.eu/feed/", "lane": "thinktank", "lang": "en", "filter": "china", "enabled": False},
    {"name": "SWP Berlin (EN)", "type": "rss", "value": "https://www.swp-berlin.org/en/SWPPublications.xml", "lane": "thinktank", "lang": "en", "filter": "china", "enabled": False},
    {"name": "CER", "type": "rss", "value": "https://www.cer.eu/rss.xml", "lane": "thinktank", "lang": "en", "filter": "china", "enabled": False},
]

# v2 版 sources.json 里的自动种子条目(升级时静默丢弃,新版默认源已覆盖同等功能)
_V2_SEED_VALUES = {
    "https://feeds.cms.handelsblatt.com/schlagzeilen",
    "https://feeds.cms.handelsblatt.com/unternehmen",
    "https://feeds.cms.handelsblatt.com/politik",
    "https://rss.dw.com/rdf/rss-en-bus",
    "https://rss.dw.com/rdf/rss-en-asia",
    "site:reuters.com (China OR Chinese) (Europe OR EU OR Germany OR German) when:1d",
    "site:bloomberg.com (China OR Chinese) (Europe OR EU OR Germany OR German) when:1d",
}


def load_sources() -> list[dict]:
    sources = None
    if CONFIG_PATH.exists():
        try:
            sources = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            # 解析失败不静默覆盖用户配置:先备份损坏文件再重建默认
            try:
                CONFIG_PATH.rename(CONFIG_PATH.with_suffix(".json.bak"))
            except OSError:
                pass
            sources = None
    if PROFILE is not None:
        # 档案配置绝不走 v2 迁移/默认种子:手写档案缺 lane 是常态,
        # 迁移逻辑会把整份精选信源换成 71 个默认源(评审确认的 high bug)。
        # 缺字段仅在内存补默认值,不回写文件。
        sources = sources or []
        for s in sources:
            s.setdefault("lane", "custom")
            s.setdefault("enabled", True)
            s.setdefault("filter", "none")
            s.setdefault("lang", "en")
        return sources
    if sources is None:
        save_sources(DEFAULT_SOURCES)
        return json.loads(json.dumps(DEFAULT_SOURCES))
    # v2 -> v3 迁移:老格式条目没有 lane 字段(仅默认档案)
    if any("lane" not in s for s in sources):
        default_values = {s["value"] for s in DEFAULT_SOURCES}
        migrated = json.loads(json.dumps(DEFAULT_SOURCES))
        for s in sources:
            if "lane" in s or s.get("value") in _V2_SEED_VALUES or s.get("value") in default_values:
                continue  # v2 种子条目或与新默认重复 → 由新默认源接管
            migrated.append({
                "name": s.get("name", "未命名"), "type": s.get("type", "rss"),
                "value": s.get("value", ""), "lane": "custom", "lang": "en",
                "filter": "china" if s.get("keyword_filter", True) else "none",
                "enabled": s.get("enabled", True),
            })
        save_sources(migrated)
        return migrated
    return sources


def save_sources(sources: list[dict]) -> None:
    if EPHEMERAL_CONFIG:
        return  # p3 配置码模式:个人的源增删只活在会话里,绝不写共享文件
    CONFIG_PATH.write_text(
        json.dumps(sources, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# 2. 归类体系(8 类,双语关键词+实体表)与优先级规则
#    匹配在规范化文本上进行:小写、标点转空格、首尾补空格,
#    因此短词可用 " ev " 形式做词边界保护。
#    计分:关键词命中=1、实体命中=3,标题命中权重×2;总分≥2 归入该类。
# ---------------------------------------------------------------------------

CATEGORIES = [
    dict(id="china-eu-politics", emoji="🏛️", zh="中欧政治外交", en="EU-China Politics",
         kw=["eu-china", "china-eu", "sino-european", "summit", "state visit", "diplomat", "ambassador",
             "foreign minister", "foreign policy", "embassy", "taiwan", "one china", "tibet", "dalai lama",
             "uyghur", "xinjiang", "hong kong", "human rights", "forced labour", "forced labor", "ukraine",
             "russia", "peace plan", "no-limits", "dual-use", "sanction", "de-risking", "derisking",
             "decoupling", "systemic rival", "economic coercion", "lithuania", "european parliament",
             "european commission", "european council", "eeas", "nato", " g7 ", " g20 ", "belt and road",
             "global gateway", "strategic autonomy", "united front", "confucius institute", "interference",
             "disinformation", "reciprocity", " 16+1 ", " 17+1 ", "investment agreement", "wolf warrior",
             "visa-free", "munich security conference",
             "gipfel", "staatsbesuch", "botschafter", "außenminister", "außenpolitik", "botschaft",
             "uigur", "hongkong", "menschenrechte", "zwangsarbeit", "russland", "friedensplan", "sanktion",
             "entkopplung", "risikominderung", "systemrival", "seidenstraße", "einflussnahme",
             "desinformation", "europaparlament", "europäisches parlament", "eu-kommission",
             "europäischer rat", "chinapolitik", "china-politik", "konfuzius-institut", "gegenseitigkeit",
             "investitionsabkommen", "münchner sicherheitskonferenz", "visafrei"],
         entities=["xi jinping", " xi ", "li qiang", "wang yi", "fu cong", "lin jian", "mao ning",
                   "von der leyen", "kallas", "antonio costa", "antónio costa", "rutte", "orban", "orbán",
                   "macron", " ccp ", " cpc ", "politburo", "merics", " ecfr ", "bruegel", "rhodium group"]),
    dict(id="germany-china", emoji="🇩🇪", zh="德中关系", en="Germany-China",
         kw=["germany", "german", "berlin", "bundestag", "chancellor", "chancellery", "sino-german",
             "german-chinese", "mittelstand", "china strategy", "government consultations",
             "port of hamburg", "hamburg", "duisburg", "german intelligence",
             "bundesregierung", "bundeskanzler", "kanzler", "kanzleramt", "bundesrat", "auswärtiges amt",
             "außenministerium", "wirtschaftsministerium", "verteidigungsministerium", "china-strategie",
             "chinastrategie", "chinareise", "china-reise", "regierungskonsultationen",
             "deutsche wirtschaft", "deutsche unternehmen", "deutsche industrie", "deutsche autobauer",
             "hamburger hafen", "koalition", " cdu ", " csu ", " spd ", " afd ", "grüne", " linke ",
             "verfassungsschutz", "umfrage", "asien-pazifik-ausschuss", "deutsch-chinesisch"],
         entities=[" merz ", "friedrich merz", "wadephul", "klingbeil", "katherina reiche", "steinmeier",
                   "pistorius", "scholz", " vw ", "volkswagen", " audi ", "porsche", " bmw ", "mercedes",
                   "basf", "siemens", "bosch", " zf ", "continental", "schaeffler", "thyssenkrupp",
                   "infineon", "kuka", "cosco", "hhla", " bdi ", " dihk ", " vda ", "bundeswehr"]),
    dict(id="france-china", emoji="🇫🇷", zh="法中关系", en="France-China",
         kw=[" france", " french", " français", " francais", " franco ", "franco-chinese",
             "franco-chinois", "sino-french", "sino-français", " paris", " élysée", " elysee",
             "quai d'orsay", "frankreich", "französisch", "对法", "法中", "中法", "法国", "巴黎",
             "马克龙", "爱丽舍"],
         entities=["macron", "barrot", "lecornu", "airbus", " lvmh ", "kering", "hermès", " hermes",
                   "l'oréal", "l'oreal", "renault", "michelin", "danone", "sanofi", " edf ",
                   "framatome", "orano", "alstom", "veolia", "carrefour", " accor", "thales",
                   "safran", "dassault", " ariane", "cognac", "bordeaux", "armagnac", " cma cgm",
                   "totalenergies", "schneider electric", "stmicro", "soitec", "空客", "达索",
                   "米其林", "赛诺菲", "家乐福"]),
    dict(id="trade-defence", emoji="⚖️", zh="贸易防御与经济安全", en="Trade Defence",
         kw=["tariff", "anti-subsidy", "antisubsidy", "anti-dumping", "antidumping", "countervailing",
             "trade defence", "trade defense", "trade war", "trade dispute", "trade talks", "trade barrier",
             "dumping", "overcapacity", "subsidy", "subsidies", "state aid", "export control",
             "export restriction", "export ban", "export licence", "export license", "import ban",
             "rare earth", "gallium", "germanium", "graphite", "antimony", "critical raw material",
             "critical minerals", "foreign subsidies regulation", "investment screening", "fdi screening",
             "golden power", " wto ", "market access", "anti-coercion", "de minimis", "customs",
             "minimum price", "price undertaking", "price commitment", "provisional duties",
             "definitive duties", "quota", "retaliat", "economic security", "supply chain",
             "brandy", "cognac", "market economy status", "safeguard",
             "zoll", "zölle", "strafzoll", "strafzölle", "ausgleichszoll", "antisubvention",
             "anti-subvention", "subvention", "handelskrieg", "handelsstreit", "handelskonflikt",
             "handelsgespräche", "handelsbarriere", "überkapazität", "exportkontroll", "ausfuhrkontroll",
             "exportbeschränkung", "ausfuhrverbot", "exportverbot", "exportstopp", "einfuhrverbot",
             "seltene erden", "seltenerd", "graphit", "antimon", "kritische rohstoffe",
             "drittstaatliche subventionen", "investitionsprüfung", "investitionskontrolle",
             "außenwirtschaftsgesetz", "welthandelsorganisation", "marktzugang", "vergeltung",
             "mindestpreis", "preisverpflichtung", "kontingent", "wirtschaftssicherheit", "lieferkette",
             "beihilfe", "schweinefleisch", "milchprodukte", "weinbrand"],
         entities=["sefcovic", "šefčovič", "weyand", "dg trade", "mofcom", "wang wentao", "he lifeng",
                   "cccme", "eurofer", " crrc ", " temu ", " shein", "eu chamber", "eskelund"]),
    dict(id="autos-ev", emoji="🚗", zh="汽车与电池", en="Autos, EVs & Batteries",
         kw=["electric vehicle", "electric car", " ev ", " evs ", "ev maker", "ev tariff", "ev plant",
             "battery", "batteries", "battery cell", "gigafactory", "cathode", "anode", " lfp ",
             "solid-state", "plug-in", "phev", "hybrid", "automaker", "carmaker", "car maker",
             "automotive", "auto industry", "car industry", "price war", "charging", "car plant",
             "autonomous driving", "self-driving", "robotaxi", "new energy vehicle", "car sales",
             "ev sales", "registrations", "szeged", "debrecen",
             "elektroauto", " e-auto", "elektrofahrzeug", "elektromobilität", " e-mobilität", "autobauer",
             "autohersteller", "automobil", "autoindustrie", "autozulieferer", "zulieferer", "batterie",
             "batteriezelle", "batteriefabrik", "gigafabrik", "feststoffbatterie", "plug-in-hybrid",
             "preiskrieg", "ladeinfrastruktur", "ladesäule", "verbrenner", "neuzulassungen",
             "autonomes fahren", "autogipfel", " pkw "],
         entities=[" byd ", " catl ", " nio ", "xpeng", "geely", " chery", " saic ", "mg motor", "leapmotor",
                   "great wall", " gwm ", "zeekr", "lynk", "polestar", "volvo", "denza", "dongfeng",
                   "changan", "hongqi", "gotion", "svolt", "eve energy", " calb ", "farasis", "northvolt",
                   "tesla", "stellantis", "renault", " vw ", "volkswagen", " audi ", "porsche", " bmw ",
                   "mercedes", "opel", " vda ", " acea ", " kba "]),
    dict(id="energy-solar", emoji="☀️", zh="能源与光伏", en="Energy & Solar",
         kw=["solar", "photovoltaic", "polysilicon", "wafer", "inverter", "wind turbine", "wind farm",
             "wind power", "offshore wind", "onshore wind", "hydrogen", "electrolyser", "electrolyzer",
             "heat pump", "power grid", "electricity grid", "grid operator", "energy storage",
             "battery storage", "renewable", "clean tech", "cleantech", "green tech", "energy transition",
             "nuclear", "cbam", "carbon border",
             "photovoltaik", "solarmodul", "solarzelle", "solarindustrie", "solarhersteller",
             "polysilizium", "wechselrichter", "windkraft", "windrad", "windturbine", "windpark",
             "offshore-wind", "wasserstoff", "elektrolyseur", "wärmepumpe", "stromnetz", "netzbetreiber",
             "energiespeicher", "erneuerbare", "energiewende", "atomkraft", "co2-grenzausgleich",
             "klimatechnologie"],
         entities=["longi", "jinko", " trina", "ja solar", "tongwei", "sungrow", " deye ", "goldwind",
                   "mingyang", "ming yang", "envision", "meyer burger", "solarwatt", "sma solar",
                   "siemens energy", "siemens gamesa", "vestas", "orsted", "ørsted", "nordex",
                   "state grid", " cgn ", "solarpower europe", "windeurope", "bundesnetzagentur"]),
    dict(id="tech-ai-chips", emoji="🤖", zh="科技·AI·芯片", en="Tech, AI & Chips",
         kw=["semiconductor", "chip", "chips act", "foundry", "lithography", " euv", "artificial intelligence",
             " ai ", "ai act", "ai model", "large language model", " llm ", " gpu ", "computing power",
             "data center", "data centre", " 5g ", " 6g ", "telecom", "network equipment", "cybersecurity",
             "data protection", "data transfer", "cloud", "quantum", "surveillance", "facial recognition",
             "spyware", "social media", "robotics", "humanoid", "drone",
             "halbleiter", "chiphersteller", "chipfabrik", "chipindustrie", "lithografie", "lithographie",
             "künstliche intelligenz", " ki ", "cybersicherheit", "datenschutz", "datentransfer",
             "überwachung", "gesichtserkennung", "mobilfunk", "netzausrüster", "rechenzentrum", "quanten",
             "robotik", "humanoide", "drohne", "tiktok-verbot", "spionagesoftware", "digitalpolitik"],
         entities=["huawei", " zte ", "asml", "zeiss", "trumpf", "tsmc", " smic ", "nvidia", "infineon",
                   "aixtron", "nexperia", "wingtech", "tiktok", "bytedance", "deepseek", "alibaba",
                   "tencent", "baidu", "xiaomi", " dji ", "hikvision", "dahua", "unitree",
                   "deutsche telekom", "vodafone", "ericsson", "nokia", " bsi ", " sap "]),
    # 国防与战略原材料:军工/国防/航空航天 + 稀土等战略物资(SCMP 核心领域)。
    # 原「安全与间谍」并入取消:间谍案仍由 germany-china 关键词(verfassungsschutz 等)
    # 和 BREAKING_KEYWORDS(spionage/razzia/festnahme…)覆盖,不会漏报,只是不再单列 tab。
    dict(id="defence-materials", emoji="🛡️", zh="国防与战略原材料", en="Defence & Strategic Materials",
         kw=["defence", "defense", "military", "armed forces", "arms export", "arms industry",
             "armament", "weapons", "ammunition", "artillery", "missile", "air defence",
             "air defense", "fighter jet", "warship", "frigate", "submarine", "dual-use",
             "aerospace", "space industry", "space station", "satellite", " rocket", "launcher",
             "hypersonic", "military drone", "war game", "military exercise", "naval",
             "rüstung", "verteidigung", "bundeswehr", "militär", "waffen", "munition", " panzer",
             "kampfjet", "fregatte", "u-boot", "raumfahrt", "luftfahrt", "satellit", "rakete",
             "wehrtechnik", "streitkräfte", "militärübung",
             "défense", "militaire", "armement", "spatial", "porte-avions",
             "军工", "国防", "军演", "航母", "航天", "卫星", "导弹",
             "rare earth", "seltene erden", "seltenerd", "terres rares", "稀土",
             "critical raw material", "critical minerals", "kritische rohstoffe",
             "strategic materials", "matériaux critiques", "gallium", "germanium", "graphite",
             "graphit", "antimony", "antimon", "tungsten", "wolfram", "lithium", "cobalt",
             "kobalt", "nickel", "magnesium", " titan ", "titanium", "neodym", "permanent magnet",
             "dauermagnet", "magnet export", "magnet supply", "stockpile", "bevorratung",
             "versorgungssicherheit", "supply security", " mining", " mine ", "bergbau",
             "refining", "raffinerie", "smelter", "关键矿产", "战略物资"],
         entities=["rheinmetall", "hensoldt", " knds ", "krauss-maffei", "diehl", " mbda ",
                   " tkms ", "thyssenkrupp marine", "naval group", "thales", "dassault", "safran",
                   "leonardo", " saab ", "arianegroup", " ariane", " esa ", " dlr ",
                   "isar aerospace", " ohb ", "lynas", "mp materials", "vacuumschmelze",
                   "neo performance", "umicore", " casc ", " casic ", " avic ", "norinco",
                   "comac", " pla ", "people's liberation army", "中国商飞"]),
    # china-macro 是兜底类:仅当条目未命中任何其他类时才归入(避免宏观词污染主题 tab)
    dict(id="china-macro", emoji="📊", zh="中国宏观(参考)", en="China Macro",
         kw=[" gdp ", "growth target", "economic growth", "stimulus", "deflation", "consumer prices",
             " cpi ", "producer prices", "property market", "property crisis", "real estate",
             "local government debt", "trade surplus", "export growth", "exports rose", "exports fell",
             "yuan", "renminbi", " rmb ", "five-year plan", "third plenum", "national people's congress",
             "two sessions", "youth unemployment", "retail sales", " pmi ", "involution",
             "common prosperity", "capital flight", " fdi ",
             "wachstum", "wirtschaftswachstum", "konjunktur", "konjunkturpaket", "verbraucherpreise",
             "immobilienkrise", "immobilienmarkt", "handelsüberschuss", "handelsbilanz",
             "exportüberschuss", "fünfjahresplan", "volkskongress", "jugendarbeitslosigkeit",
             "binnennachfrage", "chinas wirtschaft", "chinesische wirtschaft", "kapitalflucht"],
         entities=[" pboc ", "pan gongsheng", " ndrc ", "evergrande", "country garden", "vanke",
                   " imf ", " iwf "]),
]

# 摘要导出时的主分类顺序:具体压倒宽泛
SPECIFICITY = ["defence-materials", "trade-defence", "autos-ev", "tech-ai-chips",
               "energy-solar", "germany-china", "france-china", "china-eu-politics",
               "china-macro"]
CAT_BY_ID = {c["id"]: c for c in CATEGORIES}

# --- 突发/优先信号 ---
BREAKING_KEYWORDS = [
    "sanction", "tariff", " raid", "arrest", "detained", "spy", "espionage", "export ban",
    "export control", "export restriction", "import ban", "entity list", "blacklist",
    "unreliable entity", "state visit", "summit", "anti-subsidy", "anti-dumping", "countervailing",
    "investigation launched", "launches investigation", "opens investigation", "launches probe",
    "opens probe", "probe into", "blocked", "blocks", "vetoed", "veto", "banned", " ban ", " bans ",
    "expel", "summon", "retaliat", "seiz", "suspend", "halt", "cyberattack", "hacked",
    "indict", "charged with", "convict", "provisional duties", "definitive duties",
    "minimum import price", "wto complaint", "files complaint",
    "razzia", "festnahme", "festgenommen", "verhaftet", "spionage", "spion", "staatsbesuch",
    "sanktion", "strafzoll", "strafzölle", "ausfuhrverbot", "exportverbot", "exportstopp",
    "einfuhrverbot", "exportkontroll", "gipfel", "untersagt", "blockiert", "verboten",
    "einbestellt", "ausgewiesen", "vergeltung", "beschlagnahmt", "gestoppt", "ermittlungen",
    "anklage", "angeklagt", "verurteilt", "durchsuchung", "cyberangriff", "hackerangriff",
    "eilmeldung", "spionageverdacht", "übernahme untersagt",
]

_EU_CTX = [" europ", " eu ", " germany", " german ", " germans ", " deutschland", " berlin",
           " brussels", " brüssel", " france", " french", " paris", " frankreich"]
_CN_CTX = [" china", " chines", " peking", " beijing", " sino", " chine", " chinois", " pékin",
           "中国", "北京", "中方"]
# (实体, 语境词表) —— 实体+语境同现即标记优先;语境为 None 表示实体单独出现即优先
PRIORITY_PAIRS = [
    ("xi jinping", _EU_CTX), ("li qiang", _EU_CTX), ("wang yi", _EU_CTX),
    ("he lifeng", _EU_CTX), ("wang wentao", _EU_CTX),
    ("fu cong", None), ("nexperia", None),
    (" merz ", _CN_CTX), ("wadephul", _CN_CTX), ("klingbeil", _CN_CTX), ("steinmeier", _CN_CTX),
    ("von der leyen", _CN_CTX), ("sefcovic", _CN_CTX), ("šefčovič", _CN_CTX), ("kallas", _CN_CTX),
    ("antonio costa", _CN_CTX), ("antónio costa", _CN_CTX), ("orban", _CN_CTX), ("orbán", _CN_CTX),
    ("generalbundesanwalt", _CN_CTX), ("bundesanwaltschaft", _CN_CTX),
    ("macron", _CN_CTX), ("barrot", _CN_CTX), ("airbus", _CN_CTX), ("cognac", _CN_CTX),
    (" cosco", [" hamburg", " germany", " deutschland", " europ", " hafen"]),
    (" catl ", [" hungary", " ungarn", " debrecen", " erfurt", " thuringia", " thüringen",
                " germany", " europ", " eu "]),
    (" byd ", [" hungary", " ungarn", " szeged", " germany", " deutschland", " europ", " eu ",
               " tariff", " zoll", " plant", " factory", " werk"]),
]

# --- 相关性闸门 ---
# 词首空格 = 词首边界保护(否则 "chines"⊂machines、"sino"⊂casino、"chery"⊂archery)
# china: 欧洲综合流必须提到中国相关词才保留
CHINA_GATE = [
    " china", " chines", " sino", " peking", " beijing", " hongkong", " hong kong", " taiwan",
    " taipei", " tibet", " xinjiang", " uigur", " uyghur", " macau", " macao", "volksrepublik",
    " südchines", " ostchines",  # südchinesisches Meer 等德语复合词
    " xi jinping", " li qiang", " wang yi", " mofcom",
    " byd ", " catl ", " nio ", " xpeng", " geely", " chery", " saic ", " mg motor", " leapmotor",
    " great wall motor", " gwm ", " zeekr", " denza", " dongfeng", " changan", " hongqi",
    " gotion", " svolt", " eve energy", " calb ", " farasis",
    " huawei", " zte ", " xiaomi", " alibaba", " tencent", " baidu", " bytedance", " tiktok",
    " deepseek", " temu ", " shein", " dji ", " hikvision", " dahua", " unitree", " smic ",
    " wingtech", " nexperia", " lenovo", " cosco", " longi", " jinko", " trina", " ja solar",
    " tongwei", " sungrow", " deye ", " goldwind", " mingyang", " zpmc", " crrc ", " cccme",
    " seltene erden", " rare earth", " konfuzius", " confucius",
    # 法语(Le Monde/Figaro/France24-FR 等法语综合流的中国闸门)
    " chine", " chinois", " pékin", " pekin", " taïwan", " ouïghour", " hongkongais",
    # 中文(RFI 中文、DW 中文等中文综合流)
    "中国", "中方", "中共", "北京", "中欧", "中德", "中法", "台湾", "台海", "香港", "新疆",
    "华为", "比亚迪", "宁德时代", "稀土", "习近平",
]
# europe: 中方宽流必须提到欧洲相关词才保留
EUROPE_GATE = [
    " europ", " eu ", " germany", " german ", " germans ", " deutschland", " berlin", " brussels",
    " brüssel", " france", " french", " paris", " netherlands", " dutch", " hague", " italy",
    " italian", " spain", " spanish", " madrid", " poland", " polish ", " warsaw", " hungary",
    " hungarian", " budapest", " szeged", " debrecen", " britain", " british", " uk ", " london",
    " lithuania", " latvia", " estonia", " czech", " slovak", " belgium", " austria", " swiss",
    " switzerland", " sweden", " swedish", " denmark", " danish", " norway", " finland", " finnish",
    " greece", " greek", " portugal", " ireland", " romania", " bulgaria", " serbia", " nato ",
    " ecb ",
    " asml", " airbus", " volkswagen", " vw ", " bmw ", " mercedes", " porsche", " audi ", " basf",
    " siemens", " bosch", " stellantis", " renault", " volvo", " polestar", " northvolt",
    " meyer burger", " vestas", " nordex", " ericsson", " nokia", " maersk",
    " von der leyen", " macron", " merz ", " orban", " orbán", " kallas", " sefcovic", " šefčovič",
    " starmer", " tusk ",
    # 中文(中方中文源的欧洲闸门)
    "欧盟", "欧洲", "德国", "法国", "英国", "意大利", "西班牙", "波兰", "匈牙利", "荷兰",
    "布鲁塞尔", "柏林", "巴黎", "冯德莱恩", "马克龙", "默茨", "北约", "空客", "大众", "宝马",
    "奔驰", "阿斯麦",
]

def _nt(term: str) -> str:
    """词表项经过与 _norm 相同的清洗(小写、标点/连字符转空格),
    并保留显式写出的首尾空格(词边界保护,如 " ev "、" 16+1 ")。
    否则 "16+1"、"national people's congress" 这类含符号的词永远匹配不上正文。"""
    lead = " " if term.startswith(" ") else ""
    trail = " " if term.endswith(" ") else ""
    core = " ".join(re.sub(r"[^\w\s]", " ", term.lower()).split())
    return lead + core + trail


def _ntl(terms: list[str]) -> list[str]:
    return [_nt(t) for t in terms]


for _c in CATEGORIES:
    _c["kw"] = _ntl(_c["kw"])
    _c["entities"] = _ntl(_c["entities"])
BREAKING_KEYWORDS = _ntl(BREAKING_KEYWORDS)
CHINA_GATE = _ntl(CHINA_GATE)
EUROPE_GATE = _ntl(EUROPE_GATE)
PRIORITY_PAIRS = [(_nt(e), _ntl(c) if c else None) for e, c in PRIORITY_PAIRS]

# 垃圾稿黑名单:市场研究 PR 通稿等(命中即丢,保守收录以免误伤真新闻)
TITLE_BLOCKLIST = ["market analysis", "market report", "market size", "market forecast",
                   "market research", "market outlook", "fifa world cup"]

# ---------------------------------------------------------------------------
# 档案(profile)机制:URL 带 ?profile=名字 时加载 profiles/<名字>/ 下的
# 专属信源与词表——同一个部署,每人一套监控议程;不带参数 = 默认档案(现状,零变化)。
# taxonomy.json 全部字段可选:categories/specificity/breaking_keywords/priority_pairs
# 为整体替换,*_extra 为追加(闸门与黑名单只增不减,保守防漏)。
# ---------------------------------------------------------------------------

PROFILES_DIR = Path(__file__).with_name("profiles")
PROFILE: str | None = None          # 当前档案名;None = 默认
TAXONOMY_FP = "default"             # 进 fetch_all 缓存键,防跨档案词表串味
EPHEMERAL_CONFIG = False            # p3 配置码模式:个人配置只活在 URL,所有写盘旁路


def list_profiles() -> list[str]:
    if not PROFILES_DIR.is_dir():
        return []
    return sorted(p.name for p in PROFILES_DIR.iterdir()
                  if p.is_dir() and (p / "sources.json").exists())


def apply_profile(name: str) -> tuple[bool, str]:
    """切换到指定档案:重定向配置/状态路径,加载并规范化自定义词表。

    必须在任何抓取/归类调用之前执行。返回 (ok, 消息)。
    """
    global PROFILE, CONFIG_PATH, STATE_PATH, TAXONOMY_FP
    global CATEGORIES, SPECIFICITY, CAT_BY_ID, BREAKING_KEYWORDS, PRIORITY_PAIRS
    global CHINA_GATE, EUROPE_GATE, TITLE_BLOCKLIST

    # 档案名即路径片段,必须严格白名单(防 ?profile=../../ 路径穿越)
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,39}", name or ""):
        return False, f"档案名不合法: {name!r}(只允许小写字母/数字/连字符)"
    pdir = PROFILES_DIR / name
    if not (pdir / "sources.json").exists():
        return False, f"档案不存在: {name}(可用: {', '.join(list_profiles()) or '无'})"

    PROFILE = name
    CONFIG_PATH = pdir / "sources.json"
    STATE_PATH = pdir / "monitor_state.json"
    TAXONOMY_FP = f"{name}:none"

    tax_path = pdir / "taxonomy.json"
    if not tax_path.exists():
        return True, f"档案 {name}: 专属信源 + 默认词表"
    import hashlib as _hl
    raw = ""
    try:
        raw = tax_path.read_text(encoding="utf-8")
        tax = json.loads(raw)
        # 先全部解析进局部变量,完整成功后才提交到全局——
        # 否则坏词表会"半套生效"却报告已回退(评审确认)
        n_cats, n_cbi, n_spec = CATEGORIES, CAT_BY_ID, SPECIFICITY
        if cats := tax.get("categories"):
            n_cats = [dict(id=c["id"], emoji=c.get("emoji", "📌"),
                           zh=c.get("zh", c["id"]), en=c.get("en", c["id"]),
                           kw=_ntl(c.get("kw", [])), entities=_ntl(c.get("entities", [])))
                      for c in cats]
            n_cbi = {c["id"]: c for c in n_cats}
            n_spec = [s for s in tax.get("specificity", []) if s in n_cbi]
            n_spec += [c["id"] for c in n_cats if c["id"] not in n_spec]  # 兜底补全
        n_break = _ntl(tax["breaking_keywords"]) if tax.get("breaking_keywords") else BREAKING_KEYWORDS
        if pp := tax.get("priority_pairs"):
            n_pairs = [(_nt(e), _ntl(c) if c else None) for e, c in pp]
        else:
            n_pairs = PRIORITY_PAIRS
        n_cg = CHINA_GATE + _ntl(tax.get("china_gate_extra", []))
        n_eg = EUROPE_GATE + _ntl(tax.get("europe_gate_extra", []))
        n_bl = TITLE_BLOCKLIST + _ntl(tax.get("title_blocklist_extra", []))
    except Exception as exc:  # noqa: BLE001 — 坏词表不炸应用,回退默认词表(全局未动)
        TAXONOMY_FP = f"{name}:taxerr-{_hl.sha1(raw.encode()).hexdigest()[:10]}"
        return True, f"档案 {name}: taxonomy.json 解析失败({exc}),已回退默认词表"
    CATEGORIES, CAT_BY_ID, SPECIFICITY = n_cats, n_cbi, n_spec
    BREAKING_KEYWORDS, PRIORITY_PAIRS = n_break, n_pairs
    CHINA_GATE, EUROPE_GATE, TITLE_BLOCKLIST = n_cg, n_eg, n_bl
    TAXONOMY_FP = f"{name}:{_hl.sha1(raw.encode()).hexdigest()[:10]}"
    return True, f"档案 {name}: 专属信源 + 专属词表({len(CATEGORIES)} 类)"


def apply_packs_config(cfg: dict) -> tuple[list[dict], str]:
    """p3 配置码 → (会话源列表, 状态消息)。词表换成所选条线模块。

    个人配置零落盘:源列表只进 st.session_state,词表只换内存全局;
    EPHEMERAL_CONFIG 置位后 save_sources/基线写盘全部旁路。
    已知限制(与 apply_profile 相同,S2' 统一修):改的是进程级全局,
    多用户同进程会串词表 —— 本机单人预览期可接受,上云前必须去全局化。
    """
    global PROFILE, TAXONOMY_FP, EPHEMERAL_CONFIG
    global CATEGORIES, SPECIFICITY, CAT_BY_ID, PRIORITY_PAIRS
    import hashlib as _hl

    import beat_packs
    sources, cats_raw, stats = beat_packs.resolve(cfg)
    n_cats = [dict(id=c["id"], emoji=c.get("emoji", "📌"), zh=c.get("zh", c["id"]),
                   en=c.get("en", c["id"]), kw=_ntl(c.get("kw", [])),
                   entities=_ntl(c.get("entities", [])))
              for c in cats_raw]
    CATEGORIES = n_cats
    CAT_BY_ID = {c["id"]: c for c in n_cats}
    SPECIFICITY = [c["id"] for c in n_cats]           # watch 在最前 = 最具体
    if cfg.get("entities"):                           # 关注实体命中即进 ⚡重点
        PRIORITY_PAIRS = PRIORITY_PAIRS + [(_nt(e), None) for e in cfg["entities"]]
    fp = _hl.sha1(json.dumps(cfg, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:12]
    TAXONOMY_FP = f"p3:{fp}"
    EPHEMERAL_CONFIG = True
    PROFILE = "我的条线"
    return sources, (f"我的条线:{len(n_cats)} 个板块 · 专线 {stats['specialist']} 源 · "
                     f"背景 {stats['background']} 源")


_TOKEN_STOP = {
    # 聚类分词停用词:本条线新闻里几乎每条都有的词,保留会虚增相似度
    "the", "and", "for", "with", "from", "this", "that", "after", "over", "into", "amid", "says",
    "say", "said", "will", "would", "could", "has", "have", "had", "are", "was", "were", "been",
    "its", "his", "her", "new", "more", "als", "auf", "aus", "bei", "das", "dem", "den", "der",
    "des", "die", "ein", "eine", "einen", "für", "gegen", "mit", "nach", "und", "von", "vor",
    "wegen", "wie", "will", "wird", "sind", "ist", "hat", "sich", "über",
    "china", "chinas", "chinese", "chinesische", "chinesischen", "beijing", "peking",
    "europe", "european", "europa", "germany", "german", "deutschland", "brussels",
    "les", "des", "dans", "pour", "avec", "sur", "une", "aux", "par", "est", "qui", "que",
    "chine", "chinois", "chinoise", "chinoises", "pékin", "france", "french", "français",
    "francais", "frankreich",
}


def _norm(text: str) -> str:
    """小写、标点(含连字符)转空格、空白归一,首尾补空格 —— 供子串匹配用。
    连字符也转空格,使 "EU-China"/"Sino-German"/"E-Auto" 与词表统一到空格形式。"""
    text = re.sub(r"[^\w\s]", " ", text.lower())
    return " " + " ".join(text.split()) + " "


def categorize(ntitle: str, nsummary: str) -> tuple[list[str], str | None]:
    """返回 (命中的类别 id 列表, 主分类 id)。计分:kw=1/实体=3,标题×2,总分≥2 入类。"""
    scores: dict[str, int] = {}
    for cat in CATEGORIES:
        if cat["id"] == "china-macro":
            continue
        s = 0
        for kw in cat["kw"]:
            if kw in ntitle:
                s += 2
            elif kw in nsummary:
                s += 1
        for ent in cat["entities"]:
            if ent in ntitle:
                s += 6
            elif ent in nsummary:
                s += 3
        if s >= 2:
            scores[cat["id"]] = s
    if not scores and (macro := CAT_BY_ID.get("china-macro")):  # 宏观是兜底类(自定义词表可能没有)
        s = sum(2 if kw in ntitle else (1 if kw in nsummary else 0) for kw in macro["kw"]) \
            + sum(6 if e in ntitle else (3 if e in nsummary else 0) for e in macro["entities"])
        if s >= 2:
            scores["china-macro"] = s
    if not scores:
        return [], None
    primary = max(scores, key=lambda c: (scores[c], -SPECIFICITY.index(c)))
    return list(scores), primary


def is_breaking(ntitle: str, nfull: str) -> bool:
    if any(b in ntitle for b in BREAKING_KEYWORDS):
        return True
    for ent, ctx in PRIORITY_PAIRS:
        if ent in nfull and (ctx is None or any(c in nfull for c in ctx)):
            return True
    return False


# ---------------------------------------------------------------------------
# 3. 抓取 + 规范化(线程池并发,每源独立超时,不因单源挂起阻塞整体)
# ---------------------------------------------------------------------------

_FALLBACK_DATE_FORMATS = ["%b %d, %Y", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"]

# 中文本地化 RFC822 日期(如芥末堆 "星期三, 05 八月 2026 19:18:00 GMT"):
# feedparser 解析不了 → 条目全丢且 health 显示正常。译回英文再走标准解析。
_CN_DATE_TOKENS = {
    "星期一": "Mon", "星期二": "Tue", "星期三": "Wed", "星期四": "Thu",
    "星期五": "Fri", "星期六": "Sat", "星期日": "Sun", "星期天": "Sun",
    "周一": "Mon", "周二": "Tue", "周三": "Wed", "周四": "Thu",
    "周五": "Fri", "周六": "Sat", "周日": "Sun",
    "十一月": "Nov", "十二月": "Dec", "十月": "Oct",
    "一月": "Jan", "二月": "Feb", "三月": "Mar", "四月": "Apr",
    "五月": "May", "六月": "Jun", "七月": "Jul", "八月": "Aug", "九月": "Sep",
}


def _entry_time(entry) -> datetime | None:
    for attr in ("published_parsed", "updated_parsed"):
        t = getattr(entry, attr, None)
        if t:
            return datetime(*t[:6], tzinfo=timezone.utc)
    for attr in ("published", "updated"):  # 非标日期格式兜底(如 Sixth Tone "Jul 03, 2026")
        raw = getattr(entry, attr, None)
        if not raw:
            continue
        raw = raw.strip()
        for fmt in _FALLBACK_DATE_FORMATS:
            try:
                dt = datetime.strptime(raw, fmt)
                return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        if any(ch in raw for ch in ("星", "周", "月")):
            en = raw
            for cn, e in _CN_DATE_TOKENS.items():
                en = en.replace(cn, e)
            try:
                dt = email.utils.parsedate_to_datetime(en)
                return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                pass
    return None


def gnews_url(query: str, hours: int, locale: str = "en") -> str:
    """时间窗大于查询自带的 when: 时放宽之(只放宽不收窄)。locale 选 Google News 语言区。"""
    m = re.search(r"when:(\d+)([dh])", query)
    if m:
        cur_hours = int(m.group(1)) * (24 if m.group(2) == "d" else 1)
        if hours > cur_hours:
            query = re.sub(r"when:\d+[dh]", f"when:{math.ceil(hours / 24)}d", query)
    base = GNEWS_LOCALES.get(locale, GNEWS_BASE)
    return base.format(q=quote_plus(query))


def _source_url(src: dict, hours: int) -> str:
    if src["type"] == "gnews":
        return gnews_url(src["value"], hours, src.get("gnews_locale", "en"))
    return src["value"]


def _clean_gnews_title(title: str) -> tuple[str, str | None]:
    """Google News 标题带 " - Outlet" 后缀:剥出媒体名。"""
    m = re.match(r"^(.*)\s+-\s+([^-]+)$", title)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return title.strip(), None


def _tokens(title: str) -> frozenset[str]:
    t = re.sub(r"[^\w\s]", " ", title.lower())
    return frozenset(w for w in t.split() if len(w) >= 3 and w not in _TOKEN_STOP)


def test_feed(src_type: str, value: str, locale: str = "en") -> tuple[bool, str]:
    """添加源时的连通性测试。"""
    if src_type == "gnews":
        url = GNEWS_LOCALES.get(locale, GNEWS_BASE).format(q=quote_plus(value))
    else:
        url = value
    try:
        r = requests.get(url, headers=UA, timeout=FETCH_TIMEOUT)
        if r.status_code == 403:  # 与 fetch_one 一致的阅读器 UA 退避
            r = requests.get(url, headers={"User-Agent": "ChinaEUMonitor/1.0 (RSS reader; private research use)"},
                             timeout=FETCH_TIMEOUT)
        r.raise_for_status()
        parsed = feedparser.parse(r.content)
    except Exception as exc:  # noqa: BLE001
        return False, f"抓取失败: {exc}"
    if parsed.bozo and not parsed.entries:
        return False, "无法解析为有效的 RSS/Atom feed"
    if not parsed.entries:
        return False, "feed 可解析但当前没有条目"
    dated = sum(1 for e in parsed.entries if _entry_time(e))
    msg = f"OK — {len(parsed.entries)} 条,最新: {parsed.entries[0].get('title', '')[:60]}"
    if dated == 0:
        return False, "条目均无可解析时间戳,监测会将其全部丢弃 — 建议改用 Google News 查询"
    return True, msg


def fetch_one(src: dict, hours: int) -> tuple[list[dict], dict]:
    """抓取单个源。在工作线程内完成解析、闸门过滤、归类与突发标记。"""
    t0 = time.time()
    health = {"name": src["name"], "lane": src.get("lane", "custom"), "ok": False,
              "kept": 0, "total": 0, "error": "", "secs": 0.0}
    url = _source_url(src, hours)
    try:
        resp = requests.get(url, headers=UA, timeout=FETCH_TIMEOUT)
        if resp.status_code == 403:  # 部分站点(实测 DIW)封浏览器 UA、放行阅读器 UA
            resp = requests.get(url, headers={"User-Agent": "ChinaEUMonitor/1.0 (RSS reader; private research use)"},
                                timeout=FETCH_TIMEOUT)
        resp.raise_for_status()
        parsed = feedparser.parse(resp.content)
    except Exception as exc:  # noqa: BLE001
        health["error"] = f"{type(exc).__name__}: {str(exc)[:160]}"
        health["secs"] = round(time.time() - t0, 1)
        return [], health
    if parsed.bozo and not parsed.entries:
        health["error"] = "无法解析为 RSS/Atom"
        health["secs"] = round(time.time() - t0, 1)
        return [], health

    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    gate = {"china": CHINA_GATE, "europe": EUROPE_GATE}.get(src.get("filter", "none"))
    is_gnews = src["type"] == "gnews"
    items: list[dict] = []
    health["total"] = len(parsed.entries)
    n_nodate = 0

    for e in parsed.entries:
        try:
            ts = _entry_time(e)
            if ts is None:
                n_nodate += 1
                continue
            if ts < cutoff:
                continue
            raw_title = (getattr(e, "title", "") or "").strip()
            link = (getattr(e, "link", "") or "").strip()
            if not raw_title or not link:
                continue
            outlet = None
            if is_gnews:
                title, outlet = _clean_gnews_title(raw_title)
                if len(title) < 12:  # "News"、"Live" 之类无信息栏目标题
                    continue
                summary = ""  # gnews 摘要只是标题+媒体名的 HTML,无信息量,且会污染实体匹配
            else:
                title = raw_title
                summary = re.sub(r"<[^>]+>", " ", getattr(e, "summary", "") or "")
                summary = " ".join(summary.split())[:400]
            outlet = outlet or src["name"].split("·")[0].strip()

            ntitle = _norm(title)
            if any(b in ntitle for b in TITLE_BLOCKLIST):
                continue
            nsummary = _norm(summary) if summary else " "
            nfull = ntitle + nsummary
            if gate and not any(g in nfull for g in gate):
                continue
            cats, primary = categorize(ntitle, nsummary)
            items.append({
                "source": src["name"], "lane": src.get("lane", "custom"),
                "stype": src.get("stype", ""),  # 口径(p3 模式的注册表源携带;默认源为空)
                "lang": src.get("lang", "en"), "outlet": outlet,
                "title": title, "summary": summary, "link": link, "time": ts,
                "cats": cats, "primary": primary,
                "breaking": is_breaking(ntitle, nfull),
                "tokens": _tokens(title),
            })
        except Exception:  # noqa: BLE001 — 单条畸形数据(如 year>9999 的日期)不拖垮整源/整体抓取
            continue

    # 排序后再截断:不能假设 feed 是新→旧排序(旧→新的 feed 会被提前 break 丢掉最新条目)
    items.sort(key=lambda x: x["time"], reverse=True)
    items = items[:PER_SOURCE_CAP]
    health.update(ok=True, kept=len(items), secs=round(time.time() - t0, 1))
    # 有条目但日期全解析不了 = 看似健康实则零产出的静默死源(如无日期字段的 feed),必须可见
    if health["total"] > 0 and n_nodate == health["total"]:
        health["error"] = f"⚠️ 全部 {health['total']} 条日期无法解析,该源实际零产出"
    return items, health


def cluster_items(items: list[dict]) -> list[dict]:
    """跨源同题聚类:标题词集 Jaccard≥0.5 或包含关系即合并。
    (0.5 实测恰好合并同一事件的措辞变体,如 summoned/summons 各家改写)"""
    clusters: list[dict] = []
    for it in sorted(items, key=lambda x: x["time"], reverse=True):
        tk = it["tokens"]
        target = None
        for cl in clusters:
            ref = cl["tokens"]
            if not tk or not ref:
                continue
            inter = len(tk & ref)
            union = len(tk | ref)
            contained = min(len(tk), len(ref)) >= 4 and (tk <= ref or ref <= tk)
            if (union and inter / union >= 0.5) or contained:
                target = cl
                break
        if target is None:
            clusters.append({"tokens": tk, "items": [it]})
        else:
            target["items"].append(it)

    out = []
    for cl in clusters:
        its = sorted(cl["items"], key=lambda x: x["time"])  # 最早在前 = 首发
        outlets = {i["outlet"].lower() for i in its}
        cats = sorted({c for i in its for c in i["cats"]})
        # 独立报道数:通稿被聚合站原文转载(标题几乎一致)只算一家,
        # 各家自拟标题才是独立编辑判断 → 取「不同媒体数」与「标题变体数」的较小值
        variants = {" ".join(sorted(i["tokens"])) for i in its}
        out.append({
            "rep": its[0], "items": its, "n": len(its),
            "diversity": min(len(outlets), len(variants)),
            "langs": {i["lang"] for i in its},
            "cats": cats,
            "breaking": any(i["breaking"] for i in its),
            "newest": its[-1]["time"],
        })
    out.sort(key=lambda c: c["newest"], reverse=True)
    return out


@st.cache_data(ttl=600, show_spinner="正在抓取各源最新报道 …")
def fetch_all(sources_json: str, hours: int, taxo_fp: str = "default") -> tuple[list[dict], list[dict], int]:
    """并发抓取全部启用源 → (聚类列表, 各源健康状态, 原始条目数)。缓存 10 分钟。

    taxo_fp 只作缓存键:归类/突发标记在抓取时按当前词表计算,
    不同档案的词表不同,必须各自成键,否则跨档案读到错误归类。"""
    sources = [s for s in json.loads(sources_json) if s.get("enabled", True)]
    all_items: list[dict] = []
    health: list[dict] = []
    with cf.ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        for items, h in ex.map(lambda s: fetch_one(s, hours), sources):
            all_items.extend(items)
            health.append(h)
    # 完全相同链接去重(同一文章经由多个查询进入)
    seen_links: set[str] = set()
    unique = []
    for it in sorted(all_items, key=lambda x: x["time"], reverse=True):
        if it["link"] in seen_links:
            continue
        seen_links.add(it["link"])
        unique.append(it)
    # gnews 跳转链 → 真实出版商 URL(大陆用户可直达)。任何异常吃掉,绝不影响抓取;
    # 未解出的保留原 google 链(境外用户无感)。已按时间倒序,最新条目优先解。
    try:
        import gnews_decoder
        glinks = [it["link"] for it in unique if it["link"].startswith("https://news.google.com/")]
        mapping = gnews_decoder.resolve_batch(glinks)
        for it in unique:
            real = mapping.get(it["link"])
            if real:
                it["link"] = real
    except Exception:
        pass
    clusters = cluster_items(unique)
    health.sort(key=lambda h: (h["ok"], h["name"]))
    return clusters, health, len(unique)


# ---------------------------------------------------------------------------
# 4. 摘要导出
# ---------------------------------------------------------------------------

def build_digest(clusters: list[dict], hours: int) -> str:
    now = datetime.now(BERLIN)
    lines = [f"# 中欧监测早报 · {now:%Y-%m-%d %H:%M} (Berlin)",
             f"_时间窗 {hours}h · 由 China-Europe Monitor 生成_", ""]

    def fmt(cl):
        it = cl["rep"]
        t = it["time"].astimezone(BERLIN)
        extra = f"(+{cl['n'] - 1}家)" if cl["n"] > 1 else ""
        return f"- **{t:%d日%H:%M}** [{it['title']}]({it['link']}) — {it['outlet']} {extra}"

    hot = [c for c in clusters if c["breaking"] or c["diversity"] >= 2]
    if hot:
        lines.append("## ⚡ 重点")
        lines += [fmt(c) for c in hot[:15]]
        lines.append("")
    hot_ids = {id(c) for c in hot[:15]}
    for cat in CATEGORIES:
        rows = [c for c in clusters
                if id(c) not in hot_ids and c["rep"]["primary"] == cat["id"]]
        if not rows:
            continue
        lines.append(f"## {cat['emoji']} {cat['zh']}")
        lines += [fmt(c) for c in rows[:8]]
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 5. UI
# ---------------------------------------------------------------------------

st.set_page_config(page_title="News Monitor!", page_icon="📰", layout="wide")

# --- 档案解析(必须先于一切数据加载;不带 ?profile= 即默认档案 = 原平台,零变化) ---
_profile_err = None
_p3_notices: list[str] = []
_p3_sources: list[dict] | None = None
P3_CODE: str | None = None
if _qp := st.query_params.get("profile"):
    if re.match(r"^p\d+\.", _qp):
        # p3 配置码(三层筛选流生成,个人配置零落盘)
        import profile_code as _pc
        _p3_cfg, _p3_notices = _pc.decode(_qp)
        if _p3_cfg is None:
            _profile_err = _p3_notices[0] if _p3_notices else "配置码无效"
            _p3_notices = []
        else:
            P3_CODE = _qp
            _p3_sources, _p3_msg = apply_packs_config(_p3_cfg)
    else:
        _ok, _pmsg = apply_profile(_qp)
        if not _ok:
            _profile_err = _pmsg

# --- 定制屏(?setup=1):三层筛选流,零抓取;带 profile 码进入则预填当前选择 ---
if st.query_params.get("setup"):
    import beat_packs as _bp
    import profile_code as _pc
    _pf = _pc.decode(P3_CODE)[0] if P3_CODE else None
    _bp.render_setup(prefill=_pf, notices=_p3_notices)
    st.stop()

# 同一会话内切换档案 → 重载信源与 🆕 基线(否则沿用上一档案的内存数据)
_active_key = TAXONOMY_FP if P3_CODE else (PROFILE or "")
if st.session_state.get("_active_profile", "__unset__") != _active_key:
    st.session_state.sources = _p3_sources if _p3_sources is not None else load_sources()
    st.session_state.pop("baseline", None)
    st.session_state["_active_profile"] = _active_key

if "sources" not in st.session_state:
    st.session_state.sources = load_sources()

# 上次访问基线:之后发布的条目标 🆕(基线在会话内固定,不随交互刷新跳变)。
# 30 分钟宽限:短时间内刷新页面/重连不推进基线,🆕 标记不会因手滑刷新而消失。
if "baseline" not in st.session_state:
    baseline = None
    try:
        baseline = datetime.fromisoformat(json.loads(STATE_PATH.read_text())["last_visit"])
    except Exception:
        pass
    st.session_state.baseline = baseline
    now_utc = datetime.now(timezone.utc)
    if (baseline is None or (now_utc - baseline) > timedelta(minutes=30)) \
            and not EPHEMERAL_CONFIG:  # p3 模式不写共享的访问基线
        try:
            STATE_PATH.write_text(json.dumps({"last_visit": now_utc.isoformat()}))
        except Exception:
            pass

with st.sidebar:
    st.header("设置")
    if _profile_err:
        st.error(_profile_err)
    for _n in _p3_notices:
        st.info(_n)
    if P3_CODE:
        st.success(_p3_msg)
        st.markdown(f"[🎯 调整我的监控](?setup=1&profile={P3_CODE})")
    else:
        st.markdown("[🎯 定制我的监控](?setup=1) — 选条线,60 秒生成专属监控页")
    _plist = list_profiles()
    if _plist:  # 有档案才显示切换器,默认部署界面不变
        _opts = ["默认(中欧监测)"] + _plist
        _cur = _opts.index(PROFILE) if PROFILE in _plist else 0
        _sel = st.selectbox("档案", _opts, index=_cur,
                            help="每个档案 = 一套专属信源与分类词表;也可直接用 ?profile=名字 的链接")
        _want = "" if _sel == _opts[0] else _sel
        if _want != (PROFILE or ""):
            if _want:
                st.query_params["profile"] = _want
            else:
                st.query_params.pop("profile", None)
            st.rerun()
    hours = st.slider("时间窗(小时)", 6, 72, 24, step=6)
    lanes_selected = st.multiselect(
        "来源类型", options=list(LANE_LABELS), default=list(LANE_LABELS),
        format_func=lambda k: LANE_LABELS[k],
    )
    search = st.text_input("🔍 搜索(标题/摘要)", placeholder="如 BYD、Nexperia、seltene Erden …")
    c1, c2 = st.columns(2)
    with c1:
        if st.button("🔄 刷新", use_container_width=True):
            st.cache_data.clear()
            st.rerun()
    with c2:
        digest_clicked = st.button("📋 早报摘要", use_container_width=True)
    if digest_clicked:
        st.session_state.show_digest = True
    st.caption("定制/调整自己的监控 → 上方 🎯 入口")

    st.divider()
    st.subheader("监控源")

    # --- 按 lane 分组的启停/删除管理 ---
    to_delete = None
    by_lane: dict[str, list[int]] = {}
    for i, src in enumerate(st.session_state.sources):
        by_lane.setdefault(src.get("lane", "custom"), []).append(i)
    for lane in LANE_LABELS:
        idxs = by_lane.get(lane)
        if not idxs:
            continue
        n_on = sum(1 for i in idxs if st.session_state.sources[i].get("enabled", True))
        with st.expander(f"{LANE_LABELS[lane]} ({n_on}/{len(idxs)})"):
            for i in idxs:
                src = st.session_state.sources[i]
                # widget key 用「下标+源地址」:纯下标会在删除后错位套状态,
                # 纯地址会在手写档案含重复 value 时撞 key 炸页面(评审确认)
                skey = f"{i}_{src['value']}"
                c1, c2 = st.columns([6, 1])
                with c1:
                    new_state = st.checkbox(src["name"], value=src.get("enabled", True),
                                            key=f"en_{skey}")
                    if new_state != src.get("enabled", True):
                        src["enabled"] = new_state
                        save_sources(st.session_state.sources)
                        st.cache_data.clear()
                        st.rerun()
                with c2:
                    if st.button("✕", key=f"del_{skey}", help="删除该源"):
                        to_delete = i
    if to_delete is not None:
        st.session_state.sources.pop(to_delete)
        save_sources(st.session_state.sources)
        st.cache_data.clear()
        st.rerun()

    # --- 添加新源 ---
    with st.expander("➕ 添加监控源"):
        new_name = st.text_input("名称", placeholder="例如 Kiel Institute")
        new_type = st.radio("类型", ["rss", "gnews"], horizontal=True,
                            help="rss = 原生 feed 地址;gnews = Google News 查询语句(适合无 RSS 或被 Cloudflare 挡的网站)")
        if new_type == "rss":
            new_value = st.text_input("Feed URL", placeholder="https://…/feed")
        else:
            new_value = st.text_input("查询语句", placeholder="site:example.com China when:1d")
        new_lane = st.selectbox("分组", options=list(LANE_LABELS), index=len(LANE_LABELS) - 1,
                                format_func=lambda k: LANE_LABELS[k])
        new_filter = st.selectbox(
            "相关性过滤", options=["china", "europe", "none"], index=0,
            format_func=lambda v: {"china": "china — 综合流,须提到中国",
                                   "europe": "europe — 中方源,须提到欧洲",
                                   "none": "none — 已自带限定,不过滤"}[v])
        new_lang = st.selectbox("语言", ["en", "de", "fr", "zh"], index=0,
                                help="gnews 查询会按语言选 Google News 区域(法语查询在英文区搜不到)")
        if st.session_state.pop("add_ok_msg", None):
            st.success(st.session_state.pop("add_ok_msg_text", "已添加"))
        if st.button("测试并添加"):
            new_locale = {"fr": "fr", "de": "de", "zh": "zh"}.get(new_lang, "en")
            if not new_name or not new_value:
                st.warning("名称和地址/查询语句都要填")
            elif any(s["value"] == new_value for s in st.session_state.sources):
                st.warning("该地址/查询已在监控列表中")
            else:
                ok, msg = test_feed(new_type, new_value, new_locale)
                if ok:
                    entry = {
                        "name": new_name, "type": new_type, "value": new_value,
                        "lane": new_lane, "lang": new_lang, "filter": new_filter,
                        "enabled": True,
                    }
                    if new_type == "gnews" and new_locale != "en":
                        entry["gnews_locale"] = new_locale
                    st.session_state.sources.append(entry)
                    save_sources(st.session_state.sources)
                    st.cache_data.clear()
                    # st.rerun() 会吞掉本次的 st.success → 用 flag 留到下一轮显示
                    st.session_state["add_ok_msg"] = True
                    st.session_state["add_ok_msg_text"] = f"已添加: {msg}"
                    st.rerun()
                else:
                    st.error(msg)

# --- 抓取 ---
clusters, health, n_items = fetch_all(json.dumps(st.session_state.sources), hours, TAXONOMY_FP)

# --- 源健康状态(放在抓取之后,侧边栏尾部) ---
with st.sidebar:
    n_bad = sum(1 for h in health if not h["ok"])
    with st.expander(f"📡 抓取状态 {'🔴 ' + str(n_bad) + ' 个源失败' if n_bad else '🟢 全部正常'}"):
        for h in health:
            dot = "🟢" if h["ok"] else "🔴"
            line = f"{dot} {h['name']} — {h['kept']}/{h['total']}条 · {h['secs']}s"
            st.caption(line if h["ok"] else f"{line}\n{h['error']}")
        try:
            import gnews_decoder
            _ds = gnews_decoder.stats()
            _cd = f" · 熔断冷却 {_ds['cooldown_min']}min" if _ds["cooldown_min"] else ""
            st.caption(f"🔗 链接解码缓存 {_ds['cached']} 条 · 熔断 {_ds['trips']} 次{_cd}")
        except Exception:
            pass

# --- 过滤(来源类型 + 搜索) ---
lanes_set = set(lanes_selected)


def _visible(cl: dict) -> bool:
    its = [i for i in cl["items"] if i["lane"] in lanes_set]
    if not its:
        return False
    if search:
        q = search.lower()
        return any(q in i["title"].lower() or q in i["summary"].lower() for i in its)
    return True


visible = [c for c in clusters if _visible(c)]

st.title("📰 News Monitor!" + (f" · {PROFILE}" if PROFILE else ""))
st.caption(
    f"{len(visible)} 组报道({n_items} 条)· 过去 {hours}h · "
    f"更新于 {datetime.now(BERLIN):%Y-%m-%d %H:%M} Berlin · 缓存 10 分钟"
    + (f" · 档案 {PROFILE}" if PROFILE else "")
)
feedback.render(context=PROFILE or ("我的条线" if P3_CODE else "默认监测台"))

# --- 早报摘要 ---
if st.session_state.get("show_digest"):
    digest_md = build_digest(visible, hours)
    with st.expander("📋 早报摘要(Markdown,可直接粘贴)", expanded=True):
        st.download_button("⬇️ 下载 .md", digest_md,
                           file_name=f"digest_{datetime.now(BERLIN):%Y%m%d_%H%M}.md")
        st.code(digest_md, language="markdown")
        if st.button("收起摘要"):
            st.session_state.show_digest = False
            st.rerun()

# --- 标签页 ---
BREAKING_WINDOW_H = min(hours, 12)
_bw_cutoff = datetime.now(timezone.utc) - timedelta(hours=BREAKING_WINDOW_H)
# p3 模式:⚡重点 必须钉死在所选领域内(c["cats"] 非空 = 命中至少一个所选模块/关注实体)。
# 否则背景快讯层里任何"两家同题"的稿(体育/社会案件)都会靠 diversity≥2 混进重点。
hot = [c for c in visible if (c["breaking"] or c["diversity"] >= 2) and c["newest"] >= _bw_cutoff
       and (not P3_CODE or c["cats"])]

tab_defs: list[tuple[str, list[dict]]] = [(f"⚡ 重点 {len(hot)}", hot)]
for cat in CATEGORIES:
    rows = [c for c in visible if cat["id"] in c["cats"]]
    tab_defs.append((f"{cat['emoji']} {cat['zh']} {len(rows)}", rows))
tab_defs.append((f"📋 全部 {len(visible)}", visible))

MAX_CARDS = 60


def render_cluster(cl: dict) -> None:
    rep = cl["rep"]
    local = rep["time"].astimezone(BERLIN)
    badges = ""
    if cl["breaking"]:
        badges += "🔴 "
    if st.session_state.baseline and cl["newest"] > st.session_state.baseline:
        badges += "🆕 "
    with st.container(border=True):
        st.markdown(f"{badges}**[{rep['title']}]({rep['link']})**")
        meta = f"{rep['outlet']} · {local:%a %d %b %H:%M}"
        if rep.get("stype") in STYPE_LABELS:
            meta += f" · {STYPE_LABELS[rep['stype']]}"
        if cl["diversity"] >= 2:
            meta += f" · 🌐 {cl['diversity']} 家独立报道"
        if len(cl["langs"]) >= 2:
            meta += " · 跨语言"
        cat_chips = " ".join(f"`{CAT_BY_ID[c]['zh']}`" for c in cl["cats"] if c in CAT_BY_ID)
        if cat_chips:
            meta += " · " + cat_chips
        st.caption(meta)
        if rep["summary"]:
            st.write(rep["summary"][:300])
        if cl["n"] > 1:
            with st.expander(f"相关报道 ({cl['n'] - 1}) — 首发: {rep['outlet']} {local:%H:%M}"):
                for it in cl["items"][1:]:
                    t = it["time"].astimezone(BERLIN)
                    st.markdown(f"- [{it['title']}]({it['link']}) — {it['outlet']} · {t:%a %H:%M}")


SHOW_EVENTS_TAB = False  # 📅 活动板块暂时隐藏;改 True 即恢复(数据与代码都还在)

# 💬 AI 访谈定制 tab 已下线(2026-08-08 用户裁定):勾选式定制(?setup=1)取代之。
# beat_interview.py 保留在仓库,后续按 DESIGN.md 改造为维护者的领域包作者工具。
_extra_tabs = [events_monitor.tab_label()] if SHOW_EVENTS_TAB else []
tabs = st.tabs([label for label, _ in tab_defs] + _extra_tabs)
if SHOW_EVENTS_TAB:
    with tabs[-1]:
        events_monitor.render_events_tab()
def _stype_of(cl: dict) -> set[str]:
    return {i.get("stype") for i in cl["items"] if i.get("stype")}


for tab, (label, rows) in zip(tabs, tab_defs):
    with tab:
        if not rows:
            if label.startswith("⚡"):
                st.info(f"过去 {BREAKING_WINDOW_H}h 内没有触发突发信号或多源交叉的报道。")
            else:
                st.info("该类别暂无报道。可放宽时间窗或检查来源类型筛选。")
            continue
        if label.startswith("⚡"):
            st.caption(f"突发信号词或 ≥2 家独立媒体同题报道 · 最近 {BREAKING_WINDOW_H}h")
            rows = sorted(rows, key=lambda c: (c["breaking"], c["diversity"], c["newest"]), reverse=True)
        # p3 模式:领域 tab 内按口径二级筛选(先领域后口径;单一条线时全部混在一起的解法)
        if P3_CODE:
            _cnt = {t: sum(1 for c in rows if t in _stype_of(c)) for t in STYPE_LABELS}
            _opts = [t for t, n in _cnt.items() if n]
            if len(_opts) >= 2:
                _pick = st.pills("口径", options=["all"] + _opts, selection_mode="single",
                                 default="all", label_visibility="collapsed",
                                 format_func=lambda t, _c=_cnt, _n=len(rows): (
                                     f"全部 {_n}" if t == "all"
                                     else f"{STYPE_LABELS[t]} {_c[t]}"),
                                 key=f"stype_{re.sub(r'[0-9 ]+$', '', label)}")
                if _pick and _pick != "all":
                    rows = [c for c in rows if _pick in _stype_of(c)]
        for cl in rows[:MAX_CARDS]:
            render_cluster(cl)
        if len(rows) > MAX_CARDS:
            st.caption(f"… 另有 {len(rows) - MAX_CARDS} 组未显示(收窄时间窗或用搜索定位)")
