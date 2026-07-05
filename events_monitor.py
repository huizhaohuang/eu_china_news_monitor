"""
📅 活动 tab — 会议/展会/活动跟踪(独立于新闻监测逻辑)
========================================================
设计原则(与新闻流相反,见 README「📅 活动 tab」节):
- 活动是实体不是流:主键 = 活动名 + 届次年份;不去重合并、不追加流。
- 抓取与提醒分离:v1 纯本地零爬虫,提醒逻辑每次渲染时本地计算(零网络成本)。
- 提醒基于「行动日」:press_deadline(媒体注册截止,如有)否则 start_date。
  默认档位语义:T-60 申请 press accreditation;T-30 向参展企业发采访请求;T-14 最后窗口。
- needs_verification=true 的条目不参与提醒(避免基于错误日期提醒),
  在 UI 中显著标记 ⚠️,由用户人工核实(建议 AUMA 数据库)后转正。
- 严禁在代码或种子数据中构造、猜测任何 URL;source_url 只允许人工填入。

数据文件:
- events.json        活动数据,首次运行自动生成种子(纳入 git,同 sources.json)
- events_state.json  抓取状态时间戳(v2 用,gitignore,同 monitor_state.json)
"""

from __future__ import annotations

import concurrent.futures as cf
import hashlib
import html as html_mod
import json
import re
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

import feedparser
import pandas as pd
import requests
import streamlit as st

BERLIN = ZoneInfo("Europe/Berlin")
EVENTS_PATH = Path(__file__).with_name("events.json")
STATE_PATH = Path(__file__).with_name("events_state.json")

SECTOR_EMOJI = {"auto": "🚗", "energy": "☀️", "chemicals": "🧪", "policy": "🏛️", "thinktank": "🧠"}
SECTOR_ZH = {"auto": "汽车", "energy": "能源", "chemicals": "化工", "policy": "政策", "thinktank": "智库"}
STATUSES = ["todo", "registered", "interview_requested", "skipped", "done"]
STATUS_ZH = {"todo": "待处理", "registered": "已注册", "interview_requested": "已发采访请求",
             "skipped": "跳过", "done": "完成"}
ACTIVE_STATUSES = {"todo", "registered", "interview_requested"}
DEFAULT_OFFSETS = [60, 30, 14]
TIER_HINTS = {60: "申请 press accreditation", 30: "向参展企业发采访请求", 14: "最后窗口"}
AUMA_URL = "https://www.auma.de/messen-finden/"

# ---------------------------------------------------------------------------
# 种子数据 —— 日期与链接仅收录 2026-07 经主办方官网核实的条目;
# 其余 start_date/end_date/source_url 一律 null + needs_verification,
# 由用户人工核实后在「⚙️ 管理」视图填入(严禁代码自行猜测补全)。
# ---------------------------------------------------------------------------

SEED_EVENTS = {
    "schema_version": 1,
    "events": [
        {
            "id": "the-smarter-e-europe-2027",
            "name": "The Smarter E Europe (Intersolar / ees / Power2Drive / EM-Power)",
            "edition_year": 2027,
            "sector": "energy",
            "city": "München",
            "venue": "Messe München",
            "start_date": "2027-06-08",
            "end_date": "2027-06-10",
            "conference_start": "2027-06-07",
            "press_deadline": None,
            "action_status": "todo",
            "reminder_offsets": [60, 30, 14],
            "source_url": "https://www.thesmartere.de/upcoming-exhibition-dates",
            "notes": "每年6月,慕尼黑,四展同期。2026届为6月23-25日。会议在 ICM,2027年6月7-8日。"
                     "备用参考: https://www.intersolar.de/exhibition-quick-facts",
            "needs_verification": False,
            "last_checked": None,
        },
        {
            "id": "iaa-mobility-2027",
            "name": "IAA Mobility 2027",
            "edition_year": 2027,
            "sector": "auto",
            "city": "München",
            "venue": None,
            "start_date": None, "end_date": None, "conference_start": None, "press_deadline": None,
            "action_status": "todo",
            "reminder_offsets": [60, 30, 14],
            "source_url": None,
            "notes": "两年一届(奇数年),往届在9月初。",
            "needs_verification": True,
            "last_checked": None,
        },
        {
            "id": "hannover-messe-2027",
            "name": "Hannover Messe 2027",
            "edition_year": 2027,
            "sector": "energy",
            "city": "Hannover",
            "venue": None,
            "start_date": None, "end_date": None, "conference_start": None, "press_deadline": None,
            "action_status": "todo",
            "reminder_offsets": [60, 30, 14],
            "source_url": None,
            "notes": "每年4月前后(工业技术/能源)。",
            "needs_verification": True,
            "last_checked": None,
        },
        {
            "id": "e-world-2027",
            "name": "E-world energy & water 2027",
            "edition_year": 2027,
            "sector": "energy",
            "city": "Essen",
            "venue": None,
            "start_date": None, "end_date": None, "conference_start": None, "press_deadline": None,
            "action_status": "todo",
            "reminder_offsets": [60, 30, 14],
            "source_url": None,
            "notes": "每年2月。",
            "needs_verification": True,
            "last_checked": None,
        },
        {
            "id": "k-2028",
            "name": "K 2028",
            "edition_year": 2028,
            "sector": "chemicals",
            "city": "Düsseldorf",
            "venue": None,
            "start_date": None, "end_date": None, "conference_start": None, "press_deadline": None,
            "action_status": "todo",
            "reminder_offsets": [60, 30, 14],
            "source_url": None,
            "notes": "三年一届(化工/塑料)。",
            "needs_verification": True,
            "last_checked": None,
        },
        {
            "id": "automechanika-2028",
            "name": "Automechanika 2028",
            "edition_year": 2028,
            "sector": "auto",
            "city": "Frankfurt",
            "venue": None,
            "start_date": None, "end_date": None, "conference_start": None, "press_deadline": None,
            "action_status": "todo",
            "reminder_offsets": [60, 30, 14],
            "source_url": None,
            "notes": "两年一届(汽车后市场)。",
            "needs_verification": True,
            "last_checked": None,
        },
        {
            "id": "izb-2028",
            "name": "IZB 2028",
            "edition_year": 2028,
            "sector": "auto",
            "city": "Wolfsburg",
            "venue": None,
            "start_date": None, "end_date": None, "conference_start": None, "press_deadline": None,
            "action_status": "todo",
            "reminder_offsets": [60, 30, 14],
            "source_url": None,
            "notes": "两年一届(汽车供应商展)。",
            "needs_verification": True,
            "last_checked": None,
        },
        {
            "id": "merics-china-forecast-2027",
            "name": "MERICS China Forecast 2027",
            "edition_year": 2027,
            "sector": "thinktank",
            "city": "Berlin",
            "venue": None,
            "start_date": None, "end_date": None, "conference_start": None, "press_deadline": None,
            "action_status": "todo",
            "reminder_offsets": [21, 7],
            "source_url": None,
            "notes": "往届在1月中旬(2024届1月17日、2025届1月15日)。活动页: https://merics.org/en/events",
            "needs_verification": True,
            "last_checked": None,
        },
    ],
}


