# V46_BUSINESS_DEPTH_LAYOUT_FIX
# 商业英语场景工具：每日抓取新商业/外刊内容，转成可直接用于工作的商务英语场景页。
# 运行：python3 one_article_marked_v8.py
# 输出：
#   output/index.html
#   output/latest.html
#   output/editor.html
#   output/history.json
#   output/archive/day-YYYY-MM-DD.html

import json
import re
import html
import sys
from pathlib import Path
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin

try:
    import feedparser
except Exception:
    feedparser = None

try:
    import requests
    from bs4 import BeautifulSoup
except Exception:
    requests = None
    BeautifulSoup = None

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT / "output"
ARCHIVE_DIR = OUTPUT_DIR / "archive"
CONFIG_PATH = ROOT / "config.json"
USED_ARTICLES_PATH = ROOT / "used_articles.json"

MARKER = "V46_BUSINESS_DEPTH_LAYOUT_FIX"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    )
}

DEFAULT_CONFIG = {
    "business_mode": True,
    "fresh_days_priority": 14,
    "fresh_days_normal": 30,
    "fresh_days_fallback": 90,
    "use_seed_when_no_fresh_match": True,
    "entry_limit_per_feed": 80,
    "feeds": [
        {"name": "The Guardian Business", "url": "https://www.theguardian.com/uk/business/rss"},
        {"name": "The Guardian Money", "url": "https://www.theguardian.com/money/rss"},
        {"name": "BBC Business", "url": "https://feeds.bbci.co.uk/news/business/rss.xml"},
        {"name": "BBC Technology", "url": "https://feeds.bbci.co.uk/news/technology/rss.xml"},
        {"name": "MIT News AI", "url": "https://news.mit.edu/rss/topic/artificial-intelligence2"},
        {"name": "NPR Business", "url": "https://feeds.npr.org/1006/rss.xml"}
    ]
}

PRACTICAL_CATEGORIES = [
    "报价与议价",
    "订单与交付",
    "付款与催款",
    "客户跟进",
    "投诉与售后",
    "合作与开发"
]

BUSINESS_VIEW_TOPICS = [
    "成本上涨",
    "需求疲软",
    "供应链",
    "物流",
    "关税",
    "汇率",
    "AI",
    "消费趋势"
]

