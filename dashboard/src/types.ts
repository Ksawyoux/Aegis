export interface Incident {
  id: string;
  dedup: string;
  service: string;
  alert: string | null;
  severity: "critical" | "high" | "medium" | "low";
  status: "open" | "investigating" | "summarized" | "failed";
  confidence: "high" | "medium" | "low" | null;
  opened: string;
  window: string;
  window_start: string;
  window_end: string;
  window_full: string;
  cites: number;
  root_cause: string | null;
  root_cites: string[];
  action: string | null;
  timeline: { at: string; what: string; cites: string[] }[];
  ruled_out: { statement: string; cites: string[] }[];
  similar: { statement: string; cites: string[] }[];
  evidence: EvidenceRow[];
  provenance_ok: boolean;
  run_id: string | null;
}

export interface EvidenceRow {
  kind: string;
  uid: string;
  label: string;
  raw: string;
}

export interface TraceEvent {
  kind: string;
  payload: Record<string, unknown>;
}

export interface IncidentDetail extends Incident {
  trace: TraceEvent[];
}

export interface TelemetrySeriesPoint {
  bucket_start: string;
  status_class: string;
  count: number;
  source_cites: string[];
}

export interface TemplateAnomaly {
  template_hash: string;
  level: string;
  count: number;
  baseline: number;
  delta: number;
  exemplar: { cite?: string; message?: string } | null;
}

export interface Telemetry {
  effective_window: { start: string; end: string };
  baseline_window: { start: string; end: string };
  baseline_sparse: boolean;
  series: TelemetrySeriesPoint[];
  top_templates: TemplateAnomaly[];
  status_breakdown: { status_class: string; count: number }[];
  sample_trace_ids: string[];
}

export interface CommitRef {
  cite: string;
  sha: string;
  message: string;
  files_changed: {
    path: string;
    status: string;
    hunks: string | null;
    hunks_omitted: string | null;
  }[];
}

export interface DeploymentRef {
  cite: string;
  environment: string;
  started_at: string;
  status: string;
  commit: CommitRef | null;
}

export interface DiffPayload {
  window: { start: string; end: string };
  focus: { service: string; commits: CommitRef[]; deployments: DeploymentRef[] };
  other_services: Record<string, { commits: number; deployments: number }>;
  unattributed: {
    uid: string;
    resource_name: string;
    action: string;
    applied_at: string;
    attribute_diff: Record<string, unknown>;
  }[];
  counts: { commits: number; deployments: number; infra_changes: number };
}

export interface IngestStats {
  watermarks: { source: string; last_cursor: string }[];
  reasons: { reason: string; count: number }[];
  recent: { reason: string; source_file: string; source_offset: number; raw: string }[];
  total_unresolved: number;
}

export interface CodeReviewRow {
  sha: string;
  source: "push" | "pull_request";
  pr_number: number | null;
  verdict: "clean" | "warn" | "fail";
  files_changed: number;
  additions: number;
  deletions: number;
  findings: number | unknown[];
  created_at: string;
  service: string;
}

export interface ReviewFinding {
  rule_id: string;
  severity: "high" | "medium" | "low";
  path: string;
  line: number;
  message: string;
  evidence: string;
  remediation: string;
}

export interface ReviewDetail {
  sha: string;
  source: "push" | "pull_request";
  pr_number: number | null;
  verdict: "clean" | "warn" | "fail";
  service: string;
  files_changed: number;
  additions: number;
  deletions: number;
  findings: ReviewFinding[];
  patch: string | null;
  created_at: string;
}

export interface DashboardSnapshot {
  generated_at: string;
  counts: Record<string, number>;
  reviews: CodeReviewRow[];
}