# ---------------------------------------------------------------------------
# 数据层
# ---------------------------------------------------------------------------

def load_events() -> dict:
    data = None
    if EVENTS_PATH.exists():
        try:
            data = json.loads(EVENTS_PATH.read_text(encoding="utf-8"))
            assert isinstance(data.get("events"), list)
        except Exception:
            # 与 sources.json 同策略:损坏文件先备份再重建,不静默覆盖
            try:
                EVENTS_PATH.rename(EVENTS_PATH.with_suffix(".json.bak"))
            except OSError:
                pass
            data = None
    if data is None:
        data = json.loads(json.dumps(SEED_EVENTS))
        save_events(data)
    return data


def save_events(data: dict) -> None:
    EVENTS_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _parse_date(s) -> date | None:
    if not s or not isinstance(s, str):
        return None
    try:
        return date.fromisoformat(s.strip())
    except ValueError:
        return None


def _today_berlin() -> date:
    return datetime.now(BERLIN).date()


def action_date(ev: dict) -> date | None:
    """行动日:press_deadline(媒体注册截止)优先,否则开展日。"""
    return _parse_date(ev.get("press_deadline")) or _parse_date(ev.get("start_date"))


def _is_confirmed(ev: dict) -> bool:
    return not ev.get("needs_verification", False) and action_date(ev) is not None


# ---------------------------------------------------------------------------
# 提醒计算(纯函数,便于测试)
# ---------------------------------------------------------------------------

def compute_reminders(events: list[dict], today: date) -> list[dict]:
    """返回已触发的提醒档位列表。

    触发条件:日期已核实、状态活跃(todo/registered/interview_requested)、
    行动日未过,且 today >= 行动日 - offset。
    返回项:{id, name, offset, trigger_date, action_date, days_left, hint}
    """
    out = []
    for ev in events:
        if not _is_confirmed(ev) or ev.get("action_status") not in ACTIVE_STATUSES:
            continue
        ad = action_date(ev)
        if ad < today:
            continue
        for offset in ev.get("reminder_offsets") or DEFAULT_OFFSETS:
            trigger = ad - timedelta(days=int(offset))
            if trigger <= today:
                out.append({
                    "id": ev["id"], "name": ev["name"], "offset": int(offset),
                    "trigger_date": trigger, "action_date": ad,
                    "days_left": (ad - today).days,
                    "hint": TIER_HINTS.get(int(offset), ""),
                })
    out.sort(key=lambda r: (r["action_date"], -r["offset"]))
    return out


def pending_reminder_count(today: date | None = None) -> int:
    """有已触发提醒的活动数(去重),供 tab 标题计数。"""
    try:
        events = load_events()["events"]
        reminders = compute_reminders(events, today or _today_berlin())
        return len({r["id"] for r in reminders})
    except Exception:
        return 0


def tab_label() -> str:
    # 标签必须恒定:st.tabs 按标签字符串记忆选中 tab,计数变化(如在 tab 内
    # 把活动标记完成)会让整个标签列表变化 → 用户被弹回第一个新闻 tab。
    # 待处理计数改在 tab 内部顶部显示(render_events_tab)。
    return "📅 活动"


def check_date_drift() -> None:
    """v2 桩:日期变更检测(本版不实现,勿在 v1 调用)。

    设计(见 events_tab_spec):打开应用时检查 events_state.json 的 last_fetch,
    超过 7 天才触发,不加入 launchd 定时。对每个含 source_url 的已核实活动,
    抓取该页面,检查已知 start_date 字符串(多种日期格式变体,如
    "8.6.2027" / "June 8" / "08.06.2027")是否仍出现;消失则仅将该活动标记
    needs_verification=true,由人工核对——**不**尝试自动解析出新日期,
    不自动改动任何字段。第二步(更远)再评估 ICS 日历订阅源与 icalendar 依赖。
    """
    raise NotImplementedError("v2 功能:日期漂移检测尚未实现")


# ---------------------------------------------------------------------------
# UI 辅助
# ---------------------------------------------------------------------------

def _fmt_range(ev: dict) -> str:
    s, e = ev.get("start_date"), ev.get("end_date")
    if not s:
        return "日期待核实"
    return f"{s} ~ {e}" if e and e != s else s


def _sector_tag(ev: dict) -> str:
    sec = ev.get("sector", "")
    return f"{SECTOR_EMOJI.get(sec, '📌')} {SECTOR_ZH.get(sec, sec)}"


def _urgency_badge(days_left: int) -> str:
    if days_left <= 14:
        return f"🔴 剩 {days_left} 天"
    if days_left <= 30:
        return f"🟡 剩 {days_left} 天"
    return f"🟢 剩 {days_left} 天"


def _unverified_warning(events: list[dict]) -> None:
    n = sum(1 for ev in events if ev.get("needs_verification"))
    if n:
        st.warning(f"⚠️ {n} 个活动日期未核实,不参与提醒。请到「⚙️ 管理」视图核实后"
                   f"取消勾选「待核实」(建议用 AUMA 展会数据库检索: {AUMA_URL})")


