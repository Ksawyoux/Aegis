export interface Incident {
  id: number;
  dedup_key: string;
  status: "open" | "investigating" | "summarized" | "failed";
  root_cause: string | null;
  service: string;
  alert_name: string | null;
  confidence: "high" | "medium" | "low" | null;
  window_start: string;
  window_end: string;
}

export interface CodeReviewRow {
  sha: string;
  source: "push" | "pull_request";
  pr_number: number | null;
  verdict: "clean" | "warn" | "fail";
  files_changed: number;
  additions: number;
  deletions: number;
  findings: number;
  created_at: string;
  service: string;
}

export interface DashboardSnapshot {
  generated_at: string;
  counts: Record<string, number>;
  incidents: Incident[];
  reviews: CodeReviewRow[];
}

export type Verdict = "clean" | "warn" | "fail";
