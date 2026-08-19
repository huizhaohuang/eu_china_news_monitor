"""黄金基线:锁死改造前的现状(2026-08-08 冻结)。

P0 的每一步重构(词表去全局化 / 缓存拆分 / 条目级过滤)都必须在
本文件全绿的前提下合入 —— 逻辑层指纹逐字节相等,应用层无异常且
标签页结构不变。指纹只在**有意**改动词表/源清单时才允许更新,
更新时必须在提交信息里写明原因。

  运行: .venv/bin/python -m pytest tests/test_baseline.py -v
  跳过联网 e2e: SKIP_E2E=1 .venv/bin/python -m pytest ...
"""

import hashlib
import json
import os
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

# ---- 冻结值(2026-08-08) ----
TAXONOMY_FP = "1f0a5c8b6b9d1ef66b476172054f5d094648d348"
# 2026-08-18 有意变更:默认台新增 FreightWaves(用户点名;filter=china 只进涉华物流稿)
SOURCES_FP = "fbc190efc5626ec1e86eca26e4df8c90a7cfa208"
SOURCES_TOTAL = 72
SOURCES_ENABLED = 57
CATEGORY_IDS = ["china-eu-politics", "germany-china", "france-china",
                "trade-defence", "autos-ev", "energy-solar",
                "tech-ai-chips", "defence-materials", "china-macro"]
# 2026-08-08 有意变更:💬 定制 tab 下线(用户裁定,勾选式定制 ?setup=1 取代)
TAB_PREFIXES = ["⚡ 重点", "🏛️ 中欧政治外交", "🇩🇪 德中关系", "🇫🇷 法中关系",
                "⚖️ 贸易防御与经济安全", "🚗 汽车与电池", "☀️ 能源与光伏",
                "🤖 科技·AI·芯片", "🛡️ 国防与战略原材料", "📊 中国宏观(参考)",
                "📋 全部"]


def _exec_head():
    """执行 china_europe_monitor.py 的纯逻辑前半段(不进 streamlit 页面渲染)。"""
    src = (REPO / "china_europe_monitor.py").read_text(encoding="utf-8")
    head = src[: src.index("# 4. 摘要导出")]
    ns = {"__file__": str(REPO / "china_europe_monitor.py")}
    exec(compile(head, "china_europe_monitor_head", "exec"), ns)  # noqa: S102
    return ns


def test_taxonomy_fingerprint_frozen():
    ns = _exec_head()
    payload = [ns["CATEGORIES"], ns["SPECIFICITY"], ns["BREAKING_KEYWORDS"],
               ns["PRIORITY_PAIRS"], ns["CHINA_GATE"], ns["EUROPE_GATE"],
               ns["TITLE_BLOCKLIST"]]
    fp = hashlib.sha1(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                 default=list).encode()).hexdigest()
    assert fp == TAXONOMY_FP, "词表指纹变了 —— 若是有意改动请更新冻结值并在提交里说明"


def test_category_ids_and_order():
    ns = _exec_head()
    assert [c["id"] for c in ns["CATEGORIES"]] == CATEGORY_IDS


def test_sources_inventory_frozen():
    sources = json.loads((REPO / "sources.json").read_text(encoding="utf-8"))
    assert len(sources) == SOURCES_TOTAL
    assert sum(1 for s in sources if s.get("enabled", True)) == SOURCES_ENABLED
    fp = hashlib.sha1(json.dumps(sorted(s["value"] for s in sources),
                                 ensure_ascii=False).encode()).hexdigest()
    assert fp == SOURCES_FP, "源清单变了 —— 若是有意改动请更新冻结值并在提交里说明"


def test_personal_config_paths_not_tracked():
    """零落盘断言的本地版(CI 版在 .github/workflows/privacy-guard.yml)。"""
    import subprocess
    tracked = subprocess.run(["git", "ls-files"], cwd=REPO, capture_output=True,
                             text=True, check=True).stdout.splitlines()
    bad = [p for p in tracked
           if p.startswith("profiles/") or p.endswith(".profile.json")
           or Path(p).name == "subscribers.json"]
    assert not bad, f"个人配置文件进入了 git: {bad}"
    gitignore = (REPO / ".gitignore").read_text(encoding="utf-8").splitlines()
    for pat in ("profiles/", "subscribers.json", "*.profile.json"):
        assert pat in gitignore, f".gitignore 缺少 {pat}"


@pytest.mark.skipif(os.environ.get("SKIP_E2E") == "1", reason="跳过联网 e2e")
def test_app_boots_and_tab_structure():
    """端到端:真实抓取全部源(约 60-90 秒,需联网)。"""
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(REPO / "china_europe_monitor.py"), default_timeout=120)
    at.run()
    assert not at.exception, f"应用抛异常: {at.exception}"
    assert at.title and at.title[0].value == "📰 News Monitor!"
    labels = [re.sub(r"\s+\d+$", "", t.label) for t in at.tabs]
    assert labels == TAB_PREFIXES, f"标签页结构变了: {labels}"