# ---------------------------------------------------------------------------
# 视图 1:行动倒计时
# ---------------------------------------------------------------------------

def _render_countdown(data: dict, today: date) -> None:
    events = data["events"]
    _unverified_warning(events)
    reminders = compute_reminders(events, today)
    triggered_ids = {r["id"] for r in reminders}

    rows = []
    for ev in events:
        if not _is_confirmed(ev) or ev.get("action_status") not in ACTIVE_STATUSES:
            continue
        ad = action_date(ev)
        if ad < today:
            continue
        days_left = (ad - today).days
        if days_left <= 90 or ev["id"] in triggered_ids:
            rows.append((ad, days_left, ev))
    rows.sort(key=lambda r: r[0])

    with st.expander("档位含义(默认 60/30/14 天)"):
        st.markdown("- **T-60** 申请 press accreditation\n"
                    "- **T-30** 向参展企业发采访请求\n"
                    "- **T-14** 最后窗口(酒店/行程/机位)")

    if not rows:
        st.info("90 天内没有需要行动的已核实活动。查看「🗓️ 年历」了解更远的安排,"
                "或到「⚙️ 管理」核实待定日期、添加新活动。")
        return

    for ad, days_left, ev in rows:
        with st.container(border=True):
            c1, c2 = st.columns([3, 1])
            with c1:
                st.markdown(f"**{SECTOR_EMOJI.get(ev.get('sector'), '📌')} {ev['name']}**")
                meta = f"{ev.get('city') or '?'} · 开展 {_fmt_range(ev)}"
                if ev.get("conference_start"):
                    meta += f" · 会议 {ev['conference_start']} 起"
                st.caption(meta)
                basis = "媒体注册截止" if _parse_date(ev.get("press_deadline")) else "开展日"
                line = f"{_urgency_badge(days_left)} · 行动日 {ad}({basis})"
                tiers = [r for r in reminders if r["id"] == ev["id"]]
                if tiers:
                    cur = min(tiers, key=lambda r: r["offset"])  # 已触发的最紧档位 = 当前阶段
                    hint = f" {cur['hint']}" if cur["hint"] else ""
                    line += f" · 当前档位 **T-{cur['offset']}**{hint}"
                st.markdown(line)
                if ev.get("source_url"):
                    st.caption(f"[官网信息页]({ev['source_url']})")
            with c2:
                new_status = st.selectbox(
                    "状态", STATUSES,
                    index=STATUSES.index(ev.get("action_status", "todo")),
                    format_func=lambda s: STATUS_ZH.get(s, s),
                    key=f"evst_{ev['id']}",
                )
                if new_status != ev.get("action_status"):
                    ev["action_status"] = new_status
                    save_events(data)
                    st.rerun()


# ---------------------------------------------------------------------------
# 视图 2:年历(未来 18 个月)
# ---------------------------------------------------------------------------

def _render_calendar(data: dict, today: date) -> None:
    events = data["events"]
    sectors = st.multiselect(
        "板块筛选", options=list(SECTOR_EMOJI),
        default=list(SECTOR_EMOJI),
        format_func=lambda s: f"{SECTOR_EMOJI[s]} {SECTOR_ZH[s]}",
    )
    picked = [ev for ev in events if ev.get("sector") in sectors]

    unverified = [ev for ev in picked if ev.get("needs_verification") or not _parse_date(ev.get("start_date"))]
    dated = [(ev, _parse_date(ev["start_date"])) for ev in picked
             if not ev.get("needs_verification") and _parse_date(ev.get("start_date"))]

    # 整月对齐的 18 个月窗口(按天数近似会把最后一个月拦腰截断,漏掉月内后半的活动)
    month_cursor = today.replace(day=1)
    _m = today.month - 1 + 18
    horizon = date(today.year + _m // 12, _m % 12 + 1, 1)  # 第 19 个月的 1 号(不含)
    months: dict[str, list] = {}
    for ev, sd in sorted(dated, key=lambda t: t[1]):
        if sd < month_cursor or sd >= horizon:
            continue
        months.setdefault(f"{sd.year}年{sd.month}月", []).append(ev)

    for month, evs in months.items():
        st.subheader(month)
        for ev in evs:
            status = ev.get("action_status", "todo")
            line = (f"{SECTOR_EMOJI.get(ev.get('sector'), '📌')} **{ev['name']}** — "
                    f"{ev.get('city') or '?'} · {_fmt_range(ev)} · {STATUS_ZH.get(status, status)}")
            if status == "skipped":
                st.caption(f"~~{ev['name']}~~ — {ev.get('city') or '?'} · {_fmt_range(ev)} · 跳过")
            else:
                st.markdown(line)
                if ev.get("notes"):
                    st.caption(ev["notes"])
    if not months:
        st.info("筛选范围内没有日期已核实的活动。")

    if unverified:
        st.subheader("📌 日期待核实")
        for ev in unverified:
            st.markdown(f"⚠️ {SECTOR_EMOJI.get(ev.get('sector'), '📌')} **{ev['name']}** — "
                        f"{ev.get('city') or '?'} · 日期待核实")
            if ev.get("notes"):
                st.caption(ev["notes"])


# ---------------------------------------------------------------------------
# 视图 3:管理(data_editor 全字段编辑 + 校验保存 + Markdown 导出)
# ---------------------------------------------------------------------------

_EDIT_COLS = ["id", "name", "edition_year", "sector", "city", "venue", "start_date",
              "end_date", "conference_start", "press_deadline", "action_status",
              "reminder_offsets", "source_url", "notes", "needs_verification", "last_checked"]


def _events_to_df(events: list[dict]) -> pd.DataFrame:
    rows = []
    for ev in events:
        row = {k: ev.get(k) for k in _EDIT_COLS}
        row["reminder_offsets"] = ",".join(str(o) for o in (ev.get("reminder_offsets") or DEFAULT_OFFSETS))
        rows.append(row)
    return pd.DataFrame(rows, columns=_EDIT_COLS)


def _cell(v):
    """data_editor 单元格 → 干净的 python 值(NaN/空串 → None)。"""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, str):
        v = v.strip()
        return v or None
    return v


