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
    "investment", "investments", "partnership", "partnerships", "competitiveness",
    "economic security", "capacity", "manufacturing capacity", "service offering",
    "shipping network", "less-than-truckload", "ltl", "open to all businesses",
    "business customers", "enterprise customers",
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
    {"id": "material-cost-rise", "keywords": ["raw material", "raw materials", "materials", "commodity", "commodities", "critical minerals", "rare earth", "input costs", "costs", "prices", "manufacturer", "manufacturing"]},
    {"id": "delivery-delay", "keywords": ["delay", "delivery", "lead time", "lead times", "supply chain", "production", "shipment", "shipping", "factory", "capacity"]},
    {"id": "shipping-cost-rise", "keywords": ["freight", "shipping", "logistics", "port", "container", "containers", "route", "delivery", "shipping capacity", "freight rates"]},
    {"id": "deposit-reminder", "keywords": ["payment", "cash flow", "working capital", "deposit", "invoice", "liquidity", "payment terms"]},
    {"id": "balance-payment", "keywords": ["payment", "invoice", "cash flow", "shipment", "credit", "balance", "receivables", "payment terms"]},
    {"id": "price-too-high", "keywords": ["price", "prices", "pricing", "margin", "margins", "input costs", "inflation", "buyer", "buyers", "value", "supplier", "suppliers", "cost pressure"]},
    {"id": "discount-request", "keywords": ["discount", "demand", "buyer", "buyers", "sales", "pricing", "budget", "order volume", "purchase order"]},
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

def teaching_pack_for_scenario(scenario_id: str, article: Article, paragraphs: list[str]) -> dict[str, object]:
    if scenario_id == "material-cost-rise":
        return {
            "title": "原材料与供应链变化，如何向客户解释价格和交付风险？",
            "category": "报价与议价",
            "businessView": "原材料与供应链",
            "level": "Level2",
            "coreProblem": "客户不只关心价格，还会关心材料供应是否稳定、报价能保持多久、交期有没有风险。",
            "scenario": {
                "problem": "文章提到关键材料、加工能力或供应链缺口，这会影响成本、报价有效期和交付确定性。",
                "wrong": "只说 raw material cost increased，客户听完只会觉得你在找涨价理由。",
                "strategy": "把沟通拆成三层：材料供应变化、对报价/交期的影响、客户现在可以选择的下单窗口或替代方案。",
            },
            "judgement": [
                {"type": "商业信号：材料供应不只是价格问题", "reading": "critical minerals、processing capacity、materials supply chain 说明企业担心的是供应稳定性，而不只是短期成本。", "response": "回复客户时要同时说明 price validity、material availability 和 delivery planning。"},
                {"type": "客户心理：怕你借机涨价", "reading": "客户会怀疑供应商是否把外部新闻当成涨价理由，所以不能只说市场涨了。", "response": "用具体边界降低抵触：当前报价保留到某日期，之后按材料确认情况更新。"},
                {"type": "工作动作：给可选方案", "reading": "供应链不确定时，客户需要能向内部解释的选择，而不是一句 take it or leave it。", "response": "给两个方向：提前锁料保持当前价，或接受较长交期等待下一轮材料确认。"},
            ],
            "phrases": [
                ["material availability", "材料可得性", "解释为什么报价和交期需要确认。", "We need to confirm material availability before keeping the same delivery schedule."],
                ["secure the material", "锁定材料", "让客户理解尽快确认订单的意义。", "If the order is confirmed this week, we can secure the material for your production plan."],
                ["price validity", "报价有效期", "控制价格承诺边界。", "The current price validity can be kept until [date]."],
                ["supply chain stability", "供应链稳定性", "把新闻转成客户能理解的风险。", "We are monitoring supply chain stability before confirming the next production batch."],
            ],
            "expressions": [
                "Material availability is becoming more important for production planning.",
                "We can keep the current price valid for orders confirmed before [date].",
                "If you confirm the order earlier, we can secure the material and reduce the delivery risk.",
                "The updated offer may depend on the next material confirmation from our supplier.",
            ],
            "templates": make_templates(
                "Material Availability and Price Validity",
                "Recent changes in the materials supply chain may affect both price validity and production planning. We are checking material availability before confirming the next batch.",
                "If your order can be confirmed before [date], we can try to secure the material under the current offer. If the confirmation is later, we may need to update the price or delivery schedule.",
                "We do not want to make an uncertain commitment before the material is secured, so the current offer should be treated as valid only within the stated window."
            ),
            "practice": {
                "level1": "填空：We need to confirm material ______ before keeping the same delivery schedule.",
                "level2": "把这句话改得更专业：The material is hard to buy now, so the price may change.",
                "level3": "客户问为什么报价有效期变短。请写一封 80 词以内英文回复，说明材料供应、报价有效期和下单窗口。"
            },
        }
    if scenario_id == "shipping-cost-rise":
        return {
            "title": "物流方案变化，如何向客户解释运费和交付选择？",
            "category": "订单与交付",
            "businessView": "物流与交付",
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
            "templates": make_templates(
                "Shipping Options for Your Order",
                "The logistics market is offering more service choices, but each option has a different cost and delivery window. For your order, we can compare the standard option, the faster option, and a consolidated shipment.",
                "Please let us know whether cost, speed, or stability is the priority. Based on your choice, I will prepare the most suitable shipping proposal.",
                "Before we confirm the final freight cost, we need to align on the shipping method and delivery requirement."
            ),
            "practice": {
                "level1": "填空：Please let us know whether cost or delivery ______ is the priority.",
                "level2": "把这句话改得更专业：Shipping is expensive now, you choose.",
                "level3": "客户嫌运费高。请写一封 80 词以内英文回复，给两个物流方案并让客户选择优先级。"
            },
        }
    return {}

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
    item = {
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
    item.update(teaching_pack_for_scenario(scenario_id, article, paragraphs))
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
