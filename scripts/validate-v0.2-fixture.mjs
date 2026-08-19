import fs from "node:fs";
import process from "node:process";

const file = new URL("../fixtures/issue-01-v0.2/records.json", import.meta.url);
const bundle = JSON.parse(fs.readFileSync(file, "utf8"));

const errors = [];
const assert = (condition, message) => { if (!condition) errors.push(message); };
const unique = (items) => new Set(items).size === items.length;

assert(bundle.fixture_version === "0.2", "fixture_version必须为0.2");
assert(bundle.signals.length === 8, `应有8条信号，实际为${bundle.signals.length}`);
assert(unique(bundle.signals.map((item) => item.signal_id)), "signal_id必须唯一");

const sources = bundle.signals.flatMap((item) => item.source_records);
const claims = bundle.signals.flatMap((item) => item.evidence_claims);
const events = bundle.signals.map((item) => item.intelligence_event);
const stories = bundle.signals.map((item) => item.editorial_story);
const sourceIds = new Set(sources.map((item) => item.source_id));
const claimIds = new Set(claims.map((item) => item.claim_id));
const eventIds = new Set(events.map((item) => item.event_id));
const storyIds = new Set(stories.map((item) => item.story_id));

assert(unique([...sourceIds]), "source_id必须唯一");
assert(unique([...claimIds]), "claim_id必须唯一");
assert(unique([...eventIds]), "event_id必须唯一");
assert(unique([...storyIds]), "story_id必须唯一");

for (const source of sources) {
  for (const field of ["schema_version", "record_type", "source_id", "source_url", "canonical_url", "raw_title", "collected_at", "access_status", "content_hash"]) assert(source[field] !== undefined && source[field] !== "", `${source.source_id}缺少${field}`);
  try { new URL(source.source_url); new URL(source.canonical_url); } catch { errors.push(`${source.source_id}包含无效URL`); }
}

for (const claim of claims) {
  assert(sourceIds.has(claim.source_id), `${claim.claim_id}引用不存在的source_id`);
  assert(["unverified", "verified", "conflicted", "rejected"].includes(claim.verification_status), `${claim.claim_id}核验状态无效`);
}

for (const event of events) {
  for (const sourceId of event.source_ids) assert(sourceIds.has(sourceId), `${event.event_id}引用不存在的source_id: ${sourceId}`);
  for (const claimId of event.claim_ids) assert(claimIds.has(claimId), `${event.event_id}引用不存在的claim_id: ${claimId}`);
  assert(event.source_ids.includes(event.primary_source_id), `${event.event_id}的主要来源不在source_ids中`);
  assert(event.fact_summary.length >= 20, `${event.event_id}事实摘要过短`);
}

for (const story of stories) {
  assert(eventIds.has(story.primary_event_id), `${story.story_id}引用不存在的主要事件`);
  for (const eventId of story.related_event_ids) assert(eventIds.has(eventId), `${story.story_id}引用不存在的event_id: ${eventId}`);
  for (const sectionName of ["what_happened", "why_it_matters", "limitations"]) {
    const section = story[sectionName];
    assert(section?.text, `${story.story_id}缺少${sectionName}.text`);
    assert(Array.isArray(section?.claim_ids) && section.claim_ids.length > 0, `${story.story_id}的${sectionName}未绑定证据`);
    for (const claimId of section?.claim_ids ?? []) assert(claimIds.has(claimId), `${story.story_id}引用不存在的claim_id: ${claimId}`);
  }
  assert(story.what_happened.statement_type === "fact", `${story.story_id}的what_happened必须为fact`);
  for (const link of story.source_links) {
    assert(sourceIds.has(link.source_id), `${story.story_id}原文链接引用不存在的source_id`);
    try { new URL(link.url); } catch { errors.push(`${story.story_id}包含无效原文URL`); }
  }
  for (const number of story.key_numbers) assert(claimIds.has(number.claim_id), `${story.story_id}关键数字未绑定有效claim_id`);
}

for (const eventId of bundle.weekly_issue.timeline_event_ids) assert(eventIds.has(eventId), `周报引用不存在的event_id: ${eventId}`);
for (const storyId of bundle.weekly_issue.top_story_ids) assert(storyIds.has(storyId), `周报引用不存在的story_id: ${storyId}`);
assert(storyIds.has(bundle.weekly_issue.lead_story_id), "周报首篇文章不存在");

const mediumStories = stories.filter((story) => story.confidence === "confidence.medium");
assert(mediumStories.length === 1 && mediumStories[0].story_id === "story_202633_museglimmer", "应仅有Muse Glimmer为中置信度");
assert(mediumStories[0]?.editorial_status === "draft", "中置信度观察项不得自动进入已审核状态");
assert(bundle.weekly_issue.no_event_dates.length === 2, "应保留2个已检查无重点事件日期");

if (errors.length) {
  console.error(`v0.2 fixture validation failed (${errors.length})`);
  for (const error of errors) console.error(`- ${error}`);
  process.exit(1);
}

console.log(JSON.stringify({
  result: "pass",
  signals: bundle.signals.length,
  source_records: sources.length,
  evidence_claims: claims.length,
  intelligence_events: events.length,
  editorial_stories: stories.length,
  high_confidence_stories: stories.filter((story) => story.confidence === "confidence.high").length,
  medium_confidence_stories: mediumStories.length,
  no_event_dates: bundle.weekly_issue.no_event_dates.length
}, null, 2));
