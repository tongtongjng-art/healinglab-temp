import json
import re
import sys
import html
import hashlib
import argparse
from pathlib import Path
from datetime import datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin

import feedparser
import requests
from dateutil import parser as date_parser
from bs4 import BeautifulSoup

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
OUTPUT_DIR = ROOT / "output"

USED_ARTICLES_PATH = ROOT / "used_articles.json"

# ElevenLabs TTS 已禁用：当前版本不在页面展示音频，也不自动生成音频。
ELEVENLABS_API_KEY = ""
ELEVENLABS_VOICE_ID = ""

def generate_audio(text, output_path):
    """调用ElevenLabs生成音频文件"""
    try:
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}"
        headers = {
            "xi-api-key": ELEVENLABS_API_KEY,
            "Content-Type": "application/json"
        }
        data = {
            "text": text,
            "model_id": "eleven_multilingual_v2",
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.75
            }
        }
        resp = requests.post(url, json=data, headers=headers, timeout=30)
        if resp.status_code == 200:
            output_path.write_bytes(resp.content)
            print(f"音频生成成功：{output_path}")
            return True
        else:
            print(f"音频生成失败：{resp.status_code} {resp.text[:200]}")
            return False
    except Exception as e:
        print(f"音频生成异常：{e}")
        return False


def normalize_article_link(link):
    link = (link or "").strip()
    link = link.split("#", 1)[0].split("?", 1)[0].rstrip("/")
    return link


def normalize_article_title(title):
    return clean_text(title or "").lower().strip()


def scan_archive_used_articles():
    """
    从已有历史页面里提取旧标题和原文链接。
    这样升级版本后，也能尽量避开之前已经用过的文章。
    """
    links = set()
    titles = set()

    archive_dirs = [
        ROOT / "output" / "archive",
        Path("/var/www/html/daily/archive"),
    ]

    for archive_dir in archive_dirs:
        if not archive_dir.exists():
            continue

        for p in archive_dir.glob("day-*.html"):
            try:
                html = p.read_text(encoding="utf-8-sig", errors="ignore")
            except Exception:
                continue

            # 原文链接一般在 “打开原文” 的 href 里。
            for m in re.finditer(r'href=["\']([^"\']+)["\']', html):
                link = normalize_article_link(m.group(1))
                if link.startswith("http"):
                    links.add(link)

            # 英文标题一般在 <strong>英文标题：</strong> 后面。
            m = re.search(r"英文标题：</strong>\s*([^<]+)", html)
            if m:
                title = normalize_article_title(m.group(1))
                if title:
                    titles.add(title)

    return {"links": sorted(links), "titles": sorted(titles)}


def load_used_articles(include_archive=True):
    data = {"links": [], "titles": []}

    if USED_ARTICLES_PATH.exists():
        try:
            raw = json.loads(USED_ARTICLES_PATH.read_text(encoding="utf-8-sig"))
            if isinstance(raw, dict):
                data["links"].extend(raw.get("links", []))
                data["titles"].extend(raw.get("titles", []))
        except Exception:
            pass

    if include_archive:
        archived = scan_archive_used_articles()
        data["links"].extend(archived.get("links", []))
        data["titles"].extend(archived.get("titles", []))

    # 统一清理、去重、规范化。
    data["links"] = sorted({normalize_article_link(x) for x in data["links"] if normalize_article_link(x)})
    data["titles"] = sorted({normalize_article_title(x) for x in data["titles"] if normalize_article_title(x)})

    return data


def save_used_article(article, max_keep=300):
    data = load_used_articles(include_archive=True)
    link = normalize_article_link(article.get("link"))
    title = normalize_article_title(article.get("title"))

    if link and link not in data["links"]:
        data["links"].append(link)
    if title and title not in data["titles"]:
        data["titles"].append(title)

    data["links"] = data["links"][-max_keep:]
    data["titles"] = data["titles"][-max_keep:]
    USED_ARTICLES_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8-sig")


def is_used_article(article):
    data = load_used_articles(include_archive=True)
    used_links = set(data.get("links", []))
    used_titles = set(data.get("titles", []))
    link = normalize_article_link(article.get("link"))
    title = normalize_article_title(article.get("title"))
    return bool((link and link in used_links) or (title and title in used_titles))


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0 Safari/537.36"
    )
}

COMMON_CAPITAL_WORDS = {
    "The", "This", "That", "These", "Those", "A", "An", "And", "But", "Or", "If",
    "In", "On", "At", "For", "From", "As", "When", "While", "After", "Before",
    "It", "Its", "They", "Their", "He", "She", "We", "You", "I", "My", "Our",
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
    "January", "February", "March", "April", "May", "June", "July", "August",
    "September", "October", "November", "December", "British", "American",
    "English", "Chinese", "European", "Australian", "Guardian", "BBC"
}

STOPWORDS = {
    "the", "and", "that", "this", "with", "from", "there", "their", "would", "could",
    "should", "about", "after", "before", "because", "while", "where", "which", "when",
    "what", "were", "been", "being", "have", "has", "had", "will", "they", "them",
    "than", "then", "into", "over", "under", "also", "just", "more", "some", "many",
    "people", "said", "says", "say", "one", "two", "can", "may", "might", "must",
    "for", "are", "was", "not", "but", "you", "your", "our", "out", "all", "any"
}

EXPRESSION_RULES = [
    # 趋势 / 调查 / 观点类
    ("no longer ...", r"\bno longer\b", "不再……；表达观念或状态变化。", "原文句子"),
    ("be worth the time or money", r"\bworth (?:the )?(?:time|money|cost|effort)\b|\bworth\b.{0,40}\b(?:time|money|cost|effort)\b", "值得花时间/金钱/精力；讨论价值判断。", "原文句子"),
    ("poll/survey shows ...", r"\b(?:poll|survey|research|study|data) (?:shows?|finds?|suggests?|reveals?)\b", "调查/研究显示……；引出事实依据。", "原文句子"),
    ("be more likely to ...", r"\bmore likely to\b", "更有可能……；比较概率或趋势。", "原文句子"),
    ("be less likely to ...", r"\bless likely to\b", "不太可能……；比较概率或趋势。", "原文句子"),
    ("there is no getting away from the fact that ...", r"\bno getting away from the fact that\b", "不可否认的是……；强调现实情况。", "原文句子"),
    ("the fact that ...", r"\bthe fact that\b", "……这一事实；引出事实或观点。", "原文句子"),

    # 解释 / 原因 / 结果
    ("one reason is that ...", r"\bone reason\b", "一个原因是……；解释原因。", "原文句子"),
    ("because of ...", r"\bbecause of\b", "因为……；后面接名词或名词短语。", "原文句子"),
    ("as a result", r"\bas a result\b", "结果是……；连接原因和结果。", "原文句子"),
    ("enough to ...", r"\benough to\b", "足以……；表达程度。", "原文句子"),
    ("make it seem like ...", r"\bmake it seem like\b|\bmakes it seem like\b|\bmade it seem like\b", "让它看起来像……；表达表象。", "原文句子"),
    ("this means that ...", r"\bthis means that\b", "这意味着……；解释含义。", "原文句子"),
    ("this shows how ...", r"\bthis shows how\b", "这说明……；从事实引出观点。", "原文句子"),

    # 对比 / 转折 / 选择
    ("not just ..., but also ...", r"\bnot just\b|\bnot only\b", "不只是……，而且……；扩展观点。", "原文句子"),
    ("not ..., but ...", r"\bnot\b.{0,80}\bbut\b", "不是……而是……；表达转折或纠正。", "原文句子"),
    ("rather than ...", r"\brather than\b", "而不是……；比较两种选择。", "原文句子"),
    ("instead of ...", r"\binstead of\b", "而不是……；表达替代选择。", "原文句子"),
    ("whether ... or ...", r"\bwhether\b.{0,120}\bor\b", "无论是……还是……；列举两种可能。", "原文句子"),
    ("regardless of ...", r"\bregardless of\b", "无论……；不受某因素影响。", "原文句子"),

    # 生活/职场/学习常用
    ("when it comes to ...", r"\bwhen it comes to\b", "说到……；引出具体话题。", "原文句子"),
    ("the way people ...", r"\bthe way\b", "人们……的方式；表达变化很常用。", "原文句子"),
    ("for many people", r"\bfor many people\b|\bfor many\b", "对很多人来说；引出普遍感受。", "原文句子"),
    ("in daily life", r"\bdaily life\b|\beveryday life\b", "在日常生活中；用于生活化表达。", "原文句子"),
    ("in the long run", r"\bin the long run\b", "从长远来看。", "原文句子"),
    ("over time", r"\bover time\b", "随着时间推移。", "原文句子"),
    ("on top of ...", r"\bon top of\b", "在……之外还；表达额外负担或增加。", "原文句子"),
    ("be challenging for ...", r"\bchallenging\b", "对……来说有挑战；描述困难。", "原文句子"),

    # 行动 / 改变
    ("want to / need to ...", r"\bwant to\b|\bneed to\b", "想要/需要……；基础但实用。", "原文句子"),
    ("try to ...", r"\btry to\b", "试着……；口语高频表达。", "原文句子"),
    ("be trying to ...", r"\btrying to\b", "正在试图……；表达当前努力。", "原文句子"),
    ("be becoming / has become ...", r"\bbecoming\b|\bhas become\b|\bhave become\b", "正在变得/已经变得……；描述变化。", "原文句子"),
    ("used to ...", r"\bused to\b", "过去常常……；描述过去和现在的变化。", "原文句子"),
    ("make it easier to ...", r"\bmake it easier\b|\bmakes it easier\b|\bmade it easier\b", "让做某事更容易。", "原文句子"),
    ("find it hard/difficult to ...", r"\bfind it hard\b|\bfind it difficult\b|\bfound it hard\b|\bfound it difficult\b", "觉得做某事很难。", "原文句子"),
]


FALLBACK_EXPRESSIONS = [
    ("This paragraph is about ...", "这段主要讲的是……；复述开头用。", "This paragraph is about a change in daily life."),
    ("The key point is that ...", "重点是……；概括核心观点。", "The key point is that small habits can change how people learn."),
    ("One detail I noticed is ...", "我注意到的一个细节是……；补充细节。", "One detail I noticed is that people want something easier to continue."),
    ("This shows how ...", "这说明……；从事实引出观点。", "This shows how everyday choices can shape learning habits."),
    ("I think this matters because ...", "我觉得这重要，因为……；表达个人看法。", "I think this matters because confidence is built through small practice.")
]


def load_config():
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))