def _validate_df(df: pd.DataFrame) -> tuple[list[dict], list[str]]:
    events, errors, seen_ids = [], [], set()
    for i, row in enumerate(df.to_dict("records"), start=1):
        ev = {k: _cell(row.get(k)) for k in _EDIT_COLS}
        rid = ev["id"] or ""
        label = ev["name"] or rid or f"第{i}行"
        if not rid:
            errors.append(f"{label}: id 不能为空(格式如 iaa-mobility-2027)")
            continue
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", rid):
            errors.append(f"{label}: id 只能用小写字母/数字/连字符")
        if rid in seen_ids:
            errors.append(f"{label}: id 重复 ({rid})")
        seen_ids.add(rid)
        if not ev["name"]:
            errors.append(f"{rid}: name 不能为空")
        try:
            ev["edition_year"] = int(ev["edition_year"])
            assert 2020 <= ev["edition_year"] <= 2100
        except Exception:
            errors.append(f"{label}: edition_year 须为 2020-2100 的年份")
        if ev["sector"] not in SECTOR_EMOJI:
            errors.append(f"{label}: sector 须为 {'/'.join(SECTOR_EMOJI)}")
        if ev["action_status"] not in STATUSES:
            errors.append(f"{label}: action_status 须为 {'/'.join(STATUSES)}")
        for f in ("start_date", "end_date", "conference_start", "press_deadline"):
            if ev[f]:
                d = _parse_date(ev[f])
                if d is None:
                    errors.append(f"{label}: {f} 须为 YYYY-MM-DD 或留空")
                else:
                    ev[f] = d.isoformat()  # 归一化(fromisoformat 也接受 20270608 等变体)
        sd, ed = _parse_date(ev["start_date"]), _parse_date(ev["end_date"])
        if sd and ed and ed < sd:
            errors.append(f"{label}: end_date 早于 start_date")
        try:
            offs = [int(x) for x in str(row.get("reminder_offsets") or "").replace(" ", "").split(",") if x]
            assert offs and all(o > 0 for o in offs)
            ev["reminder_offsets"] = sorted(set(offs), reverse=True)
        except Exception:
            errors.append(f"{label}: 提醒档位须为正整数(逗号分隔,如 60,30,14)")
        if ev["source_url"] and not re.match(r"^https?://", ev["source_url"]):
            errors.append(f"{label}: source_url 须以 http(s):// 开头(只做格式校验,不自动补全)")
        ev["needs_verification"] = bool(row.get("needs_verification"))
        if sd is None:
            ev["needs_verification"] = True  # 无开展日的活动一律视为待核实
        events.append(ev)
    return events, errors


def _export_markdown(events: list[dict], today: date) -> str:
    lines = [f"# 活动清单 · {today}", ""]
    by_sector: dict[str, list[dict]] = {}
    for ev in events:
        by_sector.setdefault(ev.get("sector", "?"), []).append(ev)
    for sec in SECTOR_EMOJI:
        evs = by_sector.get(sec)
        if not evs:
            continue
        lines.append(f"## {SECTOR_EMOJI[sec]} {SECTOR_ZH[sec]}")
        evs.sort(key=lambda e: (_parse_date(e.get("start_date")) or date.max))
        for ev in evs:
            flag = " ⚠️日期待核实" if ev.get("needs_verification") else ""
            status = STATUS_ZH.get(ev.get("action_status", "todo"), "")
            url = f" · [官网]({ev['source_url']})" if ev.get("source_url") else ""
            lines.append(f"- **{_fmt_range(ev)}** {ev['name']} — "
                         f"{ev.get('city') or '?'} · {status}{flag}{url}")
        lines.append("")
    return "\n".join(lines)


def _render_manage(data: dict, today: date) -> None:
    st.caption("直接编辑表格(底部可加新行),点「保存」写入 events.json。"
               "日期格式 YYYY-MM-DD;无开展日的条目自动视为「待核实」,不参与提醒。"
               f"日期核实建议用 [AUMA 展会数据库]({AUMA_URL}),核实后填入日期并取消勾选「待核实」。")
    df = _events_to_df(data["events"])
    edited = st.data_editor(
        df, num_rows="dynamic", hide_index=True, key="events_editor",
        column_config={
            "id": st.column_config.TextColumn("id", help="唯一 slug:活动名-年份", required=True),
            "name": st.column_config.TextColumn("活动名", required=True, width="large"),
            "edition_year": st.column_config.NumberColumn("届次年份", min_value=2020, max_value=2100, step=1),
            "sector": st.column_config.SelectboxColumn("板块", options=list(SECTOR_EMOJI)),
            "city": st.column_config.TextColumn("城市"),
            "venue": st.column_config.TextColumn("场馆"),
            "start_date": st.column_config.TextColumn("开展日", help="YYYY-MM-DD,未核实留空"),
            "end_date": st.column_config.TextColumn("闭展日"),
            "conference_start": st.column_config.TextColumn("会议开始"),
            "press_deadline": st.column_config.TextColumn("媒体注册截止", help="留空则提醒基于开展日"),
            "action_status": st.column_config.SelectboxColumn("状态", options=STATUSES),
            "reminder_offsets": st.column_config.TextColumn("提醒档位(天)", help="逗号分隔,如 60,30,14"),
            "source_url": st.column_config.TextColumn("信息页 URL", help="仅人工填入,不自动补全"),
            "notes": st.column_config.TextColumn("备注", width="large"),
            "needs_verification": st.column_config.CheckboxColumn("待核实", help="勾选时不参与提醒"),
            "last_checked": st.column_config.TextColumn("上次核对", disabled=True),
        },
    )
    # 未保存修改提示:data_editor 的编辑状态在切换子视图时会被 Streamlit 清掉
    _ed_state = st.session_state.get("events_editor") or {}
    if any(_ed_state.get(k) for k in ("edited_rows", "added_rows", "deleted_rows")):
        st.warning("⚠️ 有未保存的修改 — 切换视图或关闭页面会丢失,请先点「💾 保存」")

    if _n := st.session_state.pop("ev_saved_n", None):
        st.success(f"已保存 {_n} 个活动")

    c1, c2 = st.columns(2)
    with c1:
        if st.button("💾 保存到 events.json"):
            events, errors = _validate_df(edited)
            if errors:
                for e in errors:
                    st.error(e)
            else:
                data["events"] = events
                save_events(data)
                # st.rerun() 会吞掉本次 st.success → 用 flag 留到下一轮显示
                st.session_state["ev_saved_n"] = len(events)
                st.rerun()
    with c2:
        st.download_button("⬇️ 导出 Markdown 清单",
                           _export_markdown(data["events"], today),
                           file_name=f"events_{today}.md")


