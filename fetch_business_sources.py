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
MAX_LIVE_ITEMS = 5
MIN_SELECTION_SCORE = 95

FEEDS = [
    {"name": "Supply Chain Dive", "url": "https://www.supplychaindive.com/feeds/news/", "quality": 12},
    {"name": "Manufacturing Dive", "url": "https://www.manufacturingdive.com/feeds/news/", "quality": 12},
    {"name": "Packaging Dive", "url": "https://www.packagingdive.com/feeds/news/", "quality": 11},
    {"name": "Payments Dive", "url": "https://www.paymentsdive.com/feeds/news/", "quality": 10},
    {"name": "CFO Dive", "url": "https://www.cfodive.com/feeds/news/", "quality": 9},
    {"name": "Transport Dive", "url": "https://www.transportdive.com/feeds/news/", "quality": 9},
    {"name": "FreightWaves", "url": "https://www.freightwaves.com/news/feed", "quality": 10},
    {"name": "The Loadstar", "url": "https://theloadstar.com/feed/", "quality": 10},
    {"name": "Food Logistics", "url": "https://www.foodlogistics.com/rss", "quality": 9},
    {"name": "Logistics Management", "url": "https://www.logisticsmgmt.com/rss", "quality": 9},
    {"name": "Modern Materials Handling", "url": "https://www.mmh.com/rss", "quality": 9},
    {"name": "IndustryWeek", "url": "https://www.industryweek.com/rss.xml", "quality": 9},
    {"name": "Financial Times", "url": "https://www.ft.com/?format=rss", "quality": 9},
    {"name": "Wall Street Journal", "url": "https://feeds.a.dj.com/rss/WSJcomUSBusiness.xml", "quality": 9},
    {"name": "The Guardian Business", "url": "https://www.theguardian.com/business/rss", "quality": 4},
    {"name": "BBC Business", "url": "https://feeds.bbci.co.uk/news/business/rss.xml", "quality": 2},
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

B2B_CORE_CONTEXT = [
    "supplier", "suppliers", "buyer", "buyers", "customer", "customers",
    "client", "clients", "manufacturer", "manufacturers", "procurement",
    "purchasing", "vendor", "vendors", "distributor", "distributors",
    "exports", "exporters", "imports", "importers", "orders", "order",
    "supply chain", "logistics", "shipping", "freight", "shipment",
    "shipments", "factory", "factories", "production", "inventory",
    "raw material", "raw materials", "input costs", "payment", "invoice",
    "cash flow", "margin", "margins", "pricing", "lead time", "lead times",
    "delivery", "contract", "contracts", "quality",
]

HIGH_VALUE_CONTEXT = [
    "supply chain disruption", "supplier disruption", "supplier risk",
    "critical minerals", "rare earth", "raw material shortage", "materials shortage",
    "input cost", "input costs", "cost pressure", "cost pressures",
    "production delay", "delivery delay", "lead time", "lead times",
    "shipping cost", "freight rate", "freight rates", "container rates",
    "working capital", "cash flow", "payment terms", "invoice payment",
    "purchase order", "purchase orders", "order volume", "order volumes",
    "manufacturing capacity", "factory output", "inventory levels",
    "quality issue", "product recall", "after-sales",
]

COMMERCIAL_LENS_CONTEXT = [
    "pricing power", "price pressure", "margin pressure", "protect margins",
    "customer demand", "buyer demand", "weaker demand", "soft demand",
    "strong demand", "competitive pressure", "market share", "sales growth",
    "revenue growth", "profit warning", "profitability", "cost cutting",
    "cost control", "budget pressure", "capital spending", "investment plans",
    "business confidence", "customer confidence", "order cancellations",
    "delayed orders", "supplier negotiations", "procurement strategy",
    "commercial terms", "contract terms", "payment terms", "risk management",
    "working capital", "cash conversion", "inventory management",
]

CONSUMER_ONLY_CONTEXT = [
    "driver", "drivers", "motorist", "motorists", "commuter", "commuters",
    "household", "households", "family car", "petrol", "diesel", "pump",
    "pumps", "gasoline", "renters", "homeowners", "mortgage", "grocery",
    "groceries", "supermarket shoppers", "holidaymakers", "tourists",
    "fuel", "gas prices", "oil changes", "cost of living", "retail market",
    "shoppers", "families", "pensioners", "bills", "energy bills",
    "car insurance", "rail fares", "commuting costs",
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
    "/environment/", "/travel/", "/technology/games/",
]

PREFER_URL_PARTS = [
    "/business/", "/money/", "/worklife/", "/news/business", "/markets/",
    "/companies/", "/economy/", "/management/",
]

SCENARIOS = [
    {"id": "price-too-high", "keywords": ["price", "prices", "pricing", "margin", "margins", "cost", "costs", "input costs", "inflation", "buyer", "buyers", "value", "supplier", "suppliers", "cost pressure"]},
    {"id": "discount-request", "keywords": ["discount", "demand", "buyer", "buyers", "sales", "pricing", "budget", "order volume", "purchase order"]},
    {"id": "material-cost-rise", "keywords": ["raw material", "raw materials", "materials", "commodity", "commodities", "critical minerals", "rare earth", "input costs", "costs", "prices", "manufacturer", "manufacturing"]},
    {"id": "delivery-delay", "keywords": ["delay", "delivery", "lead time", "lead times", "supply chain", "production", "shipment", "shipping", "factory", "capacity"]},
    {"id": "shipping-cost-rise", "keywords": ["freight", "shipping", "logistics", "port", "container", "containers", "route", "delivery", "shipping capacity", "freight rates"]},
    {"id": "deposit-reminder", "keywords": ["payment", "cash flow", "working capital", "deposit", "invoice", "liquidity", "payment terms"]},
    {"id": "balance-payment", "keywords": ["payment", "invoice", "cash flow", "shipment", "credit", "balance", "receivables", "payment terms"]},
    {"id": "no-reply-follow-up", "keywords": ["sales", "customer", "customers", "client", "clients", "demand", "buyer", "buyers", "confidence", "orders", "pipeline"]},
    {"id": "sample-follow-up", "keywords": ["sample", "product", "quality", "design", "testing", "prototype", "supplier", "manufacturer"]},
    {"id": "quality-complaint", "keywords": ["quality", "recall", "complaint", "defect", "safety", "customer service", "after-sales", "supplier"]},
]

SOURCE_QUALITY = {str(feed["name"]): int(feed["quality"]) for feed in FEEDS}

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

def b2b_core_score(text: str) -> int:
    return keyword_score(text, B2B_CORE_CONTEXT)

def high_value_score(text: str) -> int:
    return keyword_score(text, HIGH_VALUE_CONTEXT)

def commercial_lens_score(text: str) -> int:
    return keyword_score(text, COMMERCIAL_LENS_CONTEXT)

def consumer_only_score(text: str) -> int:
    return keyword_score(text, CONSUMER_ONLY_CONTEXT)

def is_work_relevant(text: str) -> bool:
    work = work_context_score(text)
    core = b2b_core_score(text)
    lens = commercial_lens_score(text)
    consumer = consumer_only_score(text)
    if core < 4 and lens < 4:
        return False
    if work < 6 and lens < 6:
        return False
    if consumer > 0 and (core + lens) < 12:
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

def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()

def child_text(node: ET.Element, *names: str) -> str:
    wanted = {name.lower() for name in names}
    for child in list(node):
        if local_name(child.tag) in wanted and child.text:
            return clean_text(child.text)
    return ""

def entry_link(node: ET.Element) -> str:
    for child in list(node):
        if local_name(child.tag) != "link":
            continue
        href = child.attrib.get("href")
        if href:
            return clean_text(href)
        if child.text:
            return clean_text(child.text)
    return ""

def feed_entries(root: ET.Element) -> list[ET.Element]:
    entries: list[ET.Element] = []
    for node in root.iter():
        name = local_name(node.tag)
        if name in {"item", "entry"}:
            entries.append(node)
    return entries

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
    for item in feed_entries(root):
        title = child_text(item, "title")
        link = entry_link(item)
        summary = child_text(item, "description", "summary", "content")
        published = parse_date(child_text(item, "pubDate", "published", "updated"))
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
            core = b2b_core_score(joined)
            lens = commercial_lens_score(joined)
            consumer = consumer_only_score(joined)
            if kw <= 0 or business < 2 or (work + lens) < 6 or (core + lens) < 5:
                continue
            if consumer > 0 and (core + lens) < 12:
                continue
            score = kw * 4 + business + work * 3 + core * 5 + lens * 6 + size * 3 - consumer * 8
            candidates.append((score, size, -start, window))
    if not candidates:
        return []
    candidates.sort(reverse=True)
    return candidates[0][3]

def extract_article_paragraphs(article: Article, keywords: list[str], require_work: bool = True) -> list[str]:
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
    if not picked and not require_work:
        picked = best_general_business_window(paragraphs, keywords)
    return picked if len(picked) >= MIN_PARAGRAPHS else []

def best_general_business_window(paragraphs: list[str], keywords: list[str]) -> list[str]:
    """Fallback window for good business articles whose RSS summary is too thin."""
    candidates: list[tuple[int, int, int, list[str]]] = []
    for size in range(MAX_PARAGRAPHS, MIN_PARAGRAPHS - 1, -1):
        for start in range(0, len(paragraphs) - size + 1):
            window = paragraphs[start:start + size]
            joined = " ".join(window)
            business = keyword_score(joined, BUSINESS_CONTEXT)
            work = work_context_score(joined)
            core = b2b_core_score(joined)
            lens = commercial_lens_score(joined)
            consumer = consumer_only_score(joined)
            if business < 4 or (work + lens) < 7 or (core + lens) < 6:
                continue
            if consumer > 0 and (core + lens) < 12:
                continue
            score = keyword_score(joined, keywords) * 3 + business * 3 + work * 3 + core * 5 + lens * 6 + size * 3 - consumer * 8
            candidates.append((score, size, -start, window))
    if not candidates:
        return []
    candidates.sort(reverse=True)
    return candidates[0][3]

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


def selection_reason(article: Article, scenario_id: str, text: str) -> dict[str, object]:
    return {
        "scenarioId": scenario_id,
        "sourceQuality": SOURCE_QUALITY.get(article.source, article.quality),
        "workScore": work_context_score(text),
        "b2bScore": b2b_core_score(text),
        "highValueScore": high_value_score(text),
        "commercialLensScore": commercial_lens_score(text),
        "consumerPenalty": consumer_only_score(text),
    }

def total_selection_score(article: Article, text: str, scenario_keywords: list[str]) -> int:
    return (
        keyword_score(text, scenario_keywords) * 3
        + keyword_score(text, BUSINESS_CONTEXT) * 2
        + work_context_score(text) * 3
        + b2b_core_score(text) * 6
        + high_value_score(text) * 8
        + commercial_lens_score(text) * 7
        + SOURCE_QUALITY.get(article.source, article.quality)
        + preferred_url_score(article.url)
        - consumer_only_score(text) * 10
    )

def build_live_item(scenario: dict[str, object], article: Article, paragraphs: list[str], window: int, score: int) -> dict[str, object]:
    scenario_id = str(scenario["id"])
    selection_text = f"{article.title} {article.summary} {' '.join(paragraphs)}"
    return {
        "scenarioId": scenario["id"],
        "sourceDate": article.published.date().isoformat(),
        "freshness": f"自动抓取｜最近{window}天",
        "selectionScore": score,
        "selection": selection_reason(article, scenario_id, selection_text),
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
            core = b2b_core_score(text)
            consumer = consumer_only_score(text)
            score = total_selection_score(article, text, keywords)
            if keyword_score(text, keywords) >= 2 and not excluded(text) and is_work_relevant(text):
                scored.append((score, article))
        scored.sort(key=lambda pair: (pair[0], pair[1].published), reverse=True)
        for _, article in scored[:8]:
            paragraphs = extract_article_paragraphs(article, keywords)
            if paragraphs:
                full_text = f"{article.title} {article.summary} {' '.join(paragraphs)}"
                final_score = max(score, total_selection_score(article, full_text, keywords))
                if final_score < MIN_SELECTION_SCORE:
                    continue
                return build_live_item(scenario, article, paragraphs, window, final_score), window
        # Second pass: some feeds have short summaries, so inspect likely business URLs before falling back to seeds.
        relaxed: list[tuple[int, Article]] = []
        for article in articles:
            if article.published < cutoff or excluded_url(article.url) or excluded(f"{article.title} {article.summary}"):
                continue
            url_bonus = preferred_url_score(article.url)
            if url_bonus <= 0:
                continue
            text = f"{article.title} {article.summary}"
            core = b2b_core_score(text)
            consumer = consumer_only_score(text)
            score = total_selection_score(article, text, keywords)
            if consumer > 0 and core < 8:
                continue
            relaxed.append((score, article))
        relaxed.sort(key=lambda pair: (pair[0], pair[1].published), reverse=True)
        for _, article in relaxed[:10]:
            paragraphs = extract_article_paragraphs(article, keywords, require_work=False)
            if paragraphs:
                full_text = f"{article.title} {article.summary} {' '.join(paragraphs)}"
                final_score = max(score, total_selection_score(article, full_text, keywords))
                if final_score < MIN_SELECTION_SCORE:
                    continue
                return build_live_item(scenario, article, paragraphs, window, final_score), window
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
            if len(live_items) >= MAX_LIVE_ITEMS:
                break

    live_items.sort(
        key=lambda item: (
            int(item.get("selectionScore", 0)),
            str(item.get("sourceDate", "")),
        ),
        reverse=True,
    )

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
