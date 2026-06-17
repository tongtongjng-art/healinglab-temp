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

GLOBAL_EXCLUDE_TOPICS = [
    "housing market", "house prices", "home prices", "mortgage", "real estate",
    "celebrity", "film", "music", "sports", "election", "politician",
    "crime", "murder", "lawsuit", "pure stock", "stock market debut",
]

GLOBAL_BUSINESS_TERMS = [
    "business", "company", "companies", "consumer demand", "retail", "supplier", "supply chain",
    "shipping", "freight", "tariff", "tariffs", "currency", "exchange rate", "cost", "pricing",
    "inventory", "cash flow", "payment", "beauty", "pet care", "home goods", "small appliances",
    "health and wellness", "ai", "automation", "productivity", "budget pressure", "cautious spending",
]

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
    {
        "name": "Retail Dive",
        "url": "https://www.retaildive.com/feeds/news/",
        "quality": 8,
    },
    {
        "name": "Marketing Dive",
        "url": "https://www.marketingdive.com/feeds/news/",
        "quality": 7,
    },
    {
        "name": "CIO Dive",
        "url": "https://www.ciodive.com/feeds/news/",
        "quality": 7,
    },
]

SCENARIOS = [
    {
        "id": "price-too-high",
        "keywords": [
            "price", "prices", "pricing", "margin", "margins", "cost", "costs", "inflation",
            "customer", "customers", "buyer", "buyers", "value", "supplier", "suppliers",
            "tariff", "tariffs", "currency", "exchange rate", "budget pressure", "cautious spending",
        ],
    },
    {
        "id": "discount-request",
        "keywords": ["discount", "demand", "buyer", "buyers", "sales", "pricing", "budget", "consumer demand", "retail trend", "cautious spending", "budget pressure"],
    },
    {
        "id": "material-cost-rise",
        "keywords": ["raw material", "materials", "commodity", "commodities", "input costs", "costs", "prices", "tariffs", "currency", "exchange rate", "inventory", "supply chain"],
    },
    {
        "id": "delivery-delay",
        "keywords": ["delay", "delivery", "supply chain", "production", "shipment", "shipping", "inventory", "supplier risk", "lead time"],
    },
    {
        "id": "shipping-cost-rise",
        "keywords": ["freight", "shipping", "logistics", "port", "container", "route", "delivery", "warehouse", "fulfilment", "last mile", "inventory"],
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
        "keywords": ["sample", "product", "quality", "consumer", "design", "testing", "pet care", "health and wellness", "beauty", "home goods", "small appliances", "retail trend"],
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
        joined = f"{title} {summary}".lower()
        if any(bit in joined for bit in GLOBAL_EXCLUDE_TOPICS):
            continue
        if not any(bit in joined for bit in GLOBAL_BUSINESS_TERMS):
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


def commercial_pack_for_scenario(scenario_id: str) -> dict[str, object]:
    packs = {
        "price-too-high": {
            "title": "成本上涨与买方压价：如何把价格压力转成价值说明？",
            "category": "成本与利润",
            "businessView": "成本上涨",
            "commercialSignal": "客户压价背后通常是预算压力、需求放缓或内部审批收紧。",
            "affectedIndustries": "B2B 供应商 / 消费品 / 跨境电商 / 批发零售 / 制造业",
            "chinaInsight": "不要只学一句降价回复，要学会解释成本、价值、交付责任和可替代方案。",
            "businessEnglish": "We can review the scope and payment terms before adjusting the price.",
        },
        "discount-request": {
            "title": "消费谨慎与折扣请求：如何不牺牲利润地回应？",
            "category": "需求变化",
            "businessView": "需求疲软",
            "commercialSignal": "当需求变谨慎，客户会更频繁测试折扣、账期和订单条件。",
            "affectedIndustries": "零售 / 消费品 / 美妆个护 / 家居小家电 / 跨境电商",
            "chinaInsight": "折扣不是单向让利，应和数量、付款、交付、规格或长期合作绑定。",
            "businessEnglish": "The discount can be reviewed based on the order quantity and payment terms.",
        },
        "material-cost-rise": {
            "title": "材料与成本波动：如何判断报价和供应风险？",
            "category": "成本与利润",
            "businessView": "成本上涨",
            "commercialSignal": "原材料、能源、关税或汇率变化会影响报价有效期、库存和交付承诺。",
            "affectedIndustries": "制造业 / 工业品 / 化工材料 / 消费品供应链 / B2B 采购",
            "chinaInsight": "把成本变化转成报价有效期、替代材料、库存锁定和客户沟通方案。",
            "businessEnglish": "The current price can be kept for orders confirmed before the validity date.",
        },
        "delivery-delay": {
            "title": "交付不确定性：如何提前沟通风险和新时间表？",
            "category": "供应链",
            "businessView": "供应链",
            "commercialSignal": "供应链或生产节奏变化会直接影响交付承诺和客户体验。",
            "affectedIndustries": "制造业 / 物流 / 外贸订单 / 跨境履约 / 批发零售",
            "chinaInsight": "交付问题不能等客户追问，要主动给原因、影响、新时间和补救动作。",
            "businessEnglish": "We will share the revised delivery schedule and keep you updated on the progress.",
        },
        "shipping-cost-rise": {
            "title": "物流方案变化：如何判断履约成本和交付选择？",
            "category": "供应链",
            "businessView": "供应链",
            "commercialSignal": "物流、仓储和履约能力正在影响企业成本、交付体验和客户选择。",
            "affectedIndustries": "跨境电商 / 批发零售 / 消费品 / 物流服务 / B2B 供应商",
            "chinaInsight": "不要只看运费涨跌，要看履约方式、库存位置和交付承诺如何改变客户决策。",
            "businessEnglish": "We can compare the total landed cost and delivery window before confirming the best option.",
        },
        "deposit-reminder": {
            "title": "现金流与排产：如何把付款节点说成订单推进？",
            "category": "成本与利润",
            "businessView": "库存压力",
            "commercialSignal": "现金流和付款周期会影响企业是否能锁定库存、排产和交付窗口。",
            "affectedIndustries": "制造业 / 批发贸易 / 供应链金融 / 跨境订单 / 零售库存",
            "chinaInsight": "催款不是催债，而是说明付款和排产、备料、交付之间的关系。",
            "businessEnglish": "Payment timing will help us secure the production schedule and delivery window.",
        },
        "balance-payment": {
            "title": "发货前付款节点：如何不伤关系地推进尾款？",
            "category": "成本与利润",
            "businessView": "库存压力",
            "commercialSignal": "尾款延迟会影响出货、库存占用和现金回笼。",
            "affectedIndustries": "制造业 / 外贸订单 / 批发贸易 / 跨境履约 / 物流",
            "chinaInsight": "先给订单状态，再把尾款和发货安排绑定，避免语气像催债。",
            "businessEnglish": "Once the balance is received, we can arrange shipment immediately.",
        },
        "no-reply-follow-up": {
            "title": "需求不确定时，如何让客户继续回复？",
            "category": "商务表达",
            "businessView": "需求疲软",
            "commercialSignal": "客户不回复可能来自预算未定、需求变弱、内部审批慢或替代方案比较。",
            "affectedIndustries": "B2B 销售 / 跨境电商 / 消费品 / 客户成功 / 外贸开发",
            "chinaInsight": "不要反复问 any update，要给客户一个更容易回复的小问题或选项。",
            "businessEnglish": "Would it be helpful if I prepare a revised option based on your target budget?",
        },
        "sample-follow-up": {
            "title": "品类趋势与样品验证：如何把兴趣推进到下一步？",
            "category": "行业机会",
            "businessView": "零售趋势",
            "commercialSignal": "新品类、新消费信号和样品反馈会决定客户是否继续推进订单。",
            "affectedIndustries": "美妆个护 / 宠物护理 / 健康消费 / 家居小家电 / 消费品供应链",
            "chinaInsight": "样品跟进不要只问喜不喜欢，要问测试结果、规格、包装、使用场景和下一步计划。",
            "businessEnglish": "May I know how the sample evaluation is going?",
        },
        "quality-complaint": {
            "title": "质量风险与品牌信任：如何先稳住客户？",
            "category": "供应链",
            "businessView": "零售趋势",
            "commercialSignal": "质量问题不仅是售后成本，也会影响客户信任、复购和品牌声誉。",
            "affectedIndustries": "消费品 / 美妆个护 / 宠物护理 / 家居用品 / B2B 供应链",
            "chinaInsight": "第一封回复不要争责任，要先承接、收集证据、给调查节点和后续方案。",
            "businessEnglish": "We take your feedback seriously and will look into it immediately.",
        },
    }
    return packs.get(scenario_id, {})


def build_live_item(scenario: dict[str, object], article: Article, paragraphs: list[str]) -> dict[str, object]:
    scenario_id = str(scenario["id"])
    item = {
        "scenarioId": scenario_id,
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
    item.update(commercial_pack_for_scenario(scenario_id))
    return item


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
        "note": "Articles are filtered for global business trends that can become commercial judgement and business-English output. cn and insight are intentionally blank unless a translation/GPT step fills them.",
        "scenarios": live_items,
    }
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUTPUT} with {len(live_items)} scenario matches")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