# ---------------------------------------------------------------------------
# 📡 发现 —— 会议/论坛公告雷达(v2)
# 渠道清单来自 2026-07 多机构实测:德国主要机构(Kiel/DIHK/MERICS/SWP/DGAP)
# 均无活动 RSS,但活动页多为服务端渲染 → 三类渠道:
#   rss   活动型 feed(ECFR/DIW/Politico Live/DG TRADE,实测确认含活动条目)
#   gnews 机构名查询(被墙机构的唯一通道;多数需德语区 locale)
#   page  页面变化侦测:抓取纯文本行,与基线比对,新增的中国相关行 → 提示
# 原则不变:任何发现只进候选收件箱,由人工「加入跟踪」;不自动写库、不猜日期。
# ---------------------------------------------------------------------------

EVENT_SOURCES_PATH = Path(__file__).with_name("event_sources.json")
DISCOVERY_TTL_DAYS = 3          # 打开发现视图时,距上次扫描超过此天数自动重扫
_GNEWS = {
    "en": "https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en",
    "de": "https://news.google.com/rss/search?q={q}&hl=de&gl=DE&ceid=DE:de",
}
_HEADERS = {  # Asia Society / euagenda 等站点要求完整浏览器头,仅 UA 会 403
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.8,de;q=0.6",
    "Sec-Fetch-Site": "none", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document",
}

