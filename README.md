# 中欧新闻监测 · China-Europe News Monitor

为柏林中欧地缘政治 + 产经条线记者定制的新闻监测台。刷新即得过去 6–72 小时内、
59 个精选信源(46 个默认启用)的中欧交叉新闻,按 8 个主题自动归类,突发信号自动标红。

## 运行

```bash
pip install -r requirements.txt
streamlit run china_europe_monitor.py
```

首次运行会在脚本旁生成 `sources.json`(信源配置,侧边栏可增删启停)和
`monitor_state.json`(上次访问时间,用于 🆕 标记)。

## 功能

| 功能 | 说明 |
|---|---|
| ⚡ 重点 tab | 突发信号词(制裁/关税裁决/搜查/召见/国事访问…,中英德三语)+ ≥2 家独立媒体同题报道,最近 12h |
| 9 个主题 tab | 中欧政治外交 / 德中关系 / **法中关系** / 贸易防御 / 汽车与电池 / 能源光伏 / 科技AI芯片 / **国防与战略原材料** / 中国宏观(兜底);**多标签归类**,一篇稿可同时出现在多个相关 tab |
| 跨源聚类 | 同题报道合并为一张卡,标注首发媒体与时间,可展开看各家标题("Handelsblatt 首发…"的出处依据) |
| 🌐 独立报道数 | 通稿被聚合站原样转载只算一家;各家自拟标题才算独立编辑判断 |
| 📋 早报摘要 | 一键生成 Markdown(按主题分组、柏林时间戳、链接),可下载或直接粘给编辑 |
| 相关性闸门 | 欧洲综合流须提到中国(`china` 闸门),中方宽流须提到欧洲(`europe` 闸门),已限定的查询不过滤(`none`) |
| 并发抓取 | 46 源约 6 秒,每源独立超时;📡 抓取状态面板可见每源健康度;缓存 10 分钟 |
| 兜底安全网 | 两条不限站点的 Google News 全网查询(China×Germany / China×EU),未订阅媒体的大新闻也不漏 |

## 信源结构(sources.json)

```json
{
  "name": "Reuters · China×Europe",
  "type": "gnews",            // rss = 原生 feed;gnews = Google News RSS 查询
  "value": "site:reuters.com (China OR Chinese) (EU OR Europe ...) when:1d",
  "lane": "wires",            // de-media | fr-media | eu-brussels | wires | cn-media | industry | gov | thinktank | custom
  "lang": "en",               // en | de | fr | zh
  "filter": "china",          // china | europe | none(相关性闸门)
  "gnews_locale": "fr",       // 可选:gnews 查询的 Google News 语言区(en/de/fr,默认 en)
  "enabled": true
}
```

注意:**法语 site: 查询在英文区(hl=en-US)返回 0 条**(2026-07 实测,Les Echos/La Tribune),
法语 gnews 源必须带 `"gnews_locale": "fr"`。闸门词表为中英德法四语
(RFI 中文、DW 中文等中文源的闸门为中文词)。

约定俗成的经验(2026-07 实测):

- Politico.eu / Euractiv / consilium.europa.eu 的原生 RSS 被 Cloudflare 挡,走 gnews。
- Global Times / Xinhua 的原生 RSS 已停止更新(内容停在 2018),必须走 gnews。
- FT / Reuters / Bloomberg / WSJ 无公开内容 RSS,走 gnews site: 查询。
- SCMP feed 模式:`scmp.com/rss/{id}/feed`(318199 中国外交、318421 中国经济、36 科技)。
- gnews 按**正文**匹配查询词,标题可能与中国无关,所以通讯社 gnews 源配 `filter: "china"`。
- 时间窗放宽到 >24h 时,gnews 查询里的 `when:1d` 会自动放宽(只放宽不收窄)。

## 📅 活动 tab(会议/展会跟踪)

与新闻流逻辑相反的实体模型:主键 = 活动名+届次年份,种子数据+人工核定,v1 零爬虫。
代码独立在 `events_monitor.py`,数据在 `events.json`(首次运行自动生成种子,纳入 git);
`events_state.json`(v2 抓取时间戳)与 `monitor_state.json` 一样不入库。