def clean_text(text):
    if text is None:
        return ""
    text = str(text)
    text = re.sub(r"<script[\s\S]*?</script>", " ", text, flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()

def esc(x):
    return html.escape(str(x or ""), quote=True)

def today_str():
    return datetime.now().strftime("%Y-%m-%d")

def read_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return default

def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def load_config():
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        raw = read_json(CONFIG_PATH, {})
        if isinstance(raw, dict):
            # 保留原 config 里的 feeds，同时允许 business_v45 覆盖。
            cfg.update(raw)
            if isinstance(raw.get("business_v45"), dict):
                cfg.update(raw["business_v45"])
    if not cfg.get("feeds"):
        cfg["feeds"] = DEFAULT_CONFIG["feeds"]
    return cfg

def normalize_link(link):
    return (link or "").split("#", 1)[0].split("?", 1)[0].rstrip("/")

def normalize_title(title):
    return clean_text(title).lower()

def load_used():
    data = read_json(USED_ARTICLES_PATH, {"links": [], "titles": []})
    if not isinstance(data, dict):
        data = {"links": [], "titles": []}
    return {
        "links": set(normalize_link(x) for x in data.get("links", []) if normalize_link(x)),
        "titles": set(normalize_title(x) for x in data.get("titles", []) if normalize_title(x)),
    }

def save_used_article(item, max_keep=500):
    old = read_json(USED_ARTICLES_PATH, {"links": [], "titles": []})
    if not isinstance(old, dict):
        old = {"links": [], "titles": []}
    links = [normalize_link(x) for x in old.get("links", []) if normalize_link(x)]
    titles = [normalize_title(x) for x in old.get("titles", []) if normalize_title(x)]
    link = normalize_link(item.get("link"))
    title = normalize_title(item.get("title"))
    if link and link not in links:
        links.append(link)
    if title and title not in titles:
        titles.append(title)
    write_json(USED_ARTICLES_PATH, {"links": links[-max_keep:], "titles": titles[-max_keep:]})

def parse_date(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    try:
        dt = parsedate_to_datetime(str(value))
        if dt.tzinfo:
            dt = dt.replace(tzinfo=None)
        return dt
    except Exception:
        pass
    for fmt in ("%Y-%m-%d", "%d %b %Y", "%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(str(value)[:32], fmt)
        except Exception:
            continue
    return None

def article_age_days(item):
    dt = parse_date(item.get("published") or item.get("updated") or "")
    if not dt:
        return None
    return max(0, (datetime.now() - dt).days)

def display_date(item):
    dt = parse_date(item.get("published") or item.get("updated") or "")
    return dt.strftime("%Y-%m-%d") if dt else ""

SCENARIO_RULES = [
    {
        "id": "price-too-high",
        "title": "客户嫌价格贵，该怎么回复？",
        "category": "报价与议价",
        "business_view": "成本上涨",
        "level": "Level1",
        "keywords": ["price", "prices", "cost", "costs", "expensive", "inflation", "higher costs", "rising costs", "price pressure", "margin"],
        "problem": "客户觉得报价高，要求你解释或降价。",
        "wrong_way": "不要马上说 no，也不要立刻无条件降价。",
        "strategy": "先承认客户对价格的顾虑，再解释价值、成本和质量标准，最后给可选方案。"
    },
    {
        "id": "discount-request",
        "title": "客户要求折扣，怎么不卑不亢回复？",
        "category": "报价与议价",
        "business_view": "成本上涨",
        "level": "Level1",
        "keywords": ["discount", "discounts", "promotion", "lower price", "price cut", "cheaper", "negotiate"],
        "problem": "客户要求折扣，希望你进一步让价。",
        "wrong_way": "不要只说 our price is final，也不要用 very cheap 这种低价值表达。",
        "strategy": "说明当前报价已尽量有竞争力，同时把折扣和数量、付款条件、规格调整绑定。"
    },
    {
        "id": "raw-material-costs",
        "title": "原材料涨价，如何向客户解释调价？",
        "category": "报价与议价",
        "business_view": "成本上涨",
        "level": "Level2",
        "keywords": ["raw material", "materials", "commodity", "input costs", "production costs", "factory costs", "sourcing costs"],
        "problem": "由于原材料或生产成本变化，需要向客户解释价格调整。",
        "wrong_way": "不要只说 price increased because costs increased，显得粗糙且没有说服力。",
        "strategy": "说明成本变化是市场因素，并强调你方仍在控制成本、维持质量和稳定供应。"
    },
    {
        "id": "delivery-delay",
        "title": "交期延迟，怎么提前告知客户？",
        "category": "订单与交付",
        "business_view": "供应链",
        "level": "Level1",
        "keywords": ["delay", "delayed", "delivery", "lead time", "shipment", "production schedule", "supply chain disruption"],
        "problem": "生产或交付时间出现延迟，需要提前通知客户。",
        "wrong_way": "不要等客户来催才解释，也不要把责任全部推给工厂/物流。",
        "strategy": "先告知事实和影响，再给新的时间节点和补救方案。"
    },
    {
        "id": "freight-costs",
        "title": "运费上涨，如何让客户理解？",
        "category": "订单与交付",
        "business_view": "物流",
        "level": "Level2",
        "keywords": ["shipping", "freight", "logistics", "container", "transport", "port", "sea freight", "air freight"],
        "problem": "物流或运费上涨，客户质疑总价或交付成本。",
        "wrong_way": "不要只说 shipping is expensive now。",
        "strategy": "把运费变化解释为市场波动，并提供不同运输方案或报价有效期。"
    },
    {
        "id": "deposit-reminder",
        "title": "催定金，怎么礼貌但有效？",
        "category": "付款与催款",
        "business_view": "供应链",
        "level": "Level1",
        "keywords": ["deposit", "advance payment", "down payment", "payment terms", "production start"],
        "problem": "客户确认订单后还没付定金，影响排产。",
        "wrong_way": "不要直接催：Please pay quickly。",
        "strategy": "把付款和生产安排关联起来，提醒对方付款后才能锁定排产。"
    },
    {
        "id": "balance-payment",
        "title": "催尾款，怎么不尴尬？",
        "category": "付款与催款",
        "business_view": "供应链",
        "level": "Level1",
        "keywords": ["balance payment", "remaining payment", "final payment", "payment reminder", "before shipment"],
        "problem": "货已完成或准备发货，需要提醒客户支付尾款。",
        "wrong_way": "不要用 threatening 的语气威胁客户。",
        "strategy": "用发货节点提醒付款，强调收到尾款后可立即安排出运。"
    },
    {
        "id": "no-reply-follow-up",
        "title": "客户不回复，如何跟进不烦人？",
        "category": "客户跟进",
        "business_view": "需求疲软",
        "level": "Level1",
        "keywords": ["demand", "slowdown", "consumer demand", "sales", "retail", "buyer", "customer", "response", "follow up"],
        "problem": "报价或样品发出后客户没有回复，需要再次跟进。",
        "wrong_way": "不要一直问 Any update?，容易显得没有信息量。",
        "strategy": "提供一个新的理由跟进，例如价格有效期、库存、样品反馈或市场变化。"
    },
    {
        "id": "sample-follow-up",
        "title": "样品寄出后，如何跟进反馈？",
        "category": "客户跟进",
        "business_view": "消费趋势",
        "level": "Level1",
        "keywords": ["sample", "samples", "prototype", "testing", "feedback", "review", "evaluation"],
        "problem": "样品寄出后，需要客户确认测试结果或下一步。",
        "wrong_way": "不要只说 Have you checked the sample?，太硬。",
        "strategy": "先确认是否收到，再邀请对方反馈具体维度。"
    },
    {
        "id": "quality-complaint",
        "title": "客户质量投诉，如何稳住关系？",
        "category": "投诉与售后",
        "business_view": "供应链",
        "level": "Level2",
        "keywords": ["quality", "complaint", "defect", "defective", "issue", "after-sales", "refund", "replacement"],
        "problem": "客户反馈质量问题，需要先安抚再核实。",
        "wrong_way": "不要第一时间否认责任，也不要还没核实就承诺赔偿。",
        "strategy": "先表达重视，再要求照片/视频/批次信息，最后承诺核实并给方案。"
    },
    {
        "id": "ai-business-work",
        "title": "AI 改变工作方式，如何对客户表达效率提升？",
        "category": "合作与开发",
        "business_view": "AI",
        "level": "Level2",
        "keywords": ["ai", "artificial intelligence", "automation", "technology", "productivity", "efficiency"],
        "problem": "你想向客户说明 AI 或自动化带来的效率、服务和成本优势。",
        "wrong_way": "不要空泛地说 We use AI, so we are better。",
        "strategy": "把 AI 说成具体流程改进：更快响应、更准数据、更稳定交付。"
    },
    {
        "id": "tariff-currency",
        "title": "关税或汇率变化，如何解释报价有效期？",
        "category": "报价与议价",
        "business_view": "关税",
        "level": "Level2",
        "keywords": ["tariff", "tariffs", "currency", "exchange rate", "dollar", "yuan", "fx", "trade"],
        "problem": "关税或汇率波动影响报价，需要限制报价有效期。",
        "wrong_way": "不要只说 price may change。",
        "strategy": "说明报价受外部因素影响，并明确有效期和重新确认条件。"
    }
]

SEED_SCENARIOS = {
    "price-too-high": {
        "source": "Business scenario template",
        "excerpt_title": "Price pressure and rising costs",
        "excerpt": [
            {
                "en": "Many companies are facing pressure from rising input costs, while buyers are becoming more careful about spending.",
                "cn": "很多公司正面对投入成本上升的压力，同时买家在支出上也变得更加谨慎。"
            }
        ],
        "judgement": [
            "客户说价格贵，不一定是真的买不起，也可能是在测试你的让步空间。",
            "如果马上降价，会削弱报价可信度，也容易让客户继续压价。",
            "更稳妥的回复是：理解顾虑 → 解释价值和成本 → 给可选方案。"
        ],
        "phrases": [
            ["I understand your concern about the price.", "我理解您对价格的顾虑。", "客户嫌贵时的第一句缓冲。"],
            ["our most competitive rate", "我们目前最有竞争力的价格", "说明你不是随便报价。"],
            ["limited room for further reduction", "进一步降价空间有限", "不直接说 no，但明确边界。"],
            ["adjust the specifications", "调整规格", "给客户一个降成本替代方案。"]
        ],
        "email": [
            "I understand your concern about the price.",
            "We have already offered our most competitive rate based on the current specifications and quality requirements.",
            "To maintain the same quality standard, there is limited room for further reduction.",
            "If budget is the main concern, we can also review the specifications together and see whether there is a more suitable option."
        ],
        "template_subject": "Regarding Your Price Concern",
        "template_body": "Dear [Name],\n\nThank you for your feedback. I understand your concern about the price.\n\nBased on the current specifications and quality requirements, we have already offered our most competitive rate. To maintain the same quality standard, there is limited room for further reduction.\n\nIf budget is the main concern, we can review the specifications together and see whether there is a more suitable option.\n\nPlease let me know your target budget, and I will check what we can do.\n\nBest regards,\n[Your Name]"
    }
}

def seed_content_for(rule):
    phrases = core_phrases_for_rule(rule)
    subject, body = email_template_for_rule(rule)
    return {
        "source": "Business scenario template",
        "excerpt_title": f"Business context for {rule['title']}",
        "excerpt": [
            {
                "en": source_context_sentence(rule),
                "cn": scenario_source_explanation(rule)
            }
        ],
        "judgement": detailed_judgement_for_rule(rule),
        "phrases": phrases,
        "email": email_expressions_for_rule(rule),
        "template_subject": subject,
        "template_body": body,
    }

def source_context_sentence(rule):
    view = rule.get("business_view", "市场变化")
    mapping = {
        "成本上涨": "When costs rise across materials, labor, or operations, suppliers often face pressure to explain pricing without damaging the customer relationship.",
        "供应链": "When production schedules or supply chains change, clear communication helps customers understand the impact and make timely decisions.",
        "物流": "When freight rates or shipping capacity fluctuate, the total landed cost may change even if the product price remains stable.",
        "需求疲软": "When demand becomes uncertain, buyers may slow down decisions, compare more suppliers, or delay replies before placing an order.",
        "消费趋势": "When customers test new products or samples, timely feedback helps both sides decide whether to adjust specifications or move to the next step.",
        "AI": "When companies use automation or AI tools, customers care less about the technology itself and more about faster response, fewer errors, and better service.",
        "关税": "When tariffs or exchange rates change, suppliers need to control quotation validity and explain pricing conditions clearly.",
        "汇率": "When tariffs or exchange rates change, suppliers need to control quotation validity and explain pricing conditions clearly.",
    }
    return mapping.get(view, "Business changes often affect customer decisions, so the reply should turn background information into a practical next step.")

def scenario_source_explanation(rule):
    return (
        f"这段不是让用户背新闻，而是给「{rule['title']}」提供商业背景："
        f"客户的反应往往和「{rule['business_view']}」有关。回复时要把背景转成工作话术，"
        "比如解释原因、说明边界、给替代方案，而不是空泛地说抱歉或马上让步。"
    )

IRRELEVANT_TITLE_PATTERNS = [
    "home price", "home prices", "housing", "real estate", "mortgage", "rent", "bay area",
    "celebrity", "football", "movie", "music", "restaurant", "tourism", "wedding",
    "stock market debut", "ipo wealth", "billionaire", "luxury home"
]

STRONG_WORK_KEYWORDS = [
    "supplier", "factory", "shipment", "shipping", "freight", "delivery", "lead time",
    "order", "payment", "deposit", "balance", "customer", "buyer", "discount", "quotation",
    "quote", "tariff", "exchange rate", "raw material", "materials", "supply chain", "logistics",
    "consumer demand", "retail demand", "quality", "defect", "complaint", "after-sales"
]

def is_irrelevant_item(item):
    text = " ".join([item.get("title", ""), item.get("summary", "")]).lower()
    return any(p in text for p in IRRELEVANT_TITLE_PATTERNS)

def classify_item(item):
    text = " ".join([item.get("title", ""), item.get("summary", ""), item.get("source", "")]).lower()
    title_low = item.get("title", "").lower()
    if is_irrelevant_item(item):
        return None, 0
    work_relevance = sum(1 for kw in STRONG_WORK_KEYWORDS if kw in text)
    best = None
    best_score = -1
    for rule in SCENARIO_RULES:
        score = 0
        for kw in rule["keywords"]:
            kwl = kw.lower()
            if kwl in text:
                score += 10 + min(len(kw), 20) // 4
            if kwl in title_low:
                score += 14
        if rule["id"] in {"price-too-high", "discount-request"} and work_relevance == 0:
            score -= 18
        if work_relevance:
            score += min(work_relevance * 8, 24)
        if score > best_score:
            best_score = score
            best = rule
    if best and best_score >= 18:
        return best, best_score
    return None, 0

def article_score(item):
    rule, score = classify_item(item)
    if not rule:
        return -999
    age = article_age_days(item)
    if age is None:
        recency = 0
    elif age <= 7:
        recency = 30
    elif age <= 14:
        recency = 22
    elif age <= 30:
        recency = 12
    elif age <= 90:
        recency = 3
    else:
        recency = -100
    source_bonus = 0
    s = item.get("source", "").lower()
    if any(x in s for x in ["guardian", "bbc", "npr", "mit"]):
        source_bonus += 8
    return score + recency + source_bonus

def fetch_feed_items(cfg):
    if feedparser is None:
        print("feedparser not installed; use seed fallback.")
        return []
    items = []
    per_feed = int(cfg.get("entry_limit_per_feed", 80))
    for feed in cfg.get("feeds", []):
        name = feed.get("name", "RSS")
        url = feed.get("url", "")
        if not url:
            continue
        try:
            print("抓取 RSS：", name)
            parsed = feedparser.parse(url)
            for e in parsed.entries[:per_feed]:
                title = clean_text(e.get("title", ""))
                link = e.get("link", "")
                summary = clean_text(e.get("summary", e.get("description", "")))
                published = e.get("published", e.get("updated", ""))
                if title and link:
                    items.append({
                        "source": name,
                        "title": title,
                        "link": link,
                        "summary": summary,
                        "published": published,
                    })
        except Exception as ex:
            print("RSS 失败：", name, ex)
    # dedup
    dedup = {}
    for item in items:
        key = normalize_link(item.get("link")) or normalize_title(item.get("title"))
        if key and key not in dedup:
            dedup[key] = item
    return list(dedup.values())

def filter_candidates_by_window(items, days, allow_evergreen=False):
    used = load_used()
    out = []
    for item in items:
        link = normalize_link(item.get("link"))
        title = normalize_title(item.get("title"))
        if link in used["links"] or title in used["titles"]:
            continue
        age = article_age_days(item)
        if age is not None and age > days:
            continue
        rule, score = classify_item(item)
        if not rule:
            continue
        if days > 30 and not allow_evergreen:
            # 90 天兜底只允许长期有效商业趋势，排除汇率/关税/具体市场价格等强时效。
            if rule.get("business_view") in {"汇率", "关税"}:
                continue
        out.append((article_score(item), rule, item))
    out.sort(key=lambda x: x[0], reverse=True)
    return out

def select_today_item(items, cfg):
    windows = [
        (int(cfg.get("fresh_days_priority", 14)), "最近14天", False),
        (int(cfg.get("fresh_days_normal", 30)), "最近30天", False),
        (int(cfg.get("fresh_days_fallback", 90)), "最近90天长期有效内容", True),
    ]
    for days, label, evergreen in windows:
        candidates = filter_candidates_by_window(items, days, evergreen)
        if candidates:
            score, rule, item = candidates[0]
            item["fresh_window"] = label
            item["content_type"] = "新商业内容"
            return rule, item
    return None, None

def fetch_article_paragraphs(url, max_count=2):
    if not requests or not BeautifulSoup or not url:
        return []
    try:
        print("抓正文：", url)
        r = requests.get(url, headers=HEADERS, timeout=22)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        for tag in soup(["script", "style", "noscript", "svg", "footer", "nav", "header", "aside"]):
            tag.decompose()
        paragraphs = []
        seen = set()
        for p in soup.find_all("p"):
            text = clean_text(p.get_text(" "))
            if not text:
                continue
            low = text.lower()
            if any(x in low for x in ["newsletter", "advertisement", "sign up", "cookies", "privacy", "all rights reserved"]):
                continue
            wc = len(re.findall(r"\b[A-Za-z]+(?:[-'][A-Za-z]+)?\b", text))
            if wc < 28 or wc > 130:
                continue
            key = text.lower()
            if key not in seen:
                paragraphs.append(text)
                seen.add(key)
            if len(paragraphs) >= max_count:
                break
        return paragraphs
    except Exception as e:
        print("正文抓取失败：", e)
        return []

def simple_translate_or_explain(text, rule):
    return scenario_source_explanation(rule)

def detailed_judgement_for_rule(rule):
    rid = rule["id"]
    data = {
        "price-too-high": [
            "客户说贵，可能是在测试让步空间，也可能是真的有预算压力；不要第一句就降价。",
            "你的重点不是证明客户错了，而是解释：价格包含什么、为什么不能无限压低、还能换什么方案。",
            "可让步的部分放在规格、数量、付款条件或长期合作上，不要直接牺牲质量标准。",
            "下一步应询问目标预算，并给一到两个替代配置，让客户继续留在谈判桌上。"
        ],
        "discount-request": [
            "客户要折扣时，真正想确认的是：是否还有谈判空间，而不是只要一句 yes/no。",
            "不要把折扣说成免费让利，要把折扣和订单数量、付款速度、长期合作绑定。",
            "如果不能降价，也要给客户一个可选择路径：调整数量、规格、包装或交期。",
            "下一步应让客户确认目标价、数量和可接受调整项。"
        ],
        "raw-material-costs": [
            "涨价解释不能只说成本涨了，要说明这是市场因素，不是临时加价。",
            "客户关心的是涨价是否合理、是否还会继续涨、报价能锁多久。",
            "表达重点：我们已吸收部分成本，但为保持质量和稳定供应，需要做价格调整。",
            "下一步应给出新价格生效时间、报价有效期和可替代方案。"
        ],
        "delivery-delay": [
            "交期延迟最怕被客户发现后你才解释，所以必须主动、提前、给新节点。",
            "不要把责任全部推给工厂或物流，这会让客户觉得你无法控制供应链。",
            "表达重点：事实影响、更新日期、补救动作、后续同步机制。",
            "下一步应给客户一个明确的新交付日期，并承诺何时再次更新。"
        ],
        "freight-costs": [
            "运费上涨不是产品涨价，但会影响客户的总成本，所以要分开解释产品价和物流价。",
            "不要只说 shipping is expensive，要说明运费受市场波动、舱位或路线影响。",
            "表达重点：提供不同运输选项，让客户在时效和成本之间选择。",
            "下一步应给客户至少两个物流方案：更快但贵、更慢但省。"
        ],
        "deposit-reminder": [
            "催定金不要像讨债，要把付款和排产绑定：收到定金后才能锁定生产计划。",
            "客户拖定金可能是内部审批、现金流或优先级问题；你的话术要明确但不压迫。",
            "表达重点：温和提醒、说明影响、给付款资料、确认生产安排。",
            "下一步应让客户确认预计付款时间，避免无限等待。"
        ],
        "balance-payment": [
            "催尾款要绑定发货节点：货已完成/待出运，收到尾款即可安排发货。",
            "不要威胁客户，也不要说 ship first unless paid 这种生硬表达。",
            "表达重点：当前订单状态、尾款金额、付款后动作、需要文件支持。",
            "下一步应请求付款确认，并说明发货安排。"
        ],
        "no-reply-follow-up": [
            "客户不回复不一定是拒绝，也可能是预算、内部审批、项目推迟或优先级下降。",
            "不要连续问 Any update?，要给对方一个回复理由。",
            "表达重点：补充有用信息、设置轻量问题、降低回复成本。",
            "下一步可以问：是否还在评估、是否需要调整报价、是否要我下周再跟进。"
        ],
        "sample-follow-up": [
            "样品跟进不能只问收到没，要引导客户从质量、包装、尺寸、功能等维度反馈。",
            "客户没反馈可能是还没测试，也可能是不满意但不想明说。",
            "表达重点：确认收到、邀请具体反馈、提出下一步。",
            "下一步应让客户选择：继续测试、修改样品、进入报价或小批量订单。"
        ],
        "quality-complaint": [
            "质量投诉第一步不是争辩责任，而是承认重视并收集证据。",
            "不要在核实前承诺赔偿，也不要第一时间否认客户反馈。",
            "表达重点：重视问题、索要照片视频批次号、承诺内部核查、给时间点。",
            "下一步应明确：我们会在多少时间内给初步处理方案。"
        ],
    }
    return data.get(rid, [
        f"这个内容可落到「{rule['title']}」场景。",
        f"客户反应可能受到「{rule['business_view']}」影响。",
        "回复要从解释背景转向解决动作。",
        "下一步给客户一个可执行选择，而不是只表达态度。"
    ])

def action_steps_for_rule(rule):
    return [
        "先承认对方关注点，降低对抗感。",
        "再解释业务原因：成本、交期、付款、质量或供应链。",
        "明确你的边界：不能无条件让步，不能牺牲质量或排期。",
        "给替代方案：调整规格、数量、付款条件、运输方式或时间点。",
        "最后推动下一步：请客户确认预算、付款时间、反馈或选择方案。"
    ]

def build_dynamic_content(rule, item, paragraphs):
    excerpt = []
    for p in paragraphs[:2]:
        excerpt.append({"en": p, "cn": simple_translate_or_explain(p, rule)})
    if not excerpt:
        seed = seed_content_for(rule)
        excerpt = seed["excerpt"]
    phrases = core_phrases_for_rule(rule)
    email = email_expressions_for_rule(rule)
    subject, body = email_template_for_rule(rule)
    return {
        "source": item.get("source", "Business source"),
        "excerpt_title": item.get("title", rule["title"]),
        "excerpt": excerpt,
        "judgement": detailed_judgement_for_rule(rule),
        "phrases": phrases,
        "email": email,
        "template_subject": subject,
        "template_body": body,
    }

def core_phrases_for_rule(rule):
    rid = rule["id"]
    common = {
        "price-too-high": [
            ["I understand your concern about the price.", "我理解您对价格的顾虑。", "客户嫌贵时的缓冲句。"],
            ["our most competitive rate", "我们目前最有竞争力的价格", "说明报价不是随意给出的。"],
            ["limited room for further reduction", "进一步降价空间有限", "不直接说 no，但划清边界。"],
            ["adjust the specifications", "调整规格", "给客户降本替代方案。"],
        ],
        "discount-request": [
            ["review the price based on quantity", "根据数量重新评估价格", "把折扣和订单量绑定。"],
            ["a better rate", "更好的价格", "比 cheap 更商务。"],
            ["if the order quantity can be increased", "如果订单数量可以增加", "谈判时设置条件。"],
            ["work within your budget", "在您的预算内寻找方案", "不承诺降价，但保留合作空间。"],
        ],
        "raw-material-costs": [
            ["rising raw material costs", "原材料成本上涨", "解释涨价原因。"],
            ["maintain the same quality standard", "维持同样质量标准", "说明不降质。"],
            ["absorb part of the cost", "承担部分成本", "表达我方也在分担压力。"],
            ["price adjustment", "价格调整", "比 price increase 更柔和。"],
        ],
        "delivery-delay": [
            ["a slight delay in the production schedule", "生产计划略有延迟", "提前通知交期问题。"],
            ["the updated delivery date", "更新后的交付日期", "明确新时间。"],
            ["minimize the impact", "尽量降低影响", "表达补救态度。"],
            ["keep you updated", "持续向您同步进展", "稳定客户预期。"],
        ],
        "freight-costs": [
            ["freight rates have increased", "运费上涨", "解释物流成本。"],
            ["shipping option", "运输方案", "提供不同选择。"],
            ["price validity", "报价有效期", "应对运价波动。"],
            ["the most cost-effective option", "最具成本效益的方案", "避免只谈贵。"],
        ],
        "deposit-reminder": [
            ["arrange production after receiving the deposit", "收到定金后安排生产", "催定金核心句。"],
            ["secure the production schedule", "锁定生产排期", "说明付款原因。"],
            ["kindly arrange the payment", "请安排付款", "礼貌催款。"],
            ["proceed with the order", "推进订单", "常用商务表达。"],
        ],
        "balance-payment": [
            ["the goods are ready for shipment", "货物已准备发货", "催尾款前提。"],
            ["remaining balance", "剩余尾款", "尾款表达。"],
            ["arrange shipment immediately", "立即安排发货", "把付款和发货绑定。"],
            ["payment confirmation", "付款确认", "催款后需要的动作。"],
        ],
        "no-reply-follow-up": [
            ["just following up on", "跟进一下……", "客户不回复时开头。"],
            ["any feedback on", "对……是否有反馈", "比 any update 更具体。"],
            ["still of interest to you", "是否仍然感兴趣", "判断客户意向。"],
            ["I’d be happy to assist", "我很乐意协助", "礼貌收尾。"],
        ],
        "sample-follow-up": [
            ["have you received the samples", "您是否已收到样品", "样品跟进第一步。"],
            ["initial feedback", "初步反馈", "要求客户反馈。"],
            ["testing result", "测试结果", "样品评估语境。"],
            ["next step", "下一步", "推动成交。"],
        ],
        "quality-complaint": [
            ["look into this issue", "调查这个问题", "投诉处理核心句。"],
            ["batch number", "批次号", "追溯质量问题。"],
            ["photos or videos for reference", "照片或视频供参考", "获取证据。"],
            ["provide a suitable solution", "提供合适解决方案", "承诺但不盲目赔偿。"],
        ],
        "ai-business-work": [
            ["improve response efficiency", "提高响应效率", "说明 AI 价值。"],
            ["streamline the workflow", "优化工作流程", "商务 AI 表达。"],
            ["reduce manual work", "减少人工操作", "效率表达。"],
            ["support faster decision-making", "支持更快决策", "客户价值表达。"],
        ],
        "tariff-currency": [
            ["exchange rate fluctuation", "汇率波动", "报价变化原因。"],
            ["tariff changes", "关税变化", "外部因素。"],
            ["valid for seven days", "有效期七天", "限制报价有效期。"],
            ["reconfirm the price", "重新确认价格", "报价更新动作。"],
        ],
    }
    return common.get(rid) or common["price-too-high"]

def email_expressions_for_rule(rule):
    rid = rule["id"]
    data = {
        "price-too-high": ["I understand your concern about the price.", "Our offer is based on the current specifications, quality standard, and order quantity.", "There is limited room for further reduction if we keep the same quality level.", "If budget is the main concern, we can review the specifications and see whether a more suitable option is possible."],
        "discount-request": ["Thank you for sharing your target price with us.", "For the current quantity, this is already a very competitive rate.", "If the order quantity can be increased, we can review whether a better rate is possible.", "We can also adjust the specifications to better match your budget."],
        "raw-material-costs": ["Due to the recent increase in raw material costs, we need to make a price adjustment.", "We have tried to absorb part of the cost on our side.", "To maintain the same quality standard, the updated price will apply from [date].", "The current quotation is valid until [date]."],
        "delivery-delay": ["I would like to update you on the production schedule.", "There may be a slight delay due to the current production arrangement.", "The updated delivery date is [date].", "We will do our best to minimize the impact and keep you updated."],
        "freight-costs": ["Freight rates have increased recently due to market changes.", "The product price remains the same, but the shipping cost needs to be updated.", "We can offer different shipping options for your comparison.", "Please let us know whether you prefer a faster option or a more cost-effective option."],
        "deposit-reminder": ["This is a kind reminder regarding the deposit payment.", "Once we receive the deposit, we can secure the production schedule.", "Please let us know if you need the proforma invoice or bank details again.", "Could you please confirm the estimated payment date?"],
        "balance-payment": ["The goods are ready for shipment.", "This is a kind reminder for the remaining balance.", "Once we receive the payment confirmation, we will arrange shipment immediately.", "Please let us know if you need any shipping document from our side."],
        "no-reply-follow-up": ["I just wanted to follow up on my previous email.", "May I know if this project is still under discussion on your side?", "If the price, specifications, or delivery time need to be adjusted, I’d be happy to review them with you.", "Would it be better for me to follow up again next week?"],
        "sample-follow-up": ["May I confirm whether you have received the samples?", "We would appreciate your feedback on the material, size, packaging, and overall quality.", "If any adjustment is needed, we can revise the sample accordingly.", "Please let us know the next step when convenient."],
        "quality-complaint": ["Thank you for bringing this issue to our attention.", "We take quality feedback seriously and will check it with our team.", "Could you please share photos, videos, and the batch number for reference?", "We will look into this and provide a suitable solution as soon as possible."],
    }
    return data.get(rid, ["Thank you for your message.", "I understand your concern and will check the details carefully.", "We can review the available options together.", "Please let me know your preferred next step."])

def email_template_for_rule(rule):
    subject_map = {
        "price-too-high": "Regarding Your Price Concern", "discount-request": "Regarding Your Discount Request", "raw-material-costs": "Update on Price Adjustment", "delivery-delay": "Update on Delivery Schedule", "freight-costs": "Update on Freight Cost", "deposit-reminder": "Kind Reminder for Deposit Payment", "balance-payment": "Kind Reminder for Balance Payment", "no-reply-follow-up": "Following Up on Our Previous Discussion", "sample-follow-up": "Following Up on Sample Feedback", "quality-complaint": "Regarding Your Quality Feedback", "ai-business-work": "How We Improve Response Efficiency", "tariff-currency": "Regarding Price Validity",
    }
    subject = subject_map.get(rule["id"], "Regarding Your Request")
    exprs = email_expressions_for_rule(rule)
    body = "Dear [Name],\n\n" + "\n\n".join(exprs) + "\n\nBest regards,\n[Your Name]"
    return subject, body

def email_variants_for_rule(rule):
    subject, body = email_template_for_rule(rule)
    soft = body.replace("Please let us know", "Could you please let us know").replace("Could you please confirm", "May I kindly confirm")
    firm = body.replace("I understand", "We understand").replace("If", "To move forward, if")
    return [
        {"name": "标准版", "note": "大多数客户都能直接用。", "subject": subject, "body": body},
        {"name": "更柔和版", "note": "适合老客户或关系较好的客户。", "subject": subject, "body": soft},
        {"name": "边界更清楚版", "note": "适合客户持续压价、拖款或要求不合理时。", "subject": subject, "body": firm},
    ]

def practice_for_rule(rule):
    return {"level1": ["填空：I understand your concern about the ______.", "填空：We can review the ______ together."], "level2": ["把这句话改得更专业：Our price is already very cheap.", "把这句话改得更礼貌：You must pay first."], "level3": [f"客户说：{sample_client_line(rule)} 请写一封 80 词以内的英文回复。", "要求：先理解客户，再解释原因，最后给下一步方案。"]}

def sample_client_line(rule):
    rid = rule["id"]
    return {"price-too-high": "Your price is too high. Can you reduce 15%?", "discount-request": "Can you give us a better discount?", "raw-material-costs": "Why did the price increase again?", "delivery-delay": "Why is the delivery delayed? We need the goods urgently.", "freight-costs": "The shipping cost is too high. Can you use a cheaper option?", "deposit-reminder": "We will arrange payment later. Please start production first.", "balance-payment": "Can you ship first? We will pay later.", "quality-complaint": "There is a quality problem with the goods.", "no-reply-follow-up": "客户一直没有回复你的报价邮件。", "sample-follow-up": "客户收到样品后一周没有反馈。"}.get(rid, "Can you offer a better solution?")

def build_today_content(cfg):
    items = fetch_feed_items(cfg)
    rule, item = select_today_item(items, cfg)
    if rule and item:
        paragraphs = fetch_article_paragraphs(item.get("link"), 2)
        content = build_dynamic_content(rule, item, paragraphs)
        source_date = display_date(item)
        print("今日使用新内容：", item.get("title"), item.get("fresh_window"))
    else:
        # 兜底种子：按日期轮换 10 个核心场景，不伪装成外刊。
        index = int(datetime.now().strftime("%j")) % 10
        rule = SCENARIO_RULES[index]
        item = {
            "source": "内置场景模板",
            "title": rule["title"],
            "link": "",
            "summary": rule["problem"],
            "published": "",
            "fresh_window": "无合适新内容，使用场景模板",
            "content_type": "场景模板",
        }
        content = seed_content_for(rule)
        source_date = ""
        print("今日使用种子场景：", rule["title"])

    return {
        "id": rule["id"],
        "title": rule["title"],
        "category": rule["category"],
        "business_view": rule["business_view"],
        "level": rule["level"],
        "target_user": "外贸业务员 / 销售 / 客服 / 运营",
        "work_scenario": {
            "problem": rule["problem"],
            "wrong_way": rule["wrong_way"],
            "strategy": rule["strategy"],
        },
        "source_excerpt": {
            "source": content["source"],
            "title": content["excerpt_title"],
            "url": item.get("link", ""),
            "source_date": source_date,
            "fresh_window": item.get("fresh_window", ""),
            "content_type": item.get("content_type", "新商业内容"),
            "paragraphs": content["excerpt"],
        },
        "business_judgement": content["judgement"],
        "core_phrases": [
            {"phrase": p[0], "meaning": p[1], "use_case": p[2], "example": example_for_phrase(p[0])}
            for p in content["phrases"]
        ],
        "email_expressions": content["email"],
        "email_template": {
            "subject": content["template_subject"],
            "body": content["template_body"],
        },
        "practice": practice_for_rule(rule),
        "cta": ["复制邮件模板", "收藏这个场景", "查看相关场景"],
        "updated_date": today_str(),
    }

def example_for_phrase(phrase):
    examples = {
        "I understand your concern about the price.": "I understand your concern about the price, and I’d like to explain what is included in our offer.",
        "our most competitive rate": "This is already our most competitive rate for the current quantity.",
        "limited room for further reduction": "There is limited room for further reduction if we keep the same quality standard.",
        "adjust the specifications": "If budget is the main concern, we can adjust the specifications together.",
    }
    return examples.get(phrase, f"We can use “{phrase}” in this situation.")

def build_all_seed_pages():
    pages = []
    for rule in SCENARIO_RULES[:10]:
        content = seed_content_for(rule)
        pages.append({
            "id": rule["id"],
            "title": rule["title"],
            "category": rule["category"],
            "business_view": rule["business_view"],
            "level": rule["level"],
            "target_user": "外贸业务员 / 销售 / 客服 / 运营",
            "work_scenario": {
                "problem": rule["problem"],
                "wrong_way": rule["wrong_way"],
                "strategy": rule["strategy"],
            },
            "source_excerpt": {
                "source": content["source"],
                "title": content["excerpt_title"],
                "url": "",
                "source_date": "",
                "fresh_window": "种子场景",
                "content_type": "场景模板",
                "paragraphs": content["excerpt"],
            },
            "business_judgement": content["judgement"],
            "core_phrases": [
                {"phrase": p[0], "meaning": p[1], "use_case": p[2], "example": example_for_phrase(p[0])}
                for p in content["phrases"]
            ],
            "email_expressions": content["email"],
            "email_template": {
                "subject": content["template_subject"],
                "body": content["template_body"],
            },
            "practice": practice_for_rule(rule),
            "cta": ["复制邮件模板", "收藏这个场景", "查看相关场景"],
            "updated_date": today_str(),
        })
    return pages

def item_to_history(content, href):
    return {
        "date": content.get("updated_date", today_str()),
        "title": content.get("title", ""),
        "category": content.get("category", ""),
        "business_view": content.get("business_view", ""),
        "level": content.get("level", "Level1"),
        "content_type": content.get("source_excerpt", {}).get("content_type", ""),
        "href": href,
    }

def render_home_page(today_content, all_pages):
    updated = today_content.get("updated_date", today_str())
    excerpt = today_content.get("source_excerpt", {})
    cards = "".join(
        f'<a class="feed-card" href="{esc("latest.html" if p.get("id")==today_content.get("id") else "archive/"+p.get("id","")+".html")}" data-cat="{esc(p["category"])}" data-view="{esc(p["business_view"])}" data-level="{esc(p["level"])}">'
        f'<span>{esc(p["category"])}</span><b>{esc(p["title"])}</b><em>{esc(p["business_view"])}｜{esc(p["level"])}</em></a>'
        for p in all_pages
    )
    cat_nav = "".join(f'<button class="chip" data-filter="{esc(c)}">{esc(c)}</button>' for c in PRACTICAL_CATEGORIES)
    view_nav = "".join(f'<button class="chip view" data-filter="{esc(v)}">{esc(v)}</button>' for v in BUSINESS_VIEW_TOPICS)
    judgement = detailed_judgement_for_rule({"id": today_content["id"], "title": today_content["title"], "business_view": today_content["business_view"]})
    key_points = "".join(f"<li>{esc(x)}</li>" for x in judgement[:3])
    return base_html(title="商业英语场景库", body=f"""
<header class="home-hero"><p class="eyebrow">Business English for Real Work</p><h1>先解决工作问题，再学英文表达。</h1><p class="sub">报价、催款、交期、投诉、客户跟进：每天用一条商业内容，拆成一个可直接复制的工作场景。</p></header>
<section class="focus-card"><div class="focus-left"><div class="label-row"><span>{esc(today_content["category"])}</span><span>{esc(today_content["business_view"])}</span><span>{esc(today_content["level"])}</span><span>{esc(excerpt.get("content_type"))}</span></div><h2>今日主场景：{esc(today_content["title"])}</h2><p class="problem">{esc(today_content["work_scenario"]["problem"])}</p><ul class="key-list">{key_points}</ul><div class="actions"><a class="primary" href="latest.html?v=46">查看完整模板</a><button onclick="copyTodayEmail()">复制今日邮件</button></div></div><div class="focus-right"><b>今日来源</b><p>{esc(excerpt.get("source") or "内置场景模板")}</p><small>更新：{esc(updated)}　原文：{esc(excerpt.get("source_date") or "无")}　窗口：{esc(excerpt.get("fresh_window") or "今日")}</small></div></section>
<section class="panel" id="practical"><div class="section-head"><h2>实战场景</h2><p>按你现在遇到的问题进入，不按英语知识点进入。</p></div><div class="tools" id="catTools"><button class="chip active" data-filter="全部">全部</button>{cat_nav}</div><div class="feed-grid" id="feedGrid">{cards}</div></section>
<section class="panel" id="view"><div class="section-head"><h2>商业视野</h2><p>趋势不是用来泛读的，是用来解释客户行为和写邮件的。</p></div><div class="tools" id="viewTools"><button class="chip active" data-filter="全部">全部</button>{view_nav}</div></section>
<script>const TODAY_EMAIL={json.dumps(today_content.get("email_template",{}).get("body", ""), ensure_ascii=False)};</script>
""", is_home=True)

def render_content_page(content, all_pages=None):
    all_pages = all_pages or []
    title = content["title"]
    updated = content["updated_date"]
    excerpt = content["source_excerpt"]
    source_link = f'<a class="source-link" href="{esc(excerpt.get("url"))}" target="_blank" rel="noopener">打开原文来源</a>' if excerpt.get("url") else ""
    excerpt_html = "".join(f'<div class="excerpt"><p class="en-text">{esc(p["en"])}</p><p class="cn-text">{esc(p["cn"])}</p></div>' for p in excerpt.get("paragraphs", []))
    judgement_html = "".join(f"<li>{esc(x)}</li>" for x in content["business_judgement"])
    steps_html = "".join(f"<li>{esc(x)}</li>" for x in action_steps_for_rule(content))
    phrase_html = "".join(f'<div class="phrase"><b>{esc(x["phrase"])}</b><p>{esc(x["meaning"])}</p><small>使用场景：{esc(x["use_case"])}</small><em>{esc(x["example"])}</em></div>' for x in content["core_phrases"])
    expr_html = "".join(f"<li>{esc(x)}</li>" for x in content["email_expressions"])
    variants = email_variants_for_rule(content)
    variant_html = "".join(f'<div class="email-variant"><h3>{esc(v["name"])}<small>{esc(v["note"])}</small></h3><p><b>Subject：</b>{esc(v["subject"])}</p><pre>{esc(v["body"])}</pre><button onclick="copyText(this)">复制这一版</button></div>' for v in variants)
    practice = content["practice"]
    practice_html = f"""<div class="practice-grid"><div><h3>Level1｜直接套用</h3>{''.join('<p>'+esc(x)+'</p>' for x in practice.get('level1', []))}</div><div><h3>Level2｜灵活改写</h3>{''.join('<p>'+esc(x)+'</p>' for x in practice.get('level2', []))}</div><div><h3>Level3｜真实回复</h3>{''.join('<p>'+esc(x)+'</p>' for x in practice.get('level3', []))}</div></div>"""
    related = "".join(f'<a class="mini-card" href="archive/{esc(p["id"])}.html"><span>{esc(p["category"])}</span><b>{esc(p["title"])}</b></a>' for p in all_pages[:6])
    return base_html(title=f"{title}｜商业英语场景库", body=f"""
<header class="detail-hero"><a class="back" href="index.html?v=46">← 返回首页 Feed</a><div class="label-row"><span>{esc(content["category"])}</span><span>{esc(content["business_view"])}</span><span>{esc(content["level"])}</span><span>{esc(excerpt.get("content_type"))}</span></div><h1>{esc(title)}</h1><p>{esc(content["work_scenario"]["problem"])}</p><small>更新：{esc(updated)}　原文：{esc(excerpt.get("source_date") or "无")}　窗口：{esc(excerpt.get("fresh_window") or "今日")}</small></header>
<section class="panel priority"><div class="section-head"><h2>1 工作场景：先判断怎么回</h2><p>这部分是重点，不是普通英语阅读。</p></div><div class="scene-grid"><div><b>用户真实问题</b><p>{esc(content["work_scenario"]["problem"])}</p></div><div><b>不建议怎么说</b><p>{esc(content["work_scenario"]["wrong_way"])}</p></div><div><b>推荐策略</b><p>{esc(content["work_scenario"]["strategy"])}</p></div></div></section>
<section class="panel source-panel"><div class="section-head"><h2>2 外刊/商业原文摘录</h2><p>可切换：英文 / 中英对照 / 中文解释。</p></div><p class="source-meta"><b>来源：</b>{esc(excerpt.get("source"))}｜<b>原文标题：</b>{esc(excerpt.get("title"))}</p>{source_link}<div class="mode-tabs"><button class="active" data-mode="bilingual">中英对照</button><button data-mode="en">只看英文</button><button data-mode="cn">只看中文解释</button></div><div id="excerptBox" class="bilingual">{excerpt_html}</div></section>
<section class="panel priority"><div class="section-head"><h2>3 工作应用判断</h2><p>把新闻背景转成客户沟通决策。</p></div><ul class="judgement">{judgement_html}</ul><h3>建议回复路径</h3><ol class="steps">{steps_html}</ol></section>
<section class="panel"><div class="section-head"><h2>4 核心词组</h2><p>只保留这个场景真能用的表达。</p></div><div class="phrase-grid">{phrase_html}</div></section>
<section class="panel"><div class="section-head"><h2>5 邮件表达</h2><p>按邮件功能拆句：缓冲、解释、方案、推进。</p></div><ul class="expression-list">{expr_html}</ul></section>
<section class="panel" id="template"><div class="section-head"><h2>6 完整邮件模板</h2><p>给三种语气，不只一封空泛模板。</p></div><div class="variant-grid">{variant_html}</div></section>
<section class="panel"><div class="section-head"><h2>7 分级练习</h2><p>Level 只是练习强度，不是导航。</p></div>{practice_html}</section>
<section class="panel"><div class="section-head"><h2>相关场景</h2><p>继续解决相邻工作问题。</p></div><div class="mini-grid">{related}</div></section>
""", is_home=False)

def base_html(title, body, is_home=False):
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, viewport-fit=cover"><meta name="generator" content="{MARKER}"><title>{esc(title)}</title><style>
:root{{--ink:#14211d;--muted:#62706b;--line:#dfe8e4;--card:#fffdf8;--green:#1f6b57;--green2:#e7f3ee;--orange:#bd6a42;--blue:#426d9e;}}*{{box-sizing:border-box}}html,body{{margin:0;padding:0;overflow-x:hidden;-webkit-text-size-adjust:100%;}}body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",Arial,sans-serif;background:linear-gradient(135deg,#fbfaf5,#edf6f1);color:var(--ink);}}a{{color:inherit}}.top{{position:sticky;top:0;z-index:20;background:rgba(246,243,235,.9);backdrop-filter:blur(14px);border-bottom:1px solid var(--line)}}.top-inner{{width:min(100% - 24px,1080px);margin:0 auto;display:flex;align-items:center;justify-content:space-between;gap:12px;padding:10px 0}}.logo{{font-weight:950;color:var(--green)}}.nav{{display:flex;gap:8px;overflow:auto;white-space:nowrap}}.nav a{{text-decoration:none;border:1px solid var(--line);background:#fff;border-radius:999px;padding:7px 10px;font-size:13px;font-weight:850;color:var(--green)}}.shell{{width:min(100% - 24px,1080px);margin:0 auto;padding:22px 0 42px}}.home-hero,.detail-hero{{padding:24px 0 18px}}.eyebrow{{color:var(--green);font-weight:950;margin:0 0 8px}}.home-hero h1,.detail-hero h1{{font-size:clamp(34px,7vw,66px);line-height:1.02;letter-spacing:-.055em;margin:0 0 12px}}.home-hero .sub,.detail-hero p{{font-size:clamp(15px,3.6vw,19px);line-height:1.7;color:var(--muted);max-width:780px;margin:0 0 10px}}.back{{display:inline-block;margin-bottom:12px;text-decoration:none;color:var(--green);font-weight:900}}.label-row{{display:flex;gap:8px;flex-wrap:wrap;margin:10px 0}}.label-row span{{border-radius:999px;padding:7px 10px;background:var(--green2);color:var(--green);font-size:12px;font-weight:950}}.focus-card,.panel{{background:rgba(255,253,248,.96);border:1px solid var(--line);border-radius:22px;box-shadow:0 14px 38px rgba(35,55,48,.08);padding:18px;margin:14px 0}}.focus-card{{display:grid;grid-template-columns:1.8fr .9fr;gap:18px;align-items:stretch;border:2px solid rgba(31,107,87,.18)}}.focus-card h2{{font-size:clamp(24px,4.8vw,38px);line-height:1.15;margin:8px 0}}.problem{{font-size:17px;line-height:1.7;color:var(--muted)}}.key-list{{line-height:1.8;padding-left:20px}}.focus-right{{background:#f4faf6;border-radius:16px;padding:14px;color:var(--muted);line-height:1.7}}.focus-right b{{display:block;color:var(--green);margin-bottom:8px}}.actions{{display:flex;gap:10px;flex-wrap:wrap;margin-top:16px}}.primary,.actions button,.email-variant button{{border:0;border-radius:999px;background:var(--green);color:#fff;text-decoration:none;font-weight:950;padding:10px 14px;cursor:pointer}}.section-head{{display:flex;justify-content:space-between;gap:12px;align-items:flex-start;margin-bottom:12px}}.section-head h2{{font-size:23px;margin:0;letter-spacing:-.02em}}.section-head p{{margin:0;color:var(--muted);font-size:13px;line-height:1.6;max-width:360px}}.priority{{border-left:6px solid var(--green)}}.scene-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:12px}}.scene-grid div{{border:1px solid var(--line);background:#f9fbf8;border-radius:16px;padding:14px}}.scene-grid b{{display:block;color:var(--green);margin-bottom:7px}}.scene-grid p{{margin:0;color:var(--muted);line-height:1.7}}.source-meta{{line-height:1.6}}.source-link{{display:inline-flex;margin:4px 0 10px;color:var(--green);font-weight:900;text-decoration:none}}.mode-tabs{{display:flex;gap:8px;flex-wrap:wrap;margin:10px 0}}.mode-tabs button{{border:1px solid var(--line);background:#fff;border-radius:999px;padding:8px 12px;font-weight:900;color:var(--green)}}.mode-tabs button.active{{background:var(--green);color:#fff;border-color:var(--green)}}.excerpt{{border-left:4px solid var(--green);background:#f8fbf7;border-radius:14px;padding:14px;margin:10px 0}}.en-text{{font-family:Georgia,"Times New Roman",serif;font-size:18px;line-height:1.78;margin:0 0 10px}}.cn-text{{color:var(--muted);line-height:1.75;margin:0;font-size:14px}}#excerptBox.en .cn-text{{display:none}}#excerptBox.cn .en-text{{display:none}}ul.judgement,.expression-list,.steps{{line-height:1.85;padding-left:22px}}.phrase-grid,.practice-grid,.variant-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px}}.phrase,.practice-grid div,.email-variant{{border:1px solid var(--line);border-radius:16px;background:#fbfaf6;padding:14px}}.phrase b{{display:block;color:var(--green);font-size:16px;margin-bottom:6px}}.phrase p{{margin:0 0 7px}}.phrase small{{display:block;color:var(--muted);line-height:1.55}}.phrase em{{display:block;margin-top:8px;font-size:13px;color:#40504a;line-height:1.6}}.email-variant h3{{margin:0 0 8px;color:var(--green)}}.email-variant h3 small{{display:block;color:var(--muted);font-weight:500;font-size:12px;margin-top:5px}}.email-variant pre{{white-space:pre-wrap;background:#fff8f3;border:1px solid #efd7c6;border-radius:14px;padding:12px;line-height:1.65;font-size:13px;overflow:auto}}.practice-grid h3{{margin:0 0 8px;font-size:15px;color:var(--green)}}.practice-grid p{{font-size:14px;line-height:1.65;margin:8px 0;color:#3d4b46}}.tools{{display:flex;gap:8px;overflow:auto;margin:10px 0 14px;padding-bottom:3px}}.chip{{flex:0 0 auto;border:1px solid var(--line);border-radius:999px;background:#fff;padding:8px 11px;font-size:13px;font-weight:900;color:var(--green)}}.chip.active{{background:var(--green);color:#fff;border-color:var(--green)}}.feed-grid,.mini-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:12px}}.feed-card,.mini-card{{display:block;text-decoration:none;border:1px solid var(--line);background:#fffdf8;border-radius:17px;padding:14px}}.feed-card span,.mini-card span{{font-size:12px;color:var(--green);font-weight:950}}.feed-card b,.mini-card b{{display:block;margin:7px 0;font-size:16px;line-height:1.45}}.feed-card em{{font-style:normal;color:var(--muted);font-size:13px}}@media(max-width:720px){{.top-inner{{align-items:flex-start;flex-direction:column}}.nav{{width:100%}}.focus-card{{grid-template-columns:1fr}}.section-head{{display:block}}.panel,.focus-card{{border-radius:18px;padding:15px}}.home-hero h1,.detail-hero h1{{font-size:clamp(32px,10vw,48px)}}}}
</style></head><body><div class="top"><div class="top-inner"><div class="logo">Business English for Real Work</div><nav class="nav"><a href="index.html?v=46">首页 Feed</a><a href="index.html#practical">实战场景</a><a href="index.html#view">商业视野</a><a href="latest.html#template">邮件模板</a></nav></div></div><main class="shell">{body}</main><script>
function copyText(btn){{const pre=btn.parentElement.querySelector('pre'); const txt=pre?pre.innerText:''; if(navigator.clipboard)navigator.clipboard.writeText(txt); alert('已复制');}}
function copyTodayEmail(){{const txt=window.TODAY_EMAIL||''; if(navigator.clipboard)navigator.clipboard.writeText(txt); alert('已复制今日邮件模板');}}
document.querySelectorAll('.mode-tabs button').forEach(btn=>{{btn.addEventListener('click',()=>{{document.querySelectorAll('.mode-tabs button').forEach(b=>b.classList.remove('active'));btn.classList.add('active');const box=document.getElementById('excerptBox');if(box)box.className=btn.dataset.mode;}})}});
function setupFilter(containerId, attr){{const root=document.getElementById(containerId);const grid=document.getElementById('feedGrid');if(!root||!grid)return;root.querySelectorAll('.chip').forEach(btn=>{{btn.addEventListener('click',()=>{{root.querySelectorAll('.chip').forEach(b=>b.classList.remove('active'));btn.classList.add('active');const val=btn.getAttribute('data-filter');grid.querySelectorAll('.feed-card').forEach(card=>{{const ok=val==='全部'||card.getAttribute(attr)===val;card.style.display=ok?'block':'none';}});}});}});}}
setupFilter('catTools','data-cat');setupFilter('viewTools','data-view');</script></body></html>"""

def build_editor_page():
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,viewport-fit=cover">
<meta name="generator" content="{MARKER}">
<title>商业英语场景工具｜编辑说明</title>
<style>
body{{margin:0;background:#f5f1e8;color:#14231e;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",Arial,sans-serif}}
.wrap{{width:min(100% - 24px,900px);margin:0 auto;padding:28px 0}}
.card{{background:#fffdf8;border:1px solid #dfe8e4;border-radius:22px;padding:20px;margin:14px 0;box-shadow:0 12px 36px rgba(35,55,48,.08)}}
h1{{font-size:clamp(32px,8vw,56px);line-height:1;margin:0 0 10px;color:#1f6b57}}
p,li{{line-height:1.8;color:#52615c}}
code,pre{{background:#f0f5f2;border-radius:10px;padding:2px 6px}}
a{{color:#1f6b57;font-weight:800}}
</style>
</head>
<body>
<div class="wrap">
  <h1>商业英语场景工具</h1>
  <p>当前版本：{MARKER}</p>
  <div class="card">
    <h2>现在的产品逻辑</h2>
    <p>底层仍然每日抓取外刊/商业新闻，但前台不再叫“外刊精读”。新内容会被转成真实工作场景：报价、催款、交期、投诉、客户跟进。</p>
  </div>
  <div class="card">
    <h2>抓取时间范围</h2>
    <ul>
      <li>优先：最近 14 天。</li>
      <li>正常：最近 30 天。</li>
      <li>兜底：最近 90 天长期有效商业内容。</li>
      <li>仍无合适内容：使用内置场景模板，并明确标注“场景模板”。</li>
    </ul>
  </div>
  <div class="card">
    <h2>页面入口</h2>
    <p><a href="./index.html">打开商业英语首页</a></p>
    <p><a href="./latest.html">打开最新场景</a></p>
    <p><a href="./history.json">查看历史数据</a></p>
  </div>
</div>
</body>
</html>"""

def update_history(content, archive_href):
    hist_path = OUTPUT_DIR / "history.json"
    hist = read_json(hist_path, [])
    if not isinstance(hist, list):
        hist = []
    item = item_to_history(content, archive_href)
    key = item["date"] + "|" + item["title"]
    hist = [x for x in hist if str(x.get("date","") + "|" + x.get("title","")) != key]
    hist.insert(0, item)
    write_json(hist_path, hist[:300])

def write_outputs():
    cfg = load_config()
    OUTPUT_DIR.mkdir(exist_ok=True)
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

    today = today_str()
    today_content = build_today_content(cfg)
    all_pages = build_all_seed_pages()
    all_for_feed = [today_content] + [p for p in all_pages if p["id"] != today_content["id"]]

    index_html = render_home_page(today_content, all_for_feed)
    latest_html = render_content_page(today_content, all_for_feed)
    archive_name = f"day-{today}.html"
    archive_path = ARCHIVE_DIR / archive_name

    (OUTPUT_DIR / "index.html").write_text(index_html, encoding="utf-8")
    (OUTPUT_DIR / "latest.html").write_text(latest_html, encoding="utf-8")
    archive_path.write_text(latest_html, encoding="utf-8")
    (OUTPUT_DIR / "editor.html").write_text(build_editor_page(), encoding="utf-8")

    update_history(today_content, f"/daily/archive/{archive_name}")

    for page in all_pages:
        p_html = render_content_page(page, all_for_feed)
        (ARCHIVE_DIR / f"{page['id']}.html").write_text(p_html, encoding="utf-8")

    save_used_article({
        "title": today_content.get("source_excerpt", {}).get("title") or today_content["title"],
        "link": today_content.get("source_excerpt", {}).get("url", "")
    })

    print("生成完成：")
    print(" -", OUTPUT_DIR / "index.html", "首页 Feed")
    print(" -", OUTPUT_DIR / "latest.html", "今日详情页")
    print(" -", OUTPUT_DIR / "editor.html")
    print(" -", archive_path)
    print(" -", OUTPUT_DIR / "history.json")
    print("版本：", MARKER)

if __name__ == "__main__":
    write_outputs()