# relevance: china=须含中国词 | beat=中国词或条线词(贸易/稀土/芯片…) | all=整页/整源皆相关
DEFAULT_EVENT_SOURCES = {
    "schema_version": 1,
    "channels": [
        # --- 活动型 RSS(实测确认条目为活动) ---
        {"id": "ecfr-events", "name": "ECFR · 活动 feed", "type": "rss",
         "value": "https://ecfr.eu/feed/?post_type=event", "relevance": "china", "sector": "thinktank", "enabled": True},
        {"id": "diw-events", "name": "DIW Berlin · 活动 feed", "type": "rss",
         "value": "https://www.diw.de/de/rss_events.xml", "relevance": "china", "sector": "thinktank", "enabled": True},
        {"id": "politico-live", "name": "Politico Live · 活动 feed", "type": "rss",
         "value": "https://www.politico.eu/events/feed/", "relevance": "beat", "sector": "policy", "enabled": True},
        {"id": "dgtrade-events", "name": "DG TRADE · 活动 feed", "type": "rss",
         "value": "https://policy.trade.ec.europa.eu/node/5/rss_en", "relevance": "all", "sector": "policy", "enabled": True},
        {"id": "euiss-rss", "name": "EUISS · feed(混合)", "type": "rss",
         "value": "https://www.iss.europa.eu/rss.xml", "relevance": "china", "sector": "thinktank", "enabled": True},
        {"id": "gmf-rss", "name": "GMF · feed(混合)", "type": "rss",
         "value": "https://www.gmfus.org/rss.xml", "relevance": "china", "sector": "thinktank", "enabled": True},
        # --- 页面变化侦测(服务端渲染,实测可抓) ---
        {"id": "kiel-gcc", "name": "Kiel · Global China Conversations", "type": "page",
         "value": "https://www.kielinstitut.de/events/global-china-conversations/", "relevance": "all", "sector": "thinktank", "enabled": True},
        {"id": "kiel-events", "name": "Kiel Institut · 活动页", "type": "page",
         "value": "https://www.kielinstitut.de/events/", "relevance": "china", "sector": "thinktank", "enabled": True},
        {"id": "dihk-newsroom", "name": "DIHK · Newsroom", "type": "page",
         "value": "https://www.dihk.de/de/newsroom", "relevance": "china", "sector": "policy", "enabled": True},
        {"id": "merics-events", "name": "MERICS · 活动页", "type": "page",
         "value": "https://merics.org/en/events", "relevance": "all", "sector": "thinktank", "enabled": True},
        {"id": "dgap-events", "name": "DGAP · 活动页", "type": "page",
         "value": "https://dgap.org/en/events", "relevance": "china", "sector": "thinktank", "enabled": True},
        {"id": "ahk-china", "name": "AHK 大中华区 · Events Hub", "type": "page",
         "value": "https://china.ahk.de/en/events-hub", "relevance": "all", "sector": "policy", "enabled": True},
        {"id": "bruegel-events", "name": "Bruegel · 活动页", "type": "page",
         "value": "https://www.bruegel.org/events", "relevance": "china", "sector": "thinktank", "enabled": True},
        {"id": "ceps-events", "name": "CEPS · 活动页", "type": "page",
         "value": "https://www.ceps.eu/ceps-events/", "relevance": "china", "sector": "thinktank", "enabled": True},
        {"id": "cer-events", "name": "CER · 活动页", "type": "page",
         "value": "https://www.cer.eu/events", "relevance": "china", "sector": "thinktank", "enabled": True},
        {"id": "apa-events", "name": "APA(亚太委员会/APK)· 活动页", "type": "page",
         "value": "https://asien-pazifik-ausschuss.de/en/events/", "relevance": "all", "sector": "policy", "enabled": True},
        {"id": "dcw-events", "name": "DCW 德中经济联合会 · 活动日历", "type": "page",
         "value": "https://www.dcw-ev.de/de/veranstaltungen.html", "relevance": "all", "sector": "policy", "enabled": True},
        {"id": "oav-events", "name": "OAV 东亚协会 · 活动页", "type": "page",
         "value": "https://www.oav.de/veranstaltungen/", "relevance": "china", "sector": "policy", "enabled": True},
        {"id": "gmf-berlin", "name": "GMF · 柏林活动页", "type": "page",
         "value": "https://www.gmfus.org/events?location[12]=12", "relevance": "china", "sector": "thinktank", "enabled": True},
        {"id": "euractiv-events", "name": "EURACTIV · 活动日历", "type": "page",
         "value": "https://events.euractiv.com/", "relevance": "china", "sector": "policy", "enabled": True},
        {"id": "asiasociety-ch", "name": "Asia Society Switzerland · 活动页", "type": "page",
         "value": "https://asiasociety.org/switzerland/events", "relevance": "china", "sector": "thinktank", "enabled": False},
        {"id": "fes-events", "name": "FES · 活动页", "type": "page",
         "value": "https://www.fes.de/veranstaltungen", "relevance": "china", "sector": "thinktank", "enabled": False},
        # --- gnews(被墙机构与通用雷达;de = 德语区) ---
        {"id": "gn-kiel", "name": "Kiel Institute(gnews)", "type": "gnews",
         "value": '"Kiel Institute" China when:30d', "locale": "en", "relevance": "all", "sector": "thinktank", "enabled": True},
        {"id": "gn-dihk", "name": "DIHK China(gnews·德)", "type": "gnews",
         "value": "DIHK China when:30d", "locale": "de", "relevance": "all", "sector": "policy", "enabled": True},
        {"id": "gn-merics", "name": "MERICS(gnews·德)", "type": "gnews",
         "value": "MERICS China when:30d", "locale": "de", "relevance": "all", "sector": "thinktank", "enabled": True},
        {"id": "gn-swp", "name": "SWP(gnews·德)", "type": "gnews",
         "value": '"Stiftung Wissenschaft und Politik" China when:30d', "locale": "de", "relevance": "all", "sector": "thinktank", "enabled": True},
        {"id": "gn-koerber", "name": "Körber(gnews·德)", "type": "gnews",
         "value": '"Körber-Stiftung" (China OR "Berlin Foreign Policy Forum") when:60d', "locale": "de", "relevance": "all", "sector": "thinktank", "enabled": True},
        {"id": "gn-kas", "name": "KAS(gnews·德)", "type": "gnews",
         "value": '"Konrad-Adenauer-Stiftung" China when:30d', "locale": "de", "relevance": "all", "sector": "thinktank", "enabled": True},
        {"id": "gn-chatham", "name": "Chatham House(gnews)", "type": "gnews",
         "value": '"Chatham House" China when:30d', "locale": "en", "relevance": "all", "sector": "thinktank", "enabled": True},
        {"id": "gn-euccc", "name": "中国欧盟商会(gnews)", "type": "gnews",
         "value": '"European Chamber of Commerce in China" when:30d', "locale": "en", "relevance": "all", "sector": "policy", "enabled": True},
        {"id": "gn-apk", "name": "APK 亚太大会(gnews·德)", "type": "gnews",
         "value": '"Asien-Pazifik-Konferenz" (Wirtschaft OR APK OR BDI) when:90d', "locale": "de", "relevance": "all", "sector": "policy", "enabled": True},
        {"id": "gn-inta", "name": "欧洲议会 INTA(gnews)", "type": "gnews",
         "value": '"European Parliament" INTA China when:30d', "locale": "en", "relevance": "all", "sector": "policy", "enabled": True},
        # 通用雷达:gnews 按正文匹配查询词,须再加标题级中国闸门(relevance=china),
        # 否则 "AfD kicks off conference" 这类正文提中国的无关标题会漏进来(实测)
        {"id": "gn-de-generic", "name": "德语通用雷达(intitle:China)", "type": "gnews",
         "value": "intitle:China (Konferenz OR Forum OR Gipfel OR Tagung OR Kongress) (Berlin OR Frankfurt OR Deutschland) when:14d", "locale": "de", "relevance": "china", "sector": "policy", "enabled": True},
        {"id": "gn-en-generic", "name": "英语通用雷达(Berlin/Brussels)", "type": "gnews",
         "value": "(conference OR forum OR summit) China (Berlin OR Brussels) when:14d", "locale": "en", "relevance": "china", "sector": "policy", "enabled": True},
        {"id": "gn-thinktank", "name": "智库活动雷达(gnews)", "type": "gnews",
         "value": "Bruegel OR MERICS OR ECFR China (event OR conference OR roundtable) when:30d", "locale": "en", "relevance": "all", "sector": "thinktank", "enabled": True},
        {"id": "gn-kammer", "name": "商会邀请雷达(gnews·德)", "type": "gnews",
         "value": '"China" ("lädt ein" OR Anmeldung OR Veranstaltung OR Konferenz) (DIHK OR IHK OR BDI OR Handelskammer) when:30d', "locale": "de", "relevance": "china", "sector": "policy", "enabled": True},
    ],
}

# 中国相关(候选/页面行的相关性闸门,四语)
_CHINA_HINTS = [" china", " chine", " chines", " chinois", " sino", " peking", " beijing",
                " pékin", " taiwan", " hongkong", " hong kong", "中国", "中德", "中欧", "中法",
                "北京", "台湾", "稀土", " byd ", " catl ", "huawei", "greater china",
                "deutsch-chinesisch", "德中", "中方"]
# 条线相关(relevance=beat 的补充词)
_BEAT_HINTS = _CHINA_HINTS + [" trade", " tariff", " zoll", "handels", "export control",
                              "exportkontroll", "rare earth", "seltene erden", "semiconductor",
                              "halbleiter", " chip", "battery", "batterie", " ev ", "solar",
                              "competitiveness", "wettbewerbsfähigkeit", "economic security",
                              "wirtschaftssicherheit", "supply chain", "lieferkette", "de-risking"]
# 活动词(gnews 候选须在标题中含活动词,过滤普通报道)
_EVENT_HINTS = ["conference", "forum", "summit", "roundtable", "briefing", "webinar", "workshop",
                "symposium", "hearing", "panel", "lecture", " talk", "dialogue", " dialog",
                "konferenz", "tagung", "gipfel", "kongress", "veranstaltung", "podium",
                "diskussion", "vortrag", "einladung", "lädt ein", "anmeldung", "colloque",
                "sommet", "论坛", "峰会", "研讨会", "讲座", "年会"]


