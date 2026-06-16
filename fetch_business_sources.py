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
- It writes a Chinese translation for each paragraph when a public translation request succeeds.
- If translation is unavailable, it leaves cn empty instead of fabricating a reading note.
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
import urllib.parse
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
TRANSLATE_TIMEOUT = 12

FEEDS = [
    {"name": "The Guardian Business", "url": "https://www.theguardian.com/business/rss", "quality": 8},
    {"name": "BBC Business", "url": "https://feeds.bbci.co.uk/news/business/rss.xml", "quality": 5},
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

WORK_CONTEXT = [
    "supplier", "suppliers", "buyer", "buyers", "manufacturer", "manufacturers",
    "procurement", "purchasing", "vendor", "vendors", "wholesale", "distributor",
    "distributors", "retailer", "retailers", "exports", "exporters", "imports",
    "importers", "orders", "order book", "supply chain", "logistics", "shipping",
    "freight", "container", "containers", "shipment", "shipments", "factory",
    "factories", "production", "inventory", "stock levels", "raw material",
    "raw materials", "input costs", "payment", "invoice", "cash flow", "margin",
    "margins", "pricing", "cost pressures", "working capital", "lead times",
    "delivery", "quality", "recall", "after-sales",
]

CONSUMER_ONLY_CONTEXT = [
    "driver", "drivers", "motorist", "motorists", "commuter", "commuters",
    "household", "households", "family car", "petrol", "diesel", "pump",
    "pumps", "gasoline", "renters", "homeowners", "mortgage", "grocery",
    "groceries", "supermarket shoppers", "holidaymakers", "tourists",
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

def work_context_score(text: str) -> int:
    return keyword_score(text, WORK_CONTEXT)

def consumer_only_score(text: str) -> int:
    return keyword_score(text, CONSUMER_ONLY_CONTEXT)

def is_work_relevant(text: str) -> bool:
    work = work_context_score(text)
    consumer = consumer_only_score(text)
    if work < 3:
        return False
    if consumer >= work and work < 8:
        return False
    return True

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
        if excluded_url(link) or excluded(joined) or not has_business_context(joined) or not is_work_relevant(joined):
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
            work = work_context_score(joined)
            consumer = consumer_only_score(joined)
            if kw <= 0 or business < 2 or work < 3:
                continue
            if consumer >= work and work < 8:
                continue
            score = kw * 4 + business + work * 5 + size * 3 - consumer * 5
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

def split_for_translation(text: str, limit: int = 460) -> list[str]:
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        if not sentence:
            continue
        if len(sentence) > limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(sentence[:limit])
            continue
        candidate = f"{current} {sentence}".strip()
        if len(candidate) > limit and current:
            chunks.append(current)
            current = sentence
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks

def translate_chunk(text: str) -> str:
    params = urllib.parse.urlencode({"q": text, "langpair": "en|zh-CN"})
    url = f"https://api.mymemory.translated.net/get?{params}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=TRANSLATE_TIMEOUT) as response:
        data = json.loads(response.read().decode("utf-8", errors="replace"))
    translated = str(data.get("responseData", {}).get("translatedText", "")).strip()
    translated = html.unescape(translated)
    if not translated or translated.lower() == text.lower():
        return ""
    return translated

def translate_to_chinese(paragraph: str) -> str:
    translated_parts: list[str] = []
    for chunk in split_for_translation(paragraph):
        try:
            translated = translate_chunk(chunk)
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError, ValueError) as exc:
            print(f"[warn] translation failed: {exc}", file=sys.stderr)
            return ""
        if not translated:
            return ""
        translated_parts.append(translated)
        time.sleep(0.2)
    return "".join(translated_parts)


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
                    "cn": translate_to_chinese(paragraph),
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
            work = work_context_score(text)
            consumer = consumer_only_score(text)
            score = (
                keyword_score(text, keywords)
                + keyword_score(text, BUSINESS_CONTEXT)
                + work * 4
                + article.quality
                + preferred_url_score(article.url)
                - consumer * 4
            )
            if keyword_score(text, keywords) >= 2 and not excluded(text) and is_work_relevant(text):
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
        "note": "Articles are filtered for supplier/buyer/manufacturing/orders/logistics/payment work contexts. cn is a paragraph translation when available; no paywalls are bypassed and no article paragraphs are fabricated.",
        "scenarios": live_items,
    }
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUTPUT} with {len(live_items)} scenario matches")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
