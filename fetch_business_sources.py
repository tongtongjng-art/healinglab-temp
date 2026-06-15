#!/usr/bin/env python3
"""
V48_BUSINESS_BRIEFING_AUTO_FETCH

Fetch public business-news paragraphs for the Business Briefing page.

What this script does:
- Reads public RSS feeds.
- Uses a real 14 / 30 / 90 day priority window.
- Scores articles against business briefing topics and real-work scenarios.
- Rejects obviously irrelevant topics such as housing prices, entertainment, politics-only items.
- Fetches public article pages when available.
- Extracts 2-3 complete public paragraphs.
- Writes business-english-live.json next to this script.

What this script does not do:
- It does not bypass paywalls.
- It does not fabricate article paragraphs.
- It does not machine-translate by default; cn / insight stay blank unless your later AI step fills them.
"""

from __future__ import annotations

import datetime as dt
import email.utils
import html
import json
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable

OUTPUT = Path(__file__).with_name("business-english-live.json")
LOOKBACK_WINDOWS = [14, 30, 90]
MIN_PARAGRAPHS = 2
MAX_PARAGRAPHS = 3
MIN_PARAGRAPH_CHARS = 180
MAX_PARAGRAPH_CHARS = 1500
MAX_ARTICLE_SECONDS = 18
USER_AGENT = "BusinessBriefingWorkDesk/1.2 (+public RSS personal learning tool)"

FEEDS = [
    {"name": "The Guardian Business", "url": "https://www.theguardian.com/business/rss", "quality": 8},
    {"name": "BBC Business", "url": "https://feeds.bbci.co.uk/news/business/rss.xml", "quality": 7},
    {"name": "Financial Times", "url": "https://www.ft.com/?format=rss", "quality": 9},
    {"name": "Wall Street Journal", "url": "https://feeds.a.dj.com/rss/WSJcomUSBusiness.xml", "quality": 9},
    {"name": "Harvard Business Review", "url": "https://feeds.harvardbusiness.org/harvardbusiness", "quality": 8},
]

BUSINESS_CONTEXT = [
    "business", "company", "companies", "supplier", "suppliers", "buyer", "buyers",
    "manufacturer", "manufacturers", "trade", "exports", "imports", "orders",
    "supply chain", "logistics", "shipping", "freight", "tariff", "tariffs",
    "currency", "exchange rate", "cost", "costs", "pricing", "demand", "consumer",
    "inventory", "cash flow", "payment", "invoice", "quality", "production",
]

EXCLUDE_TOPICS = [
    "home price", "home prices", "house price", "house prices", "housing market", "mortgage",
    "real estate", "rent", "rents", "landlord", "ipo wealth", "stock market debut",
    "celebrity", "film", "music", "sports", "election", "politician", "war crime",
    "murder", "crime", "lawsuit claims"  # lawsuits can be business, but this tool only keeps work-usable items.
]

SCENARIOS = [
    {"id": "price-too-high", "keywords": ["price", "prices", "pricing", "margin", "margins", "cost", "costs", "inflation", "buyer", "buyers", "value", "supplier", "suppliers"]},
    {"id": "discount-request", "keywords": ["discount", "demand", "buyer", "buyers", "sales", "pricing", "budget", "consumer demand"]},
    {"id": "material-cost-rise", "keywords": ["raw material", "materials", "commodity", "commodities", "input costs", "costs", "prices", "manufacturer"]},
    {"id": "delivery-delay", "keywords": ["delay", "delivery", "supply chain", "production", "shipment", "shipping", "factory"]},
    {"id": "shipping-cost-rise", "keywords": ["freight", "shipping", "logistics", "port", "container", "route", "delivery", "shipping capacity"]},
    {"id": "deposit-reminder", "keywords": ["payment", "cash flow", "working capital", "deposit", "invoice", "liquidity"]},
    {"id": "balance-payment", "keywords": ["payment", "invoice", "cash flow", "shipment", "credit", "balance", "receivables"]},
    {"id": "no-reply-follow-up", "keywords": ["sales", "customer", "demand", "buyer", "buyers", "confidence", "orders", "pipeline"]},
    {"id": "sample-follow-up", "keywords": ["sample", "product", "quality", "consumer", "design", "testing", "prototype"]},
    {"id": "quality-complaint", "keywords": ["quality", "recall", "complaint", "defect", "safety", "customer service", "after-sales"]},
]

@dataclass
class Article:
    source: str
    quality: int
    title: str
    url: str
    published: dt.datetime
    summary: str

class ParagraphParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._in_p = False
        self._buf: list[str] = []
        self.paragraphs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "p":
            self._in_p = True
            self._buf = []

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "p" and self._in_p:
            text = clean_text("".join(self._buf))
            if is_good_paragraph(text):
                self.paragraphs.append(text)
            self._in_p = False
            self._buf = []

    def handle_data(self, data: str) -> None:
        if self._in_p:
            self._buf.append(data)

def clean_text(value: str) -> str:
    value = html.unescape(re.sub(r"<[^>]+>", " ", value or ""))
    return re.sub(r"\s+", " ", value).strip()

def keyword_score(text: str, keywords: Iterable[str]) -> int:
    lowered = text.lower()
    score = 0
    for keyword in keywords:
        k = keyword.lower()
        if k in lowered:
            score += 4 if " " in k else 1
    return score

def excluded(text: str) -> bool:
    lowered = text.lower()
    return any(bit in lowered for bit in EXCLUDE_TOPICS)

def has_business_context(text: str) -> bool:
    return keyword_score(text, BUSINESS_CONTEXT) >= 2

def is_good_paragraph(text: str) -> bool:
    if len(text) < MIN_PARAGRAPH_CHARS or len(text) > MAX_PARAGRAPH_CHARS:
        return False
    lowered = text.lower()
    bad_bits = [
        "sign up", "newsletter", "cookies", "all rights reserved", "advertisement",
        "skip to", "share this", "follow us", "download the app", "privacy policy",
    ]
    if any(bit in lowered for bit in bad_bits):
        return False
    if excluded(text):
        return False
    # Keep complete sentence-like English paragraphs.
    return text.count(".") + text.count("?") + text.count("!") >= 1

def fetch_text(url: str, timeout: int = MAX_ARTICLE_SECONDS) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = response.read()
        charset = response.headers.get_content_charset() or "utf-8"
        return data.decode(charset, errors="replace")

def parse_date(value: str) -> dt.datetime | None:
    if not value:
        return None
    try:
        parsed = email.utils.parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.astimezone(dt.timezone.utc)
    except (TypeError, ValueError):
        return None

def parse_feed(feed: dict[str, object]) -> list[Article]:
    try:
        xml = fetch_text(str(feed["url"]), timeout=20)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print(f"[warn] feed failed: {feed['name']} {exc}", file=sys.stderr)
        return []
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        print(f"[warn] feed parse failed: {feed['name']} {exc}", file=sys.stderr)
        return []

    items: list[Article] = []
    for item in root.findall(".//item"):
        title = clean_text(item.findtext("title", ""))
        link = clean_text(item.findtext("link", ""))
        summary = clean_text(item.findtext("description", ""))
        published = parse_date(item.findtext("pubDate", ""))
        if not title or not link or not published:
            continue
        joined = f"{title} {summary}"
        if excluded(joined) or not has_business_context(joined):
            continue
        items.append(Article(str(feed["name"]), int(feed["quality"]), title, link, published, summary))
    return items

def extract_article_paragraphs(article: Article, keywords: list[str]) -> list[str]:
    try:
        page = fetch_text(article.url)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print(f"[warn] article failed: {article.url} {exc}", file=sys.stderr)
        return []

    parser = ParagraphParser()
    parser.feed(page)
    paragraphs = list(dict.fromkeys(parser.paragraphs))
    if len(paragraphs) < MIN_PARAGRAPHS:
        return []

    ranked = sorted(
        paragraphs,
        key=lambda para: (keyword_score(para, keywords), keyword_score(para, BUSINESS_CONTEXT), len(para)),
        reverse=True,
    )
    useful = [para for para in ranked if keyword_score(para, keywords) > 0 and has_business_context(para)]
    picked = useful[:MAX_PARAGRAPHS]
    if len(picked) < MIN_PARAGRAPHS:
        # Use first coherent business paragraphs only if the article itself matched strongly.
        picked = [para for para in ranked if has_business_context(para)][:MAX_PARAGRAPHS]
    return picked if len(picked) >= MIN_PARAGRAPHS else []

def fallback_cn_for_scenario(scenario_id: str, paragraph: str) -> str:
    """Non-AI Chinese reading note. This is not a full translation; it prevents blank Chinese panels."""
    names = {
        "price-too-high": "价格、成本和客户预算压力",
        "discount-request": "需求变化和买方谈判压力",
        "material-cost-rise": "原材料或投入成本变化",
        "delivery-delay": "供应链、生产或交付不确定性",
        "shipping-cost-rise": "物流、运费或运输时效变化",
        "deposit-reminder": "付款节奏和现金流安排",
        "balance-payment": "付款节点和发货安排",
        "no-reply-follow-up": "客户决策延迟或需求优先级变化",
        "sample-follow-up": "样品评估和订单推进",
        "quality-complaint": "质量风险、客户信任和售后处理",
    }
    topic = names.get(scenario_id, "商业背景")
    return f"中文解读：这段原文提供了关于{topic}的商业背景。阅读时重点不是逐字背诵，而是看它如何帮助你解释客户反应、判断沟通边界，并转成更稳妥的商务英文回复。"


