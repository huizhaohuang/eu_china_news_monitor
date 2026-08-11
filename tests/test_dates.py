"""中文本地化 RFC822 日期解析(芥末堆式 pubDate 曾导致整源静默零条)。"""

from datetime import datetime, timezone
from types import SimpleNamespace


def test_chinese_localized_pubdate(head_ns):
    entry = SimpleNamespace(published="星期三, 05 八月 2026 19:18:00 GMT")
    ts = head_ns["_entry_time"](entry)
    assert ts == datetime(2026, 8, 5, 19, 18, 0, tzinfo=timezone.utc)


def test_chinese_weekday_variants(head_ns):
    for wd in ("周五", "星期五"):
        entry = SimpleNamespace(published=f"{wd}, 12 十二月 2025 08:00:00 +0800")
        ts = head_ns["_entry_time"](entry)
        assert ts is not None and ts.day == 12 and ts.month == 12


def test_existing_formats_still_work(head_ns):
    f = head_ns["_entry_time"]
    assert f(SimpleNamespace(published="Jul 03, 2026")).month == 7
    assert f(SimpleNamespace(published="2026-08-01T10:00:00+00:00")).day == 1
    assert f(SimpleNamespace(published="garbage")) is None
    assert f(SimpleNamespace()) is None
