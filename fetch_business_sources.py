#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Business Briefing RSS fetcher — strict quality gate version

核心原则：
- 不拿半段 RSS 摘要硬做 1-9 层
- 原文正文抓不到足够信息，就不发布
- 宁可当天不更新，也不发布空、浅、硬扯的文章
"""

import json
import re
import time
import html
import hashlib
import urllib.request
from pathlib import Path
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime

try:
    import feedparser
except ImportError:
    raise SystemExit("Missing dependency: feedparser. Install with: pip3 install feedparser")


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
            "raw material", "transport costs", "port", "container"
        ],
        "titleCn": "供应链与成本压力正在改变企业交付和利润逻辑",
        "why": "这篇文章值得读，是因为它能帮助判断成本、库存、物流或供应稳定性是否正在影响企业利润和交付承诺。",
        "action": "适合转化为报价解释、交期说明、供应商沟通、库存判断和内部风险汇报。",
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
        "why": "这篇文章值得读，是因为它能帮助判断关税、汇率或贸易规则如何改变跨境交易的真实成本。",
        "action": "适合转化为报价重算、价格有效期说明、客户解释和采购路线调整。",
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
        "why": "这篇文章值得读，是因为它能帮助判断消费者预算、偏好和购买动机是否正在发生变化。",
        "action": "适合转化为选品判断、价格解释、客户开发话术和销售汇报。",
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
        "why": "这篇文章值得读，是因为它能帮助判断库存、折扣、零售需求和利润之间的压力传导。",
        "action": "适合转化为库存决策、促销规划、渠道沟通和销售复盘。",
        "english": "rebalance inventory while protecting margins",
    },
    "ai-productivity": {
        "signal": "AI Productivity",
        "keywords": [
            "ai", "artificial intelligence", "generative ai", "automation",
            "productivity", "workflow", "efficiency", "software", "chatbot",
            "customer service", "enterprise ai", "automate", "operational efficiency",
            "process", "tools", "workload"
        ],
        "titleCn": "AI 效率工具正在改变工作流程和运营成本结构",
        "why": "这篇文章值得读，是因为它能帮助判断 AI 是否真正进入工作流程，而不是停留在内容生成层面。",
        "action": "适合转化为内部提案、流程优化、客服自动化和效率汇报。",
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
        "why": "这篇文章值得读，是因为它能帮助理解企业如何在不确定环境中调整战略、组织和资源配置。",
        "action": "适合转化为经营复盘、管理沟通、英文汇报和商务判断表达。",
        "english": "recalibrate our operating model",
    },
}

EXCLUDE_PATTERNS = [
    r"\belection\b", r"\bcampaign\b", r"\bparliament\b", r"\bminister\b",
    r"\bcelebrity\b", r"\bhollywood\b", r"\bfilm\b", r"\bmovie\b",
    r"\bsport\b", r"\bfootball\b", r"\bsoccer\b", r"\btennis\b",
    r"\bhouse prices\b", r"\bstock price\b", r"\bshare price\b",
    r"\bshares rise\b", r"\bshares fall\b", r"\bearnings per share\b",
    r"\bquarterly profit beats\b", r"\bcrypto\b", r"\bbitcoin\b",
]

SOURCE_PRIORITY = {
    "Wall Street Journal Business": 9,
    "Harvard Business Review": 8,
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
MAX_OUTPUT = 8

# 严格质量门槛
MIN_CATEGORY_SCORE = 6
MIN_EFFECTIVE_PARAGRAPHS = 2
MIN_TOTAL_WORDS = 240
MIN_AVG_PARAGRAPH_WORDS = 65


def clean_html(value: str) -> str:
    value = html.unescape(value or "")
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def word_count(text: str) -> int:
    return len(re.findall(r"[A-Za-z][A-Za-z'-]*", text or ""))


def parse_date(entry) -> str:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if parsed:
        try:
            return datetime(*parsed[:6], tzinfo=timezone.utc).strftime("%Y-%m-%d")
        except Exception:
            pass

    raw = entry.get("published") or entry.get("updated") or ""
    if raw:
        try:
            return parsedate_to_datetime(raw).strftime("%Y-%m-%d")
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
            raw = resp.read(500_000)
            charset = resp.headers.get_content_charset() or "utf-8"
            return raw.decode(charset, errors="ignore")
    except Exception:
        return ""


def extract_paragraphs_from_html(doc: str):
    if not doc:
        return []

    doc = re.sub(r"(?is)<script.*?</script>", " ", doc)
    doc = re.sub(r"(?is)<style.*?</style>", " ", doc)
    doc = re.sub(r"(?is)<noscript.*?</noscript>", " ", doc)

    paras = re.findall(r"(?is)<p[^>]*>(.*?)</p>", doc)
    cleaned = []
    for p in paras:
        text = clean_html(p)
        wc = word_count(text)

        if wc < 45 or wc > 180:
            continue
        if any(bad in text.lower() for bad in [
            "sign up", "newsletter", "cookies", "all rights reserved",
            "advertisement", "subscribe", "log in", "privacy policy",
            "terms of use", "©", "read more"
        ]):
            continue
        if not re.search(r"[a-zA-Z]", text):
            continue
        # 摘要截断型内容，不能当正文
        if text.endswith("[...]") or text.endswith("..."):
            continue

        cleaned.append(text)
        if len(cleaned) >= 4:
            break

    return cleaned


def quality_check(title: str, summary: str, paragraphs: list, cat_score: int):
    reasons = []
    full = " ".join(paragraphs)
    total_words = word_count(full)
    avg_words = int(total_words / max(1, len(paragraphs)))

    if cat_score < MIN_CATEGORY_SCORE:
        reasons.append(f"category_score_too_low={cat_score}")

    if len(paragraphs) < MIN_EFFECTIVE_PARAGRAPHS:
        reasons.append(f"not_enough_body_paragraphs={len(paragraphs)}")

    if total_words < MIN_TOTAL_WORDS:
        reasons.append(f"body_too_short_words={total_words}")

    if avg_words < MIN_AVG_PARAGRAPH_WORDS:
        reasons.append(f"paragraphs_too_thin_avg_words={avg_words}")

    # 只有标题 + 提问式摘要，通常不可拆成 1-9 层
    combined_summary = f"{title} {summary}".lower()
    question_marks = combined_summary.count("?")
    if question_marks >= 2 and total_words < 240:
        reasons.append("question_summary_without_enough_body")

    # 常见 RSS 摘要截断
    if "[...]" in summary or summary.strip().endswith("..."):
        if total_words < 240:
            reasons.append("rss_summary_truncated")

    return reasons


def make_cn_explanation(category: str, source_name: str, title: str) -> str:
    signal = CATEGORY_RULES[category]["signal"]
    return f"这篇来自 {source_name} 的报道可归入「{signal}」。它的价值不在标题本身，而在于能否从原文中提炼出对成本、需求、流程或供应链的具体影响。"


def make_insight(category: str) -> str:
    return {
        "supply-chain-cost": "阅读时重点看：成本压力从哪里来，企业是否正在改变采购、库存、交付或报价策略。",
        "tariff-currency": "阅读时重点看：政策、关税或汇率变化是否会改变最终成交成本和报价有效期。",
        "consumer-demand": "阅读时重点看：消费者是变得更谨慎、更重视价值，还是转向新的品类和场景。",
        "retail-inventory": "阅读时重点看：库存压力是否正在带来促销、折扣、现金流和利润率变化。",
        "ai-productivity": "阅读时重点看：AI 是否进入了真实工作流，是否减少了人工重复劳动或改变了组织决策。",
        "business-strategy": "阅读时重点看：企业是在扩张、收缩、提效，还是重新分配资源。",
    }.get(category, "阅读时重点看：这条新闻能否转化为具体工作判断。")


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

    if not title:
        return None, "empty_title"
    if not is_recent(date_str):
        return None, "too_old"

    seed_text = f"{title} {summary}"
    if excluded(seed_text):
        return None, "excluded_topic"

    category, cat_score, scores = classify(seed_text)
    if cat_score <= 0:
        return None, "no_relevant_category"

    page_html = fetch_url_text(url)
    paragraphs = extract_paragraphs_from_html(page_html)

    quality_reasons = quality_check(title, summary, paragraphs, cat_score)
    if quality_reasons:
        return None, "; ".join(quality_reasons)

    rules = CATEGORY_RULES[category]
    uid_raw = f"{source_name}-{title}-{url}"
    uid = hashlib.sha1(uid_raw.encode("utf-8")).hexdigest()[:10]
    total_score = cat_score + SOURCE_PRIORITY.get(source_name, 5) + recency_score(date_str) + min(8, word_count(" ".join(paragraphs)) // 80)

    para_objs = [{
        "en": p,
        "cn": "【待精译】这是一段自动抓取的英文原文。请在 AI 精修后补入准确中文翻译，避免机器规则硬译误导读者。",
        "insight": "",
    } for p in paragraphs[:3]]

    english = rules["english"]

    return {
        "id": uid,
        "scenarioId": category,
        "trend": category,
        "signal": rules["signal"],
        "pill": source_name.split()[0],
        "sourceType": "Auto Pick",
        "titleCn": rules["titleCn"],
        "desc": f"这篇文章已抓取到足够英文正文，可先作为「{rules['signal']}」候选原文阅读。深度中文拆解需经过 AI 或人工精修后发布。",
        "why": rules["why"],
        "action": rules["action"],
        "english": english,
        "breakdown": "",
        "judgement": "",
        "win": "",
        "lose": "",
        "userUse": "",
        "analysisStatus": "needs_ai_enrichment",
        "deepReady": False,
        "template": template_for_category(category, english),
        "practice": practice_for_category(category),
        "sourceDate": date_str,
        "score": total_score,
        "quality": "strong",
        "publishable": True,
        "qualityMeta": {
            "bodyParagraphs": len(paragraphs),
            "bodyWords": word_count(" ".join(paragraphs)),
            "categoryScore": cat_score,
        },
        "source": {
            "name": source_name,
            "title": title,
            "url": url,
            "summary": summary,
            "paragraphs": para_objs,
        },
        "_debugScores": scores,
    }, None


def fetch_feed(source_name: str, url: str):
    print(f"[feed] {source_name}: {url}")
    accepted = []
    rejected = []

    try:
        feed = feedparser.parse(url)
    except Exception as e:
        return accepted, [(source_name, "", f"feed_failed={e}")]

    if getattr(feed, "bozo", False):
        print(f"[warn] feed parse warning: {source_name} {getattr(feed, 'bozo_exception', '')}")

    for entry in feed.entries[:35]:
        title = clean_html(entry.get("title", ""))
        try:
            item, reason = build_article(entry, source_name)
            if item:
                accepted.append(item)
            else:
                rejected.append((source_name, title, reason or "rejected"))
        except Exception as e:
            rejected.append((source_name, title, f"article_failed={e}"))

    return accepted, rejected


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



def json_for_prompt(obj):
    return json.dumps(obj, ensure_ascii=False, indent=2)


def build_ai_enrichment_prompt(item):
    """Create one prompt that asks AI to enrich the selected article into full website JSON."""
    source = item.get("source", {})
    paras = source.get("paragraphs", [])
    excerpt_payload = {
        "id": item.get("id"),
        "trend": item.get("trend"),
        "signal": item.get("signal"),
        "title": source.get("title"),
        "sourceName": source.get("name"),
        "sourceUrl": source.get("url"),
        "sourceDate": item.get("sourceDate"),
        "english": item.get("english"),
        "paragraphs": [
            {"en": p.get("en", "")}
            for p in paras[:3]
            if p.get("en")
        ],
    }

    return f"""
