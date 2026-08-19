"""共享:执行 china_europe_monitor.py 的纯逻辑前半段(不进 streamlit 页面渲染)。"""

import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


@pytest.fixture(scope="session")
def head_ns():
    src = (REPO / "china_europe_monitor.py").read_text(encoding="utf-8")
    # 切到「UI 段之前」= 纯逻辑层。用正则锚定章节编号而非注释原文,
    # 免得改一句文档就打断整个测试套(2026-08 踩过:章节注释一改,16 个测试报错)。
    m = re.search(r"^# \d+\. .*摘要导出", src, re.M)
    head = src[: m.start()]
    ns = {"__file__": str(REPO / "china_europe_monitor.py")}
    exec(compile(head, "china_europe_monitor_head", "exec"), ns)  # noqa: S102
    return ns
