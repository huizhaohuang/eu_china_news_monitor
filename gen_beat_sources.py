"""生成 BEAT_SOURCES.md —— 按条线分组的监控源人工审校清单。

用法: .venv/bin/python gen_beat_sources.py
数据源: source_registry.json + packs.json;registry 改动后重跑即可再生成。
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parent

REGION_ZH = {"cn": "🇨🇳 中国国内", "cn-eu": "🤝 中欧", "cn-us": "🇺🇸 中美", "cross": "🌐 跨区域"}
REGION_ORDER = ["cn", "cn-eu", "cn-us", "cross"]
TYPE_ZH = {"gov": "官方", "wechat-mirror": "公众号镜像", "mainstream": "主流媒体",
           "industry": "行业媒体", "wires": "通讯社", "thinktank": "智库"}


def main() -> None:
    reg = json.loads((REPO / "source_registry.json").read_text(encoding="utf-8"))
    packs = json.loads((REPO / "packs.json").read_text(encoding="utf-8"))

    by_domain: dict[str, list[dict]] = defaultdict(list)
    for s in reg["sources"]:
        for d in s.get("domains", []):
            by_domain[d].append(s)

    def row(s: dict, current_domain: str) -> str:
        flag = "✅" if s.get("enabled", True) else "⏸"
        val = s["value"].replace("|", "\\|")
        if len(val) > 60:
            val = val[:57] + "…"
        others = [d for d in s.get("domains", []) if d not in (current_domain, "general")]
        note = (s.get("caveat") or "").replace("|", "\\|")[:70]
        extra = f"亦属:{','.join(others)}" if others else ""
        note = f"{extra} {note}".strip()
        return (f"| {flag} | {s['name']} | {TYPE_ZH.get(s['source_type'], s['source_type'])} "
                f"| {s['type']} | {s.get('lang', '')} | `{val}` | {note} |")

    def section(sources: list[dict], current_domain: str) -> list[str]:
        out = []
        by_region: dict[str, list[dict]] = defaultdict(list)
        for s in sources:
            by_region[s["region"]].append(s)
        for r in REGION_ORDER:
            ss = by_region.get(r)
            if not ss:
                continue
            ss.sort(key=lambda s: (not s.get("enabled", True), s["source_type"], s["name"]))
            n_on = sum(1 for s in ss if s.get("enabled", True))
            out.append(f"\n**{REGION_ZH[r]}**({n_on} 启用 / {len(ss)} 在库)\n")
            out.append("| 状态 | 源 | 类型 | 抓法 | 语言 | 抓取语句 | 备注 |")
            out.append("|---|---|---|---|---|---|---|")
            out.extend(row(s, current_domain) for s in ss)
        return out

    lines = [
        "# 各条线监控源清单(人工审校版)",
        "",
        f"> 生成于 {date.today()},数据源 `source_registry.json`({len(reg['sources'])} 条)。",
        "> **改法**:直接在本文件里批注——划掉不要的、在备注列写意见、或列出想加的媒体名,"
        "我按批注改注册表后重跑 `gen_beat_sources.py` 再生成本文件。",
        "> 状态:✅ = 已启用(实际在抓) ⏸ = 在库未启用(观察期,说一声即可开)。",
        "> 抓法:rss = 原生 feed;gnews = Google News 查询(语句见抓取语句列)。",
        "",
        "## 总览",
        "",
        "| 条线 | 启用 | 在库 | 中国国内 | 中欧 | 中美 | 跨区域 |",
        "|---|---|---|---|---|---|---|",
    ]

    pack_order = [p["id"] for p in packs["packs"]]
    for pid in pack_order:
        p = next(x for x in packs["packs"] if x["id"] == pid)
        ss = by_domain.get(pid, [])
        n_on = sum(1 for s in ss if s.get("enabled", True))
        rc = {r: sum(1 for s in ss if s["region"] == r) for r in REGION_ORDER}
        lines.append(f"| {p['emoji']} {p['zh']} | {n_on} | {len(ss)} "
                     f"| {rc['cn']} | {rc['cn-eu']} | {rc['cn-us']} | {rc['cross']} |")
    gen = by_domain.get("general", [])
    n_on = sum(1 for s in gen if s.get("enabled", True))
    rc = {r: sum(1 for s in gen if s["region"] == r) for r in REGION_ORDER}
    lines.append(f"| 🌐 背景快讯层(不分条线) | {n_on} | {len(gen)} "
                 f"| {rc['cn']} | {rc['cn-eu']} | {rc['cn-us']} | {rc['cross']} |")

    for pid in pack_order:
        p = next(x for x in packs["packs"] if x["id"] == pid)
        ss = by_domain.get(pid, [])
        lines.append(f"\n---\n\n## {p['emoji']} {p['zh']} `{pid}`")
        if not ss:
            lines.append("\n(该条线暂无专线源,内容全部来自背景层按词表筛出)")
            continue
        lines.extend(section(ss, pid))

    lines.append("\n---\n\n## 🌐 背景快讯层 `general`")
    lines.append("\n所有人共享的综合报道层,**跟随所选区域**(勾了中欧才会出现德法/欧盟媒体);"
                 "在定制页可整体关闭。")
    lines.extend(section(gen, "general"))

    # 休眠域:源在库但条线已下线(如 consumer→互联网),留档可复活
    pack_ids = set(pack_order) | {"general"}
    dormant: dict[str, list[dict]] = defaultdict(list)
    for d, ss in by_domain.items():
        if d not in pack_ids:
            for s in ss:
                dormant[d].append(s)
    if dormant:
        lines.append("\n---\n\n## 🗃 休眠条线源(条线已下线,源保留待复活)")
        for d, ss in dormant.items():
            lines.append(f"\n### `{d}`")
            lines.extend(section(ss, d))

    out = REPO / "BEAT_SOURCES.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"已生成 {out.name}:{len(reg['sources'])} 条源 → "
          f"{len(pack_order)} 条线 + 背景层,{len(lines)} 行")


if __name__ == "__main__":
    main()
