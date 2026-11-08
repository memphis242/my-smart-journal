import type { SeedCompilation } from './seed-types';

/**
 * Entry indexes refer to positions in SEED_ENTRIES. Each compilation only cites entries
 * that are at least as old as the compilation itself.
 */
export const SEED_COMPILATIONS: readonly SeedCompilation[] = [
  {
    days_ago: 1,
    purpose: "monthly review",
    period: "past 30 days",
    entries: [3, 4, 5, 7, 9, 11, 13, 14, 16],
    resume_bullets: [
      {
        text: "Shipped Beacon 2.13.0 with PR #2231, making parcel_events inserts idempotent via ON CONFLICT (source, event_id) DO NOTHING; with the DBA, removed 41,212 duplicate rows and built a unique index over 380 million rows. Insert p95 moved from 18 ms to 21 ms and no duplicate events appeared in the three weeks that followed.",
        evidence: [9, 16],
      },
      {
        text: "Re-ran the Ledgerline billing reconciliation for the week hit by duplicate Beacon events and delivered a corrected file for 1,912 parcels to finance; a dry-run flag caught a wrong-period first run before anything was sent.",
        evidence: [9, 13],
      },
      {
        text: "Resolved an 82% disk-usage page on Kafka broker 3 by setting 14-day retention on the parcel-events-dlq topic, reclaiming about 210 GB.",
        evidence: [11],
      },
      {
        text: "Coached a new-hire engineer through her first on-call shadow, which ended with her writing the incident note, and reviewed her PR #2398 (retry-queue depth gauge and Grafana panel for Beacon), commenting on metric naming and a missing empty-queue test.",
        evidence: [5, 11],
      },
      {
        text: "Rehearsed the PostgreSQL 16 upgrade of the Tidepool database on a full-size staging restore: pg_upgrade --link took 4 minutes and the follow-up ANALYZE 27 minutes.",
        evidence: [4],
      },
      {
        text: "Fixed a Raft log-truncation bug in Kestrel KV (Rust, CS 6420 term project) so the randomized partition test passes 200 of 200 runs, up from 183, and demonstrated a five-node cluster with leader failover in about 1.1 s and no acknowledged writes lost.",
        evidence: [3, 14],
      },
    ],
    star_stories: [
      {
        title: "Stopping duplicate parcel events in Beacon",
        situation: "Beacon consumer restarts were writing duplicate parcel events into PostgreSQL, which inflated Ledgerline billing totals for 1,912 parcels.",
        task: "Find the root cause, stop the duplicates at the source, and clean up the existing rows with minimal downtime.",
        action: "Dropped an initial carrier-replay theory after reproducing the bug locally with a chaos script that killed the Go consumer mid-batch, then wrote the ON CONFLICT insert (PR #2231). Sequenced the dedupe, a concurrent unique index build and the Beacon 2.13.0 deploy with Desmond Achterberg (database administrator), while Yuki Haverford (site reliability engineer) kept the Ledgerline export paused. Afterwards re-ran billing for the affected week.",
        result: "41,212 duplicate rows removed, insert p95 moved from 18 ms to 21 ms, no duplicate events in the three weeks after the fix, and a corrected billing file delivered to finance for the 1,912 affected parcels.",
        evidence: [9, 13, 16],
        competencies: ["debugging", "incident response", "ownership", "cross-team collaboration"],
      },
      {
        title: "Teaching triage during an on-call page",
        situation: "Priya Natarajan (new-hire mentee) shadowed her first on-call shift when a page reported 82% disk usage on Kafka broker 3.",
        task: "Resolve the alert while showing her how to decide what to look at first.",
        action: "Narrated each check aloud (growing or flat, which topic), found a dead-letter topic with no retention, and set 14-day retention with approval from Yuki Haverford (site reliability engineer). Let Priya write the incident note and only fixed a typo in it.",
        result: "About 210 GB reclaimed on the broker and Priya wrote the incident note herself; she asked for a checklist afterwards, which prompted the idea of a one-page runbook.",
        evidence: [11],
        competencies: ["mentoring", "communication", "incident response"],
      },
    ],
    technical_topics: [
      {
        topic: "Idempotent consumers under at-least-once delivery",
        depth_notes: "Beacon's consumer commits to PostgreSQL before the Kafka offset, so a crash in between redelivers the batch and a table without a unique key accepts the duplicate. The fix is INSERT ... ON CONFLICT (source, event_id) DO NOTHING, backed by a unique index and watched through a conflict counter.",
        evidence: [9, 16],
      },
      {
        topic: "Online unique-index builds on a large PostgreSQL table",
        depth_notes: "The index could only be created after deduplicating 41,212 rows (keeping the earliest ingested_at, 38 minutes). CREATE UNIQUE INDEX CONCURRENTLY on 380 million rows took 2 hours 40 minutes without blocking writes.",
        evidence: [16],
      },
      {
        topic: "PostgreSQL major-version upgrade rehearsal",
        depth_notes: "On a full-size restore, pg_upgrade --link took 4 minutes but the ANALYZE afterwards took 27, so analyze-in-stages was proposed to bring the app back sooner. pg_partman compatibility with version 16 has not been checked.",
        evidence: [4],
      },
      {
        topic: "Raft follower log truncation",
        depth_notes: "A stale, out-of-order AppendEntries could make a follower truncate committed entries after a partition healed. The fix is to truncate only when an existing entry conflicts in term, verified by 200 randomized partition runs.",
        evidence: [14],
      },
    ],
    open_threads: [
      "Production date for the PostgreSQL 16 upgrade is not set: pg_partman compatibility with 16 is unchecked, a second rehearsal is needed, and the roughly 9 minute outage with analyze-in-stages is only an estimate.",
      "Beacon follow-ups from the retro are open: the 5,000 message consumer-lag alert threshold is still noisy and unchanged, the chaos test is not in CI, and the postmortem summary for the engineering channel has not been written.",
      "Finance's review of the corrected Ledgerline billing totals ($18,430 in credit memos for over-billed surcharges) is not final.",
      "A one-page on-call triage runbook, based on the walkthrough given to Priya, is planned but not written.",
      "Carrier surcharge columns for Ledgerline are wanted by Callum Prewitt (product manager) by Wednesday; chunking by carrier may be needed because peak memory is 6.8 GB of an 8 GB limit, and whether finance wants surcharges per parcel or per invoice is unanswered.",
      "The Kestrel KV report is due in 10 days with the grade pending; membership changes were not built and will be listed as a limitation.",
    ],
  },
  {
    days_ago: 12,
    purpose: "resume update",
    period: "past 6 months",
    entries: [11, 13, 16, 17, 18, 21, 22, 23, 24, 25, 26, 27, 29, 30, 31, 32, 34, 38],
    resume_bullets: [
      {
        text: "Diagnosed and fixed duplicate parcel events in Beacon (Go consumer, Kafka to PostgreSQL): reproduced the bug locally with a chaos script, implemented idempotent inserts with ON CONFLICT (source, event_id) DO NOTHING (PR #2231), and with the DBA removed 41,212 duplicate rows and built a unique index on a 380 million row table. Shipped as Beacon 2.13.0 with insert p95 18 ms -> 21 ms and zero duplicates in the first 10 hours.",
        evidence: [16, 22, 23, 24],
      },
      {
        text: "Cut Ledgerline reconciliation run time from 52 to 25 minutes and peak memory from 11 GB to 6.8 GB (PR #1187, LED-418) by profiling with cProfile and py-spy, switching join keys to category and int32 dtypes, replacing an iterrows surcharge loop with a vectorized merge, and reading 9 of 31 Parquet columns.",
        evidence: [29, 30, 34],
      },
      {
        text: "Implemented monthly partitioning of Tidepool's shipment_facts (PR #318) with pg_partman and a BRIN index on event_date, reviewed by the DBA; a slow dashboard query fell from p95 2.1 s to 640 ms on a staging restore.",
        evidence: [21],
      },
      {
        text: "Re-ran the Ledgerline billing reconciliation for the week hit by duplicate Beacon events and delivered a corrected file for 1,912 parcels to finance; a dry-run flag caught a wrong-period first run before anything was sent.",
        evidence: [13],
      },
      {
        text: "Diagnosed a Harbor Gate deadline-propagation bug (upstream calls built with context.Background(), Go 1.22) that left requests hanging up to 30 s, and fixed it in PR #812 with a fake-upstream test; staging p99 under a stalled upstream went from 30 s to 2 s.",
        evidence: [38],
      },
      {
        text: "Moved Harbor Gate rate-limit configuration into a Terraform module (PR #824) and load-tested it with ghz at 4,000 rps (p99 85 ms, 0.02% error rate), which exposed unbounded per-client limiter state (RSS 180 MB -> 610 MB over 10 minutes).",
        evidence: [18],
      },
      {
        text: "Served as onboarding buddy and mentor to a new-hire engineer: ran a 90-minute deploy-pipeline pairing session and a retry-queue whiteboard walkthrough, guided her first merged PR (#2204, log volume 14,000 -> 600 lines per hour), and coached her through an on-call shadow where Kafka retention on a dead-letter topic was set to 14 days (about 210 GB reclaimed).",
        evidence: [11, 26, 27, 31, 32],
      },
    ],
    star_stories: [
      {
        title: "Duplicate parcel events: from a night page to a fix in production",
        situation: "A new Beacon duplicate-ratio alert (above 0.1%) paged at about 02:15 after two consumer pods were evicted in a node drain, and Ledgerline totals for the current billing week looked inflated.",
        task: "Keep inflated numbers away from finance, find the root cause, and stop the duplicates.",
        action: "Paused the Ledgerline billing export and opened INC-2207. After two days spent on a carrier-replay theory, reproduced the duplicates locally by killing the consumer mid-batch. Agreed a dedupe-then-index plan with Desmond Achterberg (database administrator), wrote the ON CONFLICT insert in PR #2231, and shipped it as Beacon 2.13.0 alongside Desmond's index build, with Yuki Haverford (site reliability engineer) providing the rollback plan and Renata Kovacs (engineering manager) asking for the DBA's sign-off before merge. Later re-ran billing for the affected week.",
        result: "41,212 duplicate rows removed, insert p95 18 ms -> 21 ms, zero duplicates and 14 conflicts caught in the first 10 hours after deploy, and a corrected billing file delivered for 1,912 parcels.",
        evidence: [13, 16, 17, 22, 23, 24, 25],
        competencies: ["incident response", "debugging", "ownership", "cross-team collaboration"],
      },
      {
        title: "Ramping up a new hire",
        situation: "Priya Natarajan joined the team as a new hire and Renata Kovacs (engineering manager) asked the user to be her onboarding buddy.",
        task: "Get her productive on the Beacon codebase and the deploy process quickly.",
        action: "Set up her local dev environment with her (40 minutes, fixing a stale port in the README), gave her a good first issue, ran a 90-minute pairing session that walked the deploy pipeline backward from the production dashboard, whiteboarded the Beacon retry queue, left five minor review comments on her first PR, and talked through an on-call page while she shadowed.",
        result: "Her first PR (#2204) merged and cut log volume from about 14,000 to about 600 lines per hour; she ran a staging deploy herself after the pairing session and wrote the incident note for the Kafka disk alert.",
        evidence: [11, 26, 27, 31, 32],
        competencies: ["mentoring", "communication", "patience"],
      },
      {
        title: "Speeding up Ledgerline reconciliation",
        situation: "The monthly Ledgerline reconciliation took 52 minutes, peaked at 11 GB of RAM, and the pod had been OOM-killed once (LED-418).",
        task: "Cut run time and memory without changing the schedule.",
        action: "Profiled a one-week sample with cProfile and py-spy, found 61% of time in pandas merges on object-dtype keys and 22% in a row-by-row surcharge loop, then merged PR #1187 with category and int32 keys, a vectorized merge and reading 9 of 31 Parquet columns. Re-checked the Grafana run panel after the first production run to separate a 15-minute worker-slot wait from the real run time.",
        result: "Run time 52 -> 25 minutes and peak memory 11 GB -> 6.8 GB; the job can now move to the 2 a.m. slot that Callum Prewitt (product manager) asked about.",
        evidence: [29, 30, 34],
        competencies: ["performance optimization", "analytical thinking", "accuracy"],
      },
    ],
    technical_topics: [
      {
        topic: "Idempotent ingestion under at-least-once delivery",
        depth_notes: "A crash between the PostgreSQL commit and the Kafka offset commit redelivers a batch, and without a unique key the second insert succeeds. Reproduced with a chaos script (3 to 4 duplicates per 10,000 events) and fixed with ON CONFLICT (source, event_id) DO NOTHING plus a conflict counter on the Grafana board.",
        evidence: [16, 22, 24],
      },
      {
        topic: "Deduplication and online index builds in PostgreSQL",
        depth_notes: "The unique index could only be built after deduplicating (keeping the earliest ingested_at, 38 minutes on a staging copy). CREATE UNIQUE INDEX CONCURRENTLY on 380 million rows took 2 hours 40 minutes, needed roughly 40 GB of extra disk per the DBA's estimate, and slowed insert p95 from 18 ms to 21 ms.",
        evidence: [16, 17, 23],
      },
      {
        topic: "Declarative table partitioning with pg_partman",
        depth_notes: "shipment_facts is partitioned by month, with partitions created three months ahead and a nightly job that only detaches partitions older than 24 months. Partition pruning plus a BRIN index on event_date cut a dashboard query from p95 2.1 s to 640 ms on a staging restore.",
        evidence: [21],
      },
      {
        topic: "Deadline propagation in a Go gRPC proxy",
        depth_notes: "Building upstream calls from context.Background() instead of the incoming context drops the client's deadline, so requests hung up to 30 s on a stalled upstream. The fix passes the incoming context through and is covered by a test asserting DEADLINE_EXCEEDED at about 2 s.",
        evidence: [38],
      },
      {
        topic: "Profiling pandas jobs for time and memory",
        depth_notes: "cProfile and py-spy on a one-week sample showed most time in merges on object-dtype keys and in an iterrows loop. Category and int32 keys, a vectorized merge and selective Parquet column reads took peak memory from 11 GB to 6.8 GB.",
        evidence: [30, 34],
      },
    ],
    open_threads: [
      "Harbor Gate PR #829 (TTL sweep for idle per-client limiters) is in review, and the 10-minute load test has not been re-run to confirm the memory fix.",
      "Tidepool's shipment_facts partitioning (PR #318) has only been measured on a staging restore; production cutover is planned for the next month rollover.",
      "Two more context.Background() calls in Harbor Gate request paths are filed as HG-77 and not yet fixed.",
      "The Beacon 2.13.0 fix is not considered done until a week with a rebalance or two has passed; Yuki Haverford was keeping the Ledgerline export paused for a day after the deploy.",
      "A one-page on-call triage runbook, based on the walkthrough given during Priya's on-call shadow, is planned but not written.",
      "Finance's review of the corrected Ledgerline billing totals ($18,430 in credit memos for over-billed surcharges) is not final.",
    ],
  },
];
