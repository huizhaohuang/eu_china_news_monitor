# 使用指南 · 中欧新闻监测台

> 面向日常使用,5 分钟读完。技术细节(信源格式、词表调校)见 [README.md](README.md)。

---

## 一、打开网页

### 方式 1:手动启动(当前方式)

```bash
cd ~/Downloads/GitHub/eu_china_news_monitor
.venv/bin/streamlit run china_europe_monitor.py
```

终端会打印地址,浏览器访问 **http://localhost:8501** 即可。
关掉终端窗口 = 服务停止。已经有一个实例在跑时不用重复启动,直接开浏览器就行。

### 方式 2:开机常驻(推荐,一次设置)

```bash
bash setup_schedule.sh with-monitor
```

之后监测台随登录自动启动、崩溃自动重启,**任何时候**打开浏览器访问
http://localhost:8501 都在。建议把这个地址加入浏览器书签栏/设为固定标签页。

### 方式 3:手机/平板同 Wi-Fi 访问

启动时终端里会显示 `Network URL: http://192.168.x.x:8501`,
手机连同一个 Wi-Fi 后访问该地址即可(IP 会变,以终端显示为准)。

---

## 二、每天怎么用(建议流程)

**07:00 — 3 分钟晨扫**(香港比柏林早 6-7 小时,这是给 HK 报题的最后窗口):
1. 07:00 系统会自动弹通知并打开**早报摘要**(覆盖前晚 18:00 起的 13 小时)
2. 先看「⚡ 重点」部分——突发信号 + 多家独立报道的交叉新闻
3. 需要细看某条线,点摘要底部「打开完整监测台」进对应 tab

**12:00 — 午间补扫**:自动推送午报(覆盖上午 6 小时),看有没有布鲁塞尔/柏林上午发生的新动态。

**写稿时 — 按需深挖**:
- 打开监测台,点对应主题 tab(如写 EV 关税就看「⚖️ 贸易防御」+「🚗 汽车与电池」——同一篇稿会同时出现在两个相关 tab,这是特性不是重复)
- 左侧「🔍 搜索」输入公司/人名/德语关键词(如 `Nexperia`、`seltene Erden`)全局过滤
- 时间窗滑块拉到 72h 回看更久;「来源类型」多选框可只看德媒或只看中方官媒(对比各方口径本身就是稿子)

## 三、看懂界面标记

| 标记 | 含义 |
|---|---|
| 🔴 | 突发信号:标题命中三语信号词(制裁/关税裁决/召见/搜查/国事访问…)或关键人物×语境组合(如 Merz×中国、Nexperia 任何动态) |
| 🆕 | 上次使用之后新出现的报道 |
| 🌐 N 家独立报道 | N 家媒体**各自撰写**了同题报道(通稿转载不计)——跨源交叉 = 重要性信号 |
| 跨语言 | 德语和英语媒体都在报——破圈信号,HK 编辑大概率会问 |
| `类别` 灰色小标签 | 该报道命中的全部主题(多标签) |
| 相关报道 (N) 折叠条 | 点开看其他媒体的标题与时间;卡片主体显示的是**首发**媒体——写"率先报道"时的出处依据 |
| ⚡ 重点 tab | 最近 12h 内 🔴 或 🌐≥2 的报道汇总,按信号强度排序 |

## 四、定时推送

```bash
bash setup_schedule.sh    # 首次安装(07:00 早报 + 12:00 午报)
```

- 推送形式:macOS 通知 + 浏览器自动打开摘要页(HTML,链接可点)
- 摘要文件存档在 `digests/` 目录(.md 和 .html 各一份,.md 可直接粘进 Slack/邮件)
- Mac 在睡眠中错过时间点,唤醒后会**补跑**(launchd 特性)
- Mac 关机则不会推送——这是本地方案的固有限制
- 手动生成一份:`.venv/bin/python digest_push.py --hours 13`
- 改时间:编辑 `setup_schedule.sh` 末尾 `write_digest_plist` 两行的小时数,重跑脚本
- 全部卸载:`bash setup_schedule.sh remove`
- 查看运行日志:`tail -f ~/Library/Logs/chinaeu-digest.log`

