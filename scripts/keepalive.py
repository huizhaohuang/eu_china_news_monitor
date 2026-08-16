"""Streamlit Community Cloud 保活:无头浏览器真实访问 + 遇休眠页点唤醒。

为什么不能用 curl/UptimeRobot:平台判"活跃"看 WebSocket 会话,纯 HTTP GET
返回 200 也不算访问(2026-02 实证:35 个 URL 全 200,app 照样睡)。必须真实
浏览器执行 JS 建立 /_stcore/stream 连接。休眠阈值 12h(2025-03 起),
GitHub Actions 每 4h 跑一次留 3 倍冗余(Actions cron 不保证准点)。

用法: python scripts/keepalive.py <app_url>
依赖: pip install playwright && playwright install chromium
"""

from __future__ import annotations

import re
import sys
import time

from playwright.sync_api import sync_playwright

WAKE_RE = re.compile(r"get this app back up", re.I)
APP_READY_SELECTOR = "[data-testid='stApp'], .stApp, [data-testid='stAppViewContainer']"


def keep_alive(url: str) -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_timeout(5_000)  # 让 JS 起跑

        # 休眠页?点唤醒按钮(按钮可能在主页面或 iframe 里,都找一遍)
        woke = False
        for frame in [page, *page.frames]:
            try:
                btn = frame.get_by_role("button", name=WAKE_RE)
                if btn.count():
                    btn.first.click()
                    woke = True
                    break
                el = frame.get_by_text(WAKE_RE)
                if el.count():
                    el.first.click()
                    woke = True
                    break
            except Exception:
                continue

        if woke:
            print("发现休眠页,已点击唤醒,等待冷启动 …")
            page.wait_for_timeout(90_000)

        # 等 Streamlit 前端就绪(= WebSocket 已建立,本次访问计入活跃)。
        # streamlit.app 托管把真实应用渲染在 /~/+/ 子 iframe 里 —— 必须扫全部 frame。
        deadline = time.monotonic() + 120
        ready = False
        while time.monotonic() < deadline and not ready:
            for frame in page.frames:
                try:
                    if frame.locator(APP_READY_SELECTOR).count():
                        ready = True
                        break
                except Exception:
                    continue
            if not ready:
                page.wait_for_timeout(3_000)
        if ready:
            page.wait_for_timeout(10_000)  # 保持会话几秒,确保被计为真实访问
            print(f"✅ app 在线(woke={woke}) {time.strftime('%F %T')}")
            rc = 0
        else:
            print(f"❌ 等待 app 就绪超时(woke={woke})——可能唤醒失败或 URL 不对")
            rc = 1
        browser.close()
        return rc


if __name__ == "__main__":
    if len(sys.argv) != 2 or not sys.argv[1].startswith("http"):
        print("用法: python scripts/keepalive.py <app_url>")
        sys.exit(2)
    sys.exit(keep_alive(sys.argv[1]))
