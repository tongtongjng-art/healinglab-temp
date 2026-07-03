#!/usr/bin/env python3
import json
from datetime import datetime
from pathlib import Path

p = Path("business-english-live.json")
backup = Path(f"business-english-live.before_dedupe_gig_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
backup.write_text(p.read_text(encoding="utf-8"), encoding="utf-8")

d = json.loads(p.read_text(encoding="utf-8"))
items = d.get("scenarios", [])

kept = False
new_items = []

for x in items:
    s = json.dumps(x, ensure_ascii=False)
    is_gig = "Gig workers are endlessly exploited" in s

    if not is_gig:
        new_items.append(x)
        continue

    is_ai = (
        "AI PRODUCTIVITY" in s
        or "AI Productivity" in s
        or "ai-productivity" in s
        or x.get("signal") == "AI Productivity"
        or x.get("category") == "AI Productivity"
    )

    if is_ai and not kept:
        new_items.append(x)
        kept = True

d["scenarios"] = new_items
d["count"] = len(new_items)
d["generated_at"] = datetime.utcnow().isoformat() + "Z"

p.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")

print("OK deduped Gig workers")
print("backup:", backup.name)
print("count:", d["count"])
