#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Apply one AI-enriched article JSON into business-english-live.json.

Workflow:
1. Run fetch_business_sources.py
2. Copy business-ai-enrich-prompt.txt to ChatGPT
3. Save the returned JSON as business-ai-enriched.json
4. Run: python3 apply_ai_enrichment.py
5. Copy business-english-live.json to /var/www/html/daily/
"""

import json
import shutil
import sys
from pathlib import Path
from datetime import datetime, timezone

LIVE_PATH = Path("business-english-live.json")
ENRICHED_PATH = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("business-ai-enriched.json")


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def require(condition, message):
    if not condition:
        raise SystemExit(f"ERROR: {message}")


def validate_article(article):
    require(isinstance(article, dict), "AI output must be a JSON object")
    require(article.get("id"), "missing id")
    require(article.get("source"), "missing source")
    require(article["source"].get("paragraphs"), "missing source.paragraphs")

    paras = article["source"]["paragraphs"]
    require(isinstance(paras, list) and len(paras) >= 2, "source.paragraphs must contain at least 2 paragraphs")

    for i, p in enumerate(paras[:3], 1):
        require(p.get("en") and len(p.get("en", "")) > 80, f"paragraph {i} missing enough English")
        require(p.get("cn") and len(p.get("cn", "")) > 30, f"paragraph {i} missing Chinese translation")

    required = ["titleCn", "desc", "why", "action", "breakdown", "judgement", "win", "lose", "english", "template", "practice"]
    for key in required:
        require(article.get(key) and len(str(article.get(key))) >= 10, f"missing or too short: {key}")

    user_use = article.get("userUse")
    require(user_use, "missing userUse")
    if isinstance(user_use, dict):
        require(any(k in user_use for k in ["foreignTrade", "crossBorder", "workplaceEnglish", "toolAdvice"]), "structured userUse missing use-case sections")
    else:
        require(len(str(user_use)) >= 20, "userUse too short")

    article["analysisStatus"] = "ai_enriched"
    article["deepReady"] = True
    article["publishable"] = True
    article["quality"] = "strong"
    article["sourceType"] = article.get("sourceType") or "AI Enriched"
    article["pill"] = article.get("pill") or "AI Enriched"
    article["readingTime"] = article.get("readingTime") or "8 分钟"

    return article


def main():
    require(ENRICHED_PATH.exists(), f"{ENRICHED_PATH} not found. Save AI output as business-ai-enriched.json first.")

    enriched = validate_article(load_json(ENRICHED_PATH))

    if LIVE_PATH.exists():
        live = load_json(LIVE_PATH)
        backup = Path(f"business-english-live.backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json")
        shutil.copy(LIVE_PATH, backup)
        print(f"backup saved: {backup}")
    else:
        live = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "count": 0,
            "method": "manual_ai_enriched",
            "scenarios": []
        }

    scenarios = live.get("scenarios", [])
    scenarios = [x for x in scenarios if x.get("id") != enriched.get("id")]

    # Put enriched article first.
    scenarios.insert(0, enriched)
    live["scenarios"] = scenarios[:10]
    live["count"] = len(live["scenarios"])
    live["generated_at"] = datetime.now(timezone.utc).isoformat()
    live["method"] = "rss_candidates_plus_ai_enrichment"

    save_json(LIVE_PATH, live)
    print("applied AI-enriched article to business-english-live.json")
    print(f"featured article: {enriched.get('source', {}).get('title')}")


if __name__ == "__main__":
    main()
