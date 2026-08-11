"""📮 首页反馈渠道 —— 使用者直接把意见送到维护者的外部落点。

云端(Streamlit Community Cloud)无持久磁盘:休眠/重部署即清空本地文件,
所以反馈**必须**送到外部 sink。sink 由 st.secrets 配置,三选一(按优先级):

  [feedback]
  webhook_url = "..."      # 通用 POST JSON —— Slack/Discord/飞书/Google Apps Script/Formspree 均可
  # 或
  resend_api_key = "re_..." # Resend 邮件 API
  email = "you@example.com" # 收件邮箱
  from = "onboarding@resend.dev"  # 选填,默认 Resend 测试发件人

都没配 → 落到本地 feedback.jsonl(仅本机开发有效,已 gitignore;云端会丢,
表单会诚实提示"未配置")。密钥只进 Secrets,永不进公开仓库。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import requests
import streamlit as st

LOCAL_SINK = Path(__file__).with_name("feedback.jsonl")
_TIMEOUT = 10


def _secret(*path: str):
    """安全读取嵌套 secret;未配置或无 secrets 文件时返回 None,绝不抛。"""
    try:
        node = st.secrets
        for k in path:
            node = node[k]
        return str(node) if node else None
    except Exception:
        return None


def _summary(p: dict) -> str:
    who = p.get("who") or "匿名"
    ctx = p.get("context") or "默认监测台"
    return (f"📮 监测台反馈\n"
            f"来自:{who}\n"
            f"页面:{ctx}\n"
            f"时间:{p.get('time', '')}\n"
            f"—— \n{p.get('message', '')}")


def _append_local(p: dict) -> None:
    try:
        with LOCAL_SINK.open("a", encoding="utf-8") as f:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")
    except Exception:
        pass


def deliver(payload: dict) -> tuple[bool, str]:
    """把反馈送到已配置的外部 sink。返回 (是否送达外部, 给用户看的话)。
    任何 sink 失败都兜底到本地并如实告知,绝不假装成功。"""
    webhook = _secret("feedback", "webhook_url")
    if webhook:
        try:
            text = _summary(payload)
            # text=Slack / content=Discord / feedback=结构化(Apps Script/Formspree)——一份 body 通吃常见 webhook
            r = requests.post(webhook, json={"text": text, "content": text, "feedback": payload},
                              timeout=_TIMEOUT)
            r.raise_for_status()
            return True, "✅ 已提交,谢谢!"
        except Exception:
            _append_local(payload)
            return False, "⚠️ 反馈通道暂时不通,已本地留存。请稍后再试或直接联系维护者。"

    key, to = _secret("feedback", "resend_api_key"), _secret("feedback", "email")
    if key and to:
        try:
            r = requests.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {key}"},
                json={"from": _secret("feedback", "from") or "onboarding@resend.dev",
                      "to": [to],
                      "subject": f"[监测台反馈] {payload.get('who') or '匿名'} · {payload.get('context') or '默认'}",
                      "text": _summary(payload)},
                timeout=_TIMEOUT)
            r.raise_for_status()
            return True, "✅ 已提交,谢谢!"
        except Exception:
            _append_local(payload)
            return False, "⚠️ 邮件通道暂时不通,已本地留存。请稍后再试或直接联系维护者。"

    # 未配置任何外部 sink
    _append_local(payload)
    return False, "✅ 已记录(本地)。⚠️ 管理员尚未配置反馈通道,云端不会送达 —— 见 feedback.py 顶部说明。"


def render(context: str = "") -> None:
    """首页反馈入口(折叠面板,默认收起,不占版面)。context = 当前页面标识。"""
    with st.expander("📮 反馈 / 报告问题 —— 源不对?太吵?想加条线或公众号?"):
        if st.session_state.pop("_fb_done", None):
            st.success(st.session_state.pop("_fb_msg", "已提交,谢谢!"))
        with st.form("feedback_form", clear_on_submit=True):
            who = st.text_input("你是谁(选填,方便回复)", placeholder="如 Mandy / 汽车线")
            msg = st.text_area("反馈内容", height=120,
                               placeholder="哪个源太吵?想加哪个号/条线?哪个 tag 不准?看到明显跑错的稿子,贴标题最有用。")
            sent = st.form_submit_button("提交反馈")
        if sent:
            if not msg.strip():
                st.warning("写点内容再提交～")
            else:
                _, note = deliver({
                    "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "who": who.strip(),
                    "context": context or "默认监测台",
                    "message": msg.strip(),
                })
                st.session_state["_fb_done"] = True
                st.session_state["_fb_msg"] = note
                st.rerun()
