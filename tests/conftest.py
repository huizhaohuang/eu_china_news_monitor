"""共享:执行 china_europe_monitor.py 的纯逻辑前半段(不进 streamlit 页面渲染)。"""

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


@pytest.fixture(scope="session")
def head_ns():
    src = (REPO / "china_europe_monitor.py").read_text(encoding="utf-8")
    head = src[: src.index("# 4. 摘要导出")]
    ns = {"__file__": str(REPO / "china_europe_monitor.py")}
    exec(compile(head, "china_europe_monitor_head", "exec"), ns)  # noqa: S102
    return ns
