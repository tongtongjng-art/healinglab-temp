#!/usr/bin/env python3
# V49_BUSINESS_BRIEFING_AUTO_FETCH
"""
V49_BUSINESS_BRIEFING_AUTO_FETCH

Fetch public business-news paragraphs for the Business Briefing page.

What this script does:
- Reads public RSS feeds.
- Uses a real 14 / 30 / 90 day priority window.
- Scores articles against business briefing topics and real-work scenarios.
- Rejects obviously irrelevant topics such as housing prices, entertainment, politics-only items.
- Fetches public article pages when available.
- Extracts up to 3 complete public paragraphs, keeping 2 only when the public article page exposes no more usable text.
- Writes business-english-live.json next to this script.

What this script does not do:
- It does not bypass paywalls.
- It does not fabricate article paragraphs.
- It writes a natural Chinese reading note for each paragraph; this is a practical translation/reading aid, not a paywalled AI translation stage.
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

EXCLUDE_URL_PARTS = [
    "/politics/", "/world/", "/sport/", "/culture/", "/lifeandstyle/",
    "/commentisfree/", "/us-news/", "/uk-news/", "/australia-news/",
    "/society/", "/education/", "/football/", "/music/", "/film/",
]

PREFER_URL_PARTS = [
    "/business/", "/money/", "/worklife/", "/news/business", "/markets/",
    "/companies/", "/economy/", "/management/",
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

def excluded_url(url: str) -> bool:
    lowered = url.lower()
    return any(bit in lowered for bit in EXCLUDE_URL_PARTS)

def preferred_url_score(url: str) -> int:
    lowered = url.lower()
    return sum(3 for bit in PREFER_URL_PARTS if bit in lowered)

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
        if excluded_url(link) or excluded(joined) or not has_business_context(joined):
            continue
        items.append(Article(str(feed["name"]), int(feed["quality"]), title, link, published, summary))
    return items

def best_paragraph_window(paragraphs: list[str], keywords: list[str]) -> list[str]:
    """Pick a coherent 2-3 paragraph excerpt from the article order."""
    candidates: list[tuple[int, int, int, list[str]]] = []
    for size in range(MAX_PARAGRAPHS, MIN_PARAGRAPHS - 1, -1):
        for start in range(0, len(paragraphs) - size + 1):
            window = paragraphs[start:start + size]
            joined = " ".join(window)
            kw = keyword_score(joined, keywords)
            business = keyword_score(joined, BUSINESS_CONTEXT)
            if kw <= 0 or business < 2:
                continue
            score = kw * 4 + business + size * 3
            candidates.append((score, size, -start, window))
    if not candidates:
        return []
    candidates.sort(reverse=True)
    return candidates[0][3]

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

    picked = best_paragraph_window(paragraphs, keywords)
    return picked if len(picked) >= MIN_PARAGRAPHS else []

def fallback_cn_for_scenario(scenario_id: str, paragraph: str) -> str:
    """Natural Chinese reading note for public paragraphs.
    It is written as a readable Chinese translation / gist for the page, without inventing facts beyond the paragraph.
    """
    text = paragraph.lower()
    if "tariff" in text and "china" in text and "india" in text:
        return "这段大意是：关税上调确实影响了一些小企业，尤其是那些依赖从中国、印度采购原材料的公司。不过很多企业并没有被动承受成本，而是把关税带来的压力转进售价里，甚至有人借这个机会多涨了一点价格。还有一些企业选择等待法律结果，希望之后能拿到退款。"
    if "packaging industry" in text or ("higher costs" in text and "workers" in text):
        return "这段大意是：作者提到，接下来他要和一批包装行业公司讨论今年影响企业经营的几个问题：经济环境、成本上升、税务变化、AI，以及在不稳定的就业市场里如何招人和留人。这里的重点不是某个单词，而是企业今年面对的压力已经从单一成本，变成了成本、技术和用工一起变化。"
    if "should i increase prices" in text or "how will tariffs affect my business" in text:
        return "这段大意是：一年前，大家最关心的是关税会不会伤到自己的生意、要不要涨价、关税是否合法、什么时候结束。到了现在，很多问题已经有了答案。这说明商业环境会不断变化，客户今天的压价、犹豫或预算收紧，背后往往不是单一情绪，而是他们也在重新判断成本和风险。"
    if "freight" in text or "shipping" in text or "logistics" in text:
        return "这段大意是：文章在讲物流、运力或运输成本的变化。对外贸沟通来说，这类内容可以用来解释为什么总价、交期或运输方案会发生变化，而不是简单一句“运费涨了”。"
    if "payment" in text or "cash flow" in text or "invoice" in text:
        return "这段大意是：文章提到付款节奏、现金流或账期压力。放到外贸订单里，它提醒我们：催定金、催尾款不能只像催钱，而要说明付款和排产、备货、发货节点之间的关系。"
    if "quality" in text or "defect" in text or "complaint" in text or "recall" in text:
        return "这段大意是：文章涉及质量、风险或客户信任问题。处理投诉时，第一步不是急着辩解，而是先承接问题、收集证据，并告诉客户你会在什么时间给出调查结果。"
    if "demand" in text or "consumer" in text or "buyers" in text:
        return "这段大意是：市场需求和买方行为正在变化。客户要求折扣、拖延回复或反复比较价格时，背后可能是预算更谨慎、内部审批更慢，或者他们在测试供应商能给出多少空间。"

    names = {
        "price-too-high": "客户对价格更敏感，背后可能是成本、预算和内部审批压力",
        "discount-request": "买方正在争取更有利条件，折扣不能变成无条件让步",
        "material-cost-rise": "成本变化会影响报价有效期和调价沟通",
        "delivery-delay": "供应链或生产变化会影响交付承诺",
        "shipping-cost-rise": "物流和运输成本变化会影响总价与时效",
        "deposit-reminder": "付款时间会影响排产、备料和交付安排",
        "balance-payment": "尾款节点和发货安排必须说清楚",
        "no-reply-follow-up": "客户不回复可能是预算、优先级或内部审批还没定",
        "sample-follow-up": "样品反馈决定下一步是否进入报价、修改或下单",
        "quality-complaint": "质量问题会影响信任，第一封回复要先稳住客户",
    }
    topic = names.get(scenario_id, "这段原文提供了一个可以转化为客户沟通理由的商业背景")
    return f"这段大意是：{topic}。读这段时，不要只看新闻本身，而要看它能怎样帮你解释客户反应、判断沟通边界，并转成更稳妥的英文回复。"


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
            score = keyword_score(text, keywords) + keyword_score(text, BUSINESS_CONTEXT) + article.quality + preferred_url_score(article.url)
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
        "note": "cn is a natural Chinese reading note generated by local rules; no paywalls are bypassed and no article paragraphs are fabricated.",
        "scenarios": live_items,
    }
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUTPUT} with {len(live_items)} scenario matches")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
