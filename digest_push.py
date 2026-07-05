"""
定时摘要推送(由 launchd 在 07:00 / 12:00 调用,也可手动运行)
================================================================
复用 china_europe_monitor.py 的抓取/聚类/摘要逻辑(动态加载其 UI 之前的部分,
不重复实现,词表改动自动生效),生成 Markdown + HTML 摘要:

  digests/digest_YYYYMMDD_HHMM.md / .html

然后弹 macOS 系统通知,并在默认浏览器打开 HTML 摘要。

用法:
  .venv/bin/python digest_push.py --hours 13          # 07:00 早报(覆盖前晚 18 点起)
  .venv/bin/python digest_push.py --hours 6           # 12:00 午报
  .venv/bin/python digest_push.py --hours 13 --no-open  # 只生成不打开浏览器
"""
from __future__ import annotations

import argparse
import html as html_mod
import json
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parent
APP = REPO / "china_europe_monitor.py"
DIGEST_DIR = REPO / "digests"
BERLIN = ZoneInfo("Europe/Berlin")
DASHBOARD_URL = "http://localhost:8501"


def load_monitor_logic() -> dict:
    """加载监测台 UI 之前的全部逻辑(源配置、词表、抓取、聚类、摘要)。"""
    src = APP.read_text(encoding="utf-8")
    logic = src.split("\n# 5. UI")[0]
    logic = logic.replace("import streamlit as st", "st = None")
    logic = re.sub(r"@st\.cache_data\([^)]*\)\n", "", logic)  # 去掉缓存装饰器,直接调用
    ns: dict = {"__file__": str(APP)}
    exec(compile(logic, str(APP), "exec"), ns)
    return ns


def md_to_html(md: str) -> str:
    """摘要专用的极简 Markdown→HTML(标题/列表/链接/粗体),无第三方依赖。"""
    link_re = re.compile(r"\[([^\]]+)\]\((https?://[^)]+)\)")
    bold_re = re.compile(r"\*\*([^*]+)\*\*")
    out, in_list = [], False
    for line in md.splitlines():
        esc = html_mod.escape(line, quote=False)
        esc = link_re.sub(lambda m: f'<a href="{m.group(2)}" target="_blank">{html_mod.escape(m.group(1))}</a>',
                          line if not line.startswith("#") else esc)
        esc = bold_re.sub(r"<b>\1</b>", esc)
        if line.startswith("- "):
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{esc[2:]}</li>")
            continue
        if in_list:
            out.append("</ul>")
            in_list = False
        if line.startswith("## "):
            out.append(f"<h2>{esc[3:]}</h2>")
        elif line.startswith("# "):
            out.append(f"<h1>{esc[2:]}</h1>")
        elif line.startswith("_") and line.endswith("_"):
            out.append(f"<p><i>{esc.strip('_')}</i></p>")
        elif line.strip():
            out.append(f"<p>{esc}</p>")
    if in_list:
        out.append("</ul>")
    body = "\n".join(out)
    return f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<title>中欧监测摘要</title>
<style>
 body{{font-family:-apple-system,'PingFang SC',sans-serif;max-width:860px;margin:2rem auto;
      padding:0 1rem;line-height:1.55;color:#1a1a1a}}
 h1{{font-size:1.4rem}} h2{{font-size:1.1rem;margin-top:1.6rem;border-bottom:1px solid #ddd;
      padding-bottom:.2rem}}
 li{{margin:.45rem 0}} a{{color:#0b5cad;text-decoration:none}} a:hover{{text-decoration:underline}}
 .dash{{display:inline-block;margin-top:1.2rem;padding:.5rem .9rem;background:#0b5cad;color:#fff;
      border-radius:6px}}
 @media(prefers-color-scheme:dark){{body{{background:#111;color:#ddd}}
      a{{color:#7ab8f5}} h2{{border-color:#333}}}}
</style></head><body>
{body}
<a class="dash" href="{DASHBOARD_URL}">打开完整监测台 →</a>
</body></html>"""


def notify(title: str, text: str) -> None:
    try:
        subprocess.run(
            ["osascript", "-e",
             f'display notification "{text}" with title "{title}" sound name "Glass"'],
            check=False, timeout=10,
        )
    except Exception:
        pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=int, default=13)
    ap.add_argument("--no-open", action="store_true")
    args = ap.parse_args()

    ns = load_monitor_logic()
    sources = ns["load_sources"]()

    # Mac 刚唤醒时网络可能还没就绪:全部源失败则等 25 秒重试一次
    for attempt in (1, 2):
        clusters, health, n_items = ns["fetch_all"](json.dumps(sources), args.hours)
        if any(h["ok"] for h in health) or attempt == 2:
            break
        time.sleep(25)

    digest_md = ns["build_digest"](clusters, args.hours)
    n_bad = sum(1 for h in health if not h["ok"])
    hot_n = sum(1 for c in clusters if c["breaking"] or c["diversity"] >= 2)

    DIGEST_DIR.mkdir(exist_ok=True)
    stamp = f"{datetime.now(BERLIN):%Y%m%d_%H%M}"
    md_path = DIGEST_DIR / f"digest_{stamp}.md"
    html_path = DIGEST_DIR / f"digest_{stamp}.html"
    md_path.write_text(digest_md, encoding="utf-8")
    html_path.write_text(md_to_html(digest_md), encoding="utf-8")

    notify("中欧监测摘要",
           f"过去{args.hours}h:{len(clusters)} 组报道,{hot_n} 条重点"
           + (f"({n_bad} 个源抓取失败)" if n_bad else ""))
    if not args.no_open:
        subprocess.run(["open", str(html_path)], check=False)
    print(f"[{stamp}] {len(clusters)} clusters / {n_items} items / {hot_n} hot / "
          f"{n_bad} bad sources -> {html_path.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