## 4½、📅 活动 tab(不再错过 Intersolar)

最右侧的「📅 活动」tab 跟踪会议/展会/活动;有待处理提醒时,tab 内顶部会显示
「🔔 N 个活动有待处理提醒」。在「⚙️ 管理」视图编辑表格时,**切换子视图前记得先保存**
(未保存的表格修改会被 Streamlit 丢弃,界面会有 ⚠️ 提示)。

**三个子视图**(顶部单选切换):

- **⏰ 行动倒计时**:90 天内需要行动的活动,按行动日排序。🔴 ≤14 天、🟡 ≤30 天。
  "行动日" = 媒体注册截止日(填了的话),否则开展日。默认三档提醒:
  T-60 申请 press accreditation → T-30 向参展企业发采访请求 → T-14 最后窗口。
  每张卡右侧可直接改状态(已注册/已发采访请求/跳过…),即改即存。
- **🗓️ 年历**:未来 18 个月按月分组总览,可按板块(汽车/能源/化工/政策/智库)筛选。
- **⚙️ 管理**:表格直接编辑全部字段、底部加新行,「💾 保存」写入 `events.json`;
  可导出 Markdown 清单。

**⚠️ 待核实机制(重要)**:日期没核实的活动**不会触发提醒**(避免按错误日期提醒你)。
首次运行种下的 8 个活动里只有 The Smarter E 2027(Intersolar 四展)日期已核实,
其余 7 个(IAA Mobility、Hannover Messe、E-world、K、Automechanika、IZB、MERICS
China Forecast)需要你花 10 分钟核实一次:去 [AUMA 展会数据库](https://www.auma.de/messen-finden/)
或主办方官网查到日期 → 在「⚙️ 管理」填入 `start_date` 和 `source_url` →
取消勾选「待核实」→ 保存。之后倒计时和提醒就会自动接管。

**添加新活动**:「⚙️ 管理」底部空行直接填;`id` 用 `活动名-年份` 格式(如
`eu-china-summit-2027`);智库讲座这类短周期活动把提醒档位改成 `21,7` 即可。

**📡 发现(会议雷达)**:短周期会议(Kiel China Shock 研讨、DIHK China Business Forum
这类提前几周才公布的)靠这个子视图自动发现——34 个公告渠道(智库/商会/欧盟机构的
活动 feed、活动页变化侦测、Google News 查询),打开视图时超过 3 天自动重扫,
也可点「🔍 立即扫描」。发现的候选进收件箱:**➕ 跟踪**(转成待核实活动,核实日期后
参与提醒)或 **✕ 忽略**(永久不再出现)。「页面新增内容」板块是机构活动页出现的
新中国相关内容,点链接人工确认后「标记已读」。渠道清单在 `event_sources.json`,
可自行增删(type: rss/gnews/page;gnews 德语查询需 `"locale": "de"`)。

## 五、管理信源

侧边栏「监控源」按类型分组(德媒/欧盟/国际媒体/中方/行业/政府/智库),勾选框即开即关。

**添加新源**(「➕ 添加监控源」):
- 有 RSS 的网站:类型选 `rss`,填 feed 地址
- 无 RSS 或被 Cloudflare 挡的(Politico、FT 这类):类型选 `gnews`,填 Google News 查询,例如:
  - `site:kiel-institut.de China when:7d`(限定网站)
  - `(BYD OR CATL) Ungarn when:2d`(全网关键词)
- 「相关性过滤」:综合性网站选 `china`(必须提到中国才收),中国媒体选 `europe`,查询里已经带了中欧限定的选 `none`
- 点「测试并添加」会先实际抓一次,失败会告诉你原因

**📡 抓取状态**(侧边栏底部):红点 = 该源本轮抓取失败,展开看原因。个别源偶尔超时正常,持续失败再处理。

## 六、与同事共享(免费,同事零安装)

用 **Streamlit Community Cloud**(官方免费托管),两种模式选一:

### 模式 A:链接即用(推荐起步,最低摩擦)

仓库保持 public(当前状态),部署出的应用公开——同事拿链接直接用,**无需任何登录**:

1. 打开 https://share.streamlit.io → 「Continue to sign-in」→ 用 **GitHub 账号**登录并授权
2. 右上角「Create app」→「Deploy a public app from GitHub」→ 填:
   - Repository: `huizhaohuang/eu_china_news_monitor`
   - Branch: `main`
   - Main file path: `china_europe_monitor.py`
   - (可选)App URL 自定义子域名,如 `china-eu-monitor`
3. 「Deploy」→ 首次构建约 2-3 分钟 → 得到 `https://xxx.streamlit.app` 链接,发给同事即可

代价:理论上任何拿到链接的人都能访问,且能在网页里改配置(重启即还原,无持久破坏)。
新闻聚合内容不敏感,以浏览为主的用法下风险很小。

### 模式 B:私有 + 邀请(想收紧时随时切换)

1. GitHub 仓库页 → Settings → General → Danger Zone → Change visibility → **Private**
   (Streamlit 会随仓库自动把应用转为私有;免费版可有 1 个私有应用)
2. share.streamlit.io 应用列表 → 该应用「⋮」→ Settings → **Sharing** →
   在 viewers 里填同事邮箱 → 同事用该邮箱的 Google/GitHub 登录即可访问

### 部署后的日常

- **`git push` 即自动重新部署**——你本地改了信源/词表/活动种子,推送后 1-2 分钟云端同步,
  这就是「配置改动由你 push」工作流的闭环
- 云端应用右上角「Manage app」可看日志、手动 Reboot
- 应用信息页(App settings → General)可随时改自定义域名

**免费版注意事项**:
- **云端改动不持久**:在云端网页里增删信源、编辑活动,容器重启后会还原为仓库里的版本。
  分工建议:同事以浏览/搜索/导出摘要为主;改配置(加源、加活动)由你在本地改好
  `git push`——推送后云端应用会自动重新部署,同事那边就同步了。
- 应用闲置一段时间会休眠,下次访问自动唤醒(约半分钟加载)。
- 免费额度:1 GB 内存,本应用远用不满。
- 本地的 07:00/12:00 推送不受影响(那是你 Mac 上的 launchd,与云端互不相干)。

临时替代方案(不想动 GitHub 时):你 Mac 在线时运行
`brew install cloudflared && cloudflared tunnel --url http://localhost:8501`,
会生成一个临时公网链接发给同事——缺点是链接每次变、无访问控制、你的 Mac 必须开着。

## 七、常见问题

| 问题 | 处理 |
|---|---|
| 打不开 localhost:8501 | 服务没在跑。按「一、方式 1」启动;或 `tail ~/Library/Logs/chinaeu-monitor.log` 看报错 |
| 端口被占用(Port 8501 is already in use) | 已有实例在跑,直接开浏览器即可;要重启:`pkill -f streamlit` 后再启动 |
| 某 tab 内容太少 | 拉宽时间窗;或该主题今天确实没新闻(安全间谍类经常为 0,正常) |
| 页面数据旧 | 缓存 10 分钟,点侧边栏「🔄 刷新」强制重抓 |
| 切换筛选后 tab 跳回第一个 | Streamlit 已知限制(tab 标签含计数),重新点目标 tab 即可 |
| 误删了信源 | `sources.json` 在 git 里有底,`git checkout sources.json` 恢复后点刷新 |
| 换了电脑/移动了文件夹 | 重新 `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`,再重跑 `setup_schedule.sh` |

## 八、进阶调校

想让某类新闻更容易/更不容易被抓到,编辑 `china_europe_monitor.py`:

- `CATEGORIES` — 8 个类别的关键词/实体表(改完自动生效于归类和推送)
- `BREAKING_KEYWORDS` / `PRIORITY_PAIRS` — 什么算 🔴 突发
- `CHINA_GATE` / `EUROPE_GATE` — 相关性闸门
- `TITLE_BLOCKLIST` — 垃圾稿黑名单

规则:全部小写;短词/缩写用空格包裹(如 `" ev "`)做词边界;写德语词时连字符会被当空格处理(`"e-auto"` 等价于 `"e auto"`)。改完保存,浏览器里点刷新即生效(Streamlit 自动热加载)。