【任务：把下面这篇候选商业外刊精修成网站可直接读取的完整 JSON】

你是“商业外刊工作读本”的总编辑。请根据我提供的英文摘录，生成一篇克制、具体、可用于商务英文输出的精修稿。

重要约束：
1. 只根据下方英文摘录和标题分析，不要编造文章没有说的信息。
2. 不要写空话，不要写“降维打击”“压迫感”等夸张表达。
3. 每段英文摘录必须保留在 JSON 的 source.paragraphs[].en 中。
4. 每段英文摘录下面必须补充准确、自然的中文翻译，放在 source.paragraphs[].cn 中。
5. 03–06 层必须具体，不要泛泛说“影响成本、需求、供应链”。
6. 输出必须是一个完整 JSON 对象，不要 Markdown，不要解释。

候选文章数据如下：
{json_for_prompt(excerpt_payload)}

请严格输出这个 JSON 结构：

{{
  "id": "{item.get("id")}",
  "scenarioId": "{item.get("trend")}",
  "trend": "{item.get("trend")}",
  "industry": "crossborder",
  "signal": "{item.get("signal")}",
  "pill": "AI Enriched",
  "sourceType": "AI Enriched",
  "analysisStatus": "ai_enriched",
  "deepReady": true,
  "publishable": true,
  "quality": "strong",

  "titleCn": "用一句中文准确概括这篇文章的商业信号，不要空泛",
  "desc": "用一段中文说明这篇文章为什么值得读，要具体到文章内容",
  "why": "为什么读：提炼文章揭示的核心商业矛盾",
  "action": "我能拿来做什么：给外贸/跨境/职场用户一个具体可执行动作",
  "english": "从文章里提炼一个真实、高频、可复用的商务英文表达",

  "breakdown": "Layer 03 中文拆解：具体解释文章里的商业逻辑，150-220字",
  "judgement": "Layer 04 趋势判断：基于文章内容判断接下来可能影响什么，不要夸大，120-180字",
  "win": "Layer 05 受益方：具体写哪类公司/岗位/经营方式更受益",
  "lose": "Layer 05 承压方：具体写哪类公司/岗位/经营方式更承压",
  "userUse": "Layer 06 中国用户怎么用：分别点到外贸、跨境、职场英文输出，120-180字",

  "template": "Layer 08 英文邮件/briefing 模板。必须使用上面的 english 表达。80-130英文词。",
  "practice": "Layer 09 输出练习：给一个具体英文写作任务",

  "sourceDate": "{item.get("sourceDate")}",
  "readingTime": "8 分钟",

  "source": {{
    "name": "{source.get("name", "")}",
    "title": "{source.get("title", "").replace('"', '\\"')}",
    "url": "{source.get("url", "")}",
    "summary": "{source.get("summary", "").replace('"', '\\"')}",
    "paragraphs": [
      {{
        "en": "保留英文摘录第1段",
        "cn": "第1段自然中文翻译",
        "insight": "这一段对应的商业阅读提示"
      }},
      {{
        "en": "保留英文摘录第2段",
        "cn": "第2段自然中文翻译",
        "insight": "这一段对应的商业阅读提示"
      }}
    ]
  }}
}}
""".strip()


def write_ai_enrichment_files(selected):
    """Write prompt files for the top candidate and all candidates."""
    if not selected:
        return

    # Top candidate files
    top = selected[0]
    with open("business-selected-candidate.json", "w", encoding="utf-8") as f:
        json.dump(top, f, ensure_ascii=False, indent=2)

    with open("business-ai-enrich-prompt.txt", "w", encoding="utf-8") as f:
        f.write(build_ai_enrichment_prompt(top))

    # All candidate prompts, separated for manual choice
    with open("business-ai-enrich-prompts-all.txt", "w", encoding="utf-8") as f:
        for idx, item in enumerate(selected, 1):
            f.write(f"\\n\\n================ 候选 {idx}: {item.get('source', {}).get('title', '')} ================\\n\\n")
            f.write(build_ai_enrichment_prompt(item))
            f.write("\\n")


def main():
    all_items = []
    all_rejected = []

    for name, url in RSS_FEEDS.items():
        items, rejected = fetch_feed(name, url)
        all_items.extend(items)
        all_rejected.extend(rejected)

    for name, url in OPTIONAL_FEEDS.items():
        items, rejected = fetch_feed(name, url)
        all_items.extend(items)
        all_rejected.extend(rejected)

    all_items = dedupe(all_items)
    all_items.sort(key=lambda x: (x.get("score", 0), x.get("sourceDate", "")), reverse=True)
    selected = all_items[:MAX_OUTPUT]

    with open("business-candidates-rejected.txt", "w", encoding="utf-8") as f:
        for source, title, reason in all_rejected:
            f.write(f"[REJECTED] {source} | {title}\n")
            f.write(f"Reason: {reason}\n\n")

    with open("business-candidates.txt", "w", encoding="utf-8") as f:
        for i, item in enumerate(selected, 1):
            qm = item.get("qualityMeta", {})
            f.write(f"{i}. [{item['signal']}] {item['source']['title']}\n")
            f.write(f"   Source: {item['source']['name']} | Date: {item['sourceDate']} | Score: {item['score']}\n")
            f.write(f"   Body: {qm.get('bodyParagraphs')} paragraphs / {qm.get('bodyWords')} words | CategoryScore: {qm.get('categoryScore')}\n")
            f.write(f"   URL: {item['source'].get('url','')}\n")
            f.write(f"   Why: {item['why']}\n")
            f.write(f"   English: {item['english']}\n\n")

    write_ai_enrichment_files(selected)

    if not selected:
        print("no high-quality publishable article found; keeping existing business-english-live.json unchanged")
        print("wrote business-candidates-rejected.txt for review")
        return

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "count": len(selected),
        "method": "rss_keyword_scoring_v3_strict_quality_gate",
        "qualityGate": {
            "minCategoryScore": MIN_CATEGORY_SCORE,
            "minEffectiveParagraphs": MIN_EFFECTIVE_PARAGRAPHS,
            "minTotalWords": MIN_TOTAL_WORDS,
            "minAvgParagraphWords": MIN_AVG_PARAGRAPH_WORDS,
        },
        "scenarios": selected,
    }

    with open("business-english-live.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"wrote business-english-live.json with {len(selected)} strong business signal matches")
    print("wrote business-candidates.txt")
    print("wrote business-candidates-rejected.txt")
    print("wrote business-selected-candidate.json")
    print("wrote business-ai-enrich-prompt.txt")
    print("wrote business-ai-enrich-prompts-all.txt")


if __name__ == "__main__":
    main()
