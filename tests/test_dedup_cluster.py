"""重复消息修复:CJK 二元组聚类 / 链接归一去重 / 降权媒体(汽车之家)。"""

from datetime import datetime, timedelta, timezone


def _item(ns, title, link="https://x.com/a", outlet="A", minutes=0):
    return {"source": outlet, "lane": "industry", "stype": "", "lang": "zh",
            "outlet": outlet, "title": title, "summary": "", "link": link,
            "time": datetime(2026, 8, 12, 8, 0, tzinfo=timezone.utc) + timedelta(minutes=minutes),
            "cats": [], "primary": None, "breaking": False, "tokens": ns["_tokens"](title)}


def test_cjk_tokens_are_bigrams(head_ns):
    tk = head_ns["_tokens"]("中汽协:7月汽车销量下降")
    assert "中汽" in tk and "汽协" in tk and "汽车" in tk   # 字符二元组
    tk2 = head_ns["_tokens"]("BYD earnings beat expectations")
    assert "byd" in tk2 and "earnings" in tk2              # 拉丁词规则不变


def test_same_release_variants_cluster(head_ns):
    # 实测漏网样例:同一份中汽协通稿,两家转写标题小改 → 必须合并
    a = _item(head_ns, "中汽协:7月汽车国内销量154.1万辆 同比下降23.6%", "https://a.com/1", "观点网")
    b = _item(head_ns, "中汽协:7月汽车国内销量环比下降13.1% 同比下降23.6%", "https://b.com/2", "Sohu", 5)
    assert len(head_ns["cluster_items"]([a, b])) == 1


def test_different_zh_stories_do_not_merge(head_ns):
    a = _item(head_ns, "长安汽车7月销量21万辆创新高", "https://a.com/1", "A")
    b = _item(head_ns, "比亚迪欧洲建厂选址匈牙利尘埃落定", "https://b.com/2", "B", 5)
    c = _item(head_ns, "宁德时代发布麒麟电池三代续航突破", "https://c.com/3", "C", 9)
    assert len(head_ns["cluster_items"]([a, b, c])) == 3


def test_short_template_titles_do_not_merge(head_ns):
    # 实测错并样例:不同指数的快讯模板,短标题必须用更严阈值分开
    a = _item(head_ns, "科创50指数涨幅扩大至2%", "https://a.com/1", "第一财经")
    b = _item(head_ns, "韩国综合指数涨幅扩大至5%", "https://b.com/2", "第一财经", 3)
    assert len(head_ns["cluster_items"]([a, b])) == 2


def test_identical_short_titles_still_merge(head_ns):
    a = _item(head_ns, "全国蔬菜供应总体形势平稳", "https://a.com/1", "央视网")
    b = _item(head_ns, "全国蔬菜供应总体形势平稳", "https://b.com/2", "中国经济网", 3)
    assert len(head_ns["cluster_items"]([a, b])) == 1


def test_canon_link(head_ns):
    f = head_ns["_canon_link"]
    assert f("https://www.cls.cn/detail/123/") == f("https://m.cls.cn/detail/123")
    assert f("https://a.com/x?utm_source=rss&utm_medium=feed") == f("https://a.com/x")
    assert f("https://a.com/x?id=9&utm_source=t") == "a.com/x?id=9"  # 非跟踪参数保留
    assert f("not a url") == "not a url"                              # 不抛


def test_demoted_outlet_not_rep_and_not_in_diversity(head_ns):
    # 汽车之家首发,财联社后到:代表应是财联社;独立报道数不含汽车之家
    a = _item(head_ns, "新款问界M8登陆工信部公告四款车型覆盖增程纯电", "https://autohome.com.cn/1", "汽车之家", 0)
    b = _item(head_ns, "问界M8登陆工信部公告四款车型覆盖增程纯电动力", "https://cls.cn/2", "财联社", 10)
    cl = head_ns["cluster_items"]([a, b])[0]
    assert cl["rep"]["outlet"] == "财联社"
    assert cl["n"] == 2                       # 条目保留(降权≠剔除)
    assert cl["diversity"] == 1               # 汽车之家不计独立报道
    assert cl["items"][0]["outlet"] == "汽车之家"  # 首发时间序保持


def test_demoted_solo_cluster_still_shows(head_ns):
    a = _item(head_ns, "小蓝灯被叫停但它并不会消失", "https://autohome.com.cn/9", "汽车之家")
    cl = head_ns["cluster_items"]([a])[0]
    assert cl["rep"]["outlet"] == "汽车之家"   # 只有它一家时正常展示
