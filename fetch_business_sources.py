#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Business Briefing RSS fetcher
目标：为“商业外刊工作读本”生成 business-english-live.json

定位：
- 全球商业趋势
- 供应链与成本
- 关税与汇率
- 消费需求
- 零售与库存
- AI 效率
- 商务英文输出

输出兼容当前前端：
{
  "generated_at": "...",
  "scenarios": [
    {
      "scenarioId": "supply-chain-cost",
      "trend": "supply-chain-cost",
      "signal": "Supply Chain / Cost Pressure",
      "titleCn": "...",
      "why": "...",
      "action": "...",
      "english": "...",
      "source": {
        "name": "...",
        "title": "...",
        "url": "...",
        "summary": "...",
        "paragraphs": [{"en": "...", "cn": "...", "insight": "..."}]
      }
    }
  ]
}
"""

import json
import re
import time
import html
import hashlib
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime

try:
    import feedparser
except ImportError:
    raise SystemExit("Missing dependency: feedparser. Install with: pip3 install feedparser")


# 一、主粮仓：综合商业 + 供应链 + 零售 + 管理 + AI
RSS_FEEDS = {
    "The Guardian Business": "https://www.theguardian.com/business/rss",
    "BBC Business": "https://feeds.bbci.co.uk/news/business/rss.xml",
    "Wall Street Journal Business": "https://feeds.a.dj.com/rss/WSJcomUSBusiness.xml",
    "Harvard Business Review": "https://feeds.feedburner.com/harvardbusinessreview",
    "MIT Sloan Management Review": "https://sloanreview.mit.edu/feed/",
    "Supply Chain Brain": "https://www.supplychainbrain.com/rss/articles",
    "Retail Dive": "https://www.retaildive.com/feeds/news/",
    "VentureBeat AI": "https://venturebeat.com/category/ai/feed/",
}

# 可选源：有时会 403 / RSS 结构变化，不作为稳定核心
OPTIONAL_FEEDS = {
    "Financial Times": "https://www.ft.com/?format=rss",
    "McKinsey Insights": "https://www.mckinsey.com/insights/rss.xml",
    "The Economist Business": "https://www.economist.com/business/rss.xml",
}

CATEGORY_RULES = {
    "supply-chain-cost": {
        "signal": "Supply Chain / Cost Pressure",
        "keywords": [
            "supply chain", "supplier", "sourcing", "logistics", "shipping", "freight",
            "delivery", "warehouse", "inventory", "manufacturing", "factory",
            "input cost", "cost pressure", "margin pressure", "procurement",
            "raw material", "oil prices", "transport costs", "port", "container"
        ],
        "titleCn": "供应链与成本压力正在改变企业交付和利润逻辑",
        "why": "看懂成本、库存、物流和供应稳定性如何影响报价与交付。",
        "action": "转成报价解释、供应商沟通、交期说明和内部汇报中的可用表达。",
        "english": "manage cost pressure across the supply chain",
    },
    "tariff-currency": {
        "signal": "Tariff / Currency",
        "keywords": [
            "tariff", "tariffs", "duty", "customs", "trade war", "import tax",
            "export", "import", "currency", "exchange rate", "dollar", "yuan",
            "euro", "sterling", "fx", "foreign exchange", "trade policy"
        ],
        "titleCn": "关税与汇率变化正在重塑跨境成本和报价策略",
        "why": "看懂关税、汇率和贸易政策如何影响总 landed cost。",
        "action": "转成报价重算、客户解释、采购路线调整和风险提示中的可用表达。",
        "english": "reduce exposure to tariff-related cost pressure",
    },
    "consumer-demand": {
        "signal": "Consumer Demand",
        "keywords": [
            "consumer", "consumers", "demand", "spending", "shopper", "households",
            "budget pressure", "cautious spending", "price-sensitive", "value",
            "premium", "discount", "retail sales", "confidence", "cost of living",
            "beauty", "wellness", "pet care", "home goods", "small appliances"
        ],
        "titleCn": "消费需求变化正在影响定价、选品和客户沟通",
        "why": "看懂消费者预算、偏好和购买动机如何变化，避免只凭感觉判断市场。",
        "action": "转成选品判断、价格解释、客户开发和销售汇报中的可用表达。",
        "english": "consumer demand is shifting toward value-conscious choices",
    },
    "retail-inventory": {
        "signal": "Retail / Inventory",
        "keywords": [
            "retail", "retailer", "e-commerce", "ecommerce", "inventory", "stock",
            "private label", "promotion", "markdown", "discounting", "store",
            "brand", "sales channel", "holiday shopping", "category"
        ],
        "titleCn": "零售与库存变化正在影响促销、利润和现金流",
        "why": "看懂库存压力、折扣、零售需求和品牌利润之间的关系。",
        "action": "转成库存决策、促销规划、渠道沟通和销售复盘中的可用表达。",
        "english": "rebalance inventory while protecting margins",
    },
    "ai-productivity": {
        "signal": "AI Productivity",
        "keywords": [
            "ai", "artificial intelligence", "generative ai", "automation",
            "productivity", "workflow", "efficiency", "software", "chatbot",
            "customer service", "agentic", "machine learning", "enterprise ai",
            "automate", "operational efficiency"
        ],
        "titleCn": "AI 效率工具正在改变工作流程和运营成本结构",
        "why": "看懂 AI 不只是写文案，而是如何影响客服、运营、流程和生产率。",
        "action": "转成内部提案、流程优化、客服自动化和效率汇报中的可用表达。",
        "english": "streamline workflows and reduce manual workload",
    },
    "business-strategy": {
        "signal": "Business Strategy",
        "keywords": [
            "strategy", "operations", "resilience", "profit", "growth", "revenue",
            "management", "business model", "restructuring", "efficiency",
            "risk", "uncertainty", "investment", "pricing power"
        ],
        "titleCn": "企业正在重新校准增长、成本和运营韧性",
        "why": "看懂企业如何在不确定环境里调整战略、组织和资源配置。",
        "action": "转成汇报、复盘、管理沟通和商务邮件里的判断表达。",
        "english": "recalibrate our operating model",
    },
}

# 二、排除：不适合“商业判断 + 商务英文输出”的文章
EXCLUDE_PATTERNS = [
    r"\belection\b", r"\bcampaign\b", r"\bparliament\b", r"\bminister\b",
    r"\bcelebrity\b", r"\bhollywood\b", r"\bfilm\b", r"\bmovie\b",
    r"\bsport\b", r"\bfootball\b", r"\bsoccer\b", r"\btennis\b",
    r"\bproperty prices only\b", r"\bhouse prices\b",
    r"\bstock price\b", r"\bshare price\b", r"\bshares rise\b", r"\bshares fall\b",
    r"\bearnings per share\b", r"\bquarterly profit beats\b",
    r"\bcrypto\b", r"\bbitcoin\b",
]

SOURCE_PRIORITY = {
    "Wall Street Journal Business": 9,
    "Harvard Business Review": 9,
    "MIT Sloan Management Review": 8,
    "Supply Chain Brain": 8,
    "Retail Dive": 8,
    "The Guardian Business": 7,
    "BBC Business": 7,
    "VentureBeat AI": 6,
    "Financial Times": 9,
    "McKinsey Insights": 8,
    "The Economist Business": 9,
}

MAX_DAYS = 90
MAX_OUTPUT = 10


def clean_html(value: str) -> str:
    value = html.unescape(value or "")
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def parse_date(entry) -> str:
    # feedparser published_parsed
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if parsed:
        try:
            return datetime(*parsed[:6], tzinfo=timezone.utc).strftime("%Y-%m-%d")
        except Exception:
            pass

    raw = entry.get("published") or entry.get("updated") or ""
    if raw:
        try:
            dt = parsedate_to_datetime(raw)
            return dt.strftime("%Y-%m-%d")
        except Exception:
            pass

    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def is_recent(date_str: str) -> bool:
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return dt >= datetime.now(timezone.utc) - timedelta(days=MAX_DAYS)
    except Exception:
        return True


def excluded(text: str) -> bool:
    lower = text.lower()
    return any(re.search(pattern, lower) for pattern in EXCLUDE_PATTERNS)


def category_score(text: str, category: str) -> int:
    lower = text.lower()
    score = 0
    for kw in CATEGORY_RULES[category]["keywords"]:
        # phrase match
        if kw in lower:
            score += 4 if " " in kw else 2
    return score


def classify(text: str):
    scores = {cat: category_score(text, cat) for cat in CATEGORY_RULES}
    best = max(scores, key=scores.get)
    return best, scores[best], scores


def recency_score(date_str: str) -> int:
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        days = (datetime.now(timezone.utc) - dt).days
        if days <= 7:
            return 8
        if days <= 30:
            return 5
        if days <= 90:
            return 2
    except Exception:
        pass
    return 1


def fetch_url_text(url: str, timeout: int = 8) -> str:
    if not url:
        return ""
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 BusinessBriefingBot/1.0",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(350_000)
            charset = resp.headers.get_content_charset() or "utf-8"
            return raw.decode(charset, errors="ignore")
    except Exception:
        return ""


def extract_paragraphs_from_html(doc: str, fallback: str):
    if not doc:
        return [fallback] if fallback else []

    # Remove scripts/styles
    doc = re.sub(r"(?is)<script.*?</script>", " ", doc)
    doc = re.sub(r"(?is)<style.*?</style>", " ", doc)

    # Extract p tags
    paras = re.findall(r"(?is)<p[^>]*>(.*?)</p>", doc)
    cleaned = []
    for p in paras:
        text = clean_html(p)
        # keep meaningful English paragraphs
        if 80 <= len(text) <= 600 and re.search(r"[a-zA-Z]", text):
            # filter boilerplate
            if any(bad in text.lower() for bad in ["sign up", "newsletter", "cookies", "all rights reserved", "advertisement"]):
                continue
            cleaned.append(text)
        if len(cleaned) >= 3:
            break

    if not cleaned and fallback:
        cleaned = [fallback]

    return cleaned[:3]


def make_cn_explanation(category: str, source_name: str) -> str:
    mapping = {
        "supply-chain-cost": f"这段来自 {source_name} 的商业报道，核心不是单一新闻事件，而是供应链、成本、库存或交付稳定性正在发生变化。",
        "tariff-currency": f"这段来自 {source_name} 的商业报道，重点在关税、汇率或贸易规则如何改变企业的跨境成本。",
        "consumer-demand": f"这段来自 {source_name} 的商业报道，反映消费者预算、偏好或购买行为正在变化。",
        "retail-inventory": f"这段来自 {source_name} 的商业报道，说明零售、库存、折扣和利润之间的关系正在重新调整。",
        "ai-productivity": f"这段来自 {source_name} 的商业报道，说明 AI 或自动化正在影响工作流程、效率和运营成本。",
        "business-strategy": f"这段来自 {source_name} 的商业报道，反映企业正在重新校准增长、成本、风险和运营韧性。",
    }
    return mapping.get(category, mapping["business-strategy"])


def make_insight(category: str) -> str:
    return {
        "supply-chain-cost": "阅读重点：判断它会如何影响采购路线、交期、报价和客户解释。",
        "tariff-currency": "阅读重点：判断它会如何影响 landed cost、价格有效期和报价重算。",
        "consumer-demand": "阅读重点：判断需求变化背后的预算压力、消费偏好和产品定位机会。",
        "retail-inventory": "阅读重点：判断库存压力是否会引发折扣、现金流压力或渠道变化。",
        "ai-productivity": "阅读重点：判断 AI 是否真正嵌入流程，而不是停留在内容生成层面。",
        "business-strategy": "阅读重点：判断企业正在如何调整战略、流程和资源配置。",
    }.get(category, "阅读重点：从新闻里提炼可转化为工作表达的商业判断。")


def template_for_category(category: str, english: str) -> str:
    return {
        "supply-chain-cost": f"Hi [Name],\n\nWe are reviewing the current supply chain situation and will {english} where possible. I will share an updated delivery and cost assessment once we confirm the latest supplier information.\n\nBest,\n[Your Name]",
        "tariff-currency": f"Hi [Name],\n\nGiven the recent changes in tariffs and exchange rates, we are reviewing the pricing basis to {english}. I will send a revised quotation once the cost impact is confirmed.\n\nBest,\n[Your Name]",
        "consumer-demand": f"Hi [Name],\n\nRecent market signals suggest that {english}. We may need to adjust our positioning and messaging to reflect this change in customer priorities.\n\nBest,\n[Your Name]",
        "retail-inventory": f"Hi [Name],\n\nWe are reviewing current inventory and sales signals to {english}. This should help us protect margins while responding to market demand.\n\nBest,\n[Your Name]",
        "ai-productivity": f"Hi Team,\n\nWe can {english} by introducing a more structured automation process for repetitive support and operational tasks.\n\nBest,\n[Your Name]",
        "business-strategy": f"Hi Team,\n\nGiven the latest market changes, we may need to {english} and align our next steps with the current risk and cost environment.\n\nBest,\n[Your Name]",
    }.get(category, f"Hi [Name],\n\nWe are monitoring this trend closely and will {english} where appropriate.\n\nBest,\n[Your Name]")


def practice_for_category(category: str) -> str:
    return {
        "supply-chain-cost": "用英文写 2 句话，向客户说明供应链或成本变化，并提出下一步方案。",
        "tariff-currency": "用英文写 2 句话，解释关税或汇率变化对报价的影响。",
        "consumer-demand": "用英文写 2 句话，说明消费者需求变化对产品定位的影响。",
        "retail-inventory": "用英文写 2 句话，说明库存和促销策略需要如何调整。",
        "ai-productivity": "用英文写 2 句话，建议团队用自动化减少重复工作。",
        "business-strategy": "用英文写 2 句话，向团队汇报市场变化和应对方向。",
    }.get(category, "用英文写 2 句话，把这条新闻转成工作判断。")


def build_article(entry, source_name: str):
    title = clean_html(entry.get("title", ""))
    summary = clean_html(entry.get("summary", "") or entry.get("description", ""))
    url = entry.get("link", "")
    date_str = parse_date(entry)

    if not title or not is_recent(date_str):
        return None

    full_text = f"{title} {summary}"
    if excluded(full_text):
        return None

    category, cat_score, scores = classify(full_text)
    if cat_score <= 0:
        return None

    rules = CATEGORY_RULES[category]
    page_html = fetch_url_text(url)
    paragraphs = extract_paragraphs_from_html(page_html, summary or title)
    first_excerpt = paragraphs[0] if paragraphs else summary or title

    uid_raw = f"{source_name}-{title}-{url}"
    uid = hashlib.sha1(uid_raw.encode("utf-8")).hexdigest()[:10]

    total_score = cat_score + SOURCE_PRIORITY.get(source_name, 5) + recency_score(date_str)

    para_objs = []
    for p in paragraphs[:3]:
        para_objs.append({
            "en": p,
            "cn": make_cn_explanation(category, source_name),
            "insight": make_insight(category),
        })

    if not para_objs:
        para_objs = [{
            "en": summary or title,
            "cn": make_cn_explanation(category, source_name),
            "insight": make_insight(category),
        }]

    english = rules["english"]

    return {
        "id": uid,
        "scenarioId": category,
        "trend": category,
        "signal": rules["signal"],
        "pill": source_name.split()[0],
        "sourceType": "Auto Pick",
        "titleCn": rules["titleCn"],
        "desc": f"这篇文章可用于提炼「{rules['signal']}」相关的商业判断和商务英文表达。",
        "why": rules["why"],
        "action": rules["action"],
        "english": english,
        "breakdown": make_cn_explanation(category, source_name),
        "judgement": make_insight(category),
        "win": "能快速调整供应链、成本结构、运营流程和客户沟通方式的企业。",
        "lose": "只依赖单一市场、单一供应链或单一低价策略的企业。",
        "userUse": "外贸可用于客户沟通；跨境可用于选品和供应链判断；职场可用于英文汇报。",
        "template": template_for_category(category, english),
        "practice": practice_for_category(category),
        "sourceDate": date_str,
        "score": total_score,
        "source": {
            "name": source_name,
            "title": title,
            "url": url,
            "summary": summary,
            "paragraphs": para_objs,
        },
        "_debugScores": scores,
    }


def fetch_feed(source_name: str, url: str):
    print(f"[feed] {source_name}: {url}")
    try:
        feed = feedparser.parse(url)
    except Exception as e:
        print(f"[warn] feed failed: {source_name} {e}")
        return []

    if getattr(feed, "bozo", False):
        # Some feeds still parse with bozo=True; keep going but log.
        print(f"[warn] feed parse warning: {source_name} {getattr(feed, 'bozo_exception', '')}")

    articles = []
    for entry in feed.entries[:30]:
        try:
            item = build_article(entry, source_name)
            if item:
                articles.append(item)
        except Exception as e:
            print(f"[warn] article failed: {source_name} {e}")
    return articles


def dedupe(items):
    seen = set()
    out = []
    for item in items:
        key = (item["source"]["title"].strip().lower(), item["source"].get("url", ""))
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def main():
    all_items = []

    for name, url in RSS_FEEDS.items():
        all_items.extend(fetch_feed(name, url))

    # Optional feeds are tried, but failures do not matter.
    for name, url in OPTIONAL_FEEDS.items():
        all_items.extend(fetch_feed(name, url))

    all_items = dedupe(all_items)
    all_items.sort(key=lambda x: (x.get("score", 0), x.get("sourceDate", "")), reverse=True)

    selected = all_items[:MAX_OUTPUT]

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "count": len(selected),
        "method": "rss_keyword_scoring_v2_global_business_signal",
        "scenarios": selected,
    }

    with open("business-english-live.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    # Also write a readable candidates file for manual review.
    with open("business-candidates.txt", "w", encoding="utf-8") as f:
        for i, item in enumerate(selected, 1):
            f.write(f"{i}. [{item['signal']}] {item['source']['title']}\n")
            f.write(f"   Source: {item['source']['name']} | Date: {item['sourceDate']} | Score: {item['score']}\n")
            f.write(f"   URL: {item['source'].get('url','')}\n")
            f.write(f"   Why: {item['why']}\n")
            f.write(f"   English: {item['english']}\n\n")

    print(f"wrote business-english-live.json with {len(selected)} business signal matches")
    print("wrote business-candidates.txt for review")


if __name__ == "__main__":
    main()
