#!/usr/bin/env python3
import json
from datetime import datetime
from pathlib import Path

p = Path("business-english-live.json")
backup = Path(f"business-english-live.before_fix_prime_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
backup.write_text(p.read_text(encoding="utf-8"), encoding="utf-8")

d = json.loads(p.read_text(encoding="utf-8"))
items = d.get("scenarios", [])

old = "零工劳动者一直被剥削，AI可能让更多人也落入同样处境。"
new = "海外消费者不是不买了，是只在划算的时候买。"

def walk_replace(x):
    if isinstance(x, str):
        return x.replace(old, new)
    if isinstance(x, list):
        return [walk_replace(i) for i in x]
    if isinstance(x, dict):
        return {k: walk_replace(v) for k, v in x.items()}
    return x

if items:
    items[0] = walk_replace(items[0])
    items[0]["desc"] = "Reuters写到，今年Amazon Prime Day美国线上消费继续增长，但消费者更依赖折扣，更偏向刚需和计划内采购。真正的信号是：消费还在，但用户变得更会算账。"
    if isinstance(items[0].get("english"), list):
        fixed = []
        for e in items[0]["english"]:
            if isinstance(e, dict):
                fixed.append(e.get("phrase") or e.get("en") or e.get("cn") or str(e))
            else:
                fixed.append(str(e))
        items[0]["english"] = fixed

# Keep only one Gig workers article, preferably the AI PRODUCTIVITY one.
new_items = []
kept_gig = False
for x in items:
    s = json.dumps(x, ensure_ascii=False)
    is_gig = "Gig workers are endlessly exploited" in s
    if is_gig:
        is_ai = "AI PRODUCTIVITY" in s or "AI Productivity" in s or "ai-productivity" in s
        if (not kept_gig) and is_ai:
            new_items.append(x)
            kept_gig = True
        elif not kept_gig and not any("Gig workers are endlessly exploited" in json.dumps(y, ensure_ascii=False) for y in new_items):
            new_items.append(x)
            kept_gig = True
        continue
    if "CMA CGM Group Buys FedEx Supply Chain" in s:
        continue
    new_items.append(x)

d["scenarios"] = new_items
d["count"] = len(new_items)
d["generated_at"] = datetime.utcnow().isoformat() + "Z"

p.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")

print("OK fixed")
print("backup:", backup.name)
print("count:", d["count"])
