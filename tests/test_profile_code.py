"""p3 配置码编解码测试:往返一致、长度预算、损坏兜底、条线沿革映射。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import profile_code as pc


def test_roundtrip_basic():
    cfg = {"regions": ["cn-eu"], "domains": ["trade-tariff", "diplomacy"],
           "source_types": ["gov", "thinktank"]}
    code = pc.encode(cfg)
    assert code.startswith("p3.")
    out, warns = pc.decode(code)
    assert warns == []
    assert out["regions"] == ["cn-eu"]
    assert set(out["domains"]) == {"trade-tariff", "diplomacy"}
    assert set(out["source_types"]) == {"gov", "thinktank"}
    assert out["background"] is True


def test_full_config_under_600_chars():
    cfg = {
        "regions": ["cn-eu", "cn"],
        "domains": ["macro", "central-bank", "trade-tariff", "export-control",
                    "semiconductor", "ai", "nev", "battery"],
        "source_types": ["gov", "wechat-mirror", "industry", "thinktank"],
        "background": False,
        "keywords": ["留学签证", "稀土", "Nexperia", "中欧班列", "集采", "国谈",
                     "出口管制", "补贴调查", "产能过剩", "锂电池", "光伏组件", "碳关税"],
        "entities": ["宁德时代", "比亚迪", "华为", "中远海运", "国家电投", "万科",
                     "冯德莱恩", "商务部", "工信部", "发改委", "海关总署", "央行",
                     "证监会", "医保局", "药监局", "欧盟委员会", "大众汽车", "宝马",
                     "博世", "西门子"],
    }
    code = pc.encode(cfg)
    assert len(code) <= 600, f"配置码超预算: {len(code)}"
    out, warns = pc.decode(code)
    assert warns == []
    assert out["background"] is False
    assert len(out["keywords"]) == 12
    assert len(out["entities"]) == 20


def test_empty_config():
    out, warns = pc.decode(pc.encode({}))
    assert warns == []
    assert out == {"regions": [], "domains": [], "source_types": [],
                   "background": True, "keywords": [], "entities": []}


def test_truncation_caps():
    cfg = {"keywords": [f"kw{i}" for i in range(30)],
           "entities": [f"ent{i}" for i in range(40)]}
    out, _ = pc.decode(pc.encode(cfg))
    assert len(out["keywords"]) == pc.MAX_KEYWORDS
    assert len(out["entities"]) == pc.MAX_ENTITIES


def test_tampered_code_fails_gracefully():
    code = pc.encode({"regions": ["cn"]})
    out, warns = pc.decode(code[:-6] + "XXXXXX")
    assert out is None
    assert warns and "无法解析" in warns[0]


def test_truncated_code_fails_gracefully():
    code = pc.encode({"regions": ["cn"], "domains": ["macro"]})
    out, warns = pc.decode(code[: len(code) // 2])
    assert out is None


def test_wrong_version_rejected():
    code = pc.encode({"regions": ["cn"]})
    out, warns = pc.decode("p4." + code[3:])
    assert out is None
    assert "p4" in warns[0]


def test_garbage_inputs():
    for bad in ["", "p3.", "hello", "p3", None, "p3.!!!!"]:
        out, warns = pc.decode(bad)
        assert out is None
        assert warns


def test_tombstone_espionage_removed_with_notice():
    # 手工构造含已移除条线的旧码
    import base64, json, zlib
    obj = {"v": 3, "r": ["cn-eu"], "d": ["espionage", "diplomacy"]}
    raw = json.dumps(obj, separators=(",", ":")).encode()
    code = "p3." + base64.urlsafe_b64encode(zlib.compress(raw)).decode().rstrip("=")
    out, warns = pc.decode(code)
    assert out is not None
    assert out["domains"] == ["diplomacy"]
    assert any("espionage" in w and "移除" in w for w in warns)


def test_unknown_domain_dropped_with_notice():
    import base64, json, zlib
    obj = {"v": 3, "d": ["macro", "no-such-beat"]}
    raw = json.dumps(obj, separators=(",", ":")).encode()
    code = "p3." + base64.urlsafe_b64encode(zlib.compress(raw)).decode().rstrip("=")
    out, warns = pc.decode(code)
    assert out["domains"] == ["macro"]
    assert any("no-such-beat" in w for w in warns)