| 概念 | 说明 |
|---|---|
| 行动日 | `press_deadline`(媒体注册截止)优先,否则 `start_date`;倒计时与提醒都基于它 |
| 提醒档位 | 默认 T-60(申请 press accreditation)/ T-30(向参展企业发采访请求)/ T-14(最后窗口);每活动可覆盖 `reminder_offsets`(如智库活动 `[21,7]`) |
| `needs_verification` | 日期/链接未核实的条目:年历中显示 ⚠️「日期待核实」,**不参与提醒**(避免按错误日期提醒);在「⚙️ 管理」视图核实(建议 [AUMA 数据库](https://www.auma.de/messen-finden/))后取消勾选转正 |
| `action_status` | `todo` / `registered` / `interview_requested` / `skipped` / `done`;倒计时视图只显示前三种 |
| 三个子视图 | ⏰ 行动倒计时(90 天内或已触发提醒,🔴≤14 天/🟡≤30 天)· 🗓️ 年历(未来 18 个月按月分组,可按板块筛选)· ⚙️ 管理(全字段表格编辑+校验保存+Markdown 导出) |

**📡 发现(会议雷达,v2)**:短周期会议(智库研讨/商会论坛,提前数周才公布)的
自动发现。34 个渠道(2026-07 逐一实测),三种类型:

| 类型 | 说明 | 例 |
|---|---|---|
| `rss` | 活动型 feed(实测确认条目为活动) | ECFR `?post_type=event`、DIW `rss_events.xml`、Politico Live、DG TRADE |
| `page` | 页面变化侦测:抓纯文本行与基线比对,新增的中国相关行 → ⚠️ 提示 | Kiel GCC、DIHK Newsroom、MERICS、DCW(整页皆中德活动) |
| `gnews` | 被墙机构(KAS/Chatham/欧盟商会/Körber)与通用雷达;德语查询须 `"locale": "de"` | `"Kiel Institute" China`、`DIHK China` |

渠道清单在 `event_sources.json`;扫描状态/页面基线/已忽略清单在
`events_state.json`(不入库)。原则:发现只进候选收件箱,**人工「加入跟踪」后仍为
待核实状态**——不自动写库、不自动解析日期。经验:德国主要机构(Kiel/DIHK/MERICS/
SWP/DGAP)均无活动 RSS;DIW 的 RSS 端点封浏览器 UA、放行阅读器 UA(代码已做 403 退避)。

约束:代码**严禁构造/猜测任何 URL**,`source_url` 只允许人工填入
(发现雷达候选的链接来自公告 feed 本身,经人工点击确认后写入);
无 `start_date` 的条目保存时自动标记待核实。
v2 后续(未实现,见 `check_date_drift()` 桩):日期漂移检测——仅检测
已知日期是否从官网页面消失并打回待核实,不自动解析新日期。

## 档案(profiles)与访谈式配置

- `?profile=名字` → 加载 `profiles/<名>/sources.json`(+ 可选 `taxonomy.json`);
  不带参数 = 默认档案(根目录文件,即原中欧监测,行为不变)。
- `taxonomy.json`:`categories/specificity/breaking_keywords/priority_pairs`
  整体替换,`china_gate_extra/europe_gate_extra/title_blocklist_extra` 追加;
  所有词经与内置词表相同的 `_nt` 规范化(空格词边界、连字符=空格语义一致)。
- `beat_interview.py`(💬 定制 tab):Claude 访谈生成档案。硬规则:模型不发明
  RSS URL,只能选用已验证目录(根 sources.json)或组装 gnews 查询;生成后逐源
  实测 + 一轮自动修复;交付经 GitHub PR(代码硬性限定只写 `profiles/`、只开
  PR),人工 Merge 后生效。所需 Secrets 见 USAGE 第 6½ 节。

## 归类与优先级调校

关键词表在 `china_europe_monitor.py` 的 `CATEGORIES` / `BREAKING_KEYWORDS` /
`PRIORITY_PAIRS` / `CHINA_GATE` / `EUROPE_GATE`。匹配在规范化文本(小写、标点转空格、
首尾补空格)上进行,短词用 `" ev "` 形式的空格包裹做词边界保护。

计分:关键词命中 1 分、实体命中 3 分、标题命中 ×2,总分 ≥2 归入该类;
`china-macro` 是兜底类,仅在未命中任何其他类时归入。
