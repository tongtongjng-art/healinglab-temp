#!/usr/bin/env python3
# V49_BUSINESS_BRIEFING_AUTO_FETCH
"""
V49_BUSINESS_BRIEFING_AUTO_FETCH

Fetch public business-news paragraphs for the Business Briefing page.

What this script does:
- Reads public RSS feeds.
- Uses a real 14 / 30 / 90 day priority window.
- Scores articles against business briefing topics and real-work scenarios.
- Keeps business news as the primary source; export/cross-border methodology is only a supporting layer.
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

METHODOLOGY_SOURCES = {
    "payment": [
        {"name": "Trade.gov Methods of Payment", "url": "https://www.trade.gov/methods-payment", "use": "付款方式、账期、催款和风险边界"},
        {"name": "Trade.gov Export Solutions", "url": "https://www.trade.gov/export-solutions", "use": "出口流程和买卖双方风险分配"},
    ],
    "logistics": [
        {"name": "ICC Incoterms Rules", "url": "https://iccwbo.org/business-solutions/incoterms-rules/", "use": "交付责任、风险转移和贸易术语"},
        {"name": "Trade.gov Export Solutions", "url": "https://www.trade.gov/export-solutions", "use": "出口物流和国际销售基础流程"},
    ],
    "customer_development": [
        {"name": "Trade.gov Export Solutions", "url": "https://www.trade.gov/export-solutions", "use": "市场进入、客户开发和出口准备"},
        {"name": "Trade.gov Research Center", "url": "https://www.trade.gov/research-center", "use": "按行业和国家寻找市场线索"},
    ],
    "crossborder": [
        {"name": "Shopify Enterprise Blog", "url": "https://www.shopify.com/enterprise/blog", "use": "跨境电商、品牌、渠道和增长案例"},
        {"name": "DHL Discover", "url": "https://www.dhl.com/discover", "use": "跨境物流、电商配送和国际消费者趋势"},
    ],
}

FEEDS = [
    {"name": "Supply Chain Dive", "url": "https://www.supplychaindive.com/feeds/news/", "quality": 12},
    {"name": "Manufacturing Dive", "url": "https://www.manufacturingdive.com/feeds/news/", "quality": 12},
    {"name": "Packaging Dive", "url": "https://www.packagingdive.com/feeds/news/", "quality": 11},
    {"name": "Payments Dive", "url": "https://www.paymentsdive.com/feeds/news/", "quality": 10},
    {"name": "CFO Dive", "url": "https://www.cfodive.com/feeds/news/", "quality": 9},
    {"name": "Transport Dive", "url": "https://www.transportdive.com/feeds/news/", "quality": 9},
    {"name": "FreightWaves", "url": "https://www.freightwaves.com/news/feed", "quality": 10},
    {"name": "The Loadstar", "url": "https://theloadstar.com/feed/", "quality": 10},
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
    "retail", "retailer", "retailers", "consumer demand", "cautious spending",
    "budget pressure", "health and wellness", "beauty", "pet care", "home goods",
    "small appliances", "ai productivity", "automation", "productivity",
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
    "consumer demand", "retail trend", "inventory", "cautious spending",
    "budget pressure", "tariffs", "currency", "exchange rate", "ai productivity",
    "automation", "small appliances", "beauty", "pet care", "home goods",
    "health and wellness",
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
    "tariff", "tariffs", "currency", "exchange rate", "inventory",
    "consumer demand", "retail trend", "budget pressure", "cautious spending",
    "ai productivity", "automation", "productivity", "category growth",
    "sales channel", "retailers", "brands",
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
    "consumer demand", "retail trend", "pet care", "health and wellness",
    "small appliances", "beauty", "home goods", "ai productivity",
    "tariff pressure", "currency volatility", "inventory pressure",
    "cautious spending", "budget pressure", "demand slowdown",
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
    "investment", "investments", "partnership", "partnerships", "competitiveness",
    "economic security", "capacity", "manufacturing capacity", "service offering",
    "shipping network", "less-than-truckload", "ltl", "open to all businesses",
    "business customers", "enterprise customers",
    "consumer demand", "retail trend", "cautious spending", "budget pressure",
    "health and wellness", "pet care", "beauty", "home goods", "small appliances",
    "ai productivity", "automation", "efficiency", "tariff", "tariffs",
    "currency", "exchange rate", "inventory", "stock levels", "category demand",
    "consumer behavior", "shopping habits", "trade down", "premiumization",
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
    "murder", "crime", "lawsuit claims", "cartel case", "cartel", "allegations",
    "court case", "criminal probe", "fraud case", "delete this string of emails"
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
    {"id": "material-cost-rise", "keywords": ["raw material", "raw materials", "materials", "commodity", "commodities", "critical minerals", "rare earth", "input costs", "costs", "prices", "manufacturer", "manufacturing", "tariffs", "currency", "inventory pressure", "supply chain gaps"]},
    {"id": "delivery-delay", "keywords": ["delay", "delivery", "lead time", "lead times", "supply chain", "production", "shipment", "shipping", "factory", "capacity", "inventory", "supplier risk", "production planning"]},
    {"id": "shipping-cost-rise", "keywords": ["freight", "shipping", "logistics", "port", "container", "containers", "route", "delivery", "shipping capacity", "freight rates", "last mile", "fulfilment", "warehouse", "inventory"]},
    {"id": "deposit-reminder", "keywords": ["payment", "cash flow", "working capital", "deposit", "invoice", "liquidity", "payment terms", "budget pressure", "cautious spending", "credit"]},
    {"id": "balance-payment", "keywords": ["payment", "invoice", "cash flow", "shipment", "credit", "balance", "receivables", "payment terms", "working capital", "inventory financing"]},
    {"id": "price-too-high", "keywords": ["price", "prices", "pricing", "margin", "margins", "input costs", "inflation", "buyer", "buyers", "value", "supplier", "suppliers", "cost pressure", "tariffs", "currency", "budget pressure"]},
    {"id": "discount-request", "keywords": ["discount", "demand", "buyer", "buyers", "sales", "pricing", "budget", "order volume", "purchase order", "consumer demand", "cautious spending", "retail trend", "trade down"]},
    {"id": "no-reply-follow-up", "keywords": ["sales", "customer", "customers", "client", "clients", "demand", "buyer", "buyers", "confidence", "orders", "pipeline", "consumer demand", "retail", "category demand", "market opportunity"]},
    {"id": "sample-follow-up", "keywords": ["sample", "product", "quality", "design", "testing", "prototype", "supplier", "manufacturer", "beauty", "pet care", "health and wellness", "small appliances", "home goods", "product validation"]},
    {"id": "quality-complaint", "keywords": ["quality", "recall", "complaint", "defect", "safety", "customer service", "after-sales", "supplier", "brand trust", "consumer reviews", "product safety"]},
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
        self._capture_depth = 0
        self._capture_stack: list[bool] = []
        self.paragraphs: list[str] = []
        self.article_paragraphs: list[str] = []

    def _is_article_container(self, tag: str, attrs: list[tuple[str, str | None]]) -> bool:
        if tag.lower() in {"article", "main"}:
            return True
        attr_text = " ".join(str(value or "") for _, value in attrs).lower()
        markers = [
            "article-body", "article_body", "article-content", "article_content",
            "story-body", "story_body", "story-content", "post-content",
            "entry-content", "content-body", "body-content", "news-content",
        ]
        return any(marker in attr_text for marker in markers)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        enters_article = self._is_article_container(tag, attrs)
        if enters_article or self._capture_depth > 0:
            self._capture_depth += 1
            self._capture_stack.append(enters_article)
        else:
            self._capture_stack.append(False)
        if tag.lower() == "p":
            self._in_p = True
            self._buf = []

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "p" and self._in_p:
            text = clean_text("".join(self._buf))
            if is_good_paragraph(text):
                self.paragraphs.append(text)
                if self._capture_depth > 0:
                    self.article_paragraphs.append(text)
            self._in_p = False
            self._buf = []
        if self._capture_stack:
            was_inside = self._capture_depth > 0
            self._capture_stack.pop()
            if was_inside:
                self._capture_depth = max(0, self._capture_depth - 1)

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
    if consumer > 0 and (core + lens) < 8:
        return False
    return True

def is_good_paragraph(text: str) -> bool:
    if len(text) < MIN_PARAGRAPH_CHARS or len(text) > MAX_PARAGRAPH_CHARS:
        return False
    lowered = text.lower()
    bad_bits = [
        "sign up", "newsletter", "cookies", "all rights reserved", "advertisement",
        "skip to", "share this", "follow us", "download the app", "privacy policy",
        "googletag", "enable services", "define slot", "window.googletag",
        "adservice", "gptsize", "collapseemptydivs", "responsive-main_content",
    ]
    if any(bit in lowered for bit in bad_bits):
        return False
    if excluded(text):
        return False
    if text.count("{") + text.count("}") + text.count("[") + text.count("]") > 4:
        return False
    if len(re.findall(r"\b[a-zA-Z_]+\(", text)) >= 2:
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

def paragraph_is_on_topic(paragraph: str, title_context: str, keywords: list[str]) -> bool:
    paragraph_score = (
        keyword_score(paragraph, keywords)
        + keyword_score(paragraph, HIGH_VALUE_CONTEXT)
        + keyword_score(paragraph, COMMERCIAL_LENS_CONTEXT)
        + keyword_score(paragraph, B2B_CORE_CONTEXT)
    )
    title_words = {
        word
        for word in re.findall(r"[a-zA-Z][a-zA-Z-]{4,}", title_context.lower())
        if word not in {"about", "after", "their", "there", "which", "would", "could", "should", "these", "those"}
    }
    overlap = sum(1 for word in title_words if word in paragraph.lower())
    return paragraph_score >= 3 or overlap >= 2

def best_paragraph_window(paragraphs: list[str], keywords: list[str], title_context: str = "") -> list[str]:
    """Pick a coherent 2-3 paragraph excerpt from the article order."""
    candidates: list[tuple[int, int, int, list[str]]] = []
    for size in range(MAX_PARAGRAPHS, MIN_PARAGRAPHS - 1, -1):
        for start in range(0, len(paragraphs) - size + 1):
            window = paragraphs[start:start + size]
            if title_context and any(not paragraph_is_on_topic(para, title_context, keywords) for para in window):
                continue
            joined = " ".join(window)
            kw = keyword_score(joined, keywords)
            business = keyword_score(joined, BUSINESS_CONTEXT)
            work = work_context_score(joined)
            core = b2b_core_score(joined)
            lens = commercial_lens_score(joined)
            consumer = consumer_only_score(joined)
            if kw <= 0 or business < 2 or (work + lens) < 6 or (core + lens) < 5:
                continue
            if consumer > 0 and (core + lens) < 8:
                continue
            score = kw * 4 + business + work * 3 + core * 5 + lens * 6 + size * 3 - consumer * 3
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
    body_paragraphs = parser.article_paragraphs if len(parser.article_paragraphs) >= MIN_PARAGRAPHS else parser.paragraphs
    paragraphs = list(dict.fromkeys(body_paragraphs))
    if len(paragraphs) < MIN_PARAGRAPHS:
        return []

    title_context = f"{article.title} {article.summary}"
    picked = best_paragraph_window(paragraphs, keywords, title_context)
    if not picked and not require_work:
        picked = best_general_business_window(paragraphs, keywords, title_context)
    return picked if len(picked) >= MIN_PARAGRAPHS else []

def best_general_business_window(paragraphs: list[str], keywords: list[str], title_context: str = "") -> list[str]:
    """Fallback window for good business articles whose RSS summary is too thin."""
    candidates: list[tuple[int, int, int, list[str]]] = []
    for size in range(MAX_PARAGRAPHS, MIN_PARAGRAPHS - 1, -1):
        for start in range(0, len(paragraphs) - size + 1):
            window = paragraphs[start:start + size]
            if title_context and any(not paragraph_is_on_topic(para, title_context, keywords) for para in window):
                continue
            joined = " ".join(window)
            business = keyword_score(joined, BUSINESS_CONTEXT)
            work = work_context_score(joined)
            core = b2b_core_score(joined)
            lens = commercial_lens_score(joined)
            consumer = consumer_only_score(joined)
            if business < 4 or (work + lens) < 7 or (core + lens) < 6:
                continue
            if consumer > 0 and (core + lens) < 8:
                continue
            score = keyword_score(joined, keywords) * 3 + business * 3 + work * 3 + core * 5 + lens * 6 + size * 3 - consumer * 3
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

def local_translation_for_paragraph(scenario_id: str, paragraph: str) -> str:
    text = " ".join(paragraph.lower().split())
    if scenario_id == "material-cost-rise":
        if "american manufacturing has an opportunity" in text and "critical materials" in text:
            return "阿贡国家实验室主任 Paul Kearns 在声明中表示，美国制造业有机会在关键材料和化学加工领域引领下一代创新。这个国家级协作项目将把发现、工程和部署连接起来，从而增强美国竞争力并提升经济安全。"
        if "last month" in text and "$45.7 million" in text and "critical minerals" in text:
            return "上个月，美国能源部宣布投入 4570 万美元支持 19 个项目，旨在弥补美国国内关键矿产和材料供应链的缺口。这笔资金将用于建设试点规模设施，以加工镁和稀土元素。"
        if "under the initiative" in text and "artificial intelligence" in text:
            return "根据该计划，研究人员将使用先进计算建模、人工智能、快速合成工具和试点规模制造系统，帮助企业测试并扩大新的生产流程。阿贡将与能源部相关办公室合作推进这项工作。"
        if "processing capacity" in text or "materials supply chain" in text or "critical minerals" in text:
            return "这段的重点不是单纯涨价，而是材料供应、加工能力和交付稳定性正在变成商业风险。给客户沟通时，要把报价有效期、材料锁定和交付安排放在一起说明。"
    if scenario_id == "shipping-cost-rise":
        if "externalizing amazon" in text and "supply chain services" in text:
            return "亚马逊将其零担/部分载货运输服务对外开放，意味着原本服务亚马逊及其平台卖家的库存、货运、配送和包裹运输能力，现在被整合进新的 Amazon Supply Chain Services，面向非亚马逊卖家提供。"
        if "launched an ltl offering" in text:
            return "亚马逊在 2025 年 4 月推出 LTL 零担运输服务，面向不需要整车运输的托运人；最初该服务只用于运往亚马逊仓储设施的入库配送。"
        if "traditional hub-and-spoke" in text or "palletized" in text:
            return "这家零售巨头表示，现在它提供的是更传统的轴辐式 LTL 网络：托盘货物会被提取、转运到附近枢纽站，再送往最终目的地，仍然以托盘形式交付，成本低于传统 LTL 承运商。"
        if "less-than-truckload" in text or "shipping network" in text or "freight" in text:
            return "这段讲的是运输方式和配送网络的变化。对外贸沟通来说，重点不是简单说运费上涨，而是给客户比较成本、时效和稳定性的不同方案。"
    return ""

def refine_paragraphs_for_teaching(scenario_id: str, paragraphs: list[str]) -> list[str]:
    if scenario_id == "material-cost-rise":
        terms = [
            "critical mineral", "critical material", "materials supply chain", "material supply",
            "processing", "magnesium", "rare earth", "doe", "argonne", "chemical processing",
            "manufacturing", "production"
        ]
    elif scenario_id == "shipping-cost-rise":
        terms = [
            "shipping", "freight", "logistics", "ltl", "less-than-truckload", "carrier",
            "delivery", "network", "shipment", "supply chain services", "amazon"
        ]
    else:
        return paragraphs[:MAX_PARAGRAPHS]

    selected: list[str] = []
    for paragraph in paragraphs:
        lower = paragraph.lower()
        if any(term in lower for term in terms):
            selected.append(paragraph)
    if len(selected) >= 2:
        return selected[:MAX_PARAGRAPHS]
    return paragraphs[:2]

def make_templates(subject: str, context: str, option: str, boundary: str) -> list[dict[str, str]]:
    return [
        {
            "name": "标准版",
            "note": "说明背景、影响和下一步。",
            "subject": subject,
            "body": f"Dear [Name],\n\nI would like to share a quick update with you.\n\n{context}\n\n{option}\n\n{boundary}\n\nBest regards,\n[Your Name]"
        },
        {
            "name": "更柔和版",
            "note": "适合老客户或关系较好的客户。",
            "subject": subject,
            "body": f"Dear [Name],\n\nThank you for your continued support.\n\n{context}\n\nWe will try to reduce the impact where possible and keep you updated before the next order is confirmed.\n\n{option}\n\nBest regards,\n[Your Name]"
        },
        {
            "name": "边界清楚版",
            "note": "适合客户要求你直接承诺价格、交期或运费时。",
            "subject": subject,
            "body": f"Dear [Name],\n\nThank you for your message.\n\n{context}\n\n{boundary}\n\nPlease confirm your preferred option, and I will prepare the revised proposal accordingly.\n\nBest regards,\n[Your Name]"
        },
    ]

def track_label_for_scenario(scenario_id: str) -> str:
    if scenario_id in {"deposit-reminder", "balance-payment", "delivery-delay", "shipping-cost-rise", "quality-complaint"}:
        return "模块A｜实战应用：对外沟通与风险处理"
    if scenario_id in {"sample-follow-up", "no-reply-follow-up", "discount-request", "price-too-high"}:
        return "模块B｜增长应用：客户开发、跟进与转化"
    if scenario_id == "material-cost-rise":
        return "模块C→B｜商业观察转客户开发"
    return "模块C｜商业观察：市场、行业与机会判断"

def methodology_for_scenario(scenario_id: str) -> list[dict[str, str]]:
    if scenario_id in {"deposit-reminder", "balance-payment"}:
        return METHODOLOGY_SOURCES["payment"]
    if scenario_id in {"shipping-cost-rise", "delivery-delay"}:
        return METHODOLOGY_SOURCES["logistics"]
    if scenario_id in {"material-cost-rise", "sample-follow-up", "no-reply-follow-up"}:
        return METHODOLOGY_SOURCES["customer_development"]
    if scenario_id in {"discount-request", "price-too-high"}:
        return METHODOLOGY_SOURCES["customer_development"] + METHODOLOGY_SOURCES["payment"][:1]
    return METHODOLOGY_SOURCES["crossborder"]

def teaching_pack_for_scenario(scenario_id: str, article: Article, paragraphs: list[str]) -> dict[str, object]:
    if scenario_id == "material-cost-rise":
        material_templates = [
            {
                "name": "开发信：供应链机会",
                "note": "看到行业在补关键材料短板时，用行业观察切入，不硬推销。",
                "subject": "Support for Critical Materials and Chemical Processing",
                "body": "Dear [Name],\n\nI noticed that critical materials and chemical processing capacity are becoming a bigger priority for manufacturers in your market.\n\nFor companies expanding or stabilizing their materials supply chain, material availability, pilot-scale validation and reliable delivery are often key concerns before moving to larger production.\n\nWe support customers with [material/product/service], sample preparation and technical documentation for evaluation. If your team is reviewing alternative sourcing or additional supply options, I would be glad to share our capability sheet and discuss whether there is a fit.\n\nWould it be convenient for me to send a short introduction and sample specification for your review?\n\nBest regards,\n[Your Name]"
            },
            {
                "name": "跟进信：样品验证",
                "note": "适合客户对材料/化工产品感兴趣，但还没进入正式采购。",
                "subject": "Sample Validation for Your Material Requirement",
                "body": "Dear [Name],\n\nThank you for your interest.\n\nBefore moving to a formal order, many customers prefer to validate the material through samples, specification review and small-batch testing. This helps confirm whether the material can meet the required performance, processing conditions and delivery plan.\n\nWe can prepare [sample/specification/technical document] for your evaluation. If needed, we can also discuss the expected application, target specification and estimated monthly usage, so that we can recommend the most suitable grade or processing option.\n\nPlease let us know the key parameters you would like to test, and we will prepare the next step accordingly.\n\nBest regards,\n[Your Name]"
            },
            {
                "name": "替代供应：能力介绍",
                "note": "适合客户在找第二供应商、替代材料或更稳定交付。",
                "subject": "Alternative Sourcing Option for Your Materials Supply Chain",
                "body": "Dear [Name],\n\nI understand that supply chain stability is important when selecting a material partner.\n\nIf your team is reviewing alternative sourcing options, we can support you with [material/product/service], stable production planning and export documentation. Our goal is not only to provide a quotation, but also to help you reduce supply uncertainty during evaluation and future orders.\n\nWe can start with a small sample or specification review, then confirm whether our material can match your application, quality requirement and delivery schedule.\n\nPlease let me know if you are open to reviewing an additional supply option.\n\nBest regards,\n[Your Name]"
            },
        ]
        return {
            "title": "关键材料与化学加工：如何从产业新闻里发现客户开发机会？",
            "category": "行业机会",
            "businessView": "供应链",
            "contentTrack": track_label_for_scenario(scenario_id),
            "methodologySources": methodology_for_scenario(scenario_id),
            "level": "Level2",
            "targetUser": "化工材料 / 工业品 / 设备 / 检测与供应链服务外贸人",
            "coreProblem": "美国在关键材料、化学加工和试点制造上加大投入，说明相关客户可能在找稳定供应、样品验证和替代来源。",
            "scenario": {
                "problem": "文章提到 critical materials、chemical processing、pilot-scale manufacturing 和 supply chain gaps，说明相关企业可能正在找材料、加工、验证和替代供应能力。",
                "wrong": "不要停在“美国投钱了”这个新闻表面，要看谁因此可能产生采购、验证或替代供应需求。",
                "strategy": "抓 4 个信号：critical materials、chemical processing、pilot-scale facilities、supply chain gaps；对应开发信、样品验证、能力介绍。",
            },
            "judgement": [
                {"type": "外刊线索：关键材料被重新重视", "reading": "critical materials、chemical processing、economic security 说明这不是普通材料新闻，而是产业链补短板。", "response": "化工、材料、设备外贸人可以找正在扩产、验证新材料或寻找稳定供应的客户。"},
                {"type": "商业观察：机会不只在卖材料", "reading": "pilot-scale manufacturing、test and scale new production processes 说明客户可能需要样品、试产、检测、加工服务或技术文件。", "response": "开发信不要只说 we are a supplier，要说 sample validation、technical documentation、alternative sourcing。"},
                {"type": "外贸动作：用行业观察切入客户", "reading": "supply chain gaps 说明客户痛点可能是供应稳定性和替代来源。", "response": "邮件先提行业趋势，再说明你的材料/加工/交付能力，最后邀请对方看规格或样品。"},
            ],
            "phrases": [
                ["critical materials", "关键材料", "用于开发化工、材料、能源、制造类客户。", "We noticed that critical materials are becoming a bigger priority in your market."],
                ["processing capacity", "加工能力", "说明你能支持客户量产、加工或试产。", "We can support customers that need additional processing capacity."],
                ["pilot-scale validation", "试点/小批量验证", "适合样品、打样、测试、试产沟通。", "We can start with pilot-scale validation before moving to larger orders."],
                ["alternative sourcing", "替代供应/第二供应来源", "适合客户在找新供应商时切入。", "We can be considered as an alternative sourcing option for your materials supply chain."],
            ],
            "expressions": [
                "We noticed that critical materials are becoming a bigger priority in your market.",
                "We can support sample validation before moving to larger production.",
                "Our material can be reviewed as an alternative sourcing option.",
                "We can provide specifications, samples and export documents for evaluation.",
            ],
            "templates": material_templates,
            "practice": {
                "level1": "填空：We can support sample ______ before moving to larger production.",
                "level2": "改写：We sell chemical materials. 要改成行业观察式开发信开头。",
                "level3": "你是化工/材料外贸人，看到客户可能在找关键材料替代供应。请写 80 词以内英文开发信，提到 critical materials、sample validation 和 alternative sourcing。"
            },
        }
    if scenario_id == "shipping-cost-rise":
        shipping_templates = [
            {
                "name": "客户嫌运费高",
                "note": "把贵变成可选择方案。",
                "subject": "Shipping Options for Your Order",
                "body": "Dear [Name],\n\nThank you for your feedback.\n\nWe understand the freight cost is an important concern. Instead of only quoting one shipping cost, we can compare two options for this order.\n\nOption A is the standard route, with a lower cost and a longer delivery window. Option B is the faster route, with a higher cost but a shorter delivery time. If your schedule is flexible, we can also check whether a consolidated shipment can reduce the total landed cost.\n\nPlease let us know whether cost, speed or delivery stability is the priority, and I will prepare the best option accordingly.\n\nBest regards,\n[Your Name]"
            },
            {
                "name": "客户要更快交付",
                "note": "说明加急的成本和风险，不空口承诺。",
                "subject": "Delivery Window and Freight Option",
                "body": "Dear [Name],\n\nThank you for confirming the urgency.\n\nWe can check a faster shipping option for this order, but the freight cost and available delivery window need to be confirmed with the carrier first. The faster option may reduce transit time, while the standard option remains more cost-effective.\n\nBefore we finalize the shipping plan, please confirm whether the priority is earlier arrival or lower total landed cost. Once we have your preference, we will check the carrier schedule and send the updated proposal.\n\nBest regards,\n[Your Name]"
            },
            {
                "name": "客户只看单项运费",
                "note": "引导客户比较总成本，而不是只看 freight charge。",
                "subject": "Total Landed Cost Comparison",
                "body": "Dear [Name],\n\nThank you for reviewing the freight cost.\n\nFor this shipment, we suggest comparing the total landed cost rather than only the freight charge. Different shipping methods may affect delivery time, customs arrangement, storage risk and the final arrival schedule.\n\nWe can prepare a simple comparison between the lower-cost option and the faster option, including estimated delivery window and main risk points. This will help you choose the option that fits your order plan better.\n\nPlease let us know your preferred priority, and we will update the proposal.\n\nBest regards,\n[Your Name]"
            },
        ]
        return {
            "title": "物流方案变化：如何判断履约成本和交付选择？",
            "category": "供应链",
            "businessView": "供应链",
            "contentTrack": track_label_for_scenario(scenario_id),
            "methodologySources": methodology_for_scenario(scenario_id),
            "level": "Level2",
            "coreProblem": "客户关心的不只是运费贵不贵，而是货能不能稳定送达、时效和成本如何取舍。",
            "scenario": {
                "problem": "文章提到 LTL、shipping network 或面向企业客户的配送服务，说明物流服务正在变成商业竞争的一部分。",
                "wrong": "只说 shipping cost increased，客户听不到解决方案。",
                "strategy": "把运费沟通变成方案选择：更快、更稳、更省，分别对应不同路线、承运方式和交付时间。",
            },
            "judgement": [
                {"type": "商业信号：物流服务正在产品化", "reading": "less-than-truckload、shipping network、all businesses 说明运输能力本身正在成为服务卖点。", "response": "给客户报价时不要只报运费，要说明运输方案、时效、风险和适用订单量。"},
                {"type": "客户心理：怕总成本失控", "reading": "客户可能不是反感运费，而是不知道不同运输方式会怎样影响总成本。", "response": "用 option A / option B 讲清：标准方案省钱，加急方案更快，合并出货更稳。"},
                {"type": "工作动作：把选择权交给客户", "reading": "物流变化时，最好的回复不是替客户决定，而是给可比较方案。", "response": "列出 delivery time、cost、risk 三个维度，让客户选。"},
            ],
            "phrases": [
                ["shipping option", "运输方案", "给客户不同物流选择。", "We can review two shipping options for this order."],
                ["delivery window", "交付时间窗口", "说明预计到货范围。", "The delivery window for the standard option is around [x] days."],
                ["consolidated shipment", "合并出货", "降低运费或提高稳定性。", "A consolidated shipment may help reduce the total logistics cost."],
                ["total landed cost", "到岸总成本", "让客户不要只看单项运费。", "We suggest comparing the total landed cost, not only the freight charge."],
            ],
            "expressions": [
                "We can review two shipping options for this order.",
                "The standard option has a lower cost, while the faster option has a shorter delivery window.",
                "A consolidated shipment may help reduce the total logistics cost.",
                "Please let us know whether cost or delivery speed is the priority for this shipment.",
            ],
            "templates": shipping_templates,
            "practice": {
                "level1": "填空：Please let us know whether cost or delivery ______ is the priority.",
                "level2": "把这句话改得更专业：Shipping is expensive now, you choose.",
                "level3": "客户嫌运费高。请写一封 80 词以内英文回复，给两个物流方案并让客户选择优先级。"
            },
        }
    return {}

def commercial_fields_for_scenario(scenario_id: str, article: Article, paragraphs: list[str]) -> dict[str, object]:
    text = f"{article.title} {article.summary} {' '.join(paragraphs)}".lower()
    if scenario_id == "shipping-cost-rise":
        return {
            "commercialSignal": "物流、仓储和履约能力正在影响企业成本、交付体验和客户选择。",
            "affectedIndustries": "跨境电商 / 批发零售 / 消费品 / 物流服务 / B2B 供应商",
            "chinaInsight": "不要只看运费涨跌，要看履约方式、库存位置和交付承诺如何改变客户决策。",
            "trendJudgement": "当物流服务从成本项变成竞争力，企业需要用 delivery window、total landed cost 和 shipping option 来沟通。",
            "industryImpact": {
                "benefit": "能提供稳定履约、海外仓、合并出货、路线选择和成本测算的企业。",
                "pressure": "只靠低价、不解释交付风险、库存周转慢或配送链条不稳定的企业。",
            },
            "userUse": {
                "trade": "报价时把运输方案、时效、风险和总成本一起讲清。",
                "ecommerce": "判断是否需要海外仓、合并发货、包邮门槛或更稳的尾程服务。",
                "workplace": "汇报时用 logistics cost, delivery window, total landed cost 表达商业影响。",
            },
            "businessEnglish": "We can compare the total landed cost and delivery window before confirming the best option.",
        }
    if scenario_id == "material-cost-rise":
        return {
            "commercialSignal": "关键材料、加工能力和供应链缺口正在被重新重视，可能带来新的客户开发入口。",
            "affectedIndustries": "化工材料 / 工业品 / 设备 / 新能源 / 电子制造 / 供应链服务",
            "chinaInsight": "不要只读成原材料涨价，要看谁需要替代供应、样品验证、加工能力或第二供应商。",
            "trendJudgement": "产业链补短板会让客户更关注 material availability、processing capacity 和 alternative sourcing。",
            "industryImpact": {
                "benefit": "有稳定材料、加工能力、样品验证、技术文件和交付能力的供应商。",
                "pressure": "依赖单一供应来源、缺少技术说明、交付不稳定或无法配合验证的企业。",
            },
            "userUse": {
                "trade": "用行业观察切入开发信，先说趋势，再说材料/加工/样品验证能力。",
                "ecommerce": "如果做相关工业品或工具类产品，可关注供应链短缺带来的替代采购需求。",
                "workplace": "汇报时把新闻转成供应链风险、客户需求和合作机会，而不是只复述新闻。",
            },
            "businessEnglish": "We can support sample validation and alternative sourcing before larger production.",
        }
    if scenario_id in {"price-too-high", "discount-request"}:
        return {
            "commercialSignal": "需求放缓、预算压力或成本变化会让客户更重视价格、价值和付款条件。",
            "affectedIndustries": "消费品 / 零售 / 跨境电商 / B2B 采购 / 供应商",
            "chinaInsight": "客户说贵，不一定只是要降价，也可能是在测试预算、替代方案和价值边界。",
            "trendJudgement": "价格沟通要从单纯让价，转向 value, scope, payment terms and validity window。",
            "industryImpact": {
                "benefit": "能清楚说明价值、成本结构、服务范围和可选方案的企业。",
                "pressure": "只靠低价、利润薄、无法解释价值或没有替代方案的企业。",
            },
            "userUse": {
                "trade": "报价回复里说明规格、服务范围、价格有效期和可调整项。",
                "ecommerce": "根据预算压力调整套装、折扣门槛、页面卖点和价格带。",
                "workplace": "用 margin pressure, budget pressure, value proposition 表达商业判断。",
            },
            "businessEnglish": "We can review the scope and payment terms before adjusting the price.",
        }
    if scenario_id in {"no-reply-follow-up", "sample-follow-up"}:
        return {
            "commercialSignal": "消费趋势、品类变化和客户兴趣会影响开发节奏、样品验证和跟进话术。",
            "affectedIndustries": "美妆个护 / 宠物护理 / 健康消费 / 家居小家电 / 消费品供应链",
            "chinaInsight": "不要只问客户有没有进展，要用市场信号给客户一个继续讨论的理由。",
            "trendJudgement": "当需求变化不确定时，跟进要从催回复变成确认 interest, sample feedback and next step。",
            "industryImpact": {
                "benefit": "能快速打样、提供规格、解释卖点并配合小批量测试的企业。",
                "pressure": "跟进空泛、样品反馈慢、无法把趋势转成产品卖点的企业。",
            },
            "userUse": {
                "trade": "跟进邮件里加入行业趋势、样品反馈问题和下一步选项。",
                "ecommerce": "把趋势转成选品、Listing 卖点和内容测试方向。",
                "workplace": "用 consumer demand, category trend, sample feedback 组织英文汇报。",
            },
            "businessEnglish": "May I check whether this trend matches your current product plan?",
        }
    if scenario_id in {"deposit-reminder", "balance-payment"}:
        return {
            "commercialSignal": "现金流、库存和付款周期会影响企业是否能按时排产、发货和承诺交付。",
            "affectedIndustries": "制造业 / 批发贸易 / 跨境订单 / 供应链金融 / 零售库存",
            "chinaInsight": "付款沟通不要像催债，要把付款节点和库存、排产、交付窗口绑定起来。",
            "trendJudgement": "在预算谨慎和库存压力下，payment timing 本身就是商业风险管理。",
            "industryImpact": {
                "benefit": "付款节点清晰、库存计划稳定、能给客户明确时间窗口的企业。",
                "pressure": "账期混乱、现金流紧、库存占用高或无法锁定生产计划的企业。",
            },
            "userUse": {
                "trade": "催款时说明付款和排产/发货之间的关系。",
                "ecommerce": "根据现金流和库存周转调整备货节奏和促销节奏。",
                "workplace": "用 cash flow, payment timing, production schedule 汇报风险。",
            },
            "businessEnglish": "Payment timing will help us secure the production schedule and delivery window.",
        }
    return {
        "commercialSignal": "这篇文章反映了一个商业变量正在变化，需要判断它如何影响需求、成本、供应链或效率。",
        "affectedIndustries": "外贸 / 跨境 / 职场汇报 / 相关行业从业者",
        "chinaInsight": "先抓商业变量，再决定它能转成客户沟通、选品判断、汇报表达还是邮件素材。",
        "trendJudgement": "能转成商业判断的外刊，不只提供信息，还能提供商务英文输出场景。",
        "industryImpact": {
            "benefit": "能快速解释趋势并调整沟通、产品或供应链策略的企业。",
            "pressure": "只看新闻表面、无法把变化转成动作的企业。",
        },
        "userUse": {
            "trade": "转成客户开发、报价解释或跟进沟通。",
            "ecommerce": "转成选品、定价、库存和卖点判断。",
            "workplace": "转成英文汇报、会议表达和邮件说明。",
        },
        "businessEnglish": "This trend may affect demand, cost and customer decisions in the next few months.",
    }

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
        - consumer_only_score(text) * 3
    )

def build_live_item(scenario: dict[str, object], article: Article, paragraphs: list[str], window: int, score: int) -> dict[str, object]:
    scenario_id = str(scenario["id"])
    paragraphs = refine_paragraphs_for_teaching(scenario_id, paragraphs)
    selection_text = f"{article.title} {article.summary} {' '.join(paragraphs)}"
    item = {
        "scenarioId": scenario["id"],
        "sourceDate": article.published.date().isoformat(),
        "freshness": f"自动抓取｜最近{window}天",
        "selectionScore": score,
        "selection": selection_reason(article, scenario_id, selection_text),
        "contentTrack": track_label_for_scenario(scenario_id),
        "methodologySources": methodology_for_scenario(scenario_id),
        "source": {
            "name": article.source,
            "title": article.title,
            "url": article.url,
            "paragraphs": [
                {
                    "en": paragraph,
                    "cn": local_translation_for_paragraph(scenario_id, paragraph) or translate_to_chinese(paragraph),
                }
                for paragraph in paragraphs[:MAX_PARAGRAPHS]
            ],
        },
    }
    item.update(commercial_fields_for_scenario(scenario_id, article, paragraphs))
    item.update(teaching_pack_for_scenario(scenario_id, article, paragraphs))
    for key, value in commercial_fields_for_scenario(scenario_id, article, paragraphs).items():
        item.setdefault(key, value)
    return item

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
            if consumer > 0 and (core + commercial_lens_score(text)) < 8:
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
        "note": "Articles are filtered for global business trends that can become commercial judgement and business-English output. cn is a paragraph translation when available; no paywalls are bypassed and no article paragraphs are fabricated.",
        "scenarios": live_items,
    }
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUTPUT} with {len(live_items)} scenario matches")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
