# 2026-09-16 collection recovery

## Delivered changes

- Keep DingTalk globally enabled; pause daily-20260915 and daily-20260916 only.
- Reject deployment when source and destination are the same directory.
- Shared collection accounting distinguishes rolling records, reused records,
  fetched records, first-seen URLs, changed URLs, and candidate funnel counts.
  None of these counts is represented as a count of verified new events.
- Under the run execution lease, consume completed same-day coverage with an
  intent receipt, merge by the existing URL/hash rules, rebuild candidates, and
  rerun the existing review and publication pipeline. Save original collection
  and review. Restore review decisions only with matching source hash and evidence.
- The daily runner is the recovery entry point. A separate launchd trigger calls
  the same runner every five minutes between 08:00 and 20:00. Kernel leases prevent
  duplicate execution; the normal 08:00/10:10 launch schedule is retained.
- Bounded recovery state caps attempts at two per incident with cooldown.
  Completed runs with failed transient sources use the existing coverage worker
  for at most two additional source-only rounds, one hour apart. The collector's
  existing acquisition budget database is reused. 404/auth failures are not retried.
- Missing image dispatch is an explicit alert, not a claim that generation is active.
- Progress page consumes shared accounting/recovery decisions and includes both lanes.

## Verified

66 focused tests passed (collection recovery, daily runtime, progress server,
incremental collection, reliability, delivery, DingTalk). Interrupted merge replay,
duplicate ingestion, cooldown/attempt limits, and live lease protection are covered.
Using today's files in a temporary directory produced 37 new URLs and one changed
record, increasing the merged collection from 82 to 119. This is not 37 approved news.
Production recovery was started via daily_runner; progress API reports 119 records
and pending_structure while the candidate model is processing.

## Remaining acceptance and limits

- Real 08:00 scheduled cold-start acceptance must happen on the next morning run.
- A sleeping/offline Mac cannot collect; catch-up starts once the host can run.
- Image generation still needs a real callable executor; monitoring reports this
  missing capability but cannot create it by retrying.
- Source URL replacement requires verifying each replacement endpoint. No speculative
  URLs or extra sources were added in this change.
- Coverage ingestion finishes through the normal factual gates; new factual risks
  can pause for review. An ingestion receipt is not a publication approval.
- Per-source long-term yield baselines and final event-level novelty are not yet
  implemented. The UI labels URL novelty explicitly to avoid overstating freshness.
