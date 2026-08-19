#!/usr/bin/env python3
import argparse
import json
from pathlib import Path


parser = argparse.ArgumentParser()
parser.add_argument("--verified", default="verification/runs/p1-verified-events-v0.2.json")
parser.add_argument("--collection", default="collector/runs/first-live-run-v0.2.json")
args = parser.parse_args()
path = Path(args.verified)
data = json.loads(path.read_text(encoding="utf-8"))
errors = []
records = data["verification_records"]
claims = data["evidence_claims"]
events = data["intelligence_events"]
claim_ids = {item["claim_id"] for item in claims}
source_ids = {item["source_id"] for item in json.loads(Path(args.collection).read_text())["source_records"]}
source_ids.update(item["source_id"] for item in data["additional_source_records"])
if not records:
    errors.append("核验记录不能为空")
if not 5 <= len(events) <= 10:
    errors.append(f"正式事件应为5—10条，实际{len(events)}")
if sum(item["decision"] == "include" for item in records) != len(events):
    errors.append("include决策数量与正式事件数量不一致")
if not all(item["verification_status"] == "verified_primary" for item in records):
    errors.append("存在未完成原始来源核验的P1候选")
if len({item["event_id"] for item in events}) != len(events):
    errors.append("event_id不唯一")
for event in events:
    if not set(event["claim_ids"]).issubset(claim_ids):
        errors.append(f"{event['event_id']}引用无效claim")
    if event["primary_source_id"] not in source_ids:
        errors.append(f"{event['event_id']}引用无效主要来源")
    if event["primary_source_id"] not in event["source_ids"]:
        errors.append(f"{event['event_id']}主要来源不在source_ids")
    if event["status"] != "approved" or event["limitations"] is None:
        errors.append(f"{event['event_id']}缺少审核状态或限制")
if not set(data["editorial_selection"]["top_event_ids"]).issubset({item["event_id"] for item in events}):
    errors.append("top_event_ids包含未入选事件")
if errors:
    print(json.dumps({"result": "fail", "errors": errors}, ensure_ascii=False, indent=2))
    raise SystemExit(1)
print(json.dumps({"result": "pass", "reviewed": len(records), "formal_events": len(events),
                  "watch": sum(item["decision"] == "watch" for item in records),
                  "claims": len(claims), "top_events": len(data["editorial_selection"]["top_event_ids"])}, ensure_ascii=False, indent=2))
