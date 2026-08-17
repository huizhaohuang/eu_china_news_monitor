# China–Europe News Monitor

[![tests](https://github.com/huizhaohuang/eu_china_news_monitor/actions/workflows/tests.yml/badge.svg)](https://github.com/huizhaohuang/eu_china_news_monitor/actions/workflows/tests.yml)
[![privacy-guard](https://github.com/huizhaohuang/eu_china_news_monitor/actions/workflows/privacy-guard.yml/badge.svg)](https://github.com/huizhaohuang/eu_china_news_monitor/actions/workflows/privacy-guard.yml)

A self-serve news monitor built for a China-desk of journalists (macro, finance, tech,
autos, energy, health, education, shipping …). One shared deployment; every reporter
assembles their own monitoring page in ~60 seconds and saves it as a bookmark.

**Live app:** <https://china-eu-monitor.streamlit.app/> · UI in Chinese and English (`?lang=en`)

**Scale (2026-08):** a vetted registry of **415 sources** (283 active) tagged across
**16 beat packs** × 3 region tiers (China domestic / China–EU / China–US, with
China–DE / China–FR / Hong Kong sub-tiers) × 6 source types — in Chinese, English,
German and French, including WeChat-account mirror channels. The default landing page
is the original China–EU monitor: 71 curated sources, 9 themed tabs, unchanged.

## Quick start

```bash
pip install -r requirements.txt
streamlit run china_europe_monitor.py
```

First run creates `sources.json` (source config, editable in the sidebar) and
`monitor_state.json` (last-visit timestamp for 🆕 badges) next to the script.

## What it does

| Feature | Notes |
|---|---|
| ⚡ Top tab | Breaking-signal keywords (sanctions, tariff rulings, raids, summonses … zh/en/de) + stories covered by ≥2 independent outlets, last 12h. In custom mode, pinned to *your* beats only |
| Beat tabs | Multi-label classification — one story can appear under several relevant beats. Keyword/entity scoring is deterministic (kw=1, entity=3, title×2, ≥2 to qualify) |
| Cross-source clustering | Same-story variants merge into one card (first-mover outlet shown, other takes in a fold-out). CJK-aware: Chinese titles cluster via character bigrams; near-duplicate wire rewrites collapse instead of flooding the feed |
| Link decoding | Google News redirect links are resolved server-side to real publisher URLs — so readers in mainland China can actually open them (budgeted, circuit-broken, silently falls back to the original link) |
| Source-type filter | Inside every tab: Official / Mainstream / Trade media / WeChat mirrors / Newswires / Think tanks, with live counts |
| Outlet demotion | Low-quality outlets (journalist-flagged) never front a cluster and don't count toward "independent outlets" — demoted, not deleted |
| 📋 Digest | One-click Markdown digest (grouped by beat, Berlin timestamps), downloadable |
| 📮 Feedback | Front-page feedback widget delivering to the maintainer's inbox (Resend / webhook via `st.secrets`) |
| Relevance gates | European general feeds must mention China; Chinese broad feeds must mention Europe; pre-scoped queries pass through |

## Self-serve customization (`?setup=1`)

Three steps — **regions → beats → source types** — then one click generates a personal
monitoring page. The entire config lives in the URL (`?profile=p3.<code>`, ≤600 chars):

- **bookmark = save, share = share** — nothing is ever written server-side;
- supply badges are honest: beats with no dedicated sources for your regions say
  "0→fallback" up front instead of silently under-delivering;
- old config codes survive beat renames/merges via an alias table (with a notice).

## Data & privacy

The repo is public; the boundary is **"menu public, orders private"**:

| In this repo (the menu) | Never in the repo (the orders) |
|---|---|
| Code, beat vocabularies (`packs.json`), source registry (`source_registry.json`) — generic knowledge, no personal data | Personal beat selections, custom keywords, watchlist entities, subscriber emails, API keys |

Enforced three ways: `.gitignore`, a CI assertion
([privacy-guard](.github/workflows/privacy-guard.yml)) that fails any commit containing
personal-config paths, and — by design — the app has no code path that writes personal
config into the repo directory (configs serialize only to URL params and download buttons).

> **Note on internal docs:** design/strategy documents (`DESIGN.md`, `SOURCE_TREE.md`,
> `WECHAT_SOURCES.md`, `ASSESSMENT.md`, `BEAT_SOURCES.md`) are deliberately gitignored
> local working files — references to them in code comments or history will 404 for
> outside readers. The privacy summary above is the public digest of that material.

## Source config (`sources.json` / `source_registry.json`)

```jsonc
{
  "name": "Reuters · China×Europe",
  "type": "gnews",            // rss = native feed; gnews = Google News RSS query
  "value": "site:reuters.com (China OR Chinese) (EU OR Europe ...) when:1d",
  "lane": "wires",            // sidebar grouping
  "lang": "en",               // en | de | fr | zh
  "filter": "china",          // relevance gate: china | europe | none
  "gnews_locale": "zh",       // gnews language region — see trap below
  "enabled": true
}
```

`source_registry.json` (the customization catalog) adds `region`, `source_type`,
`domains` (beat tags) and `tier` per source. `gen_beat_sources.py` renders it into a
human-review inventory.

**Field-tested traps** (all verified, do not relearn them the hard way):

- **A `gnews` query for a Chinese/French/German site MUST set `gnews_locale`** —
  the English locale returns 200 with 0 items, silently.
- Localized RFC-822 dates (`星期三, 05 八月 2026 …`) break feedparser; the app
  normalizes them — without this a healthy-looking feed yields zero items.
- `site:` queries against `.gov.cn` return decade-old archive pages; count only
  entry-level fresh items.
- Politico.eu / Euractiv native RSS are Cloudflare-blocked; Reuters / Bloomberg / WSJ
  have no public RSS — use gnews `site:` queries. Most mainland-China native RSS is
  dead or frozen (live exceptions: China News Service, CGTN, Jiemian flash).
- Never invent RSS URLs. Every source in the registry was fetched and verified
  (entry dates, freshness) before inclusion — verified working sources have died
  within a month before; expect rot and re-verify periodically.

## Ops

- **Keep-alive:** Streamlit Community Cloud sleeps after 12h without traffic, and plain
  HTTP pings don't count (activity = WebSocket sessions). A
  [scheduled headless-browser visit](.github/workflows/keep-alive.yml) every 4h keeps
  the app awake and clicks the wake button if needed.
- **Events tab** (`events_monitor.py`, conference/trade-fair radar with press-deadline
  countdowns) is currently hidden behind `SHOW_EVENTS_TAB` — code and data intact.

## Tests

```bash
pip install pytest
python -m pytest tests/            # full suite (includes networked e2e, ~30s)
SKIP_E2E=1 python -m pytest tests/ # offline-only, what CI runs
```

Golden-baseline tests freeze the default monitor's behavior (taxonomy fingerprint,
source inventory, tab structure); i18n invariant tests guarantee no Chinese UI labels
leak into the English interface; clustering/dedup/decoder logic is unit-tested against
real-world regression cases.