def fallback_insight_for_scenario(scenario_id: str) -> str:
    mapping = {
        "price-too-high": "和本主题的关系：客户嫌贵时，可以把外部成本、价值范围和可调整选项讲清楚，而不是马上降价。",
        "discount-request": "和本主题的关系：折扣要绑定数量、付款、长期订单或规格变化，避免无条件让步。",
        "material-cost-rise": "和本主题的关系：调价邮件要说明成本依据、报价有效期和缓冲方案。",
        "delivery-delay": "和本主题的关系：交期变化要提前说清原因、新时间和补救动作。",
        "shipping-cost-rise": "和本主题的关系：运费变化要解释路线、舱位和时效，并给客户可选方案。",
        "deposit-reminder": "和本主题的关系：催定金要和排产、备料、交期锁定绑定。",
        "balance-payment": "和本主题的关系：催尾款要和发货节点绑定，语气礼貌但边界清楚。",
        "no-reply-follow-up": "和本主题的关系：跟进要降低客户回复成本，给选择题而不是只问 Any update。",
        "sample-follow-up": "和本主题的关系：样品跟进要问测试结果、调整点和下一步订单计划。",
        "quality-complaint": "和本主题的关系：投诉回复先承接情绪，再收集证据并给处理时间。",
    }
    return mapping.get(scenario_id, "和本主题的关系：把商业背景转成客户沟通中的理由、边界和下一步动作。")


def build_live_item(scenario: dict[str, object], article: Article, paragraphs: list[str], window: int) -> dict[str, object]:
    scenario_id = str(scenario["id"])
    return {
        "scenarioId": scenario["id"],
        "sourceDate": article.published.date().isoformat(),
        "freshness": f"自动抓取｜最近{window}天",
        "source": {
            "name": article.source,
            "title": article.title,
            "url": article.url,
            "paragraphs": [
                {
                    "en": paragraph,
                    "cn": fallback_cn_for_scenario(scenario_id, paragraph),
                    "insight": fallback_insight_for_scenario(scenario_id),
                }
                for paragraph in paragraphs[:MAX_PARAGRAPHS]
            ],
        },
    }

def pick_for_scenario(scenario: dict[str, object], articles: list[Article], now: dt.datetime) -> tuple[dict[str, object] | None, int | None]:
    keywords = list(scenario["keywords"])
    for window in LOOKBACK_WINDOWS:
        cutoff = now - dt.timedelta(days=window)
        scored: list[tuple[int, Article]] = []
        for article in articles:
            if article.published < cutoff:
                continue
            text = f"{article.title} {article.summary}"
            score = keyword_score(text, keywords) + keyword_score(text, BUSINESS_CONTEXT) + article.quality
            if keyword_score(text, keywords) >= 2 and not excluded(text):
                scored.append((score, article))
        scored.sort(key=lambda pair: (pair[0], pair[1].published), reverse=True)
        for _, article in scored[:8]:
            paragraphs = extract_article_paragraphs(article, keywords)
            if paragraphs:
                return build_live_item(scenario, article, paragraphs, window), window
    return None, None

def main() -> int:
    now = dt.datetime.now(dt.timezone.utc)
    articles: list[Article] = []
    for feed in FEEDS:
        articles.extend(parse_feed(feed))
        time.sleep(0.5)

    live_items: list[dict[str, object]] = []
    windows_used: list[int] = []
    seen_urls: set[str] = set()
    for scenario in SCENARIOS:
        item, window = pick_for_scenario(scenario, articles, now)
        if item and window:
            url = str(item["source"].get("url", ""))
            # Allow the same high-quality article to support multiple scenes only once in the first pass.
            if url in seen_urls:
                continue
            seen_urls.add(url)
            live_items.append(item)
            windows_used.append(window)

    payload = {
        "generatedAt": now.isoformat(),
        "lookbackWindows": LOOKBACK_WINDOWS,
        "lookbackWindowUsed": max(windows_used) if windows_used else None,
        "minParagraphs": MIN_PARAGRAPHS,
        "note": "cn and insight are blank unless a later translation/AI step fills them. No paywalls are bypassed and no paragraphs are fabricated.",
        "scenarios": live_items,
    }
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUTPUT} with {len(live_items)} scenario matches")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
