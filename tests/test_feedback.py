"""反馈送达:无外部 sink 时兜底本地 + 不假装送达成功。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import feedback


def test_no_sink_falls_back_local_and_is_honest(monkeypatch):
    monkeypatch.setattr(feedback, "_secret", lambda *a: None)
    captured = {}
    monkeypatch.setattr(feedback, "_append_local", lambda p: captured.update(p))
    ok, note_key = feedback.deliver({"who": "Mandy", "context": "汽车线", "message": "某源太吵"})
    assert ok is False                      # 未送达外部,绝不返回 True
    assert note_key == "fb_unconfigured"    # 消息键(显示时经 i18n 翻译)
    assert captured["who"] == "Mandy"       # 内容落到本地兜底


def test_summary_contains_fields():
    s = feedback._summary({"who": "", "context": "", "message": "hi", "time": "t"})
    assert "匿名" in s and "默认监测台" in s and "hi" in s