def clean_text(text: str) -> str:
    if not text:
        return ""
    text = str(text)
    text = re.sub(r"<script[\s\S]*?</script>", " ", text, flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.I)
    text = re.sub(r"<noscript[\s\S]*?</noscript>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def word_count(text: str) -> int:
    return len(re.findall(r"\b[a-zA-Z]+(?:[-'][a-zA-Z]+)?\b", text))


def split_sentences(text: str):
    text = clean_text(text)
    parts = re.split(r"(?<=[.!?])\s+", text)
    return [p.strip() for p in parts if len(p.strip()) > 20]


def sentence_count(text: str) -> int:
    return len(re.findall(r"[.!?]", text))


def avg_sentence_words(text: str) -> float:
    return word_count(text) / max(sentence_count(text), 1)


def parse_date(entry):
    for key in ["published", "updated", "created"]:
        value = entry.get(key)
        if value:
            try:
                return parsedate_to_datetime(value).isoformat()
            except Exception:
                return str(value)
    return ""



def parse_any_date(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        dt = date_parser.parse(str(value), fuzzy=True)
        if dt.tzinfo:
            dt = dt.replace(tzinfo=None)
        return dt
    except Exception:
        return None


def article_age_days(item):
    dt = parse_any_date(item.get("published", ""))
    if not dt:
        return None
    return (datetime.now() - dt).days


def within_max_age(item, cfg):
    max_days = int(cfg.get("max_article_age_days", 90))
    age = article_age_days(item)
    if age is None:
        return True
    return age <= max_days


def recency_score(item, cfg):
    age = article_age_days(item)
    if age is None:
        return 0
    if age < 0:
        return 0
    if age <= 7:
        return 14
    if age <= 30:
        return 9
    if age <= int(cfg.get("max_article_age_days", 90)):
        return 4
    return -80


def display_publish_date(item):
    dt = parse_any_date(item.get("published", ""))
    if not dt:
        return ""
    return dt.strftime("%Y-%m-%d")


def recent_ai_article_count(days=7):
    """从历史目录粗略统计最近 days 天 AI 文章数量，用来控制 AI 频率。"""
    count = 0
    archive_dirs = [ROOT / "output" / "archive", Path("/var/www/html/daily/archive")]
    today = datetime.now()
    ai_terms = [" ai ", "artificial intelligence", "machine learning", "generative ai", "chatgpt"]
    seen_dates = set()
    for archive_dir in archive_dirs:
        if not archive_dir.exists():
            continue
        for p in archive_dir.glob("day-*.html"):
            if p.name.endswith("-xhs.html"):
                continue
            m = re.search(r"day-(\d{4}-\d{2}-\d{2})\.html$", p.name)
            if not m:
                continue
            date_str = m.group(1)
            if date_str in seen_dates:
                continue
            try:
                dt = datetime.strptime(date_str, "%Y-%m-%d")
            except Exception:
                continue
            if (today - dt).days < 0 or (today - dt).days > days:
                continue
            try:
                html_text = " " + p.read_text(encoding="utf-8-sig", errors="ignore").lower() + " "
            except Exception:
                continue
            if any(term in html_text for term in ai_terms):
                count += 1
                seen_dates.add(date_str)
    return count


def has_ai_topic(text, cfg):
    low = " " + text.lower() + " "
    for k in cfg.get("ai_bonus_keywords", []):
        kk = k.lower()
        if kk.strip() in low:
            return True
    return False


def ai_topic_score(text, cfg):
    if not cfg.get("ai_priority_enabled", True):
        return 0
    low = " " + text.lower() + " "
    score = 0
    if has_ai_topic(low, cfg):
        score += 45
        for k in cfg.get("ai_good_context_keywords", []):
            if k.lower() in low:
                score += 8
        for k in cfg.get("ai_bad_context_keywords", []):
            if k.lower() in low:
                score -= 18
    return score



def contains_any(text, terms):
    """
    V18.2 修复：
    旧版 regex 字符类里有连字符转义问题，服务器 Python 报：
    re.error: bad character range \\-’
    这里不用 regex 判断 term 是否为英文词，改为纯字符检查。
    """
    low = text.lower()
    allowed_chars = set("abcdefghijklmnopqrstuvwxyz0123456789'’ -")

    for term in terms:
        raw = str(term or "").strip()
        if not raw:
            continue
        t = raw.lower().strip()

        # 删除早期为菜谱临时加入的极短单位词，避免误伤所有文本。
        if t in {"g", "kg", "ml"}:
            continue

        # 对英文词/英文短语做边界匹配，避免 skilled 误中 killed、postwar 误中 war。
        if t and t[0].isalnum() and t[-1].isalnum() and all(c in allowed_chars for c in t):
            pattern = r"(?<![a-z0-9])" + re.escape(t) + r"(?![a-z0-9])"
            if re.search(pattern, low):
                return True, term
        else:
            if t in low:
                return True, term

    return False, ""


def proper_noun_stats(text):
    tokens = re.findall(r"\b[A-Z][a-zA-Z]{2,}\b|\b[A-Z]{2,}\b", text)
    filtered = [t for t in tokens if t not in COMMON_CAPITAL_WORDS]
    sequences = re.findall(r"\b[A-Z][a-zA-Z]{2,}(?:\s+[A-Z][a-zA-Z]{2,})+\b", text)
    acronyms = re.findall(r"\b[A-Z]{2,}\b", text)
    return {
        "capital_count": len(filtered),
        "sequence_count": len(sequences),
        "acronym_count": len(acronyms),
        "examples": filtered[:8]
    }


def too_many_proper_nouns(text):
    wc = max(word_count(text), 1)
    stats = proper_noun_stats(text)
    # V18：AI/教育/科技文章天然有 MIT、AI、大学、机构名。
    # 原阈值太严会把真正合适的主题文章误杀，所以改为“只拦截极端专名堆砌”。
    if stats["sequence_count"] >= 7:
        return True, f"连续专名过多：{stats['sequence_count']}"
    if stats["capital_count"] >= 22 and wc < 170:
        return True, f"大写专名过多：{stats['capital_count']}"
    if stats["capital_count"] / wc > 0.20 and stats["capital_count"] >= 12:
        return True, f"专名密度过高：{stats['capital_count']}/{wc}"
    if stats["acronym_count"] >= 8:
        return True, f"缩写过多：{stats['acronym_count']}"
    return False, ""


def hard_to_speak(text):
    long_words = re.findall(r"\b[a-zA-Z]{12,}\b", text)
    allowed = {
        "relationship", "relationships", "understanding", "conversation",
        "communication", "education", "technology", "comfortable",
        "experience", "interesting", "important", "international",
        "environment", "traditional"
    }
    unusual = [w for w in long_words if w.lower() not in allowed]
    if len(unusual) >= 7:
        return True, f"长难词过多：{len(unusual)}"
    if avg_sentence_words(text) > 34:
        return True, f"平均句长过长：{avg_sentence_words(text):.1f}"
    return False, ""


def mit_topic_page_urls(base_url, max_pages):
    urls = [base_url]
    for i in range(1, int(max_pages)):
        urls.append(f"{base_url}?page={i}")
    return urls


def find_nearby_summary_and_date(h):
    """
    MIT topic page structure:
    h3 link -> summary paragraph -> date -> Read full story.
    This function walks nearby siblings/parent text instead of assuming one fixed DOM structure.
    """
    summary = ""
    published = ""

    # First try parent text.
    container = h.parent
    texts = []
    if container:
        # h parent and next few siblings often contain summary/date.
        for node in [container] + list(container.find_next_siblings(limit=6)):
            t = clean_text(node.get_text(" ", strip=True))
            if t:
                texts.append(t)

    joined = " ".join(texts)

    m = re.search(r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},\s+\d{4}\b", joined)
    if m:
        published = m.group(0)

    # Candidate summary: sentence near title, not nav/date/read-full-story.
    title = clean_text(h.get_text(" ", strip=True))
    for t in texts:
        t2 = t.replace(title, " ")
        t2 = re.sub(r"\b(Read full story|Displaying \d+|Show:|News Articles|In the Media|Audio|Page \d+|Next page)\b.*", "", t2, flags=re.I)
        t2 = clean_text(t2)
        # strip date
        t2 = re.sub(r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},\s+\d{4}\b", "", t2)
        t2 = clean_text(t2)
        if len(t2.split()) >= 8:
            summary = t2
            break

    return summary, published


def fetch_mit_topic_page_items(page_cfg, cfg):
    """
    V17 robust MIT topic page parser.
    Fetches multiple pages and prints candidate counts, so we can see whether the topic pool is really working.
    """
    items = []
    base_url = page_cfg.get("url", "")
    name = page_cfg.get("name", "MIT News")
    if not base_url:
        return items

    max_pages = int(page_cfg.get("max_pages", cfg.get("topic_page_max_pages", 5)))
    article_limit = int(cfg.get("topic_page_article_limit", 90))

    print(f"抓取专题页：{name}（最多 {max_pages} 页）")

    for page_url in mit_topic_page_urls(base_url, max_pages):
        print(f"  - 专题页：{page_url}")
        try:
            r = requests.get(page_url, timeout=25, headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
        except Exception as e:
            print(f"  专题页抓取失败：{e}")
            continue

        soup = BeautifulSoup(r.text, "html.parser")
        page_count = 0

        for h in soup.find_all(["h2", "h3"]):
            a = h.find("a", href=True)
            if not a:
                continue

            title = clean_text(a.get_text(" ", strip=True))
            if not title or title.lower() in {"next page", "read full story"}:
                continue
            if len(title.split()) < 3:
                continue

            link = urljoin(page_url, a["href"])
            if not link.startswith("http"):
                continue
            # MIT article URLs are usually /news/...; skip nav/topic links.
            if "/news/" not in link:
                continue

            summary, published = find_nearby_summary_and_date(h)
            item = {
                "source": name,
                "title": title,
                "link": link,
                "summary": summary,
                "published": published,
            }

            if not within_max_age(item, cfg):
                continue

            combined = f"{title} {summary}"
            hard_bad, reason = contains_any(combined, cfg.get("hard_avoid", []))
            soft_bad, reason2 = contains_any(combined, cfg.get("soft_avoid", []))
            if hard_bad or soft_bad:
                continue

            # For MIT AI pool, require AI topic OR good education/work/human context in title/summary.
            low = combined.lower()
            ai_like = has_ai_topic(combined, cfg)
            good_context = any(k.lower() in low for k in cfg.get("ai_good_context_keywords", []))
            if not (ai_like or good_context):
                continue

            items.append(item)
            page_count += 1
            if len(items) >= article_limit:
                break

        print(f"  本页候选：{page_count}，累计：{len(items)}")
        if len(items) >= article_limit:
            break

    return items


def fetch_topic_page_items(cfg):
    items = []
    for page_cfg in cfg.get("topic_pages", []):
        ptype = page_cfg.get("type", "")
        if ptype == "mit_topic":
            items.extend(fetch_mit_topic_page_items(page_cfg, cfg))
    return items



def fetch_theme_seed_candidates(cfg):
    """
    V18：
    主题先行候选池。先把明确符合产品方向的高质量文章放入候选，
    避免只靠 RSS 最新流碰运气。
    """
    items = []
    for raw in cfg.get("theme_seed_candidates", []):
        item = {
            "source": raw.get("source", "Theme Seed"),
            "title": clean_text(raw.get("title", "")),
            "link": raw.get("link", ""),
            "summary": clean_text(raw.get("summary", "")),
            "published": raw.get("published", ""),
            "theme": raw.get("theme", ""),
            "curated_priority": int(raw.get("curated_priority", 0)),
            "theme_seed": True,
        }
        if not item["title"] or not item["link"]:
            continue
        if not within_max_age(item, cfg):
            continue
        items.append(item)
    print(f"主题种子候选：{len(items)}")
    return items


def fetch_feed_items(cfg):
    items = []
    per_feed = int(cfg.get("entry_limit_per_feed", 80))

    if cfg.get("theme_seed_candidates_first", True):
        items.extend(fetch_theme_seed_candidates(cfg))

    if cfg.get("topic_pool_first", True):
        topic_items = fetch_topic_page_items(cfg)
        print(f"专题页候选总数：{len(topic_items)}")
        items.extend(topic_items)

    for feed in cfg["feeds"]:
        print(f"抓取 RSS：{feed['name']}")
        parsed = feedparser.parse(feed["url"])
        for entry in parsed.entries[:per_feed]:
            title = clean_text(entry.get("title", ""))
            link = entry.get("link", "")
            summary = clean_text(entry.get("summary", entry.get("description", "")))
            if not title or not link:
                continue

            item = {
                "source": feed["name"],
                "title": title,
                "link": link,
                "summary": summary,
                "published": parse_date(entry),
            }

            if not within_max_age(item, cfg):
                continue

            combined = f"{title} {summary}"
            hard_bad, _ = contains_any(combined, cfg.get("hard_avoid", []))
            soft_bad, _ = contains_any(combined, cfg.get("soft_avoid", []))
            if hard_bad or soft_bad:
                continue

            items.append(item)

    if not cfg.get("topic_pool_first", True):
        topic_items = fetch_topic_page_items(cfg)
        print(f"专题页候选总数：{len(topic_items)}")
        items.extend(topic_items)

    dedup = {}
    for item in items:
        key = normalize_article_link(item.get("link")) or normalize_article_title(item.get("title"))
        if not key:
            continue
        if key not in dedup:
            dedup[key] = item
    result = list(dedup.values())
    print(f"总候选去重后：{len(result)}")
    return result



def editorial_reject_reason(text, cfg):
    """
    主编式选题过滤：
    不是判断文章能不能读，而是判断它像不像“每日精选外刊”。
    """
    if not cfg.get("editorial_mode", True):
        return False, ""

    low = text.lower()

    for k in cfg.get("editorial_hard_avoid", []):
        bad, matched = contains_any(low, [k])
        if bad:
            return True, f"主编避雷题材：{matched}"

    # 商品导购/工具教程类：标题里出现 best/how to/set up + 具体商品词，通常不够“外刊表达精选”。
    product_words = [
        "light", "lights", "bulb", "bulbs", "lamp", "device", "devices",
        "gadget", "gadgets", "app", "apps", "screen", "TV", "camera",
        "mattress", "vacuum", "headphones", "speaker"
    ]
    if re.search(r"\b(best|buy|setup|set up|install|review|guide)\b", low):
        if any(w.lower() in low for w in product_words):
            return True, "商品导购/工具教程感太强"

    # 外貌焦虑类：不一定不能读，但不适合作为默认每日推荐。
    appearance_words = [
        "appearance", "beauty", "skin", "hair", "body", "look younger",
        "anti-ageing", "makeover", "glow"
    ]
    pressure_words = ["should", "need to", "must", "pressure", "standard", "standards"]
    if any(w in low for w in appearance_words) and any(w in low for w in pressure_words):
        return True, "外貌压力/外貌焦虑题材"

    return False, ""


def editorial_topic_score(text, source, cfg):
    """
    给文章加“像精选外刊”的审美分。
    分高 = 更生活化、更有讨论感、更适合复述。
    """
    if not cfg.get("editorial_mode", True):
        return 0

    low = text.lower()
    source_low = source.lower()
    score = 0

    # 好题材加分
    for k in cfg.get("editorial_bonus_keywords", []):
        if k.lower() in low:
            score += 9

    # 好来源轻微加分
    for s in cfg.get("editorial_ideal_sources", []):
        if s.lower() in source_low:
            score += 6

    # 有观点/解释/趋势感的标题更适合复述
    if re.search(r"\bwhy\b|\bhow\b|\bwhat\b|\breason\b|\bbenefit\b|\bchange\b", low):
        score += 18
    if re.search(r"\bstudy finds\b|\bresearch shows\b|\bpoll shows\b|\bsurvey\b", low):
        score += 16
    if re.search(r"\bmore and more\b|\bincreasingly\b|\bless likely\b|\bmore likely\b", low):
        score += 12

    # 可迁移到口语表达的日常议题
    if re.search(r"\b(daily|everyday|home|work|sleep|habit|routine|stress|confidence|learn|learning|health|nature|travel)\b", low):
        score += 16

    # 题材过窄/消费感强扣分
    for k in cfg.get("editorial_penalty_keywords", []):
        bad, _ = contains_any(low, [k])
        if bad:
            score -= 10

    # 标题过像清单/导购，扣分
    if re.search(r"\b(best|top|guide to|how to buy|things to buy)\b", low):
        score -= 18

    return score





def is_hard_news_or_market_topic(text):
    """
    V27.3：硬新闻过滤改为“边界匹配 + 明确硬新闻词”。
    旧版用 if t in low，war 会误伤 hardware/award，trial/parliament 也会误伤教育类文章。
    """
    hard_terms = [
        "breaking news", "live updates", "election", "politics live",
        "president", "minister says", "government says", "stock market",
        "interest rates", "central bank", "shares fell", "investors",
        "court case", "police", "crime", "attack", "military", "missile"
    ]
    bad, matched = contains_any(text, hard_terms)
    if bad:
        return True, f"硬新闻/市场/政治司法题材：{matched}"
    return False, ""


def is_quiz_or_trivia_topic(text):
    low = text.lower()
    terms = [
        "quiz", "news quiz", "thursday quiz", "trivia",
        "general knowledge", "test your knowledge",
        "answers to this quiz", "question 1", "question 2",
        "multiple choice"
    ]
    for t in terms:
        if t in low:
            return True, f"问答/测试/冷知识题材：{t}"
    return False, ""

def is_unsafe_default_reading_topic(text):
    """
    V14.4：
    默认每日外刊需要适合公开阅读/学习。
    排除成人、性、生殖健康、私密身体部位、低俗网络黑话等话题。
    """
    low = text.lower()

    unsafe_terms = [
        "spermaxxing", "sperm", "semen", "testicle", "testicles",
        "fertility", "male fertility", "female fertility", "reproductive",
        "reproduction", "sex", "sexual", "sexuality", "libido",
        "orgasm", "erection", "porn", "pornography", "genital",
        "genitals", "penis", "vagina", "balls", "hookup",
        "raw garlic", "dip your testicles", "ice water"
    ]

    for term in unsafe_terms:
        if term in low:
            return True, f"成人/生殖/私密身体话题：{term}"

    # 网络黑话 + 身体优化类，默认也排除。
    if "maxing" in low and any(x in low for x in ["body", "sperm", "sex", "dating", "looks", "appearance"]):
        return True, "身体优化/网络黑话题材"

    return False, ""

def is_recipe_or_cooking_instructions(text):
    """
    V14.3：
    排除菜谱/烹饪步骤/配料说明。
    允许饮食文化、营养健康类文章，但不允许具体 recipe。
    """
    low = text.lower()

    recipe_markers = [
        "recipe", "ingredients", "method", "serves", "prep time", "cook time",
        "season to taste", "stick blender", "neutral oil", "olive oil",
        "extra-virgin", "tbsp", "tsp", "tablespoon", "teaspoon",
        "ml ", " g ", " kg ", "225ml", "25ml"
    ]

    action_markers = [
        "give the", "put the", "pour", "whizz", "season", "peel", "wash",
        "boil", "fry", "roast", "bake", "chop", "slice", "stir", "mix",
        "add the", "heat the", "cook until", "serve with"
    ]

    food_item_markers = [
        "potato", "potatoes", "spuds", "chickpea", "egg mixture",
        "oil", "pan", "skins", "flavour", "flesh"
    ]

    score = 0
    score += sum(1 for x in recipe_markers if x in low) * 3
    score += sum(1 for x in action_markers if x in low) * 2
    score += sum(1 for x in food_item_markers if x in low)

    # 明确菜谱词，直接排除。
    if any(x in low for x in ["recipe", "ingredients", "method", "season to taste", "stick blender"]):
        return True

    # 多个烹饪动作 + 食材/计量词，也排除。
    if score >= 7:
        return True

    return False



def positive_daily_topic_check(text, cfg):
    """
    V15.1：
    默认每日材料必须明显命中正向/日常/学习价值主题。
    不再是“没有违规就能选”。
    """
    if not cfg.get("positive_daily_topic_mode", True):
        return True, ""

    low = text.lower()
    topics = [x.lower() for x in cfg.get("positive_daily_topics", [])]
    hits = [x for x in topics if x in low]
    min_hits = int(cfg.get("positive_daily_min_hits", 1))

    if len(hits) < min_hits:
        return False, "没有明显命中正向日常/学习主题"

    return True, ""


def heavy_negative_reject_reason(text, cfg):
    low = text.lower()
    for k in cfg.get("heavy_negative_avoid", []):
        if k.lower() in low:
            return True, f"沉重/负面题材：{k}"
    return False, ""


def final_clean_topic_reject_reason(text, source, cfg):
    """
    V14 最终选文过滤：
    目标是“每日精选外刊阅读”，不是随机新闻/游记/商品教程。
    """
    low = text.lower()
    source_low = source.lower()

    unsafe_bad, unsafe_reason = is_unsafe_default_reading_topic(text)
    if unsafe_bad:
        return True, unsafe_reason

    quiz_bad, quiz_reason = is_quiz_or_trivia_topic(text)
    if quiz_bad:
        return True, quiz_reason

    hard_bad, hard_reason = is_hard_news_or_market_topic(text)
    if hard_bad:
        return True, hard_reason

    negative_bad, negative_reason = heavy_negative_reject_reason(text, cfg)
    if negative_bad:
        return True, negative_reason

    positive_ok, positive_reason = positive_daily_topic_check(text, cfg)
    if not positive_ok:
        return True, positive_reason

    if is_recipe_or_cooking_instructions(text):
        return True, "菜谱/烹饪步骤，不适合作为每日外刊阅读材料"

    # hard avoid from config：使用边界匹配，避免 war 误伤 hardware、trial 误伤普通教育语境。
    bad, matched = contains_any(text, cfg.get("editorial_hard_avoid", []))
    if bad:
        return True, f"选题避雷：{matched}"

    # 路线游记：地名/路程/探险感太强，不适合默认每日材料
    route_words = [
        "riverboat", "voyage", "upriver", "miles", "gateway to", "andes",
        "amazon", "peru", "colombian", "trek", "hiking", "itinerary",
        "six-week", "adventure", "route", "journey"
    ]
    if ("travel" in source_low or "travel" in low) and sum(1 for w in route_words if w in low) >= 2:
        return True, "路线游记/地理探险题材，不适合作为默认每日外刊"

    # 商品导购/安装教程
    product_words = [
        "light", "lights", "bulb", "bulbs", "lamp", "device", "devices",
        "gadget", "gadgets", "screen", "tv", "camera", "mattress",
        "headphones", "speaker", "fixture", "switch"
    ]
    if re.search(r"\b(best|buy|set up|setup|install|review|guide)\b", low):
        if any(w in low for w in product_words):
            return True, "商品导购/工具教程感太强"

    # 外貌焦虑/美容压力
    appearance_words = [
        "appearance", "beauty", "skin", "hair", "body", "look younger",
        "anti-ageing", "makeover", "glow", "cosmetic", "cosmetics"
    ]
    pressure_words = ["should", "need to", "must", "pressure", "standard", "standards"]
    if any(w in low for w in appearance_words) and any(w in low for w in pressure_words):
        return True, "外貌压力/美容焦虑题材"

    # 过重公共议题
    if "climate crisis" in low or "biodiversity loss" in low:
        return True, "气候危机/生物多样性议题偏重"

    return False, ""


def clean_daily_topic_score(text, source, cfg):
    """
    V14 主编评分：
    高分 = 日常、积极、可理解、可复述、适合作为外刊学习材料。
    """
    low = text.lower()
    source_low = source.lower()
    score = 0

    for k in cfg.get("editorial_bonus_keywords", []):
        if k.lower() in low:
            score += 10

    for s in cfg.get("editorial_ideal_sources", []):
        if s.lower() in source_low:
            score += 8

    if re.search(r"\bwhy\b|\bhow\b|\bwhat\b|\breason\b|\bbenefit\b|\bchange\b", low):
        score += 18
    if re.search(r"\bstudy finds\b|\bresearch shows\b|\bpoll shows\b|\bsurvey\b", low):
        score += 18
    if re.search(r"\bmore people\b|\bmore and more\b|\bincreasingly\b|\bmore likely\b|\bless likely\b", low):
        score += 14
    if re.search(r"\b(daily|everyday|home|work|sleep|habit|routine|stress|confidence|learn|learning|health|nature|reading|books)\b", low):
        score += 18

    for k in cfg.get("editorial_penalty_keywords", []):
        if k.lower() in low:
            score -= 12

    if "travel" in source_low:
        travel_daily_words = [
            "walking", "city life", "home", "family", "food", "habit",
            "daily", "everyday", "community", "wellbeing", "work", "learn"
        ]
        if any(w in low for w in travel_daily_words):
            score += 3
        else:
            score -= int(cfg.get("travel_source_penalty", 35))

    return score



def source_quality_score(source, cfg):
    source_low = source.lower()
    score = 0

    for key, val in cfg.get("source_quality_bonus", {}).items():
        if key.lower() in source_low:
            score += int(val)

    for key, val in cfg.get("source_quality_penalty", {}).items():
        if key.lower() in source_low:
            score -= int(val)

    return score


def theme_quality_score(item, cfg):
    text = f"{item.get('source','')} {item.get('title','')} {item.get('summary','')} {item.get('theme','')}"
    low = text.lower()
    score = 0

    if item.get("theme_seed"):
        score += int(item.get("curated_priority", 0))

    for k in cfg.get("theme_quality_keywords", []):
        if k.lower() in low:
            score += 14

    for k in cfg.get("theme_hard_avoid", []):
        if k.lower() in low:
            score -= 80

    # 明确偏产品目标的标题加分
    if "ai" in low and any(x in low for x in ["work", "career", "job", "education", "learning", "student", "skill"]):
        score += 80
    if any(x in low for x in ["young people", "young workers", "graduates", "low-cost", "happiness"]):
        score += 60

    return score


def xhs_topic_reject_reason(text, source, cfg):
    """
    V33：
    小红书发布版选题过滤。
    目标不是“能不能读”，而是“适不适合发小红书外刊表达练习”。
    """
    if not cfg.get("xhs_topic_mode", True):
        return False, ""

    low = text.lower()
    source_low = source.lower()

    hard_terms = [
        "net zero", "net-zero", "clean electricity", "cheap electricity",
        "power grid", "heat pump", "heat pumps", "carbon emissions",
        "emissions", "decarbonisation", "decarbonization",
        "renewable energy", "fossil fuels", "unit of heat", "unit of power",
        "energy policy", "climate policy", "public policy",
        "stock market", "interest rates", "central bank",
        "court", "trial", "lawsuit", "parliament", "minister",
        "war", "attack", "killed", "murder", "weapon",
        "clinical trial", "medical breakthrough", "patients could", "cancer"
    ]
    for t in hard_terms:
        if topic_has(low, t):
            return True, f"不适合小红书发布的硬题材：{t}"

    # 电力/能源/气候组合，一律不作为默认图文发布选题
    energy_words = ["electricity", "energy", "power", "grid", "heat pump", "gas", "carbon", "emissions", "net zero", "clean energy"]
    if sum(1 for w in energy_words if topic_has(low, w)) >= 2:
        return True, "能源/电力/气候政策题材偏硬，不适合默认发布"

    # 硬科学来源如果没有明显生活/学习/工作入口，跳过
    friendly_words = [
        "daily", "everyday", "habit", "routine", "sleep", "focus", "attention",
        "stress", "confidence", "journaling", "writing", "reading",
        "screen-free", "device-free", "smartphone", "social media",
        "work", "workplace", "office", "career", "job", "productivity",
        "student", "students", "school", "teacher", "teachers", "education",
        "learning", "parents", "family", "friendship", "communication",
        "young people", "graduates", "ai", "artificial intelligence"
    ]
    if ("science and environment" in source_low or "smithsonian science" in source_low):
        if not topic_any(low, friendly_words):
            return True, "硬科学/环境来源且缺少生活化切口"

    if not topic_any(low, friendly_words):
        return True, "缺少生活/学习/工作/心理/习惯/教育等小红书友好切口"

    return False, ""


def xhs_topic_score(text, source, cfg):
    """
    V33：
    选题加分：让生活化、学习化、工作化、心理习惯类题材排到前面。
    """
    if not cfg.get("xhs_topic_mode", True):
        return 0

    low = text.lower()
    score = 0

    high_value = [
        "screen-free", "device-free", "smartphone", "social media",
        "journaling", "writing", "reading", "sleep", "focus", "attention",
        "habit", "routine", "stress", "confidence", "workplace", "office",
        "career", "productivity", "students", "school", "teachers",
        "learning", "education", "parents", "family", "young people",
        "graduates", "ai", "artificial intelligence", "ai at work", "ai education"
    ]
    for k in high_value:
        if topic_has(low, k):
            score += 22

    if re.search(r"\bwhy\b|\bhow\b|\bwhat\b|\bthe way\b|\breason\b|\bchange\b|\bincrease\b|\bimprove\b", low):
        score += 30

    if re.search(r"\bdaily\b|\beveryday\b|\bhome\b|\bwork\b|\bschool\b|\blearn\b|\bhabit\b|\bstress\b", low):
        score += 25

    hardish = ["electricity", "energy", "power grid", "carbon", "emissions", "net zero", "policy", "government", "market", "clinical", "medical", "physics", "engineering"]
    for k in hardish:
        if topic_has(low, k):
            score -= 35

    source_low = source.lower()
    if "life and style" in source_low:
        score += 35
    if "education" in source_low:
        score += 30
    if "science and environment" in source_low:
        score -= 45
    if "smithsonian science" in source_low:
        score -= 35

    return score


def score_article(item, cfg):
    text = f"{item['source']} {item['title']} {item['summary']}"
    low = text.lower()
    source = item["source"].lower()
    score = 0

    # 基础关键词
    for k in cfg.get("preferred_article_keywords", []):
        if k.lower() in low:
            score += 5

    # V15：来源池质量加权。优先知名且适合学习的栏目。
    score += source_quality_score(item.get("source", ""), cfg)

    # 保留少量通用加权，但不再给 Food/Travel 默认加分。
    if "education" in source:
        score += 10
    if "life" in source:
        score += 8
    if "technology" in source:
        score += 2
    if "food" in source or "travel" in source or "entertainment" in source:
        score -= 25

    score += clean_daily_topic_score(text, item.get("source", ""), cfg)
    score += ai_topic_score(text, cfg)

    # V27.3：AI 文章一周最多 1-2 次。达到上限后不硬禁，但大幅降权。
    max_ai = int(cfg.get("max_ai_articles_per_7_days", 2))
    if has_ai_topic(text, cfg) and int(cfg.get("_recent_ai_count_7d", 0)) >= max_ai:
        score -= int(cfg.get("ai_weekly_cap_penalty", 220))

    score += recency_score(item, cfg)
    score += theme_quality_score(item, cfg)

    # V17: topic-page AI candidates should not be buried below generic RSS.
    if "mit news artificial intelligence" in source:
        score += 100
    if "mit news machine learning" in source:
        score += 80

    # V15.1：正向日常主题命中越多，加分越高。
    positive_topics = [x.lower() for x in cfg.get("positive_daily_topics", [])]
    positive_hits = sum(1 for x in positive_topics if x in low)
    score += min(positive_hits, 5) * 12

    negative_bad, _ = heavy_negative_reject_reason(text, cfg)
    if negative_bad:
        score -= 150

    # 解释型、趋势型标题加分
    if re.search(r"\bwhy\b|\bhow\b|\bwhat\b", low):
        score += 10
    if re.search(r"\bhabit\b|\btrend\b|\bchange\b|\blearning\b|\bwork\b|\bhealth\b", low):
        score += 10

    # 专有名词太多，通常不好复述/不好学
    pn_bad, _ = too_many_proper_nouns(f"{item['title']} {item['summary']}")
    if pn_bad:
        score -= 30

    bad, _reason = final_clean_topic_reject_reason(text, item.get("source", ""), cfg)
    if bad:
        score -= 120

    xhs_bad, _xhs_reason = xhs_topic_reject_reason(text, item.get("source", ""), cfg)
    if xhs_bad:
        score -= 260

    return score

def fetch_article_paragraphs(url, max_count=16):
    paragraphs = []
    try:
        print("抓正文：", url)
        r = requests.get(url, headers=HEADERS, timeout=25)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        for tag in soup(["script", "style", "noscript", "svg", "footer", "nav", "header", "aside"]):
            tag.decompose()

        seen = set()
        for p in soup.find_all("p"):
            text = clean_text(p.get_text(" "))
            if not text:
                continue

            low = text.lower()
            ui_bad = [
                "sign up", "newsletter", "privacy", "cookies", "advertisement",
                "all rights reserved", "share this", "read more", "related topics",
                "follow us", "video caption", "skip to", "live updates",
                "support the guardian", "this article is more than"
            ]
            if any(x in low for x in ui_bad):
                continue

            wc = word_count(text)
            if wc < 25 or wc > 190:
                continue

            key = text.lower()
            if key not in seen:
                paragraphs.append({
                    "index": len(paragraphs) + 1,
                    "text": text
                })
                seen.add(key)

            if len(paragraphs) >= max_count:
                break

    except Exception as e:
        print("正文抓取失败：", e)

    return paragraphs



def difficulty_profile(text):
    """
    粗略判断段落是否达到 CET4+：
    - 排除太简单、太口水的段落；
    - 保留真实外刊句式；
    - 同时避免专业硬新闻难文。
    """
    wc = word_count(text)
    avg = avg_sentence_words(text)
    words = re.findall(r"\b[a-zA-Z][a-zA-Z'-]{4,}\b", text)

    long_words = []
    learning_value_words = []
    for w in words:
        lw = w.lower().strip("'")
        if lw in STOPWORDS or lw in BANNED_VOCAB_WORDS:
            continue
        if len(lw) >= 8:
            long_words.append(lw)
        if len(lw) >= 7 or re.search(r"(tion|sion|ment|ive|al|ous|able|ible|less|ful|ing|ed)$", lw):
            learning_value_words.append(lw)

    expression_count = len(expression_hits(text))

    return {
        "word_count": wc,
        "avg_sentence_words": avg,
        "long_word_count": len(set(long_words)),
        "learning_value_word_count": len(set(learning_value_words)),
        "expression_count": expression_count,
    }


def difficulty_pass_reason(text, cfg):
    if not cfg.get("difficulty_reject_too_easy", True):
        return True, "未开启难度筛选"

    prof = difficulty_profile(text)

    min_avg = float(cfg.get("difficulty_min_avg_sentence_words", 14))
    max_avg = float(cfg.get("difficulty_max_avg_sentence_words", 34))
    min_long = int(cfg.get("difficulty_min_long_words_per_paragraph", 4))
    min_value = int(cfg.get("difficulty_min_learning_value_words", 5))
    min_expr = int(cfg.get("difficulty_min_expression_hits", 1))

    if prof["avg_sentence_words"] < min_avg:
        return False, f"难度偏低：平均句长 {prof['avg_sentence_words']:.1f}，低于 {min_avg}"

    if prof["avg_sentence_words"] > max_avg:
        return False, f"句子过长：平均句长 {prof['avg_sentence_words']:.1f}，高于 {max_avg}"

    if prof["long_word_count"] < min_long:
        return False, f"难度偏低：8字母以上学习词不足 {prof['long_word_count']}/{min_long}"

    if prof["learning_value_word_count"] < min_value:
        return False, f"学习价值词不足 {prof['learning_value_word_count']}/{min_value}"

    if prof["expression_count"] < min_expr:
        return False, f"表达型短语不足 {prof['expression_count']}/{min_expr}"

    return True, "CET4+ 难度通过"


def paragraph_pass_reason(paragraph, cfg):
    text = paragraph["text"]
    wc = word_count(text)

    if wc < int(cfg["min_words_per_paragraph"]):
        return False, f"太短：{wc}词"
    if wc > int(cfg["max_words_per_paragraph"]):
        return False, f"太长：{wc}词"

    hard_bad, reason = contains_any(text, cfg["hard_avoid"])
    if hard_bad:
        return False, f"硬排除词：{reason}"

    soft_bad, reason = contains_any(text, cfg["soft_avoid"])
    if soft_bad:
        return False, f"软排除词：{reason}"

    pn_bad, reason = too_many_proper_nouns(text)
    if pn_bad:
        return False, reason

    speak_bad, reason = hard_to_speak(text)
    if speak_bad:
        return False, reason

    diff_ok, diff_reason = difficulty_pass_reason(text, cfg)
    if not diff_ok:
        return False, diff_reason

    sc = sentence_count(text)
    if sc < 2:
        return False, f"句子太少：{sc}"
    if sc > 7:
        return False, f"句子太多：{sc}"

    numbers = re.findall(r"\b\d+[\d,.%]*\b", text)
    if len(numbers) >= 5:
        return False, f"数字过多：{len(numbers)}"

    quote_count = text.count('"') + text.count("“") + text.count("”")
    if quote_count >= 6:
        return False, f"引号过多：{quote_count}"

    return True, "通过"


def sentence_containing(text, pos):
    sentences = split_sentences(text)
    running = 0
    for s in sentences:
        idx = text.find(s, running)
        if idx == -1:
            idx = text.find(s)
        if idx != -1 and idx <= pos <= idx + len(s):
            return s
        running = max(running, idx + len(s)) if idx != -1 else running
    return ""


def short_example_from_sentence(sentence, max_len=180):
    sentence = clean_text(sentence)
    if len(sentence) <= max_len:
        return sentence
    return sentence[:max_len].rsplit(" ", 1)[0] + " ..."


def expression_hits(text):
    """
    只返回真正从原文中命中的句式。
    不再用固定复述模板强行凑数量。
    """
    hits = []
    seen = set()
    low = text.lower()

    for name, pattern, meaning, _example in EXPRESSION_RULES:
        m = re.search(pattern, low, flags=re.I)
        if not m:
            continue

        key = name.lower()
        if key in seen:
            continue

        sentence = sentence_containing(text, m.start()) or text
        example = short_example_from_sentence(sentence)
        hits.append((name, meaning, example, "原文命中"))
        seen.add(key)

    # 再补充一些从原文句子中直接抽出来的高价值结构，避免只命中普通短语。
    for s in split_sentences(text):
        sl = s.lower()

        dynamic = []
        if "if " in sl and " we " in sl and " need " in sl:
            dynamic.append(("If we want ..., we need ...", "如果我们想……，就需要……；表达条件和行动。"))
        if "not " in sl and " but " in sl:
            dynamic.append(("not ..., but ...", "不是……而是……；表达转折或纠正。"))
        if "more " in sl and " than " in sl:
            dynamic.append(("more ... than ...", "比……更……；表达比较。"))
        if "from " in sl and " to " in sl:
            dynamic.append(("from ... to ...", "从……到……；表达范围或变化。"))
        if "the idea that" in sl:
            dynamic.append(("the idea that ...", "……这种想法；引出观点。"))
        if "it is possible" in sl or "it's possible" in sl:
            dynamic.append(("it is possible to ...", "有可能……；表达可能性。"))

        for name, meaning in dynamic:
            key = name.lower()
            if key not in seen:
                hits.append((name, meaning, short_example_from_sentence(s), "原文句子"))
                seen.add(key)

    return hits


def build_expressions(text, count):
    """
    V13.1：
    表达句式只取原文命中/原文句子中真正出现的结构。
    不再用固定 FALLBACK_EXPRESSIONS 补足数量。
    """
    found = expression_hits(text)

    # 给更有学习价值的结构排前面
    priority = [
        "no longer", "worth", "no getting away", "poll/survey", "more likely",
        "less likely", "not just", "not only", "not ..., but", "if we want",
        "when it comes to", "rather than", "instead of", "regardless of",
        "the fact that", "the way", "as a result", "because of"
    ]

    def score(item):
        name = item[0].lower()
        s = 0
        for i, p in enumerate(priority):
            if p in name:
                s += 100 - i
        if item[3] == "原文命中":
            s += 20
        return s

    found.sort(key=score, reverse=True)
    return found[:count]


def score_paragraph(paragraph, cfg):
    text = paragraph["text"]
    low = text.lower()
    score = 0

    for k in cfg["preferred_paragraph_keywords"]:
        if k.lower() in low:
            score += 5

    for _name, pattern, _meaning, _example in EXPRESSION_RULES:
        if re.search(pattern, low, flags=re.I):
            score += 14

    everyday = [
        "at home", "at work", "daily life", "everyday life", "go out",
        "spend time", "talk to", "used to", "try to", "want to", "need to",
        "find it", "make it", "feel like", "look for", "take time"
    ]
    for p in everyday:
        if p in low:
            score += 8

    stats = proper_noun_stats(text)
    score -= stats["capital_count"] * 2
    score -= stats["sequence_count"] * 5
    score -= stats["acronym_count"] * 4

    wc = word_count(text)
    score -= abs(wc - 95) // 6

    avg = avg_sentence_words(text)
    if 14 <= avg <= 30:
        score += 14
    elif 12 <= avg < 14:
        score += 2
    else:
        score -= 8

    prof = difficulty_profile(text)
    score += min(prof["long_word_count"], 10) * 2
    score += min(prof["learning_value_word_count"], 12)
    score += prof["expression_count"] * 6

    return score



def select_paragraphs_for_theme_seed(paragraphs, cfg):
    """
    V18.3：
    主题种子文章已经经过方向筛选，不再用旧的泛筛选规则误伤。
    只保留基础可读性要求。
    """
    passed = []
    rejected = []

    for p in paragraphs:
        text = p.get("text", "") if isinstance(p, dict) else str(p)
        wc = word_count(text)
        sc = sentence_count(text)

        if wc < 35:
            rejected.append({"index": p.get("index", 0) if isinstance(p, dict) else 0, "reason": f"太短：{wc}词", "preview": text[:160]})
            continue
        if wc > 230:
            rejected.append({"index": p.get("index", 0) if isinstance(p, dict) else 0, "reason": f"太长：{wc}词", "preview": text[:160]})
            continue
        if sc < 1 or sc > 8:
            rejected.append({"index": p.get("index", 0) if isinstance(p, dict) else 0, "reason": f"句子数量不合适：{sc}", "preview": text[:160]})
            continue

        if is_unsafe_default_reading_topic(text)[0]:
            rejected.append({"index": p.get("index", 0) if isinstance(p, dict) else 0, "reason": "安全过滤未通过", "preview": text[:160]})
            continue
        if is_recipe_or_cooking_instructions(text):
            rejected.append({"index": p.get("index", 0) if isinstance(p, dict) else 0, "reason": "菜谱内容", "preview": text[:160]})
            continue

        numbers = re.findall(r"\b\d+[\d,.%]*\b", text)
        if len(numbers) >= 8:
            rejected.append({"index": p.get("index", 0) if isinstance(p, dict) else 0, "reason": f"数字过多：{len(numbers)}", "preview": text[:160]})
            continue

        item = dict(p) if isinstance(p, dict) else {"text": text, "index": len(passed)}
        item["score"] = score_paragraph(item, cfg)
        passed.append(item)

    if len(passed) < int(cfg.get("theme_seed_min_required_paragraphs", 2)):
        # 再兜底一次：只要长度适中且安全，就取前几段。
        fallback = []
        for p in paragraphs:
            text = p.get("text", "") if isinstance(p, dict) else str(p)
            wc = word_count(text)
            if 30 <= wc <= 240 and not is_unsafe_default_reading_topic(text)[0] and not is_recipe_or_cooking_instructions(text):
                item = dict(p) if isinstance(p, dict) else {"text": text, "index": len(fallback)}
                item["score"] = score_paragraph(item, cfg)
                fallback.append(item)
            if len(fallback) >= int(cfg.get("theme_seed_selected_paragraph_count", 3)):
                break
        if len(fallback) >= int(cfg.get("theme_seed_min_required_paragraphs", 2)):
            return fallback[: int(cfg.get("theme_seed_selected_paragraph_count", 3))], rejected

    passed.sort(key=lambda x: x["score"], reverse=True)
    selected = passed[: int(cfg.get("theme_seed_selected_paragraph_count", 3))]
    selected.sort(key=lambda x: x.get("index", 0))
    return selected, rejected


def select_paragraphs_from_one_article(paragraphs, cfg):
    passed = []
    rejected = []

    for p in paragraphs:
        ok, reason = paragraph_pass_reason(p, cfg)
        if not ok:
            rejected.append({"index": p["index"], "reason": reason, "preview": p["text"][:160]})
            continue
        p = dict(p)
        p["score"] = score_paragraph(p, cfg)
        passed.append(p)

    if len(passed) < int(cfg["min_required_paragraphs"]):
        return [], rejected

    passed.sort(key=lambda x: x["score"], reverse=True)
    selected = passed[: int(cfg["selected_paragraph_count"])]
    selected.sort(key=lambda x: x["index"])
    return selected, rejected


def translate_text(text: str, enabled=True) -> str:
    text = clean_text(text)
    if not text:
        return ""
    if not enabled:
        return "【未开启翻译】"
    try:
        url = "https://translate.googleapis.com/translate_a/single"
        params = {"client": "gtx", "sl": "en", "tl": "zh-CN", "dt": "t", "q": text}
        r = requests.get(url, params=params, headers=HEADERS, timeout=25)
        r.raise_for_status()
        data = r.json()
        return "".join(seg[0] for seg in data[0] if seg and seg[0]).strip()
    except Exception:
        return "【翻译失败：可能是网络或翻译服务暂时不可用。】"


def build_expressions(text, count):
    found = expression_hits(text)
    used = {x[0] for x in found}

    for exp, meaning, example in FALLBACK_EXPRESSIONS:
        if len(found) >= count:
            break
        if exp not in used:
            found.append((exp, meaning, example, "复述补充"))
            used.add(exp)

    return found[:count]



LOCAL_VOCAB_ZH = {
    "associate professor": "副教授",
    "lecture hall": "阶梯教室；大讲堂",
    "shoots up": "迅速举起；突然升起",
    "shoot up": "迅速举起；突然升起",
    "hand shoots up": "手迅速举起来",
    "every hand shoots up": "几乎每个人都举手",
    "same question": "同样的问题",
    "getting enough protein": "摄入足够的蛋白质",
    "protein": "蛋白质",
    "nutrition": "营养学；营养",
    "essential for": "对……必不可少",
    "building and repairing tissues": "构建和修复组织",
    "repairing tissues": "修复组织",
    "making enzymes": "制造酶",
    "enzymes": "酶",
    "hormones": "激素",
    "disease-fighting antibodies": "抗病抗体",
    "antibodies": "抗体",
    "is made up of": "由……组成",
    "made up of": "由……组成",
    "smaller units": "更小的单位",
    "called amino acids": "被称为氨基酸",
    "amino acids": "氨基酸",
    "human body": "人体",
    "obtained from food": "从食物中获得",
    "obtained": "获得",
    "function": "运转；发挥功能",
    "absolute bliss": "极大的享受；非常舒服/幸福",
    "long trudge down": "漫长而费力地走下来",
    "traditional room": "传统风格的房间",
    "ancient room": "古老的房间",
    "had to be rebuilt": "不得不重建",
    "buildings were damaged": "建筑物受损",
    "had been changing": "一直在变化",
    "take the chance to": "趁机做某事",
    "move things on": "把事情往前推进；做出改变",
    "more and more": "越来越……",
    "not just": "不只是……",
    "not only": "不只是……",
    "instead of": "而不是……",
    "rather than": "而不是……",
    "the way": "……的方式",
    "make it easier": "让它更容易",
    "find it hard": "觉得……很难",
    "find it difficult": "觉得……很难",
    "one reason": "一个原因",
    "because of": "因为……",
    "for many people": "对很多人来说",
    "as a result": "结果是",
    "used to": "过去常常",
    "try to": "试着……",
    "want to": "想要……",
    "need to": "需要……",
    "appearance": "外观；表象",
    "deceptive": "有迷惑性的；看起来和实际不一样",
    "traditional": "传统的",
    "absolute": "绝对的；完全的",
    "buildings": "建筑物",
    "earthquake": "地震",
    "changing": "变化中的；正在改变",
    "damaged": "受损的",
    "rebuilt": "重建",
    "memorable": "难忘的",
    "ordinary": "普通的；平常的",
    "practical": "实用的",
    "comfortable": "舒服的",
    "natural": "自然的",
    "experience": "经历；体验",
    "confidence": "信心",
    "routine": "日常安排；惯例",
    "pressure": "压力",
    "community": "社区；群体",
    "licensed clinical social worker": "持证临床社工；有执照的临床社会工作者",
    "clinical social worker": "临床社会工作者",
    "therapist": "治疗师；心理咨询/治疗师",
    "clinical social worker and therapist": "临床社会工作者兼治疗师",
    "putting your thoughts on paper": "把想法写到纸上",
    "thoughts on paper": "写在纸上的想法",
    "external place to land": "一个可以落脚/安放的外部空间",
    "changes our perspective": "改变我们的视角",
    "expressive writing": "表达性写作",
    "upsetting experience": "令人烦心/不安的经历",
    "the brain tends to": "大脑往往会……",
    "bury it": "把它埋起来；压下去不处理",
    "move on": "继续向前；翻篇",
    "be supposed to": "应该；按理说要……",
    "not reading the room": "没看懂场合/气氛；不合时宜",
    "beat their chests": "捶胸自夸；高调炫耀",
    "tech executives": "科技公司高管",
    "industrial revolution": "工业革命",
    "afford to": "负担得起；有能力做……",
    "pay for rent": "支付房租",
    "pro-ai graduation speakers": "支持 AI 的毕业典礼演讲者",
    "valuable lessons": "有价值的经验/教训",
    "preventable accidents": "可预防的事故",
    "popular protein sources": "常见/受欢迎的蛋白质来源",
    "device-free meetings": "无设备会议；不开电脑/手机的会议",
    "return to the office": "回到办公室上班",
    "returned to the office": "回到办公室上班",
    "increase in creativity": "创造力提升",
    "increase in productivity": "生产力提升",
    "creativity and productivity": "创造力和生产力",
    "covid-19 lockdowns": "新冠封锁期",
    "staff and himself": "员工和他自己",
    "taking something from inside yourself": "把内心的东西拿出来/表达出来",
    "giving it an external place to land": "给它一个外部安放处",
}


LOCAL_VOCAB_ZH.update({
    "large language models": "\u5927\u578b\u8bed\u8a00\u6a21\u578b\uff1bAI \u8bdd\u9898\u6838\u5fc3\u8bcd\u7ec4\u3002",
    "job cuts": "\u88c1\u5458\uff1b\u5de5\u4f5c\u5c97\u4f4d\u524a\u51cf\u3002",
    "be accompanied by": "\u4f34\u968f\u7740\u2026\u2026\uff1b\u5e38\u7528\u4e8e\u63cf\u8ff0\u67d0\u4ef6\u4e8b\u51fa\u73b0\u65f6\u540c\u65f6\u51fa\u73b0\u7684\u53cd\u5e94\u3002",
    "warnings that": "\u5173\u4e8e\u2026\u2026\u7684\u8b66\u544a\uff1bthat \u540e\u9762\u63a5\u5177\u4f53\u62c5\u5fe7\u3002",
    "tends to be accompanied by": "\u5f80\u5f80\u4f1a\u4f34\u968f\u7740\u2026\u2026\uff1b\u9002\u5408\u5199\u666e\u904d\u73b0\u8c61\u3002",
    "across many more industries": "\u904d\u53ca\u66f4\u591a\u884c\u4e1a\uff1bacross \u5f3a\u8c03\u8303\u56f4\u6269\u6563\u3002",
    "come for us all": "\u6700\u7ec8\u6ce2\u53ca\u6211\u4eec\u6240\u6709\u4eba\uff1b\u8bed\u6c14\u6bd4 affect \u66f4\u6709\u538b\u8feb\u611f\u3002",
    "be affected": "\u53d7\u5230\u5f71\u54cd\uff1b\u4e2d\u6027\u800c\u9ad8\u9891\u7684\u5916\u520a\u8868\u8fbe\u3002",
    "lose your job to": "\u628a\u5de5\u4f5c\u4e22\u7ed9 / \u88ab\u2026\u2026\u53d6\u4ee3\u800c\u5931\u4e1a\u3002",
    "a substitute for": "\u2026\u2026\u7684\u66ff\u4ee3\u54c1\u3002",
    "but rather": "\u800c\u662f\uff1b\u66f4\u51c6\u786e\u5730\u8bf4\u662f\u2026\u2026\uff0c\u6bd4 but \u66f4\u6b63\u5f0f\u3002",
    "general labor substitute": "\u901a\u7528\u52b3\u52a8\u529b\u66ff\u4ee3\u7269\uff1b\u7528\u4e8e\u8ba8\u8bba\u6280\u672f\u5bf9\u52b3\u52a8\u7684\u66ff\u4ee3\u3002",
})


LOCAL_VOCAB_ZH.update({
    "weighing a move from": "???????????????????weigh a move ???????????",
    "a move from full-time to freelance work": "??????????move ???????/????",
    "terse email": "????????terse ????????????",
    "redo a task from scratch": "?????????from scratch ?????????",
    "from scratch": "??????????",
    "settled the matter instantly": "??????????settle the matter ????/??????",
    "settle the matter": "?????????????",
    "quit on the spot": "?????on the spot ????????",
    "on the spot": "???????????",
    "justify their employment": "???????/???????justify ??????????",
    "or resign": "??????????????????",
    "turns out": "?????????",
    "particularly unusual": "????????????????????????",
    "rude email": "??/???????",
    "at least one": "?????????????",
    "has become even more central to": "?????????/???",
    "central to": "????????????????",
})

BANNED_VOCAB_WORDS = {
    "kasbah", "toubkal", "morocco", "africa", "guardian", "bbc",
    "mike", "newsletter", "advertisement", "caption", "copyright",
    "facebook", "twitter", "instagram",
    # 图文版重点词组不再展示这些过基础/过泛的单词
    "graduation", "education", "information", "conversation", "definition",
    "intention", "action", "moment", "according", "variety", "popular",
    "source", "sources", "student", "students", "speaker", "speakers",
    "school", "schools", "people", "thing", "things", "article", "articles",
    "word", "words", "reason", "reasons", "example", "examples"
}


def proper_like_words(text):
    found = set()
    for m in re.finditer(r"\b[A-Z][a-zA-Z]{2,}\b|\b[A-Z]{2,}\b", text):
        w = m.group(0)
        if w not in COMMON_CAPITAL_WORDS:
            found.add(w.lower())
    return found


GENERIC_QUANTITY_NOUNS = {
    "workers", "worker", "people", "person", "jobs", "job", "students", "student",
    "children", "child", "adults", "adult", "years", "year", "months", "month",
    "days", "day", "companies", "company", "industries", "industry", "users", "user",
    "schools", "school", "families", "family", "employees", "employee",
}

GENERIC_PHRASE_HEADS = {
    "big", "large", "small", "new", "old", "good", "bad", "many", "more", "most",
    "some", "several", "different", "important", "popular", "common", "major", "minor",
    "specific", "general", "real", "normal", "daily", "public", "private",
}

GENERIC_PHRASE_TAILS = GENERIC_QUANTITY_NOUNS | {
    "things", "thing", "ways", "way", "times", "time", "places", "place", "areas", "area",
    "issues", "issue", "problems", "problem", "questions", "question", "cases", "case",
    "reports", "report", "articles", "article", "story", "stories", "news", "data",
}

VOCAB_PRIORITY_SCORES = {
    "weighing a move from": 330,
    "a move from full-time to freelance work": 328,
    "terse email": 326,
    "redo a task from scratch": 324,
    "from scratch": 322,
    "settled the matter instantly": 320,
    "settle the matter": 318,
    "quit on the spot": 316,
    "on the spot": 314,
    "justify their employment": 312,
    "or resign": 300,
    "turns out": 298,
    "particularly unusual": 296,
    "rude email": 294,
    "at least one": 292,
    "has become even more central to": 290,
    "central to": 288,
    "job cuts": 310,
    "tends to be accompanied by": 300,
    "lose your job to": 292,
    "a substitute for": 288,
    "but rather": 284,
    "come for us all": 280,
    "general labor substitute": 276,
    "across many more industries": 270,
    "large language models": 264,
    "be accompanied by": 245,
    "warnings that": 230,
    "be affected": 220,
    "in the tech industry alone": 180,
}

DATA_WORDS = r"(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|half|hundred|thousand|million|billion|\d+(?:\.\d+)?)"

LOW_VALUE_EXACT_TERMS = {
    "employment", "communication", "communications", "technicality", "government workers",
    "has become", "have become", "become", "workers", "government",
}

LOW_VALUE_VOCAB_PATTERNS = [
    # Plain data + ordinary noun is useful in the sentence, but weak as a vocabulary card.
    re.compile(rf"^(?:more than|less than|over|under|around|about|nearly|almost)?\s*(?:a\s+)?(?:{DATA_WORDS}\s+){{1,3}}(?:" + "|".join(sorted(GENERIC_QUANTITY_NOUNS)) + r")$", re.I),
    re.compile(r"^(?:in|on|at|by|for|with|from|to)\s+(?:the\s+)?(?:world|country|city|year|month|week|day)$", re.I),
]


def is_low_value_vocab_phrase(low):
    if low in LOW_VALUE_EXACT_TERMS:
        return True
    words = re.findall(r"\b[a-zA-Z][a-zA-Z'-]*\b", low)
    if not words:
        return True

    if len(words) >= 2:
        for pat in LOW_VALUE_VOCAB_PATTERNS:
            if pat.match(low):
                return True

    if len(words) == 2 and words[0] in GENERIC_PHRASE_HEADS and words[1] in GENERIC_PHRASE_TAILS:
        return True

    if len(words) == 2 and words[0] in STOPWORDS and words[1] in GENERIC_PHRASE_TAILS:
        return True

    # Keep strong collocations even if they contain a generic noun.
    strong_markers = {
        "job cuts", "labor substitute", "language model", "large language models",
        "be accompanied by", "come for", "lose your job", "a substitute for",
        "rather than", "but rather", "tends to", "across many more industries",
    }
    if any(marker in low for marker in strong_markers):
        return False

    return False


def is_bad_vocab_term(term, text):
    term = clean_text(term).strip()
    if not term:
        return True

    low = term.lower().strip(" ,.;:!?\"'\u201c\u201d\u2018\u2019()[]{}")
    if not low:
        return True
    if low in BANNED_VOCAB_WORDS:
        return True
    if is_low_value_vocab_phrase(low):
        return True

    proper_words = proper_like_words(text)
    for w in re.findall(r"\b[a-zA-Z][a-zA-Z'-]*\b", low):
        lw = w.lower().strip("'")
        if lw in BANNED_VOCAB_WORDS:
            return True
        if lw in proper_words:
            return True

    if " " not in low:
        if len(low) < 6:
            return True
        if low in STOPWORDS:
            return True

    return False


def add_vocab_candidate(cands, term, score, text):
    term = clean_text(term).strip(" ,.;:!?\"'\u201c\u201d\u2018\u2019()[]{}")
    if is_bad_vocab_term(term, text):
        return
    key = term.lower()
    score = max(score, VOCAB_PRIORITY_SCORES.get(key, 0))
    if key not in cands or score > cands[key]["score"]:
        cands[key] = {"term": term, "score": score}


def extract_key_words(text, count=8):
    """
    V15 搭配优先：
    - 优先抓原文里有学习价值的搭配/动词短语/固定表达
    - 过滤掉CET4以下的基础单词
    - 尽量不出现playing/watching/reading这类基础词
    """
    cands = {}
    low = text.lower()

    # 第一优先级：高价值固定搭配（直接从原文匹配）
    high_value_phrases = [
        # 外刊高频表达搭配
        "weighing a move from", "a move from full-time to freelance work",
        "terse email", "redo a task from scratch", "from scratch",
        "settled the matter instantly", "settle the matter", "quit on the spot",
        "on the spot", "justify their employment", "or resign", "turns out",
        "particularly unusual", "rude email", "at least one",
        "has become even more central to", "central to",
        "be accompanied by", "warnings that", "tends to be accompanied by",
        "job cuts", "lose your job to", "a substitute for", "but rather",
        "come for us all", "across many more industries", "general labor substitute",
        "large language models", "in the tech industry alone", "be affected",
        "instead of", "rather than", "carry it on", "set off",
        "a flood of", "more and more", "not just", "not only",
        "as a result", "in the long run", "over time", "on top of",
        "when it comes to", "used to",
        "make it easier", "find it hard", "find it difficult",
        "one reason", "because of", "for many people", "no longer",
        "more likely to", "less likely to", "the fact that",
        "carry on", "set aside", "take part in", "give up",
        "point out", "find out", "turn out", "come up with",
        "be associated with", "be linked to", "be connected to",
        "according to", "compared with", "compared to",
        "in addition to", "as well as", "such as",
        "be aware of", "be proud of", "be capable of",
        "take advantage of", "make use of", "make sense of",
        "at the same time", "on the other hand", "in other words",
        "to some extent", "in some ways", "in many ways",
        "more and more", "fewer and fewer", "less and less",
        # 本轮特殊词组
        "associate professor", "lecture hall", "essential for",
        "made up of", "obtained from", "amino acids",
        "screen-free", "carry it on into", "instead of watching",
        "instead of screen time", "a flood of photos",
        "licensed clinical social worker", "clinical social worker",
        "clinical social worker and therapist", "therapist",
        "putting your thoughts on paper", "thoughts on paper",
        "external place to land", "changes our perspective",
        "expressive writing", "upsetting experience",
        "the brain tends to", "bury it", "move on",
        "be supposed to", "not reading the room", "beat their chests",
        "tech executives", "industrial revolution", "afford to", "pay for rent",
        "pro-AI graduation speakers", "valuable lessons", "preventable accidents",
        "popular protein sources",
        "device-free meetings", "return to the office", "returned to the office",
        "increase in creativity", "increase in productivity",
        "creativity and productivity", "covid-19 lockdowns",
        "taking something from inside yourself", "giving it an external place to land",
    ]

    for phrase in high_value_phrases:
        if re.search(r"\b" + re.escape(phrase) + r"\b", low, flags=re.I):
            add_vocab_candidate(cands, phrase, 200, text)

    # 第二优先级：动态匹配搭配模式
    collocation_patterns = [
        r"\bweighing\s+a\s+move\s+from\s+[^,.;]+?\s+to\s+[^,.;]+",
        r"\ba\s+move\s+from\s+full-time\s+to\s+freelance\s+work\b",
        r"\bterse\s+email\b",
        r"\bredo\s+a\s+task\s+from\s+scratch\b",
        r"\bfrom\s+scratch\b",
        r"\bsettled\s+the\s+matter\s+instantly\b",
        r"\bsettle[sd]?\s+the\s+matter\b",
        r"\bquit\s+on\s+the\s+spot\b",
        r"\bon\s+the\s+spot\b",
        r"\bjustify\s+(?:their|his|her|your|our)\s+employment\b",
        r"\bor\s+resign\b",
        r"\bturns\s+out\b",
        r"\bparticularly\s+unusual\b",
        r"\brude\s+emails?\b",
        r"\bat\s+least\s+one\b",
        r"\bhas\s+become\s+even\s+more\s+central\s+to\b",
        r"\bcentral\s+to\b",
        r"\btends\s+to\s+be\s+accompanied\s+by\b",
        r"\bbe\s+accompanied\s+by\b",
        r"\bjob\s+cuts\b",
        r"\bacross\s+many\s+more\s+industries\b",
        r"\bcome\s+for\s+us\s+all\b",
        r"\b(?:will|would|can|could)?\s*be\s+affected\b",
        r"\blose\s+(?:your|their|our|one's|his|her)\s+job\s+to\b",
        r"\ba\s+substitute\s+for\b",
        r"\bbut\s+rather\b",
        r"\bgeneral\s+labor\s+substitute\b",
        r"\blarge\s+language\s+models?\b",
        r"\b(?:associate|assistant|senior|junior|clinical|visiting)\s+professor\b",
        r"\blicensed\s+clinical\s+social\s+worker\b",
        r"\bclinical\s+social\s+worker\b",
        r"\bclinical\s+social\s+worker\s+and\s+therapist\b",
        r"\bputting\s+(?:your|their|our)\s+thoughts\s+on\s+paper\b",
        r"\bthoughts\s+on\s+paper\b",
        r"\bexternal\s+place\s+to\s+land\b",
        r"\bchanges?\s+(?:our|your|their)\s+perspective\b",
        r"\bexpressive\s+writing\b",
        r"\bupsetting\s+experience\b",
        r"\bthe\s+brain\s+tends\s+to\b",
        r"\bbury\s+it\b",
        r"\bmove\s+on\b",
        r"\bbe\s+supposed\s+to\b",
        r"\b(?:not|n't)\s+reading\s+the\s+room\b",
        r"\bbeating\s+(?:their|his|her|our)\s+chests\b",
        r"\btech\s+executives\b",
        r"\bindustrial\s+revolution\b",
        r"\b(?:can|can't|cannot|could|couldn't|afford)\s+afford\s+to\b",
        r"\bpay\s+for\s+rent\b",
        r"\bpro[- ]?ai\s+graduation\s+speakers\b",
        r"\b[a-zA-Z-]+\s+(?:lessons|accidents|sources|skills|habits|tools|speakers|executives)\b",
        r"\b(?:instead|rather)\s+of\s+[a-z]+ing\b",
        r"\b(?:carry|bring|keep)\s+it\s+on\b",
        r"\b(?:set|touch|spark)\s+off\b",
        r"\ba\s+flood\s+of\b",
        r"\b(?:amino|fatty)\s+acids\b",
        r"\b[a-zA-Z]+-free\s+[a-zA-Z]+\b",
        r"\b(?:is|are|was|were|be)\s+made\s+up\s+of\b",
        r"\b(?:obtained|made|built)\s+from\s+[a-zA-Z]{4,}\b",
        r"\b(?:had|has|have)\s+been\s+[a-zA-Z]+ing\b",
        r"\b(?:more|less)\s+likely\s+to\b",
        r"\bno\s+longer\b",
        r"\bthe\s+fact\s+that\b",
        r"\bwhen\s+it\s+comes\s+to\b",
        r"\bin\s+the\s+long\s+run\b",
        r"\bon\s+the\s+other\s+hand\b",
        r"\bto\s+some\s+extent\b",
        r"\bin\s+(?:many|some|other)\s+ways\b",
    ]

    for pat in collocation_patterns:
        for m in re.finditer(pat, text, flags=re.I):
            add_vocab_candidate(cands, m.group(0), 150, text)

    # V33：自动补充“像词组”的表达，避免严格过滤后只剩 1 个词组。
    auto_phrase_patterns = [
        r"\b[a-zA-Z]+-free\s+[a-zA-Z]{4,}s?\b",
        r"\b(?:return|returned|returning)\s+to\s+the\s+office\b",
        r"\ban?\s+increase\s+in\s+[a-zA-Z]{6,}(?:\s+and\s+[a-zA-Z]{6,})?\b",
        r"\b[a-zA-Z]{6,}\s+and\s+[a-zA-Z]{6,}\b",
        r"\b(?:valuable|practical|accessible|affordable|preventable|creative|productive|device-free|screen-free)\s+[a-zA-Z]{4,}s?\b",
        r"\b[a-zA-Z]{5,}\s+(?:meetings|lockdowns|productivity|creativity|education|technology|workplace|habits|skills|lessons|speakers|accidents|sources|students|parents|children|schools|office)\b",
        r"\b(?:taking|giving|putting|turning|bringing|moving)\s+(?:something|it|this|that)\s+(?:from|into|to|on)\s+[^.?!,;]{5,55}",
    ]
    for pat in auto_phrase_patterns:
        for m in re.finditer(pat, text, flags=re.I):
            term = clean_text(m.group(0))
            # 去掉太长、太口水的自动片段
            if 2 <= word_count(term) <= 7 and len(term) <= 80:
                add_vocab_candidate(cands, term, 95, text)

    # 第三优先级：有价值的单词（不包含CET4基础词）
    basic_words_to_exclude = {
        "playing", "watching", "reading", "doing", "going", "getting",
        "showing", "started", "joined", "having", "making", "taking",
        "coming", "looking", "saying", "thinking", "feeling", "working",
        "evening", "morning", "school", "parents", "children", "people",
        "because", "instead", "rather", "after", "before", "during",
        "through", "between", "without", "within", "across", "around",
        "graduation", "education", "information", "conversation", "definition",
        "intention", "action", "moment", "preventable", "popular", "source", "sources",
        "employment", "communication", "communications", "technicality", "government", "workers",
    }

    quality_words = {
        "fluorescent", "intersection", "configuration", "mechanism",
        "infrastructure", "sustainability", "accountability", "transparency",
        "entrepreneur", "innovation", "transformation", "initiative",
        "fundamental", "significant", "substantial", "controversial",
        "phenomenon", "perspective", "consequence", "circumstances",
        "commitment", "collaboration", "competition", "contradiction",
        "sophisticated", "vulnerable", "extraordinary", "magnificent",
    }

    for w in re.findall(r"\b[a-zA-Z][a-zA-Z'-]{5,}\b", text):
        lw = w.lower().strip("'")
        if lw.endswith("'s"):
            lw = lw[:-2]
        if lw in basic_words_to_exclude:
            continue
        if lw in quality_words:
            add_vocab_candidate(cands, lw, 80, text)
        elif re.search(r"(tion|sion|ment|ance|ence|ity|ive|ous|ful|less|able|ible)$", lw):
            add_vocab_candidate(cands, lw, 40, text)

    ranked_raw = sorted(cands.values(), key=lambda x: (x["score"], " " in x["term"] or "-" in x["term"], len(x["term"])), reverse=True)

    # V33：词组优先，但保证数量。先选真正词组；不够时再补较有学习价值的单词。
    ranked = []
    for item in ranked_raw:
        t = item["term"].lower()
        is_phrase = (" " in t) or ("-" in t)
        if is_phrase or item.get("score", 0) >= 80:
            ranked.append(item)

    def can_add(selected_items, item):
        term = item["term"]
        low_term = term.lower()
        low_words = low_term.split()
        for chosen in list(selected_items):
            chosen_low = chosen["term"].lower()
            chosen_words = chosen_low.split()
            if low_term == chosen_low:
                return False
            # If a stronger long phrase is already selected, skip its shorter fragment.
            if low_term in chosen_low and len(low_words) < len(chosen_words):
                return False
            # If the new phrase is a clearer full version, replace the shorter fragment.
            if chosen_low in low_term and len(chosen_words) < len(low_words):
                selected_items.remove(chosen)
                return True
            if low_term in chosen_low and " " not in low_term:
                return False
            if chosen_low in low_term and " " not in chosen_low and len(low_words) <= 3:
                selected_items.remove(chosen)
                return True
        return True

    selected = []
    for item in ranked:
        if not can_add(selected, item):
            continue
        selected.append(item)
        if len(selected) >= count:
            break

    # 如果严格词组不足，补充中等价值候选，避免页面只有 1 个词。
    min_needed = min(count, 6)
    if len(selected) < min_needed:
        for item in ranked_raw:
            t = item["term"].lower()
            is_phrase = (" " in t) or ("-" in t)
            # 兜底仍然不收太短、太基础的单词。
            if not is_phrase and (len(t) < 8 or t in basic_words_to_exclude or t in BANNED_VOCAB_WORDS):
                continue
            if not can_add(selected, item):
                continue
            selected.append(item)
            if len(selected) >= min_needed:
                break

    def first_position(term):
        m = re.search(r"\b(" + re.escape(term) + r")\b", text, flags=re.I)
        return m.start() if m else 10**9

    selected.sort(key=lambda x: first_position(x["term"]))
    return [x["term"] for x in selected]

def explain_keyword(term):
    low = term.lower().strip()
    if low in LOCAL_VOCAB_ZH:
        return LOCAL_VOCAB_ZH[low]
    zh = translate_text(term, True)
    if not zh or zh.startswith("【翻译失败"):
        return "可理解为：" + term
    return zh.replace("\n", " ").strip()


def format_keywords_txt(keywords):
    if not keywords:
        return ["无"]
    pairs = [f"{k}：{explain_keyword(k)}" for k in keywords]
    lines = []
    # 每行最多 4 个，避免太长；整体比原来一词一块紧凑很多。
    for i in range(0, len(pairs), 4):
        lines.append(" ｜ ".join(pairs[i:i+4]))
    return lines


def format_keywords_html(keywords):
    if not keywords:
        return "<p>无</p>"
    rows = []
    for k in keywords:
        rows.append(
            "<span class='vocab-chip'>"
            f"<strong>{esc(k)}</strong>：{esc(explain_keyword(k))}"
            "</span>"
        )
    return "".join(rows)


def collect_highlight_terms(text, expressions, keywords):
    terms = []
    for k in keywords:
        if not is_bad_vocab_term(k, text) and k.lower() in text.lower():
            terms.append(k)

    seen = set()
    result = []
    for t in sorted(terms, key=len, reverse=True):
        lt = t.lower()
        if lt not in seen:
            result.append(t)
            seen.add(lt)
    return result[:12]


def mark_text_for_txt(text, terms):
    marked = text
    for term in terms:
        pattern = re.compile(r"\b(" + re.escape(term) + r")\b", flags=re.I)
        marked = pattern.sub(r"【\1】", marked, count=1)
    return marked


def mark_text_for_html(text, terms):
    marked = esc(text)
    for term in terms:
        pattern = re.compile(r"\b(" + re.escape(esc(term)) + r")\b", flags=re.I)
        marked = pattern.sub(r"<mark>\1</mark>", marked, count=1)
    return marked


def normalize_click_word(word):
    w = clean_text(word).lower().strip(".,;:!?\"'“”‘’()[]{}")
    w = re.sub(r"\s+", " ", w)
    if w.endswith("'s"):
        w = w[:-2]
    return w


def normalize_click_term(term):
    t = clean_text(term).lower().strip(".,;:!?\"'“”‘’()[]{}")
    t = re.sub(r"\s+", " ", t)
    return t


def is_click_translatable_word(word, full_text, min_len=4):
    w = normalize_click_word(word)
    if len(w) < min_len:
        return False
    if w in STOPWORDS:
        return False
    if w in BANNED_VOCAB_WORDS:
        return False
    if not re.fullmatch(r"[a-z][a-z'-]*", w):
        return False

    if w in proper_like_words(full_text):
        return False

    very_basic = {
        "make", "made", "does", "doing", "done", "like", "come", "came", "take",
        "took", "good", "back", "look", "looked", "time", "year", "years",
        "day", "days", "home", "work", "life", "live", "went", "going",
        "much", "even", "still", "only", "first", "last", "long", "same"
    }
    if w in very_basic:
        return False

    return True


def is_click_translatable_phrase(term, full_text):
    t = normalize_click_term(term)
    if not t or " " not in t:
        return False
    if is_bad_vocab_term(t, full_text):
        return False
    words = re.findall(r"\b[a-zA-Z][a-zA-Z'-]*\b", t)
    if len(words) < 2:
        return False
    return True


def load_vocab_cache():
    cache_path = ROOT / "vocab_cache.json"
    if not cache_path.exists():
        return {}
    try:
        return json.loads(cache_path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


def save_vocab_cache(cache):
    cache_path = ROOT / "vocab_cache.json"
    try:
        cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8-sig")
    except Exception:
        pass


def explain_click_term(term):
    t = normalize_click_term(term)
    if not t:
        return ""
    if t in LOCAL_VOCAB_ZH:
        return LOCAL_VOCAB_ZH[t]

    cache = load_vocab_cache()
    if t in cache and cache[t]:
        return cache[t]

    zh = translate_text(t, True)
    if not zh or zh.startswith("【翻译失败"):
        zh = "暂无中文解释，可结合上下文理解。"

    zh = zh.replace("\n", " ").strip()
    cache[t] = zh
    save_vocab_cache(cache)
    return zh


def explain_click_word(word):
    return explain_click_term(word)


def build_click_word_map(paragraph_texts, keywords, cfg):
    """
    V14.1：
    点击翻译优先支持词组，其次才是单词。
    """
    if not cfg.get("click_translate_enabled", True):
        return {}

    full_text = " ".join(paragraph_texts)
    min_len = int(cfg.get("click_translate_min_word_len", 4))
    max_words = int(cfg.get("click_translate_max_words", 90))

    ordered_terms = []

    # 重点词/词组优先作为整体加入，不拆散。
    for k in keywords:
        nk = normalize_click_term(k)
        if is_click_translatable_phrase(nk, full_text) and nk not in ordered_terms:
            ordered_terms.append(nk)

    # 本地词库里如果有短语在原文出现，也加入。
    for phrase in sorted([x for x in LOCAL_VOCAB_ZH if " " in x], key=len, reverse=True):
        if re.search(r"\b" + re.escape(phrase) + r"\b", full_text, flags=re.I):
            np = normalize_click_term(phrase)
            if np not in ordered_terms:
                ordered_terms.append(np)

    # 再补单词，但如果单词已经包含在某个重点词组里，就不重复加入。
    for w in re.findall(r"\b[a-zA-Z][a-zA-Z'-]*\b", full_text):
        nw = normalize_click_word(w)
        if not is_click_translatable_word(nw, full_text, min_len):
            continue
        if any(re.search(r"\b" + re.escape(nw) + r"\b", phrase) for phrase in ordered_terms if " " in phrase):
            continue
        if nw not in ordered_terms:
            ordered_terms.append(nw)
        if len(ordered_terms) >= max_words:
            break

    meanings = {}
    for term in ordered_terms[:max_words]:
        meanings[term] = explain_click_term(term)

    return meanings


def clickable_text_for_html(text, highlight_terms, word_meanings):
    """
    V14.1：
    优先把整组短语做成可点击块，例如 associate professor / lecture hall / is made up of。
    再处理单个学习词。
    """
    if not word_meanings:
        return esc(text)

    phrase_terms = sorted([k for k in word_meanings.keys() if " " in k], key=len, reverse=True)
    single_terms = set(k for k in word_meanings.keys() if " " not in k)

    highlight_set = {normalize_click_term(x) for x in highlight_terms}

    # 一个正则同时匹配短语和单词，短语放前面，避免被拆开。
    phrase_patterns = [re.escape(p) for p in phrase_terms]
    word_pattern = r"\b[a-zA-Z][a-zA-Z'-]*\b"
    if phrase_patterns:
        combined = r"\b(?:" + "|".join(phrase_patterns) + r")\b|" + word_pattern
    else:
        combined = word_pattern

    out = []
    last = 0

    for m in re.finditer(combined, text, flags=re.I):
        out.append(esc(text[last:m.start()]))
        raw = m.group(0)
        key = normalize_click_term(raw)

        if key in word_meanings:
            cls = "click-word"
            if key in highlight_set:
                cls += " key-word"
            out.append(f"<span class='{cls}' data-word='{esc(key)}'>{esc(raw)}</span>")
        else:
            w = normalize_click_word(raw)
            if w in single_terms:
                cls = "click-word"
                if w in highlight_set:
                    cls += " key-word"
                out.append(f"<span class='{cls}' data-word='{esc(w)}'>{esc(raw)}</span>")
            else:
                out.append(esc(raw))

        last = m.end()

    out.append(esc(text[last:]))
    return "".join(out)


def simplify_for_retell(sentence: str) -> str:
    sentence = clean_text(sentence)
    sentence = re.sub(r"\([^)]*\)", "", sentence)
    sentence = re.sub(r"\s+", " ", sentence).strip()
    words = sentence.split()
    if len(words) > 26:
        sentence = " ".join(words[:26]) + "..."
    return sentence


def choose_detail_sentence(sentences):
    if not sentences:
        return ""
    scored = []
    for s in sentences:
        low = s.lower()
        score = 0
        if any(k in low for k in ["because", "for example", "such as", "instead", "but", "however", "as a result", "one reason"]):
            score += 8
        if any(k in low for k in ["people", "many", "feel", "change", "life", "work", "school", "home", "travel", "food"]):
            score += 4
        score -= abs(len(s.split()) - 20) // 5
        scored.append((score, s))
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[0][1]


def infer_significance(text):
    low = text.lower()
    if any(k in low for k in ["school", "student", "learn", "education", "teacher"]):
        return "it shows how learning is connected with real-life needs."
    if any(k in low for k in ["travel", "tourist", "trip", "city", "hotel"]):
        return "it shows how travel is connected with everyday experience, not just sightseeing."
    if any(k in low for k in ["food", "restaurant", "cook", "meal", "coffee"]):
        return "it shows how food is connected with habits, culture and daily life."
    if any(k in low for k in ["work", "job", "office", "career"]):
        return "it shows how work can influence people's time, energy and choices."
    if any(k in low for k in ["technology", "ai", "app", "online", "social media", "phone"]):
        return "it shows how technology is changing ordinary people's lives."
    if any(k in low for k in ["family", "parents", "children", "friend", "relationship"]):
        return "it shows how relationships can shape people's feelings and decisions."
    return "it shows how a small detail can reflect a bigger change in daily life."


def build_retell_support(text):
    sentences = split_sentences(text)
    main = simplify_for_retell(sentences[0]) if sentences else simplify_for_retell(text)
    detail_raw = choose_detail_sentence(sentences[1:] if len(sentences) > 1 else sentences)
    detail = simplify_for_retell(detail_raw)
    significance = infer_significance(text)

    checklist = [
        f"1. 主旨：这段主要讲的是：{main}",
        f"2. 关键细节：可以提到：{detail if detail else main}",
        f"3. 重点/意义：这段可以理解为：{significance}",
    ]

    reference = [
        "This paragraph is mainly about " + main[0].lower() + main[1:] if main else "This paragraph is mainly about a change in daily life.",
        "The writer explains that " + detail[0].lower() + detail[1:] if detail else "The writer gives a specific detail to support this idea.",
        "The key point is that " + significance,
    ]

    standard = [
        "内容对照标准：",
        "1. 说到主旨，基本合格。",
        "2. 说到主旨 + 一个细节，比较完整。",
        "3. 说到主旨 + 细节 + 重点/意义，就是完整输出。"
    ]

    return checklist, reference, standard


def esc(x):
    return html.escape(str(x or ""))




def safe_filename_date():
    return datetime.now().strftime("%Y-%m-%d")


def ensure_archive_dirs():
    OUTPUT_DIR.mkdir(exist_ok=True)
    archive_dir = OUTPUT_DIR / "archive"
    archive_dir.mkdir(exist_ok=True)
    return archive_dir



def build_archive_index(today, article_title, title_zh, today_body_html):
    """
    V10：生成同一个主页面 index.html。
    顶部是今日练习，底部是历史目录。
    latest.html 也会写成同样内容，避免旧链接失效。
    """
    archive_dir = OUTPUT_DIR / "archive"
    entries = []
    for p in sorted([x for x in archive_dir.glob("day-*.html") if not x.name.endswith("-xhs.html")], reverse=True):
        date_part = p.stem.replace("day-", "")
        if date_part.endswith("-xhs"):
            continue

        # V12.6：历史目录不显示今天，避免页面顶部“今日内容”和底部历史目录重复出现同一天。
        if date_part == today:
            continue

        entries.append((date_part, f"archive/{p.name}"))

    items = []
    for date_part, href in entries:
        items.append(f"<li><a href='{esc(href)}'>{esc(date_part)}｜外刊表达练习</a></li>")

    if not items:
        items.append("<li>暂无历史记录</li>")

    history_html = f"""
<section class="card">
<h2>六、历史目录</h2>
<ul class="history-list">
{''.join(items)}
</ul>
</section>
"""

    unified = today_body_html.replace("</body>", history_html + "\n</body>")

    (OUTPUT_DIR / "index.html").write_text(unified, encoding="utf-8-sig")
    (OUTPUT_DIR / "latest.html").write_text(unified, encoding="utf-8-sig")
    return unified

def upload_file_sftp(sftp, local_path, remote_path):
    remote_folder = remote_path.rsplit("/", 1)[0]
    ensure_remote_dirs(sftp, remote_folder)
    sftp.put(str(local_path), remote_path)


def ensure_remote_dirs(sftp, remote_dir):
    parts = [p for p in remote_dir.split("/") if p]
    cur = ""
    for part in parts:
        cur += "/" + part
        try:
            sftp.stat(cur)
        except Exception:
            try:
                sftp.mkdir(cur)
            except Exception:
                pass


def auto_upload_outputs(cfg, today):
    if not cfg.get("auto_upload_enabled", False):
        return False, "未开启自动上传"

    upload_cfg = cfg.get("upload", {})
    host = upload_cfg.get("host", "").strip()
    port = int(upload_cfg.get("port", 22))
    username = upload_cfg.get("username", "").strip()
    password = upload_cfg.get("password", "")
    remote_dir = upload_cfg.get("remote_dir", "/var/www/html/daily").rstrip("/")

    if not host or not username or not password or "在这里填" in password:
        return False, "自动上传未执行：请先在 config.json 里填写 upload.password"

    try:
        import paramiko
        transport = paramiko.Transport((host, port))
        transport.connect(username=username, password=password)
        sftp = paramiko.SFTPClient.from_transport(transport)

        ensure_remote_dirs(sftp, remote_dir)
        ensure_remote_dirs(sftp, remote_dir + "/archive")

        upload_file_sftp(sftp, OUTPUT_DIR / "latest.html", remote_dir + "/latest.html")
        upload_file_sftp(sftp, OUTPUT_DIR / "latest.txt", remote_dir + "/latest.txt")
        upload_file_sftp(sftp, OUTPUT_DIR / "index.html", remote_dir + "/index.html")
        if (OUTPUT_DIR / "xhs.html").exists():
            upload_file_sftp(sftp, OUTPUT_DIR / "xhs.html", remote_dir + "/xhs.html")
        upload_file_sftp(sftp, OUTPUT_DIR / "archive" / f"day-{today}.html", remote_dir + f"/archive/day-{today}.html")
        upload_file_sftp(sftp, OUTPUT_DIR / "archive" / f"day-{today}.txt", remote_dir + f"/archive/day-{today}.txt")
        if (OUTPUT_DIR / "archive" / f"day-{today}-xhs.html").exists():
            upload_file_sftp(sftp, OUTPUT_DIR / "archive" / f"day-{today}-xhs.html", remote_dir + f"/archive/day-{today}-xhs.html")

        sftp.close()
        transport.close()
        return True, "自动上传完成"
    except Exception as e:
        return False, f"自动上传失败：{e}"

def combine_retell_support(paragraph_texts):
    full_text = " ".join(paragraph_texts)
    sentences = split_sentences(full_text)
    main = simplify_for_retell(sentences[0]) if sentences else simplify_for_retell(full_text)

    details = []
    for t in paragraph_texts:
        ss = split_sentences(t)
        d = choose_detail_sentence(ss[1:] if len(ss) > 1 else ss)
        d = simplify_for_retell(d)
        if d and d not in details:
            details.append(d)

    significance = infer_significance(full_text)

    checklist = [
        f"主旨：这组段落主要讲的是：{main}",
        f"关键细节：可以提到：{details[0] if len(details) > 0 else main}",
        f"补充细节：还可以提到：{details[1] if len(details) > 1 else significance}",
        f"重点/意义：这组段落可以理解为：{significance}",
    ]

    reference = [
        "These paragraphs are mainly about " + main[0].lower() + main[1:] if main else "These paragraphs are mainly about a change in daily life.",
    ]
    if details:
        reference.append("One important detail is that " + details[0][0].lower() + details[0][1:])
    if len(details) > 1:
        reference.append("Another detail is that " + details[1][0].lower() + details[1][1:])
    reference.append("The key point is that " + significance)

    return checklist, reference


def merge_expressions(paragraph_texts, count=8):
    merged = []
    seen = set()

    for para_text in paragraph_texts:
        for item in build_expressions(para_text, count):
            name = item[0]
            if name not in seen:
                merged.append(item)
                seen.add(name)
            if len(merged) >= count:
                return merged

    return merged[:count]




def build_long_sentence_analysis(paragraph_texts, max_items=3):
    return []

def format_sentence_analysis_txt(items):
    return []

def format_sentence_analysis_html(items):
    return ""


def pick_today_quote(paragraph_texts):
    """
    从截取段落中挑一句适合摘抄的句子。
    优先选有表达价值的搭配/句型，而不是简单句。
    """
    # 高价值表达搭配，出现这些加分
    high_value_phrases = [
        "instead of", "rather than", "not just", "not only",
        "as a result", "in the long run", "over time", "on top of",
        "carry it on", "set off", "a flood of", "more and more",
        "when it comes to", "the way", "used to", "has become",
        "make it easier", "find it hard", "one reason", "because of",
        "for many people", "no longer", "more likely", "less likely",
        "the fact that", "this means", "this shows", "whether or not",
    ]

    candidates = []
    for p in paragraph_texts:
        for s in split_sentences(p):
            s = clean_text(s)
            wc = word_count(s)
            if wc < 10 or wc > 35:
                continue
            if len(re.findall(r"\b\d+[\d,.%]*\b", s)) >= 3:
                continue
            if s.count('"') + s.count("'") >= 4:
                continue

            low = s.lower()
            score = min(wc, 24)

            # 高价值搭配加分
            for phrase in high_value_phrases:
                if phrase in low:
                    score += 8

            # 有从句结构加分（but/and/because/although/while/after/before）
            clause_words = ["but then", "and we", "instead of", "after", "although", "while", "because"]
            for cw in clause_words:
                if cw in low:
                    score += 4

            # 太短或太简单的句子降分
            if wc < 12:
                score -= 5

            candidates.append((score, s))

    if not candidates:
        for p in paragraph_texts:
            sentences = [clean_text(s) for s in split_sentences(p) if 8 <= word_count(s) <= 35]
            if sentences:
                q = sentences[0]
                return q, translate_text(q, True)
        return "", ""

    candidates.sort(key=lambda x: x[0], reverse=True)
    q = candidates[0][1]
    return q, translate_text(q, True)




def fetch_article_image_url(url):
    """从原文页读取 og:image / twitter:image。没有就返回空。"""
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        for key, val in [
            ("property", "og:image"),
            ("name", "twitter:image"),
            ("property", "twitter:image"),
        ]:
            tag = soup.find("meta", attrs={key: val})
            if tag and tag.get("content"):
                return urljoin(url, tag.get("content"))
    except Exception as e:
        print("封面图抓取失败：", e)
    return ""


def save_article_cover_image(article, today):
    """
    下载原文 og:image 到本地 output，供图文版封面使用。
    注意：原文图片可能有版权，公开发布前需要自己确认可用性。
    """
    image_url = fetch_article_image_url(article.get("link", ""))
    if not image_url:
        return ""
    try:
        r = requests.get(image_url, headers=HEADERS, timeout=25)
        r.raise_for_status()
        ctype = (r.headers.get("content-type") or "").lower()
        if "png" in ctype:
            ext = ".png"
        elif "webp" in ctype:
            ext = ".webp"
        else:
            ext = ".jpg"
        name = f"cover_image_{today}{ext}"
        path = OUTPUT_DIR / name
        path.write_bytes(r.content)
        print("封面图已保存：", path)
        return name
    except Exception as e:
        print("封面图保存失败：", e)
        return ""

def short_text(text, max_len=120):
    text = clean_text(text)
    if len(text) <= max_len:
        return text
    return text[:max_len].rstrip("，。；,. ") + "..."


def pick_core_sentence(paragraph_texts):
    """
    发布版只放一段精选英文，不放全文。
    优先选 35-75 词、观点/变化/结果感强的句子。
    """
    candidates = []
    for p in paragraph_texts:
        for sent in split_sentences(p):
            wc = word_count(sent)
            if wc < 18 or wc > 85:
                continue
            low = sent.lower()
            score = 0
            for k in ["change", "increase", "decrease", "instead of", "rather than", "as a result", "because", "more likely", "less likely", "the way", "people", "students", "work", "learn", "research", "study", "found", "shows", "could", "can", "should"]:
                if k in low:
                    score += 6
            if '"' in sent or "“" in sent:
                score += 4
            score += min(wc, 60) / 3
            candidates.append((score, sent))
    if candidates:
        candidates.sort(key=lambda x: x[0], reverse=True)
        return clean_text(candidates[0][1])

    for p in paragraph_texts:
        ss = split_sentences(p)
        if ss:
            return short_text(ss[0], 420)
    return ""


def build_hook_title(article, title_zh, paragraph_texts):
    """
    封面标题：不用“今日外刊精读”，改成更像发布笔记的话题钩子。
    """
    title = clean_text(title_zh or article.get("title", ""))
    low = (article.get("title", "") + " " + " ".join(paragraph_texts[:2])).lower()

    if "device-free" in low or "screen-free" in low or "smartphone" in low:
        return "少看屏幕后，\n效率真的会变高吗？"
    if topic_has(low, "ai") or topic_has(low, "artificial intelligence"):
        return "AI 越来越强，\n普通人该学什么？"
    if "journal" in low or "journaling" in low or "writing" in low:
        return "把想法写下来，\n真的会改变状态吗？"
    if "sleep" in low:
        return "越想睡好，\n可能越睡不好？"
    if "degree" in low or "university" in low or "graduates" in low:
        return "学历还值得花时间吗？"
    if "work" in low or "career" in low or "office" in low:
        return "工作方式变了，\n我们该怎么适应？"
    if "school" in low or "student" in low or "teacher" in low:
        return "学校里的一个变化，\n正在影响学生"
    if title:
        # 中文标题太长就做成两行
        title = re.sub(r"[:：｜|].*$", "", title)
        return short_text(title, 32)
    return "今天这篇外刊，\n值得读一读"




def topic_has(text, keyword):
    """
    V33：
    主题关键词安全匹配。
    特别修复 AI：不能让 said / daily / certain 里的 ai 被误判成 AI 文章。
    """
    low = (text or "").lower()
    kw = (keyword or "").lower().strip()
    if not kw:
        return False

    # AI 必须是独立词，或者明确出现 artificial intelligence / machine learning / chatbot
    if kw == "ai":
        return bool(re.search(r"(?<![a-z])ai(?![a-z])", low))
    if kw == "openai":
        return bool(re.search(r"(?<![a-z])openai(?![a-z])", low))
    if len(kw) <= 3 and kw.isalpha():
        return bool(re.search(rf"(?<![a-z]){re.escape(kw)}(?![a-z])", low))

    return kw in low


def topic_any(text, keywords):
    return any(topic_has(text, k) for k in keywords)


def detect_cover_theme(article, title_zh, paragraph_texts):
    """
    V33：
    不直接抓媒体原图做下载图，而是自动生成“主题视觉块”。
    这样：
    1. 下载 PNG 稳定有图
    2. 不依赖跨域图片
    3. 视觉更统一，更像小红书知识卡片
    """
    low = (article.get("title", "") + " " + title_zh + " " + " ".join(paragraph_texts[:3])).lower()

    themes = [
        {
            "match": ["device-free", "screen-free", "smartphone", "social media", "phone"],
            "label": "数字生活",
            "headline": "Focus Mode",
            "sub": "屏幕、专注与习惯",
            "palette": ["#5F7FA8", "#DDE8F5", "#AFC7E4"]
        },
        {
            "match": ["ai", "artificial intelligence", "machine learning", "chatbot"],
            "label": "AI 观察",
            "headline": "AI & Work",
            "sub": "技术如何改变普通人",
            "palette": ["#7D77C8", "#EEEAFE", "#CAC5F5"]
        },
        {
            "match": ["journal", "journaling", "writing", "reading", "book", "books"],
            "label": "阅读写作",
            "headline": "Write It Down",
            "sub": "写作、阅读与表达",
            "palette": ["#B47A56", "#F6E7DB", "#E7C7B0"]
        },
        {
            "match": ["sleep", "stress", "focus", "attention", "habit", "routine", "wellbeing", "well-being", "mental health"],
            "label": "状态习惯",
            "headline": "Better State",
            "sub": "睡眠、压力与专注",
            "palette": ["#6E8F74", "#E6F0E8", "#BFD8C4"]
        },
        {
            "match": ["students", "student", "school", "teacher", "teachers", "education", "learning", "graduates", "university"],
            "label": "学习教育",
            "headline": "Learn Better",
            "sub": "学校、学习与成长",
            "palette": ["#C28B49", "#F7EAD7", "#EBC896"]
        },
        {
            "match": ["work", "office", "career", "job", "productivity", "workplace", "meeting"],
            "label": "职场工作",
            "headline": "Work Shift",
            "sub": "工作方式与效率变化",
            "palette": ["#5E6B73", "#E4EAED", "#BAC8D0"]
        },
        {
            "match": ["parents", "family", "friendship", "community", "relationship", "children"],
            "label": "家庭关系",
            "headline": "Human Ties",
            "sub": "家庭、关系与日常",
            "palette": ["#B56E76", "#F7E5E8", "#E7BBC2"]
        },
    ]

    for item in themes:
        if topic_any(low, item["match"]):
            return item

    return {
        "label": "今日外刊",
        "headline": "Daily Reading",
        "sub": "生活化外刊表达",
        "palette": ["#8E7A5E", "#F3EBE0", "#DCCBB7"]
    }



def detect_cover_key(article, title_zh, paragraph_texts, cfg):
    """
    V33：
    根据文章内容判断应该调用哪个本地封面图库分类。
    """
    text = (article.get("title", "") + " " + title_zh + " " + " ".join(paragraph_texts[:3])).lower()
    themes = cfg.get("cover_library_themes", {}) or {}

    # 优先顺序要固定：AI 和 screen 很容易混，要先判 AI，再判 screen。
    order = ["ai", "screen", "education", "writing", "sleep", "work", "life"]
    for key in order:
        for kw in themes.get(key, []):
            if topic_has(text, kw):
                return key
    return "life"


def pick_local_cover_image(article, title_zh, paragraph_texts, cfg, forced_theme=""):
    """
    V33：
    从本地免版权封面图库里按主题选一张。
    服务器路径建议：
    /var/www/html/daily/assets/covers/ai/
    /var/www/html/daily/assets/covers/screen/
    /var/www/html/daily/assets/covers/education/
    /var/www/html/daily/assets/covers/writing/
    /var/www/html/daily/assets/covers/sleep/
    /var/www/html/daily/assets/covers/work/
    /var/www/html/daily/assets/covers/life/

    命名建议：
    cover-01.jpg
    cover-02.jpg
    cover-03.jpg

    返回的是相对 xhs.html 的 URL，例如：
    assets/covers/ai/cover-01.jpg
    """
    if not cfg.get("cover_library_enabled", True):
        return ""

    theme = forced_theme or detect_cover_key(article, title_zh, paragraph_texts, cfg)
    base_dir = Path(cfg.get("cover_library_dir", "/var/www/html/daily/assets/covers"))
    url_base = str(cfg.get("cover_library_url_base", "assets/covers")).strip("/")

    exts = ["*.jpg", "*.jpeg", "*.png", "*.webp"]
    candidates = []
    theme_dir = base_dir / theme
    for ext in exts:
        candidates.extend(sorted(theme_dir.glob(ext)))

    # V33：不再用 life 或根目录兜底，避免主题和图片错配。
    if not candidates:
        return ""

    # 稳定但不死板：按标题 hash 选图，同一篇文章每次固定。
    seed = abs(hash(article.get("title", "") + theme)) % len(candidates)
    chosen = candidates[seed]
    return f"{url_base}/{theme}/{chosen.name}" if chosen.parent.name == theme else f"{url_base}/{chosen.name}"


def build_chinese_overview(article, title_zh, paragraph_rows):
    """
    不做深度AI总结，只做稳妥的“这篇讲什么”。
    """
    raw_text = " ".join([r.get("raw", "") for r in paragraph_rows])
    first_zh = paragraph_rows[0].get("zh", "") if paragraph_rows else ""
    topic = clean_text(title_zh or article.get("title", ""))

    lines = []
    if topic:
        lines.append("这篇文章讲的是：" + short_text(topic, 42))

    low = raw_text.lower()
    if "device-free" in low:
        lines.append("核心变化是：有些团队开始尝试 device-free meetings，也就是开会时不使用电子设备。")
    elif "screen-free" in low:
        lines.append("核心变化是：一些学校或家庭开始尝试 screen-free time，减少屏幕干扰。")
    elif topic_has(low, "ai") or topic_has(low, "artificial intelligence"):
        lines.append("核心问题是：AI 正在改变学习、工作和技能要求。")
    elif "journaling" in low or "journal" in low:
        lines.append("核心观点是：把想法写下来，可能帮助人整理情绪和想法。")
    elif "work" in low or "office" in low:
        lines.append("核心问题是：新的工作方式正在影响人的效率、压力和选择。")
    else:
        if first_zh:
            lines.append(short_text(first_zh, 90))

    lines.append("适合积累：真实外刊表达、观点句和可复述素材。")
    return "\n\n".join(lines)


def select_publish_vocab(all_keywords, max_count=6):
    """
    发布版只保留 4-6 个表达，宁少勿滥。
    过滤掉太基础、太像孤立单词的项目。
    """
    basic = {
        "education", "information", "conversation", "definition", "action", "moment",
        "graduation", "school", "student", "students", "teacher", "teachers",
        "people", "work", "working", "technology", "according", "according to",
        "become", "have become", "preventable", "experience"
    }
    picked = []
    for k in all_keywords:
        kk = clean_text(k)
        low = kk.lower()
        if not kk or low in basic:
            continue
        # 优先短语
        if (" " in kk or "-" in kk or len(kk) >= 9) and low not in {x.lower() for x in picked}:
            picked.append(kk)
        if len(picked) >= max_count:
            break

    # 如果太少，允许补少量中等词，但仍避免太基础。
    if len(picked) < 4:
        for k in all_keywords:
            kk = clean_text(k)
            low = kk.lower()
            if not kk or low in basic or low in {x.lower() for x in picked}:
                continue
            picked.append(kk)
            if len(picked) >= max_count:
                break

    if not picked and all_keywords:
        picked = all_keywords[:min(len(all_keywords), max_count)]
    return picked[:max_count]



def xhs_publish_decision(article, title_zh, paragraph_rows, cfg):
    """
    V33：
    小红书发布严格模式。
    index.html 可以照常做学习页；xhs.html 只有适合小红书发布时才生成。
    """
    text = (
        article.get("title", "") + " " +
        title_zh + " " +
        article.get("summary", "") + " " +
        " ".join([r.get("raw", "") for r in paragraph_rows[:3]]) + " " +
        " ".join([r.get("zh", "") for r in paragraph_rows[:2]])
    ).lower()

    hard_reject = [
        "space station", "astronaut", "astronauts", "nasa", "roscosmos", "zvezda",
        "evacuation", "crew", "orbit", "spacecraft", "iss",
        "net zero", "net-zero", "electricity", "clean electricity", "cheap electricity",
        "power grid", "heat pump", "carbon emissions", "emissions", "fossil fuels",
        "climate policy", "energy policy", "public policy",
        "parliament", "minister", "government", "court", "trial", "lawsuit",
        "war", "attack", "killed", "murder", "weapon",
        "stock market", "interest rates", "central bank", "bond market",
        "clinical trial", "patients", "cancer", "disease", "medical breakthrough",
        "mars", "moon mission", "satellite", "rocket"
    ]

    for term in hard_reject:
        if topic_has(text, term):
            return False, f"硬题材不适合小红书图文：{term}", ""

    # 主题分类必须明确命中，不再用 life 兜底
    topic_rules = [
        ("ai", ["ai", "artificial intelligence", "machine learning", "chatbot", "ai at work", "ai education"]),
        ("screen", ["device-free", "screen-free", "smartphone", "phone", "social media", "screen time", "digital detox"]),
        ("education", ["student", "students", "school", "teacher", "teachers", "education", "learning", "classroom", "university", "graduates"]),
        ("writing", ["journal", "journaling", "writing", "write", "reading", "book", "books", "notebook"]),
        ("sleep", ["sleep", "stress", "tired", "routine", "habit", "wellbeing", "well-being", "mental health", "morning"]),
        ("work", ["work", "office", "career", "job", "productivity", "workplace", "meeting", "device-free meetings"]),
        ("life", ["parents", "family", "friendship", "community", "relationship", "home", "daily life", "everyday life"])
    ]

    matched = []
    for key, words in topic_rules:
        hits = [w for w in words if topic_has(text, w)]
        if hits:
            matched.append((key, len(hits), hits))

    if not matched:
        return False, "没有命中小红书友好主题，不生成发布图文", ""

    matched.sort(key=lambda x: x[1], reverse=True)
    theme = matched[0][0]

    # 太泛的 life 必须至少命中两个生活词，否则不生成
    if theme == "life" and matched[0][1] < 2:
        return False, "只命中泛生活词，主题不够明确，不生成发布图文", ""

    return True, "", theme


def build_xhs_empty_page(article, today, reason):
    title = clean_text(article.get("title", "")) or "今日文章"
    reason = clean_text(reason or "今日文章不适合做小红书图文发布。")
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>图文发布版｜{esc(today)}</title>
<style>
body {{
  margin: 0;
  background: #f3efe7;
  color: #222;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", Arial, sans-serif;
}}
.wrap {{
  max-width: 760px;
  margin: 48px auto;
  background: #fffaf0;
  border-radius: 22px;
  padding: 28px;
  box-shadow: 0 12px 36px rgba(60,45,20,.10);
}}
h1 {{ margin: 0 0 14px; font-size: 28px; }}
p {{ line-height: 1.8; color: #4d463a; }}
.box {{
  background: rgba(255,255,255,.62);
  border: 1px solid rgba(160,130,80,.18);
  border-radius: 16px;
  padding: 18px;
  margin-top: 18px;
}}
.small {{ font-size: 14px; color: #7a6f5d; }}
a {{ color: #0b65c2; text-decoration: none; }}
</style>
</head>
<body>
<div class="wrap">
  <h1>今日不生成图文发布版｜{esc(today)}</h1>
  <p>完整外刊学习页已正常生成，但这篇文章不适合直接做小红书图文发布。</p>
  <div class="box">
    <p><strong>今日文章：</strong>{esc(title)}</p>
    <p><strong>原因：</strong>{esc(reason)}</p>
  </div>
  <p class="small">现在规则：宁可今天不发图文，也不强行生成低质量、图文不匹配的卡片。</p>
  <p><a href="./index.html">返回完整学习页</a></p>
</div>
</body>
</html>"""


def split_text_for_card_pages(text, lang="en"):
    """
    V33：
    图文版按正文段落生成。
    长段自动拆页，避免手机端/PNG 底部截断。
    """
    text = clean_text(text)
    if not text:
        return []

    if lang == "zh":
        max_chars = 185
        pieces = re.split(r"(?<=[。！？；.!?])\s*", text)
        chunks, cur = [], ""
        for p in pieces:
            p = clean_text(p)
            if not p:
                continue
            if len(p) > max_chars:
                if cur:
                    chunks.append(cur)
                    cur = ""
                for i in range(0, len(p), max_chars):
                    chunks.append(p[i:i + max_chars])
                continue
            if len(cur) + len(p) > max_chars and cur:
                chunks.append(cur)
                cur = p
            else:
                cur = (cur + p) if cur else p
        if cur:
            chunks.append(cur)
        return chunks

    max_words = 72
    sentences = split_sentences(text)
    if not sentences:
        sentences = [text]

    chunks, cur, cur_wc = [], [], 0
    for sent in sentences:
        wc = word_count(sent)
        if wc > max_words:
            if cur:
                chunks.append(" ".join(cur))
                cur, cur_wc = [], 0
            words = sent.split()
            for i in range(0, len(words), max_words):
                chunks.append(" ".join(words[i:i + max_words]))
            continue

        if cur and cur_wc + wc > max_words:
            chunks.append(" ".join(cur))
            cur, cur_wc = [sent], wc
        else:
            cur.append(sent)
            cur_wc += wc

    if cur:
        chunks.append(" ".join(cur))
    return chunks


def build_xhs_export_page(article, title_zh, paragraph_rows, all_keywords, quote_raw, quote_translated, today, cover_image=""):
    """
    V34-M：
    手机端外刊学习页｜词汇标注 + 表达句式精修版。

    修正：
    1. 删除单独“难度分级”区块，只在文章卡片里保留难度标签。
    2. 表达句式聚焦可迁移结构，不再做空泛长难句。
    3. 表达句式必须给：原句 / 句式 / 中文意思 / 仿写例句。
    4. 重点表达同时保留：句式、短语、核心词汇。核心词汇必须有中文意思。
    5. 补充高频表达库：There comes a point in our lives / It turned out / Irrespective of quality / irritating / spotty / mimicking 等。
    6. 历史文章与主题分类保留。
    """
    source = clean_text(article.get("source", ""))
    pub_date = display_publish_date(article) or today
    title_raw = clean_text(article.get("title", ""))
    title_cn = clean_text(title_zh or "")
    link = article.get("link", "")

    if not title_raw:
        title_raw = title_cn or "Daily Reading"
    if not title_cn or title_cn == title_raw:
        title_cn = clean_text(title_zh or "今日外刊精读")

    paras = []
    text_all_parts = []
    zh_all_parts = []
    for row in paragraph_rows:
        raw = clean_text(row.get("raw", ""))
        zh = clean_text(row.get("zh", ""))
        if raw or zh:
            paras.append({"idx": row.get("idx", len(paras) + 1), "raw": raw, "zh": zh})
            if raw:
                text_all_parts.append(raw)
            if zh:
                zh_all_parts.append(zh)

    text_all = " ".join(text_all_parts)
    zh_all = " ".join(zh_all_parts)
    overview = build_chinese_overview(article, title_zh, paragraph_rows)

    def norm(x):
        return clean_text(x).strip().strip(".,;:!?\"'“”‘’()[]{}")

    def contains(term, text):
        if not term or not text:
            return False
        pat = r"(?<![A-Za-z])" + re.escape(term) + r"(?![A-Za-z])"
        return re.search(pat, text, flags=re.I) is not None

    def topic_category(title, source_name, body):
        s = f"{title} {source_name} {body[:1800]}".lower()
        rules = [
            ("AI科技", [" ai ", "artificial intelligence", "chatgpt", "robot", "algorithm", "machine learning"]),
            ("科技", ["technology", "tech", "digital", "app", "software", "device", "online"]),
            ("教育", ["education", "school", "student", "children", "reading", "teacher", "bbc education"]),
            ("文化历史", ["stonehenge", "archaeolog", "historian", "prehistoric", "heritage", "museum", "ancient", "medieval", "smithsonian"]),
            ("健康心理", ["health", "sleep", "stress", "mind", "mental", "wellbeing", "habit", "brain"]),
            ("社会工作", ["job", "work", "employment", "unemployed", "graduate", "society", "social"]),
            ("自然科学", ["science", "animal", "climate", "space", "experiment", "research", "study"]),
            ("生活", ["life", "travel", "food", "walk", "home", "family", "lifestyle"]),
        ]
        for name, keys in rules:
            if any(k in s for k in keys):
                return name
        return "综合"

    topic = topic_category(title_raw + " " + title_cn, source, text_all)

    def difficulty_label_from_text(text):
        try:
            prof = difficulty_profile(text)
            avg = prof.get("avg_sentence_words", 0) or 0
            lw = prof.get("long_word_count", 0) or 0
            wc = prof.get("word_count", 0) or 0
            if avg >= 30 and lw >= 24:
                return "C1"
            if avg >= 22 or lw >= 16:
                return "B2"
            if avg >= 13 or wc >= 70:
                return "B1-B2"
            return "B1"
        except Exception:
            return "B1-B2"

    level = difficulty_label_from_text(text_all)

    # 重点表达库：优先句式和短语，其次是核心词汇。
    expression_bank = [
        ("weighing a move from", "????? A ?? B?????????????????", "??"),
        ("a move from full-time to freelance work", "??????????move ?????????", "????"),
        ("terse email", "????????terse ? short ????????????", "????"),
        ("redo a task from scratch", "?????????from scratch ???????????", "????"),
        ("from scratch", "??????????", "??"),
        ("settled the matter instantly", "??????????settle the matter ????/??????", "????"),
        ("quit on the spot", "?????on the spot ????????", "????"),
        ("justify their employment", "???????/???????justify ??????????", "????"),
        ("turns out", "???????????????????????", "??"),
        ("particularly unusual", "??????? not particularly unusual ????????????", "????"),
        ("rude email", "??/???????", "????"),
        ("at least one", "????????????????", "????"),
        ("has become even more central to", "?????????/???", "??"),
        ("central to", "????????????????", "??"),
        ("tends to be accompanied by", "\u5f80\u5f80\u4f1a\u4f34\u968f\u7740\u2026\u2026\uff1b\u9002\u5408\u5199\u67d0\u4e2a\u8bdd\u9898\u51fa\u73b0\u65f6\u7684\u5e38\u89c1\u53cd\u5e94\u3002", "\u53e5\u5f0f"),
        ("be accompanied by", "\u4f34\u968f\u7740\u2026\u2026\uff1b\u6bd4 with \u66f4\u6b63\u5f0f\uff0c\u5916\u520a\u5e38\u7528\u3002", "\u53e5\u5f0f"),
        ("job cuts", "\u88c1\u5458\uff1b\u8ba8\u8bba\u5c31\u4e1a\u3001\u7ecf\u6d4e\u3001\u6280\u672f\u51b2\u51fb\u65f6\u9ad8\u9891\u3002", "\u8bdd\u9898\u8bcd\u7ec4"),
        ("across many more industries", "\u904d\u53ca\u66f4\u591a\u884c\u4e1a\uff1bacross \u5f3a\u8c03\u5f71\u54cd\u8303\u56f4\u6269\u6563\u3002", "\u8bdd\u9898\u8bcd\u7ec4"),
        ("come for us all", "\u6700\u7ec8\u6ce2\u53ca\u6240\u6709\u4eba\uff1b\u8bed\u6c14\u6bd4 affect \u66f4\u5f3a\uff0c\u9002\u5408\u5199\u7126\u8651\u548c\u98ce\u9669\u3002", "\u4eae\u70b9\u8868\u8fbe"),
        ("lose your job to", "\u56e0\u2026\u2026\u800c\u5931\u53bb\u5de5\u4f5c\uff1blose A to B \u8868\u793a A \u8f93\u7ed9 / \u88ab B \u53d6\u4ee3\u3002", "\u53e5\u5f0f"),
        ("a substitute for", "\u2026\u2026\u7684\u66ff\u4ee3\u54c1\uff1b\u79d1\u6280\u3001\u6559\u80b2\u3001\u5de5\u4f5c\u8bdd\u9898\u5e38\u7528\u3002", "\u77ed\u8bed"),
        ("but rather", "\u800c\u662f\uff1b\u66f4\u51c6\u786e\u5730\u8bf4\u662f\u2026\u2026\uff0c\u7528\u6765\u7ea0\u6b63\u524d\u9762\u7684\u8bf4\u6cd5\u3002", "\u8f6c\u6298\u7ed3\u6784"),
        # V34-M：当前教育/SEND文章常见表达 + 用户点名结构
        ("In the absence of sufficient places and timely support", "在缺乏足够名额和及时支持的情况下。absence = 缺乏；timely = 及时的。", "句式"),
        ("in the absence of", "在缺乏……的情况下。正式写作中常用来说明条件缺失。", "句式"),
        ("sufficient places", "足够的名额 / 学位 / 位置。教育资源语境中 place 常指学校名额。", "短语"),
        ("timely support", "及时支持。timely = 及时的。", "短语"),
        ("This is a testament to", "这证明了……；这体现了……。用于评价某事反映出的事实。", "句式"),
        ("is a testament to", "证明了……；体现了……。比 shows 更有表达感。", "句式"),
        ("one in three", "三分之一。用于比例表达。", "数据表达"),
        ("more than one in five", "超过五分之一。用于比例表达。", "数据表达"),
        ("make up", "占据；构成；组成。写比例或群体构成时高频。", "短语"),
        ("pupils with EHCPs", "拥有教育、健康与照护计划的学生。EHCP 是英国特殊教育支持文件。", "话题词组"),
        ("have autism", "患有/有自闭症。autism = 自闭症。", "话题表达"),
        ("speech, language and communications needs", "言语、语言和沟通需求。特殊教育语境常见表达。", "话题词组"),
        ("language and communications needs", "语言和沟通需求。", "话题词组"),
        ("mainstream", "主流的；普通学校体系的。教育语境中 mainstream school 指普通学校。", "词汇"),
        ("autism", "自闭症。教育、心理、医疗话题常见词。", "词汇"),
        ("sufficient", "足够的；充分的。", "词汇"),
        ("timely", "及时的。", "词汇"),
        ("absence", "缺乏；不存在。", "词汇"),
        ("testament", "证明；体现。", "词汇"),
        ("places", "名额；位置。教育语境中常指学校名额。", "词汇"),
        # 用户点名的表达句式
        ("There comes a point in our lives", "人生中总会有一个时刻……。适合写人生阶段、观念转变、情绪变化。", "句式"),
        ("There comes a point", "总会有一个时刻……。适合引出转折或人生感悟。", "句式"),
        ("It turned out", "结果证明；后来发现。用于表达事情的发展和原来想的不一样。", "句式"),
        ("Irrespective of quality", "不管质量如何；无论质量好坏。irrespective of = regardless of。", "让步结构"),
        ("irrespective of", "不管；不论。比 regardless of 更正式。", "让步结构"),

        # 词汇：用户点名
        ("irritating", "恼人的；令人烦躁的。常形容声音、习惯、问题。", "词汇"),
        ("spotty", "有斑点的；不稳定的；参差不齐的。具体意思看上下文。", "词汇"),
        ("mimicking", "模仿；模拟。可指行为、声音、系统对真实事物的模拟。", "词汇"),

        # Stonehenge / culture / history
        ("will have the chance to", "将有机会……。用于介绍某人将获得某种体验或机会。", "句式"),
        ("experience a unique slice of prehistoric life", "体验一小段独特的史前生活。slice of life 表示“生活的一小部分/片段”。", "亮点表达"),
        ("a unique slice of prehistoric life", "一小段独特的史前生活。适合描述沉浸式文化体验。", "话题表达"),
        ("thanks to", "多亏；由于。用于说明某件事发生的原因。", "原因结构"),
        ("the reconstruction of", "……的重建。常用于历史、建筑、文化遗产话题。", "话题表达"),
        ("a 4,500-year-old building", "一座有 4500 年历史的建筑。数字-year-old 可作复合形容词。", "描述结构"),
        ("scholars think", "学者认为……。比 people think 更正式。", "引用结构"),
        ("once stood near", "曾经位于……附近。once 表示“曾经”。", "历史表达"),
        ("commissioned by", "由……委托建造/制作。常用于项目、建筑、艺术品。", "被动结构"),
        ("the charity that manages", "管理……的慈善机构。that 引导定语从句。", "定语从句"),
        ("was built with", "以……方式建成；用……建成。后面常接材料、方法或态度。", "被动结构"),
        ("a painstakingly accurate nod to the past", "对过去高度精确的致敬。painstakingly 表示“煞费苦心地”。", "高级表达"),
        ("were sourced locally", "是在当地取材的。source 作动词，表示“采购/获取”。", "被动结构"),
        ("chosen based on what", "根据……来选择。based on what... 可引出选择依据。", "依据结构"),
        ("available millennia ago", "几千年前可获得的。millennia ago 表示“数千年前”。", "时间表达"),

        # Research / behavior
        ("research suggests that", "研究表明……。适合引出调查、实验或科学发现。", "研究句"),
        ("suggest that", "表明…… / 研究显示……。常用于引出研究发现。", "研究表达"),
        ("more unusual than previously thought", "比之前认为的更不寻常。用于表达“新发现推翻旧认知”。", "比较结构"),
        ("reveal that", "揭示…… / 表明……。比 say 更正式。", "研究表达"),
        ("have a natural tendency to", "天生有……的倾向；自然倾向于……。", "倾向表达"),
        ("have a tendency to", "有……的倾向。适合写行为习惯和心理倾向。", "倾向表达"),
        ("turn to the left", "向左转。", "动作表达"),
        ("in an anticlockwise direction", "沿逆时针方向。", "方向表达"),
        ("perform experiments", "进行实验。", "研究表达"),

        # Education / reading
        ("for the first time in five years", "五年来首次。适合写趋势变化。", "趋势句"),
        ("for the first time in", "……以来首次。适合写数据、趋势或变化节点。", "趋势句"),
        ("children and young people", "儿童和青少年。教育、阅读、心理健康类文章高频表达。", "话题词组"),
        ("poorer children", "贫困儿童 / 家境较困难的儿童。poorer 是社会经济语境。", "话题词组"),
        ("reading for pleasure", "为乐趣而阅读；非功利阅读。教育类外刊常见表达。", "话题表达"),
        ("enjoy reading", "喜欢阅读。比 like reading 更适合教育类报道。", "基础表达"),
        ("according to", "根据……。引用报告、研究、调查时常用。", "引用结构"),
        ("be more likely to", "更有可能……。写群体差异、调查结论时高频。", "比较结构"),
        ("be less likely to", "更不可能……。写群体差异和风险对比时高频。", "比较结构"),
        ("compared with", "与……相比。数据对比、群体对比常用。", "比较结构"),

        # Work / society / life
        ("leave the house", "出门；离开家。适合描述生活范围或状态。", "生活状态"),
        ("apart from", "除了……之外。比 only / except 更适合正式表达。", "连接结构"),
        ("stock up on", "储备；囤积。常用于 food, supplies, essentials。", "动词短语"),
        ("the new normal", "新常态。适合描述已经普遍但未必理想的现实。", "观点表达"),
        ("come into full focus", "变得非常清晰；更加凸显。", "高级表达"),

        # General
        ("reduce the number of decisions", "减少需要做决定的次数。效率、习惯、心理负担话题常用。", "观点表达"),
        ("truly need our attention", "真正需要我们注意力的事情。适合表达优先级和精力管理。", "观点表达"),
        ("rarely change a life overnight", "很少会一夜之间改变生活。适合表达变化不是立刻发生的。", "观点句"),
        ("play a role in", "在……中发挥作用。", "学术表达"),
        ("rather than", "而不是。", "对比结构"),
        ("instead of", "而不是。", "对比结构"),
        ("be linked to", "与……有关。", "学术表达"),
        ("be associated with", "与……相关。", "学术表达"),
        ("be expected to", "被预计会……；应该会……。", "预测结构"),
    ]

    expressions = []
    used = set()

    def add_expr(term, meaning, label="表达"):
        t = norm(term)
        if not t:
            return
        low = t.lower()
        if low in used:
            return
        used.add(low)
        expressions.append({"text": t, "meaning": meaning, "label": label})

    # A. 表达库优先。
    for term, meaning, label in expression_bank:
        if contains(term, text_all):
            add_expr(term, meaning, label)
        if len(expressions) >= 16:
            break

    # B. 正则抓结构。
    pattern_items = [
        (r"\bIn\s+the\s+absence\s+of\s+[^,.]+", "在缺乏……的情况下。用于说明某种条件不存在。", "句式"),
        (r"\bThis\s+is\s+a\s+testament\s+to\s+[^,.]+", "这证明了……；这体现了……。", "句式"),
        (r"\b(?:one|two|three|four|five|six|seven|eight|nine|ten)\s+in\s+(?:two|three|four|five|six|seven|eight|nine|ten)\b", "几分之几。用于比例表达。", "数据表达"),
        (r"\bmore\s+than\s+one\s+in\s+(?:two|three|four|five|six|seven|eight|nine|ten)\b", "超过几分之一。用于比例表达。", "数据表达"),
        (r"\bmake\s+up\b", "占据；构成；组成。", "短语"),
        (r"\bThere\s+comes\s+a\s+point(?:\s+in\s+our\s+lives)?\b", "人生中总会有一个时刻……。用于引出人生阶段或观念变化。", "句式"),
        (r"\bIt\s+turned\s+out\s+that\b", "结果证明……；后来发现……。", "句式"),
        (r"\bIt\s+turned\s+out\b", "结果证明；后来发现。", "句式"),
        (r"\bIrrespective\s+of\s+[a-zA-Z'-]+", "不管……；无论……如何。", "让步结构"),
        (r"\b(?:will|would|can|could)\s+have\s+the\s+chance\s+to\b", "将有机会……。用于介绍体验、机会或可能性。", "机会句型"),
        (r"\bthanks\s+to\s+(?:the\s+)?[a-zA-Z'-]+", "多亏 / 由于……。用于说明原因。", "原因结构"),
        (r"\b(?:was|were)\s+sourced\s+locally\b", "是在当地取材/采购的。", "被动结构"),
        (r"\bbased\s+on\s+what\b", "基于……所了解/掌握的内容。", "依据结构"),
        (r"\b(?:research|study|survey|report|findings?)\s+(?:suggests?|shows?|reveals?|finds?)\s+that\b", "研究/调查表明……。适合引出研究发现。", "研究句"),
        (r"\b(?:suggests?|shows?|reveals?|finds?)\s+that\b", "表明…… / 显示……。常用于研究发现或报道结论。", "研究句"),
        (r"\bmore\s+[a-zA-Z-]+\s+than\s+previously\s+thought\b", "比之前认为的更……。用于表达新发现。", "比较结构"),
        (r"\bfor the first time in\s+[a-zA-Z0-9 -]+", "……以来首次。适合写趋势变化。", "趋势句"),
        (r"\b(?:more|less)\s+likely\s+to\b", "更有可能 / 更不可能……。适合写群体差异。", "比较结构"),
    ]
    for pat, meaning, label in pattern_items:
        for m in re.finditer(pat, text_all, flags=re.I):
            add_expr(m.group(0), meaning, label)
            if len(expressions) >= 16:
                break
        if len(expressions) >= 16:
            break

    # C. all_keywords 补充，但保留用户关心的核心词。
    forced_word_meanings = {
        "autism": "自闭症。",
        "sufficient": "足够的；充分的。",
        "timely": "及时的。",
        "mainstream": "主流的；普通学校体系的。",
        "absence": "缺乏；不存在。",
        "testament": "证明；体现。",
        "places": "名额；位置。教育语境中常指学校名额。",
        "pupils": "学生；小学生/中学生。",
        "communications": "沟通；通信；交流。",
        "needs": "需求；需要。",
        "support": "支持；帮助。",
        "irritating": "恼人的；令人烦躁的。",
        "spotty": "有斑点的；不稳定的；参差不齐的。",
        "mimicking": "模仿；模拟。",
        "practical": "实际的；实用的。",
        "applications": "应用；用途。",
        "renovators": "翻修者；修缮者。",
        "tradespeople": "工匠；技工。",
        "genes": "基因。",
        "achievement": "成就。",
    }
    for word, meaning in forced_word_meanings.items():
        if contains(word, text_all):
            add_expr(word, meaning, "词汇")
        if len(expressions) >= 16:
            break

    bad_single = {"children", "people", "reading", "article", "school", "student", "students", "work", "life", "time", "year", "years", "education", "survey", "research", "employment", "communication", "communications", "technicality", "government", "workers", "has become", "have become"}
    for k in all_keywords or []:
        kk = norm(k)
        if not kk:
            continue
        if kk.lower() in used or kk.lower() in bad_single:
            continue
        if " " not in kk and len(kk) < 10:
            continue
        try:
            meaning = explain_keyword(kk)
        except Exception:
            meaning = "可理解为：" + kk
        add_expr(kk, meaning, "词汇")
        if len(expressions) >= 16:
            break

    expressions = expressions[:16]

    def find_sentence_with(term):
        if not term:
            return ""
        sentences = re.split(r"(?<=[.!?])\s+", text_all)
        for s in sentences:
            if contains(term, s):
                return clean_text(s)
        return ""

    # 表达句式：不做长难句，改为可复用句式。
    def make_pattern_rows():
        rows = []
        seen = set()

        def add(title, original, structure, meaning, example, note):
            original = clean_text(original or "")
            if not original:
                return
            key = (structure.lower(), original.lower()[:120])
            if key in seen:
                return
            seen.add(key)
            rows.append({"title": title, "original": original, "structure": structure, "meaning": meaning, "example": example, "note": note})

        def first_sentence_matching(pattern):
            for s in split_sentences(text_all):
                if re.search(pattern, s, flags=re.I):
                    return clean_text(s)
            return ""

        pattern_candidates = [
            (
                r"\bWhen\s+I\s+was\s+weighing\s+a\s+move\s+from\s+[^,]+\s+to\s+[^,]+,\s+[^.?!]+",
                "?????",
                "When I was weighing a move from A to B, C settled the matter.",
                "????????? A ?? B ??C ??????????",
                "When I was weighing a move from office work to remote work, one meeting settled the matter.",
                "????????????????????????",
            ),
            (
                r"\bNone\s+of\s+this,\s+it\s+turns\s+out,\s+was\s+particularly\s+unusual\b[^.?!]*",
                "??????",
                "None of this, it turns out, was particularly unusual.",
                "?????????????????",
                "None of this, it turns out, was particularly unusual in modern workplaces.",
                "??????????????????",
            ),
            (
                r"\bResearch\s+from\s+\d{4}\s+shows\s+[^.?!]+",
                "?????",
                "Research from YEAR shows that ...",
                "????????????????????",
                "Research from 2024 shows that many workers feel pressure to reply quickly.",
                "? studies show ????????????",
            ),
            (
                r"\brequiring\s+them\s+to\s+justify\s+their\s+employment\b[^.?!]+",
                "?????",
                "A required them to justify B, or resign.",
                "A ?????? B ???????????",
                "The company required workers to justify their roles, or leave.",
                "?????????????????????",
            ),
            (
                r"\bhas\s+become\s+even\s+more\s+central\s+to\s+[^.?!]+",
                "??????",
                "A has become even more central to B.",
                "A ? B ??????/???",
                "Clear communication has become even more central to remote work.",
                "??????? has become?????????",
            ),
            (
                r"\bSince\s+[^,.]{3,90},\s+[^.?!]{15,220}",
                "\u65f6\u95f4\u80cc\u666f\u53e5",
                "Since A, B has / have ...",
                "\u81ea\u4ece A \u4ee5\u6765\uff0cB \u5df2\u7ecf\u2026\u2026\u3002\u9002\u5408\u5199\u8d8b\u52bf\u3001\u53d8\u5316\u548c\u80cc\u666f\u3002",
                "Since the rise of short videos, many people have changed the way they read news.",
                "\u8fd9\u79cd\u53e5\u5f0f\u80fd\u5feb\u901f\u628a\u65f6\u95f4\u8d77\u70b9\u548c\u540e\u7eed\u5f71\u54cd\u8fde\u8d77\u6765\u3002",
            ),
            (
                r"\bAny\s+mention\s+of\s+[^,.]{2,90}\s+tends\s+to\s+be\s+accompanied\s+by\s+warnings\s+that\b[^.?!]*",
                "\u8bdd\u9898\u53cd\u5e94\u53e5",
                "Any mention of A tends to be accompanied by warnings that B.",
                "\u4efb\u4f55\u5173\u4e8e A \u7684\u8ba8\u8bba\uff0c\u5f80\u5f80\u90fd\u4f1a\u4f34\u968f\u7740 B \u8fd9\u6837\u7684\u8b66\u544a\u3002",
                "Any mention of online learning tends to be accompanied by warnings that students may lose focus.",
                "\u9002\u5408\u5199 AI\u3001\u6559\u80b2\u3001\u793e\u4f1a\u7126\u8651\u7c7b\u8bdd\u9898\uff0c\u6bd4\u5355\u5199 people worry about \u66f4\u50cf\u5916\u520a\u3002",
            ),
            (
                r"\b(?:You(?:'re| are)|We(?:'re| are)|They(?:'re| are)|People are)\s+not\s+going\s+to\s+lose\s+[^.?!]{1,100}\s+to\s+[^,.;]+,\s+but\s+[^.?!]+",
                "\u5bf9\u6bd4\u8b66\u793a\u53e5",
                "You are not going to lose A to B, but to C.",
                "\u4f60\u4e0d\u662f\u4f1a\u628a A \u8f93\u7ed9 B\uff0c\u800c\u662f\u4f1a\u8f93\u7ed9 C\u3002",
                "You are not going to lose opportunities to technology, but to people who use it better.",
                "\u8fd9\u662f\u5f88\u6709\u51b2\u51fb\u529b\u7684\u8f6c\u6298\u53e5\uff0c\u9002\u5408\u5199\u7ade\u4e89\u3001\u6280\u80fd\u548c\u5de5\u4f5c\u53d8\u5316\u3002",
            ),
            (
                r"\b[^.?!]{0,90}\bisn'?t\s+a\s+substitute\s+for\s+[^,.;]+,\s+but\s+rather\s+[^.?!]+",
                "\u7ea0\u6b63\u5b9a\u4e49\u53e5",
                "A is not a substitute for B, but rather C.",
                "A \u4e0d\u662f B \u7684\u66ff\u4ee3\u54c1\uff0c\u800c\u66f4\u51c6\u786e\u5730\u8bf4\u662f C\u3002",
                "AI is not a substitute for thinking, but rather a tool that changes how we work.",
                "\u9002\u5408\u628a\u4e00\u4e2a\u5bb9\u6613\u88ab\u8bef\u89e3\u7684\u4e1c\u897f\u91cd\u65b0\u5b9a\u4e49\u3002",
            ),
            (
                r"\bnot\s+[^,.;]{1,80},\s+but\s+rather\s+[^.?!]+",
                "\u7ea0\u6b63\u8f6c\u6298\u53e5",
                "not A, but rather B",
                "\u4e0d\u662f A\uff0c\u800c\u662f B\u3002rather \u8ba9\u8f6c\u6298\u66f4\u6b63\u5f0f\u3001\u66f4\u51c6\u786e\u3002",
                "The problem is not a lack of time, but rather a lack of clear priorities.",
                "\u5199\u89c2\u70b9\u6587\u65f6\u5f88\u5b9e\u7528\uff0c\u53ef\u4ee5\u7528\u6765\u63a8\u7ffb\u8868\u9762\u7406\u89e3\u3002",
            ),
            (
                r"\b(?:research|study|survey|report|findings?)\s+(?:suggests?|shows?|reveals?|finds?)\s+that\b[^.?!]*",
                "\u7814\u7a76\u53d1\u73b0\u53e5",
                "Research / A study suggests that ...",
                "\u7814\u7a76 / \u8c03\u67e5\u8868\u660e\u2026\u2026\u3002\u9002\u5408\u5f15\u51fa\u4e8b\u5b9e\u4f9d\u636e\u3002",
                "Research suggests that regular reading can improve attention over time.",
                "\u5916\u520a\u91cc\u5e38\u7528\u8fd9\u7c7b\u53e5\u5f0f\u6765\u5e73\u8861\u89c2\u70b9\u548c\u8bc1\u636e\u3002",
            ),
            (
                r"\b(?:more|less)\s+likely\s+to\b[^.?!]*",
                "\u6982\u7387\u5bf9\u6bd4\u53e5",
                "A is more / less likely to do B than C.",
                "A \u6bd4 C \u66f4\u6709 / \u66f4\u4e0d\u592a\u53ef\u80fd\u505a B\u3002",
                "Students who read every day are more likely to build a strong vocabulary.",
                "\u9002\u5408\u5199\u7fa4\u4f53\u5dee\u5f02\u3001\u884c\u4e3a\u8d8b\u52bf\u548c\u8c03\u67e5\u7ed3\u8bba\u3002",
            ),
            (
                r"\b(?:make|makes|made)\s+it\s+(?:easier|harder|possible|difficult)\s+to\b[^.?!]*",
                "\u5f71\u54cd\u7ed3\u679c\u53e5",
                "A makes it easier / harder to do B.",
                "A \u8ba9\u505a B \u53d8\u5f97\u66f4\u5bb9\u6613 / \u66f4\u56f0\u96be\u3002",
                "Clear examples make it easier to remember new expressions.",
                "\u8fd9\u662f\u5199\u6280\u672f\u3001\u6559\u80b2\u3001\u5de5\u4f5c\u5f71\u54cd\u65f6\u7684\u9ad8\u9891\u7ed3\u6784\u3002",
            ),
            (
                r"\b(?:be|is|are|was|were|been)\s+linked\s+to\b[^.?!]*",
                "\u5173\u8054\u8bf4\u660e\u53e5",
                "A is linked to B.",
                "A \u4e0e B \u6709\u5173\u3002\u6bd4 A causes B \u66f4\u8c28\u614e\u3001\u66f4\u7b26\u5408\u5916\u520a\u5199\u6cd5\u3002",
                "Long screen time is often linked to poor sleep quality.",
                "\u9002\u5408\u5199\u4e0d\u60f3\u628a\u56e0\u679c\u8bf4\u5f97\u592a\u7edd\u5bf9\u7684\u8bdd\u9898\u3002",
            ),
        ]

        for pat, title, structure, meaning, example, note in pattern_candidates:
            s = first_sentence_matching(pat)
            if s:
                add(title, s, structure, meaning, example, note)
            if len(rows) >= 4:
                break

        if len(rows) < 3:
            for s in split_sentences(text_all):
                if len(rows) >= 4:
                    break
                if word_count(s) < 10 or word_count(s) > 38:
                    continue
                if re.search(r"\btends\s+to\b", s, flags=re.I):
                    add("\u503e\u5411\u8868\u8fbe\u53e5", s, "A tends to do B.", "A \u5f80\u5f80\u4f1a\u505a B\uff1b\u7528\u4e8e\u6982\u62ec\u5e38\u89c1\u884c\u4e3a\u6216\u73b0\u8c61\u3002", "Public debates tend to focus on risks rather than opportunities.", "\u6bd4 usually \u66f4\u6709\u5916\u520a\u5206\u6790\u611f\u3002")
                elif re.search(r"\bacross\s+[^.?!]{3,80}", s, flags=re.I):
                    add("\u8303\u56f4\u6269\u6563\u53e5", s, "A happens across B.", "A \u5728 B \u8303\u56f4\u5185\u53d1\u751f / \u6269\u6563\u3002", "The change is happening across many parts of daily life.", "across \u5f88\u9002\u5408\u5199\u5f71\u54cd\u9762\u3001\u884c\u4e1a\u6216\u5730\u57df\u3002")
                elif re.search(r"\brather\s+than\b", s, flags=re.I):
                    add("\u66ff\u4ee3\u9009\u62e9\u53e5", s, "A rather than B", "A \u800c\u4e0d\u662f B\uff1b\u7528\u6765\u5f3a\u8c03\u771f\u6b63\u9009\u62e9\u3002", "I want steady progress rather than quick results.", "\u9002\u5408\u628a\u4e24\u4e2a\u9009\u9879\u5bf9\u6bd4\u6e05\u695a\u3002")
                elif re.search(r"\bas\s+a\s+result\b", s, flags=re.I):
                    add("\u7ed3\u679c\u8854\u63a5\u53e5", s, "As a result, S + V.", "\u7ed3\u679c\uff0c\u2026\u2026\u3002\u7528\u6765\u8fde\u63a5\u539f\u56e0\u548c\u540e\u679c\u3002", "As a result, learners remember the expression more easily.", "\u5199\u539f\u56e0\u5206\u6790\u65f6\u5f88\u7a33\u3002")

        if not rows and expressions:
            first = expressions[0]["text"]
            original = find_sentence_with(first) or (split_sentences(text_all)[0] if split_sentences(text_all) else text_all)
            add(
                "\u8868\u8fbe\u6539\u5199\u53e5",
                original,
                "Keep the useful expression, then replace the topic.",
                "\u4fdd\u7559\u539f\u6587\u7684\u597d\u8868\u8fbe\uff0c\u628a\u4e3b\u9898\u6362\u6210\u81ea\u5df1\u7684\u573a\u666f\u3002",
                "This expression can be reused to describe study, work, or daily life.",
                "\u8fd9\u662f\u6700\u540e\u515c\u5e95\uff0c\u4e5f\u4f1a\u4fdd\u7559\u539f\u53e5\uff0c\u4e0d\u518d\u751f\u6210\u7a7a\u6cdb\u7684 Use xxx \u4efb\u52a1\u3002"
            )

        return rows[:4]

    pattern_rows = make_pattern_rows()

    def attr_escape(x):
        return html.escape(str(x or ""), quote=True)

    def mark_terms_py(text):
        safe = esc(text)
        for item in sorted(expressions, key=lambda x: -len(x.get("text", ""))):
            term = item.get("text", "")
            meaning = item.get("meaning", "")
            if not term or len(term) < 3:
                continue
            pat = re.compile(r"(?<![A-Za-z])(" + re.escape(term) + r")(?![A-Za-z])", re.I)
            def repl(m):
                word = m.group(1)
                return '<span class="hl-term" data-term="' + attr_escape(term) + '" data-meaning="' + attr_escape(meaning) + '">' + esc(word) + "</span>"
            safe = pat.sub(repl, safe)
        return safe

    paragraph_html = ""
    for i, p in enumerate(paras):
        paragraph_html += f"""
          <div class="para-card">
            <div class="para-title">第 {esc(p.get('idx', i + 1))} 段</div>
            <p class="english">{mark_terms_py(p.get('raw', ''))}</p>
            <div class="para-divider"></div>
            <p class="translation">{esc(p.get('zh', ''))}</p>
          </div>
        """

    expression_html = ""
    order = {"句式": 0, "研究句": 0, "数据表达": 0, "让步结构": 0, "短语": 1, "话题词组": 1, "话题表达": 1, "原因结构": 1, "比较结构": 1, "词汇": 2}
    expressions_sorted = sorted(expressions, key=lambda x: (order.get(x.get("label", ""), 1), len(x.get("text", ""))))
    for item in expressions_sorted:
        expression_html += f"""
          <div class="expression" data-term="{attr_escape(item.get('text'))}" data-meaning="{attr_escape(item.get('meaning'))}">
            <span class="expr-label">{esc(item.get('label', '表达'))}</span>
            <b>{esc(item.get('text'))}</b>
            <span class="expr-meaning">{esc(item.get('meaning'))}</span>
          </div>
        """

    if not expression_html:
        expression_html = '<div class="expression"><span class="expr-label">表达</span><b>今日表达</b><span class="expr-meaning">这篇文章适合积累原文中的可复用句型和话题表达。</span></div>'

    patterns_html = ""
    for row in pattern_rows:
        original_html = ""
        if row.get("original"):
            original_html = f'<p class="original">原句：{esc(row.get("original"))}</p>'
        patterns_html += f"""
          <div class="pattern-card">
            <b>{esc(row.get('title'))}</b>
            {original_html}
            <p class="pattern">句式：{esc(row.get('structure'))}</p>
            <p class="meaning">意思：{esc(row.get('meaning'))}</p>
            <p class="example">例句：{esc(row.get('example'))}</p>
            <small>{esc(row.get('note'))}</small>
          </div>
        """

    # 历史文章：从 output/archive/day-*.txt 读取，显示主题分类。
    def load_history_entries(max_count=8):
        entries = []
        archive_dir = OUTPUT_DIR / "archive"
        if not archive_dir.exists():
            return entries
        for txt_path in sorted(archive_dir.glob("day-*.txt"), reverse=True):
            date_part = txt_path.stem.replace("day-", "")
            if date_part == today:
                continue
            try:
                t = txt_path.read_text(encoding="utf-8-sig", errors="ignore")
            except Exception:
                continue
            def after_label(label):
                m = re.search(re.escape(label) + r"\s*\n([^\n]+)", t)
                return clean_text(m.group(1)) if m else ""
            en_title = after_label("英文标题：") or "历史外刊"
            cn_title = after_label("中文标题：")
            source_line = after_label("来源：")
            topic_h = topic_category(en_title + " " + cn_title, source_line, t[:1200])
            level_h = difficulty_label_from_text(t[:2000])
            href = f"archive/day-{date_part}-xhs.html"
            if not (archive_dir / f"day-{date_part}-xhs.html").exists():
                href = f"archive/day-{date_part}.html"
            entries.append({"date": date_part, "title": en_title, "topic": topic_h, "level": level_h, "href": href})
            if len(entries) >= max_count:
                break
        return entries

    def history_bucket(topic_name, title_text):
        s = f"{topic_name} {title_text}".lower()
        if any(k in s for k in ["ai", "??", "??", "science", "nature", "climate", "robot", "digital", "???", "?"]):
            return "tech"
        if any(k in s for k in ["education", "school", "student", "reading", "teacher", "??", "??", "???"]):
            return "education"
        if any(k in s for k in ["culture", "history", "book", "museum", "ancient", "heritage", "??", "??", "??", "???", "???"]):
            return "humanities"
        return "life"

    history_entries = []
    seen_titles = set()
    for h in load_history_entries(max_count=60):
        title_key = h.get("title", "").lower().strip()
        if not title_key or title_key in seen_titles:
            continue
        seen_titles.add(title_key)
        h["bucket"] = history_bucket(h.get("topic", ""), h.get("title", ""))
        history_entries.append(h)

    bucket_labels = [("tech", "??"), ("education", "??"), ("humanities", "??"), ("life", "??")]
    first_active = next((key for key, _label in bucket_labels if any(h.get("bucket") == key for h in history_entries)), "tech")
    history_html = '<div class="history-tabs">'
    for key, label in bucket_labels:
        active = ' is-active' if key == first_active else ''
        history_html += f'<button type="button" class="history-tab{active}" data-history-filter="{key}">{label}</button>'
    history_html += '</div><div class="history-panels">'

    for key, label in bucket_labels:
        active = ' is-active' if key == first_active else ''
        items = [h for h in history_entries if h.get("bucket") == key]
        history_html += f'<div class="topic-group{active}" data-history-topic="{key}"><h3>{label}</h3>'
        if items:
            for h in items[:8]:
                title_show = h.get("title", "????")
                if len(title_show) > 68:
                    title_show = title_show[:66] + "..."
                history_html += f'<a class="history-item" href="{attr_escape(h.get("href", "#"))}"><b>{esc(title_show)}</b></a>'
        else:
            history_html += '<div class="history-empty">????????????</div>'
        history_html += '</div>'
    history_html += '</div>'

    source_link_html = ""
    if link:
        source_link_html = f'<a class="source-link" href="{attr_escape(link)}" target="_blank" rel="noopener">查看原文来源</a>'

    best_expr = expressions_sorted[0]["text"] if expressions_sorted else ""
    best_meaning = expressions_sorted[0]["meaning"] if expressions_sorted else ""
    today_dot = esc(today).replace("-", ".")

    page = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
  <title>Healing Lab 每日外刊｜{esc(today)}</title>
  <style>
    :root {{
      --ink:#1d252c; --muted:#68747f; --line:#dfe6e8;
      --paper:#fbfaf6; --paper-deep:#f5f2eb; --card:rgba(255,255,255,.92);
      --sage-dark:#426e60; --sage-soft:#eef6f1;
      --clay:#d78768; --clay-soft:#fbf0ea;
      --blue:#557da8; --blue-soft:#edf3f8;
      --shadow:0 18px 46px rgba(44,57,64,.12);
      --radius-lg:22px; --radius-sm:12px;
    }}
    *{{box-sizing:border-box}} html{{scroll-behavior:smooth}}
    body{{margin:0;color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",Arial,sans-serif;background:linear-gradient(135deg,rgba(255,255,255,.88),rgba(255,255,255,.96)),radial-gradient(circle at 12% 4%,rgba(127,163,145,.25),transparent 34%),radial-gradient(circle at 92% 14%,rgba(241,198,109,.25),transparent 30%),radial-gradient(circle at 50% 92%,rgba(85,125,168,.16),transparent 36%),var(--paper-deep);min-height:100vh}}
    .phone-shell{{width:min(100%,480px);margin:0 auto;padding:env(safe-area-inset-top) 14px 34px}}
    .hero{{padding:28px 4px 16px}} .brand-row{{display:flex;justify-content:space-between;align-items:center;gap:14px;margin-bottom:20px}}
    .brand-mark{{width:48px;height:48px;border-radius:50%;display:grid;place-items:center;background:var(--sage-dark);color:#fff;font-weight:900;box-shadow:0 14px 30px rgba(66,110,96,.25)}}
    .date-pill{{background:rgba(255,255,255,.78);border:1px solid var(--line);border-radius:999px;padding:9px 13px;color:var(--sage-dark);font-weight:800;font-size:13px;white-space:nowrap}}
    .hero h1{{margin:0;font-size:clamp(40px,13vw,58px);line-height:.98;letter-spacing:-.055em}} .hero .subtitle{{margin:12px 0 0;color:var(--muted);font-size:15px;line-height:1.65}}
    .quick-nav{{position:sticky;top:0;z-index:30;margin:4px -14px 16px;padding:10px 14px;overflow-x:auto;display:flex;gap:8px;background:rgba(245,242,235,.72);backdrop-filter:blur(12px);border-top:1px solid rgba(223,230,232,.7);border-bottom:1px solid rgba(223,230,232,.7)}}
    .quick-nav a{{flex:0 0 auto;text-decoration:none;color:var(--sage-dark);background:rgba(255,255,255,.78);border:1px solid var(--line);border-radius:999px;padding:7px 11px;font-size:13px;font-weight:800}}
    .section-stack{{display:grid;gap:14px}} .card{{background:var(--card);border:1px solid var(--line);border-radius:var(--radius-lg);box-shadow:var(--shadow);overflow:hidden}}
    .article-cover{{min-height:292px;padding:22px;background:linear-gradient(rgba(255,255,255,.18),rgba(255,255,255,.05)),linear-gradient(135deg,#e9f2ec 0%,#f8f0df 54%,#eef3f8 100%);position:relative;display:flex;flex-direction:column;justify-content:space-between}}
    .article-cover::after{{content:"";position:absolute;inset:15px;border:1px solid rgba(66,110,96,.22);border-radius:14px;pointer-events:none}}
    .eyebrow{{position:relative;z-index:1;width:fit-content;padding:7px 12px;border-radius:999px;background:var(--sage-dark);color:#fff;font-size:13px;font-weight:900}}
    .title-box{{position:relative;z-index:1;margin-top:34px}} .article-title-en{{margin:0;font-family:Georgia,"Times New Roman",serif;max-width:12em;font-size:clamp(30px,9vw,43px);line-height:1.06;letter-spacing:-.035em;color:var(--ink)}} .article-title-zh{{margin-top:14px;max-width:18em;font-size:16px;line-height:1.55;color:#536171;font-weight:700}}
    .meta-grid{{display:grid;grid-template-columns:1fr;gap:10px;padding:14px}} .meta-box{{background:var(--paper);border:1px solid var(--line);border-radius:var(--radius-sm);padding:12px}} .meta-box span{{display:block;color:var(--muted);font-size:12px;margin-bottom:6px}} .meta-box b{{font-size:14.5px;line-height:1.4}}
    .tags{{display:flex;gap:8px;flex-wrap:wrap;padding:0 14px 14px}} .tag{{padding:6px 10px;border-radius:999px;font-size:12px;font-weight:900;background:var(--sage-soft);color:var(--sage-dark)}} .tag.level{{background:var(--clay-soft);color:#9b4e35}} .tag.topic{{background:var(--blue-soft);color:var(--blue)}}
    .summary{{margin:0 14px 16px;padding:13px 14px;border-left:4px solid var(--clay);background:#fff8f2;border-radius:10px;color:#37434a;line-height:1.7;font-size:14.5px;white-space:pre-wrap}}
    .section{{padding:18px}} .section-head{{display:flex;align-items:baseline;justify-content:space-between;gap:12px;margin-bottom:12px}} .section h2{{margin:0;font-size:21px;letter-spacing:-.02em}} .mini-label{{color:var(--muted);font-size:12px;white-space:nowrap}}
    .para-card,.review-box{{border:1px solid var(--line);background:var(--paper);border-radius:var(--radius-sm);padding:14px}} .translation,.review-box p{{color:var(--muted);line-height:1.65;font-size:14.5px}}
    .study-route{{display:grid;gap:8px}} .route-step{{display:flex;gap:10px;align-items:flex-start;border:1px solid var(--line);background:var(--paper);border-radius:12px;padding:10px}} .route-num{{flex:0 0 auto;width:28px;height:28px;border-radius:50%;background:var(--sage-soft);color:var(--sage-dark);display:grid;place-items:center;font-weight:900;font-size:13px}} .route-step b{{display:block;font-size:14px;margin-bottom:3px}} .route-step span{{color:var(--muted);font-size:13px;line-height:1.45}}
    .para-list,.expression-list,.pattern-list,.review-grid,.history-list{{display:grid;gap:10px}} .para-title{{color:var(--sage-dark);font-weight:900;margin-bottom:10px;font-size:14px}} .para-divider{{height:1px;background:var(--line);margin:12px 0}} .english{{margin:0;font-family:Georgia,"Times New Roman",serif;font-size:18.5px;line-height:1.78;color:#25323a}}
    .hl-term{{color:var(--sage-dark);background:rgba(127,163,145,.16);border-bottom:1px solid rgba(66,110,96,.38);padding:0 2px;border-radius:4px;cursor:pointer}}
    .expression{{display:grid;grid-template-columns:auto 1fr;column-gap:10px;row-gap:3px;align-items:start;border:1px solid var(--line);background:var(--paper);border-radius:10px;padding:9px 10px;cursor:pointer}} .expr-label{{grid-row:1 / span 2;width:fit-content;padding:4px 7px;border-radius:999px;background:var(--blue-soft);color:var(--blue);font-size:12px;font-weight:900;white-space:nowrap}} .expression b{{color:var(--sage-dark);line-height:1.35;font-size:15.5px}} .expr-meaning{{color:var(--muted);line-height:1.5;font-size:14px}}
    .pattern-card{{border:1px solid #f0d6c9;background:#fff8f2;border-radius:13px;padding:12px}} .pattern-card b{{display:block;font-size:15px;margin-bottom:8px}} .pattern-card p{{margin:7px 0;line-height:1.6;font-size:14.5px}} .pattern-card .original{{color:#536171;border-left:3px solid var(--clay);padding-left:10px}} .pattern-card .pattern{{color:#25323a;font-family:Georgia,"Times New Roman",serif;font-size:16px}} .pattern-card .meaning{{color:#9b4e35}} .pattern-card .example{{color:#414b51}} .pattern-card small{{display:block;color:var(--muted);margin-top:8px;line-height:1.55}}
    .review-box{{border:1px dashed #b9c7c0;background:#fbfdfb}} .review-box b{{display:block;margin-bottom:8px;font-size:15px}}
    .history-tabs{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px;margin-bottom:12px}} .history-tab{{border:1px solid var(--line);background:var(--paper);color:var(--sage-dark);border-radius:999px;padding:8px 4px;font-size:13px;font-weight:900;cursor:pointer}} .history-tab.is-active{{background:var(--sage-dark);color:#fff;border-color:var(--sage-dark)}} .history-panels{{display:grid;gap:10px}} .topic-group{{display:none;gap:8px;margin-bottom:4px}} .topic-group.is-active{{display:grid}} .topic-group h3{{font-size:16px;margin:0;color:var(--sage-dark)}} .history-item{{display:block;text-decoration:none;color:var(--ink);border:1px solid var(--line);background:var(--paper);border-radius:12px;padding:10px}} .history-item b{{font-size:14.5px;line-height:1.35}} .history-empty{{color:var(--muted);font-size:14px;border:1px dashed var(--line);border-radius:12px;padding:10px;background:var(--paper)}}
    .source-link{{display:inline-flex;width:fit-content;margin-top:12px;text-decoration:none;color:var(--sage-dark);background:var(--sage-soft);border:1px solid rgba(66,110,96,.18);border-radius:999px;padding:8px 11px;font-size:13px;font-weight:900}}
    .tip{{position:fixed;left:14px;right:14px;bottom:16px;z-index:80;background:#1d252c;color:#fff;border-radius:16px;padding:12px 14px;box-shadow:0 14px 38px rgba(0,0,0,.22);line-height:1.6;display:none;max-width:452px;margin:0 auto}} .tip b{{color:#f1c66d}} .bottom-note{{color:var(--muted);font-size:12px;line-height:1.7;text-align:center;padding:20px 6px 2px}}
    @media (min-width:420px){{.meta-grid{{grid-template-columns:repeat(3,minmax(0,1fr))}}}} @media (max-width:360px){{.hero h1{{font-size:38px}}.article-title-en{{font-size:29px}}.section{{padding:16px}}.expression{{grid-template-columns:1fr}}.expr-label{{grid-row:auto}}}}
  </style>
</head>
<body>
  <main class="phone-shell">
    <header class="hero">
      <div class="brand-row"><div class="brand-mark">HL</div><div class="date-pill">Today · {today_dot}</div></div>
      <h1>Healing Lab<br>每日外刊</h1>
      <p class="subtitle">每天一篇短外刊，练阅读、表达和语感。</p>
    </header>

    <nav class="quick-nav" aria-label="页面导航">
      <a href="#article">今日文章</a><a href="#route">路线</a><a href="#text">精读</a><a href="#patterns">表达句式</a><a href="#expressions">重点表达</a><a href="#archive">历史</a>
    </nav>

    <div class="section-stack">
      <article class="card" id="article">
        <div class="article-cover"><span class="eyebrow">今日文章卡片</span><div class="title-box"><h2 class="article-title-en">{esc(title_raw)}</h2><div class="article-title-zh">{esc(title_cn)}</div></div></div>
        <div class="meta-grid"><div class="meta-box"><span>来源</span><b>{esc(source or "Daily Reading")}</b></div><div class="meta-box"><span>日期</span><b>{esc(pub_date)}</b></div><div class="meta-box"><span>难度</span><b>{esc(level)}</b></div></div>
        <div class="tags"><span class="tag level">{esc(level)}</span><span class="tag topic">{esc(topic)}</span><span class="tag">Patterns</span><span class="tag">Expressions</span></div>
        <p class="summary">{esc(overview or "今天这篇适合积累真实外刊表达、观点句和可复述素材。")}</p>
      </article>

      <section class="card section" id="route">
        <div class="section-head"><h2>今天这样学</h2><span class="mini-label">3 steps</span></div>
        <div class="study-route">
          <div class="route-step"><span class="route-num">01</span><div><b>先抓主题</b><span>看中文摘要，知道文章讲什么。</span></div></div>
          <div class="route-step"><span class="route-num">02</span><div><b>精读 2 段</b><span>只读核心段，不被全文吓退。</span></div></div>
          <div class="route-step"><span class="route-num">03</span><div><b>拆句式 + 记表达</b><span>优先记能直接复用的英文结构。</span></div></div>
        </div>
      </section>

      <section class="card section" id="text"><div class="section-head"><h2>今日精读</h2><span class="mini-label">Original + Meaning</span></div><div class="para-list">{paragraph_html}</div></section>

      <section class="card section" id="patterns"><div class="section-head"><h2>表达句式</h2><span class="mini-label">Sentence Patterns</span></div><div class="pattern-list">{patterns_html}</div></section>

      <section class="card section" id="expressions"><div class="section-head"><h2>重点表达</h2><span class="mini-label">Useful Expressions</span></div><div class="expression-list">{expression_html}</div></section>

      <section class="card section" id="review"><div class="section-head"><h2>今日复盘</h2><span class="mini-label">Daily Review</span></div><div class="review-grid"><div class="review-box"><b>今天最值得记住</b><p>{esc(best_expr)}<br>{esc(best_meaning)}</p></div><div class="review-box"><b>怎么用</b><p>选一个表达句式，改写成自己的生活、学习或工作场景。</p></div></div>{source_link_html}</section>

      <section class="card section" id="archive"><div class="section-head"><h2>历史文章</h2><span class="mini-label">Archive by topic</span></div>{history_html}</section>
    </div>
    <p class="bottom-note">Healing Lab Daily Reading · Mobile Learning Card Page</p>
  </main>
  <div class="tip" id="tip"></div>
  <script>
    function escText(s){{return String(s||'').replace(/[&<>"']/g,function(c){{return {{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c];}});}}
    function showTip(term,meaning){{var tip=document.getElementById('tip');tip.innerHTML='<b>'+escText(term)+'</b><br>'+escText(meaning||'暂无释义');tip.style.display='block';clearTimeout(window.__tipTimer);window.__tipTimer=setTimeout(function(){{tip.style.display='none';}},2800);}}
    document.body.addEventListener('click',function(e){{var node=e.target.closest('.hl-term,.expression');if(!node)return;showTip(node.getAttribute('data-term')||'',node.getAttribute('data-meaning')||'');}});
    document.body.addEventListener('click',function(e){{var btn=e.target.closest('.history-tab');if(!btn)return;var key=btn.getAttribute('data-history-filter');document.querySelectorAll('.history-tab').forEach(function(x){{x.classList.toggle('is-active',x===btn);}});document.querySelectorAll('.topic-group[data-history-topic]').forEach(function(panel){{panel.classList.toggle('is-active',panel.getAttribute('data-history-topic')===key);}});}});
  </script>
</body>
</html>"""
    return page

def write_outputs(article, selected_paragraphs, rejected_log, article_reject_log, cfg):
    OUTPUT_DIR.mkdir(exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")

    main_txt = OUTPUT_DIR / f"one_article_grouped_practice_{today}.txt"
    latest_txt = OUTPUT_DIR / "latest.txt"
    html_path = OUTPUT_DIR / f"one_article_grouped_practice_{today}.html"
    latest_html = OUTPUT_DIR / "latest.html"
    xhs_html_path = OUTPUT_DIR / f"xhs_cards_{today}.html"
    latest_xhs_html = OUTPUT_DIR / "xhs.html"
    debug_path = OUTPUT_DIR / f"selection_debug_{today}.txt"

    title_zh = translate_text(article["title"], cfg.get("translate_to_chinese", True))
    paragraph_texts = [p["text"] for p in selected_paragraphs]

    paragraph_rows = []
    all_keywords = []

    for idx, p in enumerate(selected_paragraphs, 1):
        raw_text = p["text"]
        expressions = []
        keywords = extract_key_words(raw_text, int(cfg.get("keyword_count_per_paragraph", 8)))
        highlight_terms = collect_highlight_terms(raw_text, expressions, keywords)
        marked_txt = mark_text_for_txt(raw_text, highlight_terms)
        marked_html = mark_text_for_html(raw_text, highlight_terms)
        zh = translate_text(raw_text, cfg.get("translate_to_chinese", True))

        if "difficulty_profile" in globals():
            prof = difficulty_profile(raw_text)
        else:
            prof = {
                "avg_sentence_words": avg_sentence_words(raw_text),
                "long_word_count": 0,
                "expression_count": len(expression_hits(raw_text)),
            }

        paragraph_rows.append({
            "idx": idx,
            "raw": raw_text,
            "marked_txt": marked_txt,
            "marked_html": marked_html,
            "highlight_terms": highlight_terms,
            "zh": zh,
            "prof": prof,
        })

        for k in keywords:
            if not any(x.lower() == k.lower() for x in all_keywords):
                all_keywords.append(k)

    total_paragraph_count = len(paragraph_rows)
    total_keyword_count = len(all_keywords)
    click_word_meanings = build_click_word_map(paragraph_texts, all_keywords, cfg)
    quote_raw, quote_translated = pick_today_quote(paragraph_texts)
    quote_en = esc(quote_raw) if quote_raw else "今日截取段落暂无适合摘抄的句子。"
    quote_zh = esc(quote_translated) if quote_translated else ""

    lines = [
        f"今日外刊表达练习｜{today}",
        "",
        "英文标题：",
        article["title"],
        "",
        "中文标题：",
        title_zh,
        "",
        "来源：",
        f"{article['source']}｜{article['link']}",
        "",
        "文章发布日期：",
        display_publish_date(article) or "未知",
        "",
        "难度定位：",
        "CET4+｜大学英语四级以上，偏真实外刊表达，不选太简单口水段，也避开专业硬新闻难文。",
        "",
        "=" * 60,
        "",
        "一、截取原文",
        f"共截取 {total_paragraph_count} 段｜重点词/词组 {total_keyword_count} 个｜蓝灰色英文词可点击查看中文",
        "",
    ]

    for row in paragraph_rows:
        lines.extend([
            row["marked_txt"],
            "",
        ])

    lines.extend([
        "二、重点词语/词组",
        "",
        *format_keywords_txt(all_keywords),
        "",
        "三、中文翻译（网页中为每段折叠显示）",
        "",
    ])

    for row in paragraph_rows:
        lines.extend([
            f"段落 {row['idx']}：",
            row["zh"],
            "",
        ])

    english_html = []
    for row in paragraph_rows:
        prof = row["prof"]
        clickable_html = clickable_text_for_html(row["raw"], row.get("highlight_terms", []), click_word_meanings)
        english_html.append(f"""
<div class="para-block">
<div class="box english">{clickable_html}</div>
<details class="translation-fold">
  <summary>展开中文翻译</summary>
  <div class="fold-content">{esc(row['zh'])}</div>
</details>
</div>
""")

    vocab_html = format_keywords_html(all_keywords)

    word_map_json = json.dumps(click_word_meanings, ensure_ascii=False)

    html_doc = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>今日外刊表达练习</title>
<style>
body {{
  margin: 0;
  padding: 10px;
  background: #f6f6f6;
  color: #222;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", Arial, sans-serif;
  line-height: 1.62;
}}
.header, .card {{
  max-width: 940px;
  margin: 9px auto;
  background: #fff;
  border-radius: 16px;
  padding: 14px;
  box-shadow: 0 2px 14px rgba(0,0,0,.06);
}}
h1 {{ font-size: 22px; margin: 0 0 8px; }}
h2 {{ font-size: 18px; margin: 0 0 10px; }}
h3 {{ font-size: 16px; margin: 12px 0 6px; }}
.meta {{ color: #666; font-size: 13px; margin: 4px 0 6px; }}
.level {{
  display: inline-block;
  background: #f2efe8;
  color: #333;
  border-radius: 999px;
  padding: 4px 9px;
  font-size: 13px;
}}
.action-row {{
  margin-top: 12px;
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}}
.pdf-btn {{
  border: 1px solid #d8d1bf;
  background: #fffdf7;
  color: #333;
  border-radius: 999px;
  padding: 7px 12px;
  font-size: 14px;
  cursor: pointer;
}}
.pdf-btn:active {{
  background: #f3ead5;
}}
.quote-card {{
  background: #fffdf8;
}}
.quote-en {{
  font-size: 16px;
  line-height: 1.75;
  font-weight: 600;
  color: #26333d;
}}
.quote-zh {{
  margin-top: 8px;
  color: #6b5f50;
  line-height: 1.7;
}}
.sentence-card {{
  background: #fafafa;
  border-left: 4px solid #c8d7df;
  border-radius: 12px;
  padding: 11px;
  margin: 10px 0;
}}
.sentence-title {{
  font-weight: 700;
  margin-bottom: 6px;
  color: #2d5263;
}}
.sentence-original {{
  font-size: 15px;
  line-height: 1.65;
  margin-bottom: 8px;
}}
.analysis-line {{
  font-size: 14px;
  margin: 5px 0;
}}
.chunk-list {{
  margin-top: 4px;
  padding-left: 20px;
}}
.chunk-label {{
  display: inline-block;
  color: #315c6f;
  font-weight: 700;
}}
.box {{
  background: #fafafa;
  border-left: 4px solid #ddd;
  padding: 10px;
  border-radius: 10px;
  font-size: 16px;
}}
.english {{
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", Arial, sans-serif;
  font-size: 16px;
}}
.zh {{ background: #fffdf7; }}
li {{ margin-bottom: 7px; }}
a {{ color: #185abc; text-decoration: none; }}
.source-tag {{
  display: inline-block;
  font-size: 12px;
  color: #666;
  background: #eee;
  border-radius: 999px;
  padding: 1px 7px;
}}
mark {{
  background: #fff0a6;
  padding: 0 2px;
  border-radius: 4px;
}}
.para-block, .trans-block {{ margin-bottom: 14px; }}
.vocab-list {{
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}}
.vocab-chip {{
  display: inline-block;
  background: #f7f4ec;
  color: #222;
  border: 1px solid #eee2c8;
  border-radius: 999px;
  padding: 4px 9px;
  font-size: 13px;
  line-height: 1.35;
}}
.vocab-chip strong {{
  font-size: 13px;
}}
.click-word {{
  display: inline;
  font: inherit;
  font-size: inherit;
  line-height: inherit;
  color: #315c6f;
  background: transparent;
  border: 0;
  border-radius: 0;
  padding: 0;
  cursor: pointer;
  text-decoration: none;
}}
.click-word.key-word {{
  display: inline;
  font: inherit;
  font-size: inherit;
  line-height: inherit;
  color: #315c6f;
  background: transparent;
  border: 0;
  border-radius: 0;
  padding: 0;
  text-decoration: none;
}}
.click-word:active {{
  background: #eef5f7;
  border-radius: 3px;
}}
.word-popup {{
  position: fixed;
  left: 12px;
  right: 12px;
  bottom: 12px;
  z-index: 9999;
  background: #222;
  color: #fff;
  border-radius: 16px;
  padding: 13px 14px;
  box-shadow: 0 6px 24px rgba(0,0,0,.25);
  font-size: 15px;
}}
.word-popup.hidden {{
  display: none;
}}
.popup-word {{
  font-weight: 700;
  font-size: 18px;
  margin-bottom: 5px;
}}
.popup-meaning {{
  line-height: 1.5;
  color: #f3f3f3;
}}
.popup-close {{
  position: absolute;
  top: 7px;
  right: 10px;
  background: transparent;
  color: #fff;
  border: 0;
  font-size: 22px;
  cursor: pointer;
}}
.history-list {{
  padding-left: 20px;
}}
.history-list li {{
  margin: 7px 0;
}}
details.translation-fold {{
  margin-top: 8px;
  background: #fffdf7;
  border: 1px solid #f0e8cf;
  border-radius: 10px;
  padding: 8px 10px;
}}
details.translation-fold summary {{
  cursor: pointer;
  color: #6a5200;
  font-size: 14px;
  user-select: none;
}}
details.translation-fold .fold-content {{
  margin-top: 8px;
  color: #333;
  font-size: 15px;
  line-height: 1.65;
}}
@media (max-width: 600px) {{
  body {{ padding: 7px; }}
  .header, .card {{ padding: 12px; border-radius: 14px; }}
  h1 {{ font-size: 20px; }}
  h2 {{ font-size: 17px; }}
  .box {{ font-size: 15.8px; }}
  .english {{ font-size: 16.8px; }}
}}

@page {{
  size: A4;
  margin: 12mm;
}}

@media print {{
  body {{
    background: #fff !important;
    padding: 0 !important;
    color: #000 !important;
  }}
  .header, .card {{
    max-width: none !important;
    margin: 0 0 12px !important;
    box-shadow: none !important;
    border-radius: 0 !important;
    border: 0 !important;
    page-break-inside: avoid;
  }}
  .pdf-btn, .action-row, .word-popup, .print-tip {{
    display: none !important;
  }}
  .english {{
    font-size: 17px !important;
    line-height: 1.7 !important;
  }}
  .box {{
    font-size: 16px !important;
  }}

  details.translation-fold {{
    border: 1px solid #ddd !important;
    background: #fff !important;
  }}
  details.translation-fold[open] {{
    page-break-inside: avoid;
  }}
  a {{
    color: #000 !important;
    text-decoration: none !important;
  }}
  .click-word {{
    color: #000 !important;
  }}
  .vocab-chip {{
    border: 1px solid #ddd !important;
    background: #fff !important;
  }}
}}

</style>
</head>
<body>
<div class="header">
<h1>今日外刊表达练习｜{today}</h1>
<p><strong>英文标题：</strong>{esc(article['title'])}</p>
<p><strong>中文标题：</strong>{esc(title_zh)}</p>
<p class="meta">来源：{esc(article['source'])}｜文章发布日期：{esc(display_publish_date(article) or "未知")}｜<a href="{esc(article['link'])}" target="_blank">打开原文</a></p>
<p class="level">难度定位：CET4+｜大学英语四级以上｜近90天主题质量优先精选</p>
<div class="action-row">
  <button class="pdf-btn" onclick="exportPDF()">导出 PDF</button>
  <a class="pdf-btn" href="xhs.html" target="_blank">图文版</a>
</div>
<p class="meta print-tip">PDF 左下角网址是浏览器“页眉和页脚”，打印时关闭它即可去掉。</p>
</div>

<section class="card">
<h2>一、截取原文</h2>
<p class="meta">共截取 {total_paragraph_count} 段｜重点词/词组 {total_keyword_count} 个｜蓝灰色英文词可点击查看中文</p>
{''.join(english_html)}
</section>

<section class="card">
<h2>二、重点词语/词组</h2>
<div class="vocab-list">{vocab_html}</div>
</section>

<section class="card quote-card">
<h2>三、今日一句摘抄</h2>
<div class="quote-en">{quote_en}</div>
<div class="quote-zh">{quote_zh}</div>
</section>

<div id="wordPopup" class="word-popup hidden">
<button class="popup-close" onclick="document.getElementById('wordPopup').classList.add('hidden')">×</button>
<div id="popupWord" class="popup-word"></div>
<div id="popupMeaning" class="popup-meaning"></div>
</div>

<script>
function exportPDF() {{
  const folds = document.querySelectorAll('details.translation-fold');
  folds.forEach(function(el) {{ el.setAttribute('open', 'open'); }});
  setTimeout(function() {{ window.print(); }}, 120);
}}

const WORD_MEANINGS = {word_map_json};
document.addEventListener('click', function(e) {{
  const target = e.target.closest('.click-word');
  if (!target) return;
  const key = (target.dataset.word || '').toLowerCase();
  const word = target.textContent || key;
  const meaning = WORD_MEANINGS[key] || '暂无中文解释，可结合上下文理解。';
  document.getElementById('popupWord').textContent = word;
  document.getElementById('popupMeaning').textContent = meaning;
  document.getElementById('wordPopup').classList.remove('hidden');
}});
</script>

</body>
</html>
"""

    cover_image = save_article_cover_image(article, today)
    xhs_doc = build_xhs_export_page(article, title_zh, paragraph_rows, all_keywords, quote_raw, quote_translated, today, cover_image)

    debug_lines = [
        f"筛选调试记录｜{today}",
        "",
        "最终文章：",
        article["title"],
        article["link"],
        "",
        "文章跳过记录：",
        "",
    ]

    for row in article_reject_log[:80]:
        debug_lines.extend([
            f"文章：{row.get('title')}",
            f"来源：{row.get('source')}",
            f"原因：{row.get('reason')}",
            "-" * 50,
        ])

    debug_lines.extend(["", "本篇段落拒绝记录：", ""])
    for r in rejected_log[:80]:
        debug_lines.extend([
            f"原文第 {r['index']} 段",
            f"原因：{r['reason']}",
            f"预览：{r['preview']}",
            "-" * 50,
        ])

    content_txt = "\n".join(lines)
    main_txt.write_text(content_txt, encoding="utf-8-sig")
    latest_txt.write_text(content_txt, encoding="utf-8-sig")
    html_path.write_text(html_doc, encoding="utf-8-sig")
    xhs_html_path.write_text(xhs_doc, encoding="utf-8-sig")
    latest_xhs_html.write_text(xhs_doc, encoding="utf-8-sig")
    debug_path.write_text("\n".join(debug_lines), encoding="utf-8-sig")


    # 历史存档：每天一个独立页面。
    # 为了避免手动多次运行把旧历史页改乱，如果当天历史文件已存在，不覆盖。
    archive_dir = ensure_archive_dirs()
    archive_html = archive_dir / f"day-{today}.html"
    archive_txt = archive_dir / f"day-{today}.txt"
    archive_xhs_html = archive_dir / f"day-{today}-xhs.html"

    if not archive_html.exists():
        archive_html.write_text(html_doc, encoding="utf-8-sig")
    if not archive_txt.exists():
        archive_txt.write_text(content_txt, encoding="utf-8-sig")
    if not archive_xhs_html.exists():
        archive_xhs_html.write_text(xhs_doc, encoding="utf-8-sig")

    # 同页入口：index.html = 今日练习 + 历史目录；latest.html 同步保持一致。
    unified_html = build_archive_index(today, article["title"], title_zh, html_doc)
    latest_html.write_text(unified_html, encoding="utf-8-sig")

    # 自动上传：默认关闭。需要在 config.json 里开启并填写密码。
    upload_ok, upload_msg = auto_upload_outputs(cfg, today)
    upload_log = OUTPUT_DIR / f"upload_log_{today}.txt"
    upload_log.write_text(upload_msg, encoding="utf-8-sig")

    # 记录已使用文章，避免后续日期重复选择同一篇。
    save_used_article(article)

    return main_txt, latest_txt, html_path, latest_html, latest_xhs_html, OUTPUT_DIR / "index.html", archive_html, archive_txt, archive_xhs_html, upload_log, debug_path

def write_no_article_report(article_reject_log):
    """
    V25：
    如果今天确实没有合格文章，也要生成一个干净的占位页面，
    避免服务器继续发布昨天/旧版本的 HTML，让页面看起来没更新。
    """
    OUTPUT_DIR.mkdir(exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")

    path = OUTPUT_DIR / f"no_suitable_one_article_{today}.txt"
    latest_txt = OUTPUT_DIR / "latest.txt"
    latest_html = OUTPUT_DIR / "latest.html"
    index_html = OUTPUT_DIR / "index.html"

    lines = [
        f"今天没有找到合格的外刊练习材料｜{today}",
        "",
        "原因：今天的候选文章没有通过安全性、可读性、段落长度或主题筛选。",
        "",
        "这不是程序错误。V25 已经启用兜底筛选：",
        "1. 先找未使用过的新文章。",
        "2. 如果没有，再允许复用高质量主题候选。",
        "3. 如果仍没有，发布这个干净提示页，避免旧页面残留。",
        "",
        "文章跳过记录：",
        "",
    ]

    for row in article_reject_log[:120]:
        lines.extend([
            f"文章：{row.get('title')}",
            f"来源：{row.get('source')}",
            f"原因：{row.get('reason')}",
            "-" * 50,
        ])

    txt = "\n".join(lines)
    path.write_text(txt, encoding="utf-8-sig")
    latest_txt.write_text(txt, encoding="utf-8-sig")

    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>今日外刊｜暂无合格文章</title>
<style>
body {{
  margin: 0;
  padding: 14px;
  background: #f6f6f6;
  color: #222;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", Arial, sans-serif;
  line-height: 1.65;
}}
.card {{
  max-width: 900px;
  margin: 18px auto;
  background: #fff;
  border-radius: 18px;
  padding: 18px;
  box-shadow: 0 2px 14px rgba(0,0,0,.06);
}}
h1 {{ font-size: 22px; margin: 0 0 10px; }}
p {{ margin: 8px 0; }}
.small {{ color: #666; font-size: 13px; }}
pre {{
  white-space: pre-wrap;
  background: #fafafa;
  border-radius: 12px;
  padding: 12px;
  font-size: 13px;
  line-height: 1.55;
}}
</style>
</head>
<body>
<div class="card">
<h1>今日外刊｜暂无合格文章｜{today}</h1>
<p>今天没有找到合格文章。页面已正常更新为干净提示页，不会再显示旧版按钮或旧版功能。</p>
<p class="small">V25 已经启用兜底筛选：先找新文章；没有新文章时，允许复用高质量主题候选；仍不合格时才显示本页。</p>
<h2>筛选记录</h2>
<pre>{esc(txt[:8000])}</pre>
</div>
</body>
</html>
"""
    latest_html.write_text(html, encoding="utf-8-sig")
    index_html.write_text(html, encoding="utf-8-sig")
    (OUTPUT_DIR / "xhs.html").write_text(html, encoding="utf-8-sig")

    return path

def paragraph_item_to_text(p):
    """
    兼容两种正文段落格式：
    1. "paragraph text"
    2. {"text": "paragraph text", ...}
    """
    if isinstance(p, str):
        return p
    if isinstance(p, dict):
        return str(p.get("text") or p.get("paragraph") or p.get("content") or "")
    return str(p or "")


def paragraph_list_to_text(paragraphs, limit=None):
    if not paragraphs:
        return ""
    data = paragraphs[:limit] if limit else paragraphs
    return " ".join(paragraph_item_to_text(p) for p in data if paragraph_item_to_text(p).strip())



def body_second_filter_reject_reason(paragraphs, item, cfg):
    """
    V15.2：
    标题摘要可能看不出问题，抓到正文后再筛一次。
    这一步专门拦截正文里才出现的沉重/负面/治疗/恐惧类内容。
    """
    if not cfg.get("body_second_filter_enabled", True):
        return False, ""

    n = int(cfg.get("body_filter_scan_paragraphs", 8))
    body_text = paragraph_list_to_text(paragraphs, n)
    combined = f"{item.get('title', '')} {item.get('summary', '')} {body_text}"

    unsafe_bad, unsafe_reason = is_unsafe_default_reading_topic(combined)
    if unsafe_bad:
        return True, f"正文不适合作为默认每日阅读：{unsafe_reason}"

    quiz_bad, quiz_reason = is_quiz_or_trivia_topic(combined)
    if quiz_bad:
        return True, f"正文为问答/测试/冷知识题材：{quiz_reason}"

    hard_bad, hard_reason = is_hard_news_or_market_topic(combined)
    if hard_bad:
        return True, f"正文偏硬新闻/市场/政治司法：{hard_reason}"

    negative_bad, negative_reason = heavy_negative_reject_reason(combined, cfg)
    if negative_bad:
        return True, f"正文偏沉重/负面：{negative_reason}"

    if is_recipe_or_cooking_instructions(combined):
        return True, "正文为菜谱/烹饪步骤"

    final_bad, final_reason = final_clean_topic_reject_reason(combined, item.get("source", ""), cfg)
    if final_bad:
        return True, f"正文二次筛选未通过：{final_reason}"

    return False, ""



def force_select_readable_paragraphs(paragraphs, cfg, max_items=3):
    """
    V24 兜底段落选择：
    当严格段落筛选没有通过时，只保留基础安全、长度和可读性，
    防止明明是好文章却因为规则太细而整天无文章。
    """
    selected = []
    rejected = []

    for i, p in enumerate(paragraphs, 1):
        raw = paragraph_item_to_text(p)
        t = clean_text(raw)
        wc = word_count(t)

        if wc < 28:
            rejected.append({"index": i, "reason": f"兜底仍太短：{wc}词", "preview": t[:160]})
            continue
        if wc > 260:
            rejected.append({"index": i, "reason": f"兜底仍太长：{wc}词", "preview": t[:160]})
            continue
        if is_unsafe_default_reading_topic(t)[0]:
            rejected.append({"index": i, "reason": "兜底安全过滤未通过", "preview": t[:160]})
            continue
        if is_recipe_or_cooking_instructions(t):
            rejected.append({"index": i, "reason": "兜底过滤菜谱/步骤", "preview": t[:160]})
            continue

        numbers = re.findall(r"\b\d+[\d,.%]*\b", t)
        if len(numbers) >= 10:
            rejected.append({"index": i, "reason": f"兜底过滤数字过多：{len(numbers)}", "preview": t[:160]})
            continue

        selected.append({
            "index": p.get("index", i) if isinstance(p, dict) else i,
            "text": t,
            "score": score_paragraph({"text": t, "index": i}, cfg),
        })

        if len(selected) >= max_items:
            break

    return selected, rejected


def pick_article_from_items(items, cfg, article_reject_log, allow_used=False, relaxed=False, label="正常筛选"):
    """
    V25：
    把选文逻辑集中到一个函数，支持第二轮兜底。
    第一轮：不重复 + 原筛选。
    第二轮：允许复用高质量主题文章 + 放宽段落筛选，但仍保留安全底线。
    """
    try_limit = int(cfg.get("fallback_articles_to_try" if relaxed else "articles_to_try", cfg.get("articles_to_try", 260)))

    for item in items[:try_limit]:
        print(f"\n[{label}] 检查文章：", item["title"])
        age = article_age_days(item)
        age_text = f"{age}天前" if age is not None else "未知日期"
        print("来源：", item["source"], "文章分：", item.get("score", 0), "发布时间：", display_publish_date(item) or age_text)

        if not within_max_age(item, cfg):
            article_reject_log.append({"title": item["title"], "source": item["source"], "reason": f"{label}：超过回溯范围"})
            continue

        used = is_used_article(item)
        if used and not allow_used:
            print("跳过：已使用过，避免每日重复")
            article_reject_log.append({"title": item["title"], "source": item["source"], "reason": f"{label}：已使用过，跳过，避免每日重复"})
            continue
        if used and allow_used:
            print("兜底：允许复用已使用过的高质量文章")

        combined = f"{item['title']} {item.get('summary', '')}"

        xhs_bad, xhs_reason = xhs_topic_reject_reason(combined, item.get("source", ""), cfg)
        if xhs_bad:
            article_reject_log.append({"title": item["title"], "source": item["source"], "reason": f"{label}：{xhs_reason}"})
            continue

        # 兜底时不再让过宽的主题/主编过滤误伤文章，但保留基础安全。
        if not relaxed:
            if not (item.get("theme_seed") and cfg.get("theme_seed_bypass_generic_filters", True)):
                hard_bad, reason = contains_any(combined, cfg["hard_avoid"])
                if hard_bad:
                    article_reject_log.append({"title": item["title"], "source": item["source"], "reason": f"{label}：硬排除词：{reason}"})
                    continue

                soft_bad, reason = contains_any(combined, cfg["soft_avoid"])
                if soft_bad:
                    article_reject_log.append({"title": item["title"], "source": item["source"], "reason": f"{label}：软排除词：{reason}"})
                    continue

            if not (item.get("theme_seed") and cfg.get("theme_seed_bypass_generic_filters", True)):
                final_bad, reason = final_clean_topic_reject_reason(combined, item.get("source", ""), cfg)
                if final_bad:
                    article_reject_log.append({"title": item["title"], "source": item["source"], "reason": f"{label}：{reason}"})
                    continue

            if not (item.get("theme_seed") and cfg.get("theme_seed_bypass_generic_filters", True)):
                editorial_bad, reason = editorial_reject_reason(combined, cfg)
                if editorial_bad:
                    article_reject_log.append({"title": item["title"], "source": item["source"], "reason": f"{label}：{reason}"})
                    continue

            min_editorial_score = int(cfg.get("editorial_min_article_score", 0))
            if item.get("score", 0) < min_editorial_score:
                article_reject_log.append({
                    "title": item["title"],
                    "source": item["source"],
                    "reason": f"{label}：主编评分偏低：{item.get('score', 0)} < {min_editorial_score}"
                })
                continue

            pn_bad, reason = too_many_proper_nouns(combined)
            if pn_bad and not item.get("theme_seed") and "mit news" not in item.get("source", "").lower():
                article_reject_log.append({"title": item["title"], "source": item["source"], "reason": f"{label}：{reason}"})
                continue
        else:
            safe_bad, safe_reason = is_unsafe_default_reading_topic(combined)
            if safe_bad:
                article_reject_log.append({"title": item["title"], "source": item["source"], "reason": f"{label}：基础安全过滤：{safe_reason}"})
                continue
            if is_recipe_or_cooking_instructions(combined):
                article_reject_log.append({"title": item["title"], "source": item["source"], "reason": f"{label}：菜谱/步骤题材"})
                continue

        paragraphs = fetch_article_paragraphs(item["link"], max_count=int(cfg.get("paragraphs_to_scan_per_article", 16)))
        if not paragraphs:
            article_reject_log.append({"title": item["title"], "source": item["source"], "reason": f"{label}：没有抓到正文段落"})
            continue

        body_text = " ".join([paragraph_item_to_text(p) for p in paragraphs[:8]])
        if relaxed:
            safe_bad, safe_reason = is_unsafe_default_reading_topic(combined + " " + body_text)
            if safe_bad:
                article_reject_log.append({"title": item["title"], "source": item["source"], "reason": f"{label}：正文安全过滤：{safe_reason}"})
                continue
            if is_recipe_or_cooking_instructions(body_text):
                article_reject_log.append({"title": item["title"], "source": item["source"], "reason": f"{label}：正文为菜谱/步骤"})
                continue
        else:
            if item.get("theme_seed") and cfg.get("theme_seed_bypass_body_second_filter", True):
                body_bad, body_reason = False, ""
            else:
                body_bad, body_reason = body_second_filter_reject_reason(paragraphs, item, cfg)

            if body_bad:
                article_reject_log.append({"title": item["title"], "source": item["source"], "reason": f"{label}：{body_reason}"})
                continue

        if relaxed:
            selected, rejected = force_select_readable_paragraphs(paragraphs, cfg, max_items=int(cfg.get("selected_paragraph_count", 3)))
        elif item.get("theme_seed") and cfg.get("theme_seed_relaxed_paragraph_filter", True):
            selected, rejected = select_paragraphs_for_theme_seed(paragraphs, cfg)
        else:
            selected, rejected = select_paragraphs_from_one_article(paragraphs, cfg)

        min_para_needed = int(cfg.get("fallback_min_required_paragraphs", 2)) if relaxed else (
            int(cfg.get("theme_seed_min_required_paragraphs", 2)) if item.get("theme_seed") else int(cfg.get("min_required_paragraphs", 2))
        )

        if len(selected) < min_para_needed:
            article_reject_log.append({
                "title": item["title"],
                "source": item["source"],
                "reason": f"{label}：合格段落不足：{len(selected)}"
            })
            continue

        if relaxed:
            item = dict(item)
            item["fallback_used"] = True

        return item, selected, rejected

    return None, [], []



# =========================
# V33 候选页 + 手机选择 + 超时兜底
# =========================

CANDIDATE_LATEST_JSON = OUTPUT_DIR / "candidates_latest.json"
SELECTED_CANDIDATE_PATH = OUTPUT_DIR / "selected_candidate.json"

def candidate_id(item):
    base = normalize_article_link(item.get("link", "")) or normalize_article_title(item.get("title", ""))
    return hashlib.sha1(base.encode("utf-8", errors="ignore")).hexdigest()[:12]


def v33_topic_tags_and_score(item):
    """
    给候选页用的轻量选题评分。
    重点不是英语难度，而是：值不值得你点开、能不能作为外刊练习题。
    """
    text = f"{item.get('title','')} {item.get('summary','')} {item.get('source','')}".lower()
    tags = []
    score = 0
    reasons = []
    risks = []

    hard_terms = {
        "航天/空间站": ["space station", "astronaut", "astronauts", "nasa", "roscosmos", "zvezda", "spacecraft", "rocket", "orbit"],
        "能源/政策": ["net zero", "electricity", "power grid", "heat pump", "carbon emissions", "emissions", "energy policy", "climate policy"],
        "政治/司法/战争": ["parliament", "minister", "government policy", "court", "trial", "lawsuit", "war", "attack", "killed", "weapon"],
        "金融市场": ["stock market", "interest rates", "central bank", "bond market", "inflation"],
        "医学疾病": ["clinical trial", "patients", "cancer", "disease", "medical breakthrough"],
    }
    for label, words in hard_terms.items():
        if any(w in text for w in words):
            return {
                "publish_score": -100,
                "tags": [label],
                "reasons": [],
                "risks": [f"硬题材：{label}，不建议做小红书图文"]
            }

    topic_rules = [
        ("AI影响普通人", [" ai ", "artificial intelligence", "machine learning", "chatbot", "ai at work", "ai education"], 34),
        ("手机/专注力", ["device-free", "screen-free", "smartphone", "phone", "social media", "screen time", "digital detox"], 34),
        ("学习/教育", ["student", "students", "school", "teacher", "teachers", "education", "learning", "classroom", "university", "graduates"], 26),
        ("写作/阅读", ["journal", "journaling", "writing", "write ", "reading", "book", "books", "notebook"], 30),
        ("睡眠/习惯/状态", ["sleep", "stress", "tired", "routine", "habit", "wellbeing", "well-being", "mental health", "morning"], 30),
        ("职场/效率", ["work", "office", "career", "job", "productivity", "workplace", "meeting"], 24),
        ("家庭/生活方式", ["parents", "family", "friendship", "community", "relationship", "home", "daily life", "everyday life"], 18),
        ("宠物/情绪共鸣", ["dog", "dogs", "cat", "cats", "pet", "pets", "foster"], 18),
    ]

    padded = " " + text + " "
    for label, words, pts in topic_rules:
        hit = False
        for w in words:
            if w.strip() == "ai":
                if re.search(r"(?<![a-z])ai(?![a-z])", text):
                    hit = True
            elif w in padded or w in text:
                hit = True
        if hit:
            tags.append(label)
            score += pts
            reasons.append(f"命中「{label}」主题，比较适合做外刊表达练习")

    if re.search(r"\bwhy\b|\bhow\b|\bwhat\b|\bthe way\b|\bchange\b|\bimprove\b|\bincrease\b|\bshould\b", text):
        score += 12
        reasons.append("标题/摘要有观点或问题感，比较容易改成封面钩子")

    if "guardian life and style" in text:
        score += 12
        reasons.append("来源偏生活方式，通常更适合小红书")
    if "guardian education" in text or "bbc education" in text:
        score += 10
        reasons.append("教育类来源，适合学习/学生/家长话题")
    if "science" in text or "environment" in text:
        score -= 12
        risks.append("科学/自然类文章可能偏知识，不一定有传播点")
    if "smithsonian" in text:
        score -= 6
        risks.append("Smithsonian 题材可能偏科普，需要人工判断共鸣感")

    if not tags:
        risks.append("没有明显生活/学习/职场/AI/习惯主题，可能不适合发布")

    return {
        "publish_score": score,
        "tags": tags or ["待人工判断"],
        "reasons": reasons or ["可作为候选，但建议人工看标题再决定"],
        "risks": risks or ["暂无明显风险"]
    }


def make_candidates(cfg):
    OUTPUT_DIR.mkdir(exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")

    items = fetch_feed_items(cfg)
    for item in items:
        item["score"] = score_article(item, cfg)
        item["id"] = candidate_id(item)
        meta = v33_topic_tags_and_score(item)
        item["publish_score"] = meta["publish_score"]
        item["candidate_tags"] = meta["tags"]
        item["candidate_reasons"] = meta["reasons"]
        item["candidate_risks"] = meta["risks"]

    filtered = []
    reject_log = []
    for item in items:
        if not within_max_age(item, cfg):
            reject_log.append({"title": item.get("title",""), "reason": "超过回溯范围"})
            continue
        if is_used_article(item):
            reject_log.append({"title": item.get("title",""), "reason": "已使用过"})
            continue
        if item.get("publish_score", 0) < 0:
            reject_log.append({"title": item.get("title",""), "reason": "硬题材或明显不适合"})
            continue
        filtered.append(item)

    filtered.sort(key=lambda x: (x.get("publish_score", 0), x.get("score", 0)), reverse=True)
    count = int(cfg.get("candidate_count", 15))
    candidates = filtered[:count]

    payload = {
        "date": today,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "candidates": candidates,
        "reject_log": reject_log[:120],
        "auto_publish_min_score": int(cfg.get("candidate_auto_publish_min_score", 35))
    }

    json_path = OUTPUT_DIR / f"candidates_{today}.json"
    latest_path = CANDIDATE_LATEST_JSON
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    latest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    html_page = build_candidates_page(payload, cfg)
    html_path = OUTPUT_DIR / "candidates.html"
    html_path.write_text(html_page, encoding="utf-8")
    print(f"候选页已生成：{html_path}")
    return html_path


def build_candidates_page(payload, cfg):
    token = cfg.get("candidate_api_token", "")
    date = payload.get("date", "")
    min_score = payload.get("auto_publish_min_score", 35)
    cards = []

    for i, item in enumerate(payload.get("candidates", []), 1):
        title = html.escape(item.get("title", ""))
        source = html.escape(item.get("source", ""))
        link = html.escape(item.get("link", ""))
        summary = html.escape(clean_text(item.get("summary", ""))[:260])
        cid = html.escape(item.get("id", ""))
        pscore = int(item.get("publish_score", 0))
        raw_score = int(item.get("score", 0)) if isinstance(item.get("score", 0), (int, float)) else item.get("score", 0)
        tags = " ".join([f"<span class='tag'>{html.escape(t)}</span>" for t in item.get("candidate_tags", [])])
        reasons = "".join([f"<li>{html.escape(r)}</li>" for r in item.get("candidate_reasons", [])[:3]])
        risks = "".join([f"<li>{html.escape(r)}</li>" for r in item.get("candidate_risks", [])[:2]])

        cards.append(f"""
        <section class="card">
          <div class="rank">#{i}</div>
          <h2>{title}</h2>
          <div class="meta">{source}｜发布适合度：<b>{pscore}</b>｜文章分：{raw_score}</div>
          <div class="tags">{tags}</div>
          <p class="summary">{summary}</p>
          <div class="cols">
            <div><h3>推荐理由</h3><ul>{reasons}</ul></div>
            <div><h3>风险提醒</h3><ul>{risks}</ul></div>
          </div>
          <div class="actions">
            <button onclick="selectCandidate('{cid}', this)">用这篇生成</button>
            <a href="{link}" target="_blank" rel="noopener">打开原文</a>
          </div>
        </section>
        """)

    cards_html = "\n".join(cards) or "<div class='empty'>今天没有找到合适候选。可以等自动兜底，或手动加入备用库。</div>"

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>候选文章｜{html.escape(date)}</title>
<style>
* {{ box-sizing: border-box; }}
body {{
  margin: 0;
  background: #eef0f3;
  color: #16181d;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", Arial, sans-serif;
}}
.wrap {{ max-width: 860px; margin: 0 auto; padding: 18px 14px 42px; }}
.header {{
  position: sticky; top: 0; z-index: 3;
  background: rgba(238,240,243,.96);
  backdrop-filter: blur(8px);
  padding: 14px 0;
  border-bottom: 1px solid rgba(20,24,30,.08);
}}
h1 {{ margin: 0 0 6px; font-size: 24px; }}
.note {{ margin: 0; color: #5f6672; line-height: 1.6; font-size: 14px; }}
.card {{
  background: #fbfbfa;
  border: 1px solid rgba(20,24,30,.09);
  border-radius: 18px;
  box-shadow: 0 12px 32px rgba(24,28,35,.08);
  padding: 18px;
  margin: 14px 0;
  position: relative;
}}
.rank {{ font-size: 13px; color: #7c8490; margin-bottom: 8px; }}
h2 {{ margin: 0 0 10px; font-size: 21px; line-height: 1.35; }}
.meta {{ color: #5f6672; font-size: 13px; margin-bottom: 10px; }}
.tag {{
  display:inline-block; padding:4px 8px; border-radius:999px;
  background:#16181d; color:#fff; font-size:12px; margin: 0 6px 6px 0;
}}
.summary {{ color:#30343b; line-height:1.65; margin: 8px 0 12px; }}
.cols {{ display:grid; grid-template-columns:1fr; gap:10px; }}
h3 {{ margin: 0 0 6px; font-size: 14px; }}
ul {{ margin:0; padding-left:18px; color:#4f5662; line-height:1.6; font-size:13px; }}
.actions {{ display:flex; gap:10px; align-items:center; margin-top:14px; }}
button {{
  border:0; background:#0f172a; color:#fff; border-radius:999px;
  padding:10px 16px; font-size:15px; cursor:pointer;
}}
button[disabled] {{ opacity:.55; cursor:not-allowed; }}
a {{ color:#0b65c2; text-decoration:none; font-size:14px; }}
.status {{
  margin-top: 12px; padding: 10px 12px; border-radius: 12px;
  background: #fff; color:#30343b; border:1px solid rgba(20,24,30,.08);
  display:none; line-height:1.55;
}}
.empty {{ background:#fff; border-radius:16px; padding:18px; margin-top:14px; }}
@media (min-width:760px) {{ .cols {{ grid-template-columns:1fr 1fr; }} }}
</style>
</head>
<body>
<div class="wrap">
  <div class="header">
    <h1>候选文章｜{html.escape(date)}</h1>
    <p class="note">手机点“用这篇生成”即可。若你没选，系统可在设定时间自动用高分候选或备用库兜底。自动发布阈值：{min_score}。</p>
    <div id="status" class="status"></div>
  </div>
  {cards_html}
</div>
<script>
const API_TOKEN = {json.dumps(token)};
async function selectCandidate(id, btn) {{
  if (!confirm('确认用这篇生成今天的外刊资料？')) return;
  const status = document.getElementById('status');
  status.style.display = 'block';
  status.textContent = '正在生成，请等 30-90 秒，不要重复点击。';
  document.querySelectorAll('button').forEach(b => b.disabled = true);

  try {{
    const res = await fetch('/candidate-api/select-candidate', {{
      method: 'POST',
      headers: {{ 'Content-Type': 'application/json' }},
      body: JSON.stringify({{ id, token: API_TOKEN }})
    }});
    const data = await res.json();
    if (!res.ok || !data.ok) throw new Error(data.error || '生成失败');
    status.innerHTML = '生成完成。<br><a href="./index.html?v=' + Date.now() + '">打开完整学习页</a>　<a href="./xhs.html?v=' + Date.now() + '">打开图文版</a>';
  }} catch (e) {{
    status.textContent = '生成失败：' + e.message + '。可能是 candidate-api 没部署，或服务器生成超时。';
    document.querySelectorAll('button').forEach(b => b.disabled = false);
  }}
}}
</script>
</body>
</html>"""


def load_candidates_payload():
    if CANDIDATE_LATEST_JSON.exists():
        try:
            return json.loads(CANDIDATE_LATEST_JSON.read_text(encoding="utf-8-sig"))
        except Exception:
            return {}
    return {}


def generate_from_candidate_id(candidate_id_value, cfg, label="手机选择"):
    payload = load_candidates_payload()
    candidates = payload.get("candidates", [])
    item = None
    for c in candidates:
        if c.get("id") == candidate_id_value:
            item = c
            break
    if item is None:
        raise SystemExit(f"找不到候选 ID：{candidate_id_value}")

    article_reject_log = []
    final_article, final_selected, final_rejected = pick_article_from_items(
        [item], cfg, article_reject_log, allow_used=False, relaxed=False, label=label
    )
    if final_article is None:
        final_article, final_selected, final_rejected = pick_article_from_items(
            [item], cfg, article_reject_log, allow_used=False, relaxed=True, label=f"{label}兜底"
        )

    if final_article is None:
        path = write_no_article_report(article_reject_log)
        raise SystemExit(f"这篇候选未能生成正文，已写入报告：{path}")

    SELECTED_CANDIDATE_PATH.write_text(json.dumps({
        "date": datetime.now().strftime("%Y-%m-%d"),
        "id": candidate_id_value,
        "title": final_article.get("title", ""),
        "source": final_article.get("source", ""),
        "selected_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    return write_outputs(final_article, final_selected, final_rejected, article_reject_log, cfg)


def load_backup_articles(cfg):
    p = ROOT / cfg.get("backup_articles_path", "backup_articles.json")
    if not p.exists():
        return []
    try:
        raw = json.loads(p.read_text(encoding="utf-8-sig"))
        if isinstance(raw, dict):
            return raw.get("articles", [])
        if isinstance(raw, list):
            return raw
    except Exception:
        return []
    return []


def auto_publish_from_candidates(cfg):
    """
    08:30 之类的超时兜底：
    1. 如果今天已经手动选过，不重复生成。
    2. 如果候选第一名达到阈值，自动用第一名。
    3. 如果候选都一般，尝试备用库。
    4. 都不行就生成“今日未发布”提示页。
    """
    today = datetime.now().strftime("%Y-%m-%d")
    if SELECTED_CANDIDATE_PATH.exists():
        try:
            selected = json.loads(SELECTED_CANDIDATE_PATH.read_text(encoding="utf-8-sig"))
            if selected.get("date") == today:
                print("今天已经手动选择过候选，不再自动覆盖。")
                return []
        except Exception:
            pass

    payload = load_candidates_payload()
    candidates = payload.get("candidates", [])
    min_score = int(cfg.get("candidate_auto_publish_min_score", 35))

    if candidates:
        top = candidates[0]
        if int(top.get("publish_score", 0)) >= min_score:
            print(f"超时自动发布：使用最高分候选：{top.get('title')}")
            return generate_from_candidate_id(top.get("id"), cfg, label="超时自动")
        print(f"最高候选分不足：{top.get('publish_score')} < {min_score}，不自动硬发。")

    backups = load_backup_articles(cfg)
    if backups:
        print("尝试使用备用库第一篇。")
        item = dict(backups[0])
        item.setdefault("source", "备用库")
        item.setdefault("summary", "")
        item.setdefault("score", 999)
        item["id"] = candidate_id(item)
        payload = {"date": today, "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "candidates": [item]}
        CANDIDATE_LATEST_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return generate_from_candidate_id(item["id"], cfg, label="备用库")

    path = write_no_article_report([{
        "title": "今日没有自动发布",
        "source": "V33",
        "reason": "你未手动选择；最高候选分不足；备用库为空。"
    }])
    print(f"未自动发布，已生成提示页：{path}")
    return [path]

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepare-candidates", action="store_true", help="只生成候选页 candidates.html")
    parser.add_argument("--use-candidate", default="", help="使用候选 ID 生成当天正式内容")
    parser.add_argument("--auto-publish", action="store_true", help="超时后自动选择高分候选或备用库")
    args = parser.parse_args()

    cfg = load_config()
    cfg.setdefault("fallback_allow_reuse_if_no_article", False)
    cfg.setdefault("fallback_relaxed_if_no_article", True)
    cfg.setdefault("fallback_articles_to_try", cfg.get("articles_to_try", 260))
    cfg.setdefault("fallback_min_required_paragraphs", 2)

    recent_ai = recent_ai_article_count(days=7)
    cfg["_recent_ai_count_7d"] = recent_ai
    print(f"最近 7 天 AI 文章次数：{recent_ai}/{int(cfg.get('max_ai_articles_per_7_days', 2))}")
    print("开始运行 V33：候选页 + 手机选择 + 超时兜底版。")

    if args.prepare_candidates:
        make_candidates(cfg)
        return

    if args.use_candidate:
        paths = generate_from_candidate_id(args.use_candidate, cfg, label="手动选择")
        print("\n生成完成：")
        for p in paths:
            print(p)
        return

    if args.auto_publish:
        paths = auto_publish_from_candidates(cfg)
        print("\n自动兜底流程完成：")
        for p in paths:
            print(p)
        return

    # 默认仍保留原来的“自动最终选文”行为，避免旧 cron 失效。
    # 但建议日常改用：
    # 07:10 server_run_daily.py --prepare-candidates
    # 08:30 server_run_daily.py --auto-publish
    items = fetch_feed_items(cfg)
    if not items:
        print("没有抓到 RSS。请检查网络。")
        write_no_article_report([{"title": "RSS 抓取失败", "source": "system", "reason": "没有抓到任何候选"}])
        return

    for item in items:
        item["score"] = score_article(item, cfg)

    items.sort(key=lambda x: x["score"], reverse=True)

    article_reject_log = []

    final_article, final_selected, final_rejected = pick_article_from_items(
        items, cfg, article_reject_log,
        allow_used=False,
        relaxed=False,
        label="第一轮"
    )

    if final_article is None and cfg.get("fallback_relaxed_if_no_article", True):
        print("\n第一轮没有找到合格文章，启动兜底：放宽筛选，但仍不复用旧文章。")
        final_article, final_selected, final_rejected = pick_article_from_items(
            items, cfg, article_reject_log,
            allow_used=bool(cfg.get("fallback_allow_reuse_if_no_article", False)),
            relaxed=True,
            label="兜底轮"
        )

    if final_article is None:
        path = write_no_article_report(article_reject_log)
        print("\n今天没有找到合格文章。已生成干净提示页和报告：")
        print(path)
        return

    paths = write_outputs(final_article, final_selected, final_rejected, article_reject_log, cfg)

    print("\n生成完成：")
    for p in paths:
        print(p)

    print("\n重点看：output/latest.html")
    print("候选页：output/candidates.html")
    print("图文版：output/xhs.html")

if __name__ == "__main__":
    main()