def _lnorm(text: str) -> str:
    text = re.sub(r"[^\w\s]", " ", text.lower())
    return " " + " ".join(text.split()) + " "


def _hit(text_norm: str, hints: list[str]) -> bool:
    return any(h in text_norm for h in hints)


def load_event_sources() -> dict:
    if EVENT_SOURCES_PATH.exists():
        try:
            data = json.loads(EVENT_SOURCES_PATH.read_text(encoding="utf-8"))
            assert isinstance(data.get("channels"), list)
            return data
        except Exception:
            pass
    data = json.loads(json.dumps(DEFAULT_EVENT_SOURCES))
    EVENT_SOURCES_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def _load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    try:
        STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass


def _page_lines(html_text: str, relevance: str) -> list[str]:
    """HTML → 规整文本行(标签边界即断行),按相关性过滤。"""
    text = re.sub(r"<(script|style|noscript)[^>]*>.*?</\1>", " ", html_text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", "\n", text)
    text = html_mod.unescape(text)
    hints = {"china": _CHINA_HINTS, "beat": _BEAT_HINTS}.get(relevance)
    out, seen = [], set()
    for raw in text.splitlines():
        line = " ".join(raw.split())
        if not (15 <= len(line) <= 220) or line in seen:
            continue
        if hints and not _hit(_lnorm(line), hints):
            continue
        seen.add(line)
        out.append(line)
    return out[:120]


def _fetch_channel(ch: dict) -> dict:
    """抓取单个发现渠道 → {id, ok, candidates|lines, error}。线程池内运行。"""
    out = {"id": ch["id"], "name": ch["name"], "type": ch["type"], "ok": False,
           "candidates": [], "lines": [], "error": ""}
    try:
        if ch["type"] == "page":
            r = requests.get(ch["value"], headers=_HEADERS, timeout=(6, 15))
            r.raise_for_status()
            out["lines"] = _page_lines(r.text, ch.get("relevance", "china"))
            out["ok"] = True
            return out
        if ch["type"] == "gnews":
            url = _GNEWS.get(ch.get("locale", "en"), _GNEWS["en"]).format(q=quote_plus(ch["value"]))
        else:
            url = ch["value"]
        # feed 抓取只带 UA:完整浏览器头(Sec-Fetch 等)会让部分 RSS 端点 403;
        # 部分站点(实测 DIW)反而封浏览器 UA、放行阅读器 UA → 403 时用阅读器标识重试
        r = requests.get(url, headers={"User-Agent": _HEADERS["User-Agent"]}, timeout=(6, 15))
        if r.status_code == 403:
            r = requests.get(url, headers={"User-Agent": "ChinaEUMonitor/1.0 (RSS reader; private research use)"},
                             timeout=(6, 15))
        r.raise_for_status()
        parsed = feedparser.parse(r.content)
        hints = {"china": _CHINA_HINTS, "beat": _BEAT_HINTS}.get(ch.get("relevance", "china"))
        for e in parsed.entries[:40]:
            title = (getattr(e, "title", "") or "").strip()
            link = (getattr(e, "link", "") or "").strip()
            if not title or not link:
                continue
            if ch["type"] == "gnews":
                title = re.sub(r"\s+-\s+[^-]+$", "", title).strip()  # 去媒体名后缀
                if not _hit(_lnorm(title), _EVENT_HINTS):
                    continue  # gnews 候选须含活动词,过滤普通报道
            nt = _lnorm(title)
            if hints and not _hit(nt, hints):
                continue
            pub = ""
            t = getattr(e, "published_parsed", None) or getattr(e, "updated_parsed", None)
            if t:
                pub = f"{t.tm_year}-{t.tm_mon:02d}-{t.tm_mday:02d}"
            out["candidates"].append({"title": title, "link": link, "published": pub,
                                      "channel": ch["name"], "sector": ch.get("sector", "policy")})
        out["ok"] = True
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"{type(exc).__name__}: {str(exc)[:120]}"
    return out


def scan_discovery(channels: list[dict], state: dict) -> dict:
    """扫描全部启用渠道,更新 state(候选、页面基线/新增行、健康、时间戳)。"""
    enabled = [c for c in channels if c.get("enabled", True)]
    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(_fetch_channel, enabled))

    dismissed = set(state.get("dismissed", []))
    known_links = {ev.get("source_url") for ev in load_events()["events"] if ev.get("source_url")}
    snapshots = state.get("page_snapshots", {})
    alerts = state.get("page_alerts", {})
    candidates, health = [], []

    for r in results:
        health.append({"name": r["name"], "ok": r["ok"], "error": r["error"],
                       "n": len(r["candidates"]) or len(r["lines"])})
        if not r["ok"]:
            continue
        if r["type"] == "page":
            base = set(snapshots.get(r["id"], {}).get("lines", []))
            if not base:  # 首次:建基线,不报警
                snapshots[r["id"]] = {"lines": r["lines"], "checked": date.today().isoformat()}
                continue
            fresh = [l for l in r["lines"] if l not in base
                     and hashlib.sha1(l.encode()).hexdigest()[:12] not in dismissed]
            merged = list(dict.fromkeys(alerts.get(r["id"], {}).get("lines", []) + fresh))[:30]
            if merged:
                alerts[r["id"]] = {"name": r["name"], "url": next(
                    c["value"] for c in enabled if c["id"] == r["id"]), "lines": merged}
            snapshots[r["id"]] = {"lines": r["lines"], "checked": date.today().isoformat()}
        else:
            for c in r["candidates"]:
                if c["link"] in dismissed or c["link"] in known_links:
                    continue
                candidates.append(c)

    # 跨渠道按标题去重(同一活动常被多个 gnews 查询同时命中)
    seen_t, unique = set(), []
    for c in sorted(candidates, key=lambda x: x.get("published", ""), reverse=True):
        key = re.sub(r"\W+", "", c["title"].lower())[:70]
        if key in seen_t:
            continue
        seen_t.add(key)
        unique.append(c)

    state.update(candidates=unique[:80], page_snapshots=snapshots, page_alerts=alerts,
                 discovery_health=health, last_scan=datetime.now(timezone.utc).isoformat())
    _save_state(state)
    return state


