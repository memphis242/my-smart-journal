import type { SeedConcept } from './seed-types';

/** Word groups the mock semantic search treats as related. Every word occurs in the seed entries. */
export const SEED_CONCEPTS: readonly SeedConcept[] = [
  {
    name: "teaching",
    words: ["pairing", "walkthrough", "whiteboard", "explained", "explaining", "shadowed", "buddy", "drew", "questions", "unblocked", "notes", "helped"],
  },
  {
    name: "onboarding",
    words: ["onboarding", "buddy", "joined", "hire", "laptop", "environment", "readme", "standup", "issue", "pastries", "shadow"],
  },
  {
    name: "database performance",
    words: ["index", "latency", "query", "queries", "pruning", "partitioning", "planner", "analyze", "statistics", "slower", "brin", "concurrently", "rows"],
  },
  {
    name: "database maintenance",
    words: ["upgrade", "rehearsal", "maintenance", "backfill", "dedupe", "restore", "snapshot", "disk", "partman", "detaches", "window", "outage"],
  },
  {
    name: "incident response",
    words: ["alert", "page", "incident", "rollback", "runbook", "paused", "evicted", "postmortem", "secondary", "retro", "drained", "inflated"],
  },
  {
    name: "on-call",
    words: ["pages", "shift", "secondary", "shadowed", "runbook", "night", "morning", "broker", "retention", "incident", "alert"],
  },
  {
    name: "debugging",
    words: ["reproduce", "reproduced", "trace", "tracing", "bug", "chaos", "logs", "diff", "printf", "profiled", "theory", "failing", "edge"],
  },
  {
    name: "fatigue",
    words: ["tired", "wiped", "sleep", "shaky", "awake", "heavy", "buzzing", "late", "nights", "toast", "eaten"],
  },
  {
    name: "running",
    words: ["run", "runs", "running", "taper", "km", "long", "easy", "marathon", "race", "tempo", "intervals", "shoes", "gels", "riverbend", "training"],
  },
  {
    name: "injury and recovery",
    words: ["shin", "splints", "physio", "fracture", "hurt", "hurts", "knee", "knees", "bike", "stairs", "stress", "imaged"],
  },
  {
    name: "cooking",
    words: ["recipe", "simmer", "simmered", "onion", "garlic", "lentils", "soup", "chili", "sauce", "cabbage", "baked", "lemon", "stock"],
  },
  {
    name: "gardening",
    words: ["plot", "tomatoes", "garlic", "mulch", "straw", "frost", "slugs", "voles", "plants", "stake", "cloves", "eggshells", "hardneck"],
  },
  {
    name: "family",
    words: ["mom", "dalia", "apartment", "weekend", "boxes", "bookshelf", "video", "specialist", "knee", "came", "cabbage"],
  },
  {
    name: "reading",
    words: ["book", "books", "club", "chapter", "persuasion", "middlemarch", "cranford", "letter", "reading", "paper", "textbook", "odile"],
  },
  {
    name: "coursework",
    words: ["course", "class", "professor", "syllabus", "grade", "term", "report", "demo", "partner", "registration", "project", "milestone"],
  },
  {
    name: "distributed systems",
    words: ["raft", "paxos", "consensus", "replication", "leader", "follower", "election", "snapshots", "linearizable", "heartbeat", "crdts", "consistency", "replicated"],
  },
  {
    name: "deadlines",
    words: ["due", "milestone", "milestones", "slips", "late", "deadline", "schedule", "slack", "demo", "report", "window", "grade"],
  },
  {
    name: "code review",
    words: ["review", "reviewed", "comments", "nits", "approved", "approval", "merged", "flagged", "comment", "description"],
  },
  {
    name: "testing",
    words: ["test", "tests", "randomized", "integration", "asserts", "passes", "failures", "failing", "fake", "replays", "chaos"],
  },
  {
    name: "deployment",
    words: ["deploy", "deployed", "release", "canary", "pipeline", "staging", "prod", "production", "redeploy", "redeployed", "image", "cutover", "rollover"],
  },
  {
    name: "monitoring",
    words: ["grafana", "dashboard", "panel", "gauge", "metric", "counter", "threshold", "alert", "board", "noisy", "lines"],
  },
  {
    name: "data quality",
    words: ["duplicate", "duplicates", "dedupe", "unique", "idempotent", "conflict", "conflicts", "inflated", "corrected", "correction", "double", "redelivers"],
  },
  {
    name: "performance optimization",
    words: ["profiled", "profile", "vectorized", "memory", "dtypes", "category", "pandas", "parquet", "columns", "merge", "loop", "iterrows", "skew"],
  },
  {
    name: "frustration",
    words: ["annoyed", "irritating", "miserable", "resent", "impatient", "noisy", "hates", "mess", "wasted", "twitchy", "wrong"],
  },
  {
    name: "satisfaction",
    words: ["proud", "relieved", "nice", "great", "good", "calm", "helpful", "obvious", "quicker", "sharp"],
  },
  {
    name: "collaboration",
    words: ["team", "partner", "sync", "both", "call", "pairing", "owns", "helped", "tobias", "desmond", "yuki"],
  },
  {
    name: "documentation",
    words: ["runbook", "readme", "wiki", "notes", "postmortem", "design", "description", "comments", "written", "write", "markdown", "report"],
  },
  {
    name: "event streaming",
    words: ["kafka", "offset", "consumer", "broker", "topic", "retention", "rebalance", "queue", "retry", "batch", "events", "redelivery"],
  },
];
