#!/usr/bin/env python3
"""
Fetch public business-news candidates for the Business English page.

What this script does:
- Reads public RSS feeds.
- Keeps articles published within the last 90 days.
- Scores articles against real-work scenarios.
- Fetches public article pages when available.
- Extracts 2-3 relevant public paragraphs without inventing content.
- Writes business-english-live.json next to the HTML page.

What this script does not do:
- It does not bypass paywalls.
- It does not use the overseas server IP.
- It does not fabricate article paragraphs when the article body is unavailable.
- It does not translate with AI by default. Fill `cn` in your existing GPT step,
  or add your own translation stage before publishing.
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
LOOKBACK_DAYS = 90
MAX_ARTICLE_SECONDS = 18
USER_AGENT = "BusinessEnglishWorkDesk/1.0 (+personal learning tool)"

FEEDS = [
    {
        "name": "The Guardian Business",
        "url": "https://www.theguardian.com/business/rss",
        "quality": 7,
    },
    {
        "name": "BBC Business",
        "url": "https://feeds.bbci.co.uk/news/business/rss.xml",
        "quality": 6,
    },
    {
        "name": "Financial Times",
        "url": "https://www.ft.com/?format=rss",
        "quality": 9,
    },
    {
        "name": "Wall Street Journal",
        "url": "https://feeds.a.dj.com/rss/WSJcomUSBusiness.xml",
        "quality": 9,
    },
    {
        "name": "Harvard Business Review",
        "url": "https://feeds.harvardbusiness.org/harvardbusiness",
        "quality": 8,
    },
]

SCENARIOS = [
    {
        "id": "price-too-high",
        "keywords": [
            "price",
            "prices",
            "pricing",
            "margin",
            "margins",
            "cost",
            "costs",
            "inflation",
            "customer",
            "customers",
            "buyer",
            "buyers",
            "value",
            "supplier",
            "suppliers",
        ],
    },
    {
        "id": "discount-request",
        "keywords": ["discount", "demand", "buyer", "buyers", "sales", "pricing", "budget"],
    },
    {
        "id": "material-cost-rise",
        "keywords": ["raw material", "materials", "commodity", "commodities", "input costs", "costs", "prices"],
    },
    {
        "id": "delivery-delay",
        "keywords": ["delay", "delivery", "supply chain", "production", "shipment", "shipping"],
    },
    {
        "id": "shipping-cost-rise",
        "keywords": ["freight", "shipping", "logistics", "port", "container", "route", "delivery"],
    },
    {
        "id": "deposit-reminder",
        "keywords": ["payment", "cash flow", "working capital", "deposit", "invoice"],
    },
    {
        "id": "balance-payment",
        "keywords": ["payment", "invoice", "cash flow", "shipment", "credit", "balance"],
    },
    {
        "id": "no-reply-follow-up",
        "keywords": ["sales", "customer", "demand", "buyer", "buyers", "confidence", "orders"],
    },
    {
        "id": "sample-follow-up",
        "keywords": ["sample", "product", "quality", "consumer", "design", "testing"],
    },
    {
        "id": "quality-complaint",
        "keywords": ["quality", "recall", "complaint", "defect", "safety", "customer service"],
    },
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


def is_good_paragraph(text: str) -> bool:
    if len(text) < 80 or len(text) > 900:
        return False
    lowered = text.lower()
    bad_bits = [
        "sign up",
        "newsletter",
        "cookies",
        "all rights reserved",
        "advertisement",
        "skip to",
        "share this",
    ]
    return not any(bit in lowered for bit in bad_bits)


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
        items.append(
            Article(
                source=str(feed["name"]),
                quality=int(feed["quality"]),
                title=title,
                url=link,
                published=published,
                summary=summary,
            )
        )
    return items


def keyword_score(text: str, keywords: Iterable[str]) -> int:
    lowered = text.lower()
    score = 0
    for keyword in keywords:
        if keyword in lowered:
            score += 3 if " " in keyword else 1
    return score


def extract_article_paragraphs(article: Article, keywords: list[str]) -> list[str]:
    try:
        page = fetch_text(article.url)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print(f"[warn] article failed: {article.url} {exc}", file=sys.stderr)
        return []

    parser = ParagraphParser()
    parser.feed(page)
    paragraphs = parser.paragraphs
    if not paragraphs:
        return []

    ranked = sorted(
        paragraphs,
        key=lambda para: keyword_score(para, keywords),
        reverse=True,
    )
    useful = [para for para in ranked if keyword_score(para, keywords) > 0]
    picked = useful[:3] if useful else paragraphs[:3]
    return picked[:3]


def fallback_summary_paragraphs(article: Article) -> list[str]:
    if is_good_paragraph(article.summary):
        return [article.summary]
    return []


def build_live_item(scenario: dict[str, object], article: Article, paragraphs: list[str]) -> dict[str, object]:
    return {
        "scenarioId": scenario["id"],
        "sourceDate": article.published.date().isoformat(),
        "freshness": "自动抓取",
        "source": {
            "name": article.source,
            "title": article.title,
            "url": article.url,
            "paragraphs": [
                {
                    "en": paragraph,
                    "cn": "",
                    "insight": "",
                }
                for paragraph in paragraphs[:3]
            ],
        },
    }


def main() -> int:
    now = dt.datetime.now(dt.timezone.utc)
    cutoff = now - dt.timedelta(days=LOOKBACK_DAYS)
    articles: list[Article] = []
    for feed in FEEDS:
        articles.extend(parse_feed(feed))
        time.sleep(0.7)

    fresh_articles = [article for article in articles if article.published >= cutoff]
    live_items: list[dict[str, object]] = []

    for scenario in SCENARIOS:
        keywords = list(scenario["keywords"])
        scored: list[tuple[int, Article]] = []
        for article in fresh_articles:
            text = f"{article.title} {article.summary}"
            score = keyword_score(text, keywords) + article.quality
            if score > article.quality:
                scored.append((score, article))
        scored.sort(key=lambda pair: (pair[0], pair[1].published), reverse=True)

        for _, article in scored[:6]:
            paragraphs = extract_article_paragraphs(article, keywords)
            if not paragraphs:
                paragraphs = fallback_summary_paragraphs(article)
            if paragraphs:
                live_items.append(build_live_item(scenario, article, paragraphs))
                break

    payload = {
        "generatedAt": now.isoformat(),
        "lookbackDays": LOOKBACK_DAYS,
        "note": "cn and insight are intentionally blank unless your translation/GPT step fills them. The page will show English only for blank translations.",
        "scenarios": live_items,
    }
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUTPUT} with {len(live_items)} scenario matches")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