def _slugify(title: str, fallback: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:48].strip("-")
    return s or f"event-{fallback}"


def _add_candidate_as_event(cand: dict) -> str:
    """候选 → needs_verification 活动条目(不猜日期;source_url 来自公告链接本身)。"""
    data = load_events()
    year = int(cand["published"][:4]) if cand.get("published") else _today_berlin().year
    base_id = _slugify(cand["title"], hashlib.sha1(cand["link"].encode()).hexdigest()[:8])
    ev_id, n = base_id, 2
    existing = {e["id"] for e in data["events"]}
    while ev_id in existing:
        ev_id, n = f"{base_id}-{n}", n + 1
    data["events"].append({
        "id": ev_id, "name": cand["title"], "edition_year": year,
        "sector": cand.get("sector", "policy"), "city": None, "venue": None,
        "start_date": None, "end_date": None, "conference_start": None, "press_deadline": None,
        "action_status": "todo", "reminder_offsets": [21, 7],
        "source_url": cand["link"],
        "notes": f"来自发现雷达 · {cand['channel']} · {cand.get('published', '')}",
        "needs_verification": True, "last_checked": None,
    })
    save_events(data)
    return ev_id


def _render_discovery(today: date) -> None:
    src = load_event_sources()
    state = _load_state()
    last = state.get("last_scan")
    stale = True
    if last:
        try:
            stale = (datetime.now(timezone.utc) - datetime.fromisoformat(last)) > timedelta(days=DISCOVERY_TTL_DAYS)
        except ValueError:
            pass

    c1, c2 = st.columns([3, 1])
    with c1:
        n_ch = sum(1 for c in src["channels"] if c.get("enabled", True))
        st.caption(f"{n_ch} 个公告渠道 · 上次扫描: "
                   f"{last[:16].replace('T', ' ') + ' UTC' if last else '从未'}"
                   f"{' · 已超 ' + str(DISCOVERY_TTL_DAYS) + ' 天,建议重扫' if last and stale else ''}")
    with c2:
        rescan = st.button("🔍 立即扫描", use_container_width=True)
    if rescan or stale:  # stale 含「从未扫描」;打开视图即自动补扫
        with st.spinner(f"正在扫描 {sum(1 for c in src['channels'] if c.get('enabled', True))} 个渠道 …"):
            state = scan_discovery(src["channels"], state)
        if rescan:
            st.rerun()

    # --- 页面变化提示 ---
    alerts = {k: v for k, v in state.get("page_alerts", {}).items() if v.get("lines")}
    if alerts:
        st.subheader(f"📄 页面新增内容 ({len(alerts)})")
        st.caption("以下机构活动页出现了上次扫描时没有的中国相关内容——点链接人工确认,确认后「标记已读」。")
        for cid, al in alerts.items():
            with st.expander(f"⚠️ {al['name']} — 新增 {len(al['lines'])} 行", expanded=True):
                for l in al["lines"]:
                    st.markdown(f"- {l}")
                cc1, cc2 = st.columns([1, 1])
                with cc1:
                    st.markdown(f"[打开页面核实]({al['url']})")
                with cc2:
                    if st.button("✓ 标记已读", key=f"ack_{cid}"):
                        state["page_alerts"].pop(cid, None)
                        _save_state(state)
                        st.rerun()

    # --- 公告候选 ---
    cands = state.get("candidates", [])
    st.subheader(f"📰 公告候选 ({len(cands)})")
    if not cands and not alerts:
        st.info("暂无新发现。渠道会在打开本视图时自动重扫(间隔 "
                f"{DISCOVERY_TTL_DAYS} 天),也可点「立即扫描」。")
    for i, cand in enumerate(cands[:40]):
        with st.container(border=True):
            cc1, cc2 = st.columns([4, 1])
            with cc1:
                st.markdown(f"**[{cand['title']}]({cand['link']})**")
                st.caption(f"{cand['channel']} · {cand.get('published') or '日期未知'}")
            with cc2:
                key = hashlib.sha1(cand["link"].encode()).hexdigest()[:12]
                if st.button("➕ 跟踪", key=f"add_{key}"):
                    ev_id = _add_candidate_as_event(cand)
                    state["candidates"] = [c for c in state.get("candidates", []) if c["link"] != cand["link"]]
                    _save_state(state)
                    st.session_state["disc_msg"] = f"已加入跟踪(待核实): {ev_id} — 请到「⚙️ 管理」核实日期"
                    st.rerun()
                if st.button("✕ 忽略", key=f"dis_{key}"):
                    state.setdefault("dismissed", []).append(cand["link"])
                    state["candidates"] = [c for c in state.get("candidates", []) if c["link"] != cand["link"]]
                    _save_state(state)
                    st.rerun()
    if msg := st.session_state.pop("disc_msg", None):
        st.success(msg)

    # --- 渠道健康 ---
    health = state.get("discovery_health", [])
    if health:
        n_bad = sum(1 for h in health if not h["ok"])
        with st.expander(f"📡 渠道状态 {'🔴 ' + str(n_bad) + ' 个失败' if n_bad else '🟢 全部正常'}"):
            for h in health:
                dot = "🟢" if h["ok"] else "🔴"
                st.caption(f"{dot} {h['name']} — {h['n']} 条" + (f" · {h['error']}" if h["error"] else ""))


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def render_events_tab() -> None:
    data = load_events()
    today = _today_berlin()
    n_pending = len({r["id"] for r in compute_reminders(data["events"], today)})
    if n_pending:
        st.markdown(f"🔔 **{n_pending} 个活动有待处理提醒**")
    view = st.radio("视图", ["⏰ 行动倒计时", "🗓️ 年历", "📡 发现", "⚙️ 管理"],
                    horizontal=True, label_visibility="collapsed", key="ev_view")
    if view.startswith("⏰"):
        _render_countdown(data, today)
    elif view.startswith("🗓️"):
        _render_calendar(data, today)
    elif view.startswith("📡"):
        _render_discovery(today)
    else:
        _render_manage(data, today)
