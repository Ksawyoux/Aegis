import { useEffect, useState } from "react";
import { fetchReviewDetail, fetchReviews } from "../api";
import type { CodeReviewRow, ReviewDetail, ReviewFinding } from "../types";
import { cardStyle, MONO } from "../ui";

export function ReviewsView({ flagged }: { flagged: number }) {
  const [reviews, setReviews] = useState<CodeReviewRow[] | null>(null);
  const [selectedSha, setSelectedSha] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void fetchReviews().then((d) => {
      if (!cancelled)
        setReviews(
          d.reviews.map((r) => ({
            ...r,
            findings: Array.isArray(r.findings) ? r.findings.length : Number(r.findings),
          })),
        );
    });
    return () => {
      cancelled = true;
    };
  }, [flagged]);

  return (
    <div style={{ padding: "24px 28px 40px", display: "flex", flexDirection: "column", gap: 16 }}>
      {reviews === null && <span style={{ color: "#616161" }}>loading reviews…</span>}
      {reviews !== null && reviews.length === 0 && (
        <div style={{ ...cardStyle, padding: 26, color: "#616161", textAlign: "center" }}>
          No reviews yet — push to a registered repository and the engine reviews the landed commit.
        </div>
      )}
      {reviews !== null && reviews.length > 0 && (
        <div style={{ ...cardStyle, overflow: "hidden" }}>
          <div style={{ display: "grid", gridTemplateColumns: "150px 130px 130px 90px 110px 1fr", gap: 10, padding: "10px 16px", background: "#fafafa", borderBottom: "1px solid #d1d1d1", fontSize: 10, letterSpacing: "0.11em", color: "#616161" }}>
            <span>COMMIT / PR</span><span>SERVICE</span><span>DIFF</span><span style={{ textAlign: "right" }}>FINDINGS</span><span>VERDICT</span><span style={{ textAlign: "right" }}>REVIEWED AT (UTC)</span>
          </div>
          {reviews.map((r) => {
            const label = r.source === "pull_request" && r.pr_number !== null
              ? `PR #${r.pr_number} · ${r.sha.slice(0, 8)}`
              : r.sha.slice(0, 12);
            return (
              <div
                key={`${r.sha}-${r.source}`}
                onClick={() => setSelectedSha(r.sha)}
                style={{ display: "grid", gridTemplateColumns: "150px 130px 130px 90px 110px 1fr", gap: 10, padding: "12px 16px", borderBottom: "1px solid #f0f0f0", fontSize: 12, alignItems: "center", cursor: "pointer", background: r.verdict === "fail" ? "#fffbfb" : "#ffffff" }}
                onMouseEnter={(e) => (e.currentTarget.style.background = "#fafafa")}
                onMouseLeave={(e) => (e.currentTarget.style.background = r.verdict === "fail" ? "#fffbfb" : "#ffffff")}
              >
                <span style={{ fontFamily: MONO }}>{label}</span>
                <span>{r.service || "—"}</span>
                <span style={{ fontFamily: MONO }}>
                  {r.files_changed}f <span style={{ color: "#0e700e" }}>+{r.additions}</span> <span style={{ color: "#bc2f32" }}>−{r.deletions}</span>
                </span>
                <span style={{ fontFamily: MONO, textAlign: "right" }}>{typeof r.findings === "number" ? r.findings : r.findings.length}</span>
                <span>
                  <span style={{ fontSize: 12, padding: "2px 8px", borderRadius: 2, border: "1px solid", ...(r.verdict === "fail" ? { background: "#fdf6f6", color: "#bc2f32", borderColor: "#f1bbbc" } : { background: "#f1faf1", color: "#0e700e", borderColor: "#9fd89f" }) }}>
                    {r.verdict === "fail" ? "flagged" : r.verdict}
                  </span>
                </span>
                <span style={{ fontFamily: MONO, textAlign: "right", color: "#616161" }}>{r.created_at.slice(0, 19)}</span>
              </div>
            );
          })}
        </div>
      )}

      {selectedSha !== null && (
        <ReviewModal sha={selectedSha} onClose={() => setSelectedSha(null)} />
      )}
    </div>
  );
}

function ReviewModal({ sha, onClose }: { sha: string; onClose: () => void }) {
  const [detail, setDetail] = useState<ReviewDetail | null>(null);
  const [tab, setTab] = useState<"findings" | "diff">("findings");

  useEffect(() => {
    setDetail(null);
    void fetchReviewDetail(sha).then((d) => setDetail(d.detail));
  }, [sha]);

  return (
    <div onClick={onClose} style={{ position: "fixed", inset: 0, background: "rgba(15,20,34,0.45)", display: "flex", alignItems: "flex-start", justifyContent: "center", padding: "48px 24px", zIndex: 50, overflow: "auto" }}>
      <div onClick={(e) => e.stopPropagation()} style={{ background: "#ffffff", border: "1px solid #d1d1d1", borderRadius: 4, boxShadow: "0 18px 50px rgba(10,15,30,0.18)", width: "min(880px, 100%)", padding: "22px 26px" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 14, marginBottom: 14 }}>
          <div>
            <div style={{ fontFamily: MONO, fontSize: 17, fontWeight: 700 }}>{sha.slice(0, 12)}</div>
            <div style={{ fontSize: 12, color: "#616161" }}>
              {detail ? `${detail.source}${detail.pr_number ? ` · PR #${detail.pr_number}` : ""} · ${detail.service || "unattributed"}` : "loading…"}
            </div>
          </div>
          {detail && (
            <span style={{ fontSize: 12, padding: "3px 8px", borderRadius: 2, border: "1px solid", ...(detail.verdict === "fail" ? { background: "#fdf6f6", color: "#bc2f32", borderColor: "#f1bbbc" } : { background: "#f1faf1", color: "#0e700e", borderColor: "#9fd89f" }) }}>
              {detail.verdict === "fail" ? "flagged" : detail.verdict}
            </span>
          )}
          <button onClick={onClose} style={{ marginLeft: "auto", border: "none", background: "transparent", fontSize: 16, cursor: "pointer", color: "#616161" }}>✕</button>
        </div>

        <div style={{ display: "flex", gap: 6, borderBottom: "1px solid #d1d1d1", marginBottom: 16 }}>
          {(["findings", "diff"] as const).map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              style={{ border: "none", background: "none", padding: "9px 14px", cursor: "pointer", fontSize: 13.5, color: tab === t ? "#115ea3" : "#616161", borderBottom: `2px solid ${tab === t ? "#115ea3" : "transparent"}`, fontWeight: tab === t ? 600 : 400 }}
            >
              {t === "findings" ? `Findings (${detail?.findings.length ?? 0})` : "File changes"}
            </button>
          ))}
        </div>

        {detail === null && <div style={{ color: "#616161" }}>loading…</div>}
        {detail !== null && tab === "findings" && (
          detail.findings.length === 0
            ? <div style={{ color: "#616161", padding: 14 }}>No rule matched any added line — clean review.</div>
            : detail.findings.map((f, i) => <FindingCard key={i} finding={f} />)
        )}
        {detail !== null && tab === "diff" && (
          detail.patch === null
            ? <div style={{ color: "#616161", padding: 14 }}>Diff was not stored for this review.</div>
            : <DiffView patch={detail.patch} />
        )}
      </div>
    </div>
  );
}

function FindingCard({ finding }: { finding: ReviewFinding }) {
  const sev = finding.severity === "high"
    ? { bg: "#fdf6f6", color: "#bc2f32", border: "#f1bbbc" }
    : finding.severity === "medium"
      ? { bg: "#fdf6f3", color: "#c43501", border: "#f4bfab" }
      : { bg: "#fafafa", color: "#616161", border: "#e0e0e0" };
  return (
    <div style={{ border: "1px solid #e0e0e0", borderRadius: 4, padding: "14px 16px", marginBottom: 12 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 8 }}>
        <span style={{ fontSize: 10, textTransform: "uppercase", letterSpacing: "0.08em", padding: "2px 9px", borderRadius: 2, fontWeight: 700, background: sev.bg, color: sev.color, border: `1px solid ${sev.border}` }}>
          {finding.severity}
        </span>
        <span style={{ fontFamily: MONO, color: "#616161" }}>{finding.rule_id}</span>
        <span style={{ fontFamily: MONO, marginLeft: "auto" }}>{finding.path}#L{finding.line}</span>
      </div>
      <p style={{ margin: "0 0 10px" }}>{finding.message}</p>
      <pre style={{ margin: "0 0 10px", fontFamily: MONO, fontSize: 11, background: "#f5f5f5", border: "1px solid #e0e0e0", borderRadius: 2, padding: 10, whiteSpace: "pre-wrap" }}>
        {finding.evidence}
      </pre>
      <div style={{ background: "#f1faf1", border: "1px solid #9fd89f", borderRadius: 4, padding: "10px 13px", fontSize: 13.5 }}>
        <b style={{ color: "#0e700e", marginRight: 6 }}>How to fix</b>
        {finding.remediation}
      </div>
    </div>
  );
}

interface DiffLine {
  kind: "add" | "del" | "hunk" | "meta" | "ctx";
  text: string;
  lineNo: number | null;
}

function DiffView({ patch }: { patch: string }) {
  const files = parsePatch(patch);
  return (
    <div>
      {files.map((file) => (
        <div key={file.path} style={{ border: "1px solid #e0e0e0", borderRadius: 4, overflow: "hidden", marginBottom: 14 }}>
          <div style={{ fontFamily: MONO, background: "#fafbfe", padding: "8px 13px", borderBottom: "1px solid #e0e0e0", fontWeight: 600, fontSize: 12 }}>
            {file.path}
          </div>
          <pre style={{ margin: 0, maxHeight: 420, overflow: "auto", fontFamily: MONO, fontSize: 12, lineHeight: 1.55 }}>
            {file.lines.map((line, i) => (
              <div
                key={i}
                style={{ display: "flex", whiteSpace: "pre", padding: "0 10px", background: line.kind === "add" ? "#f1faf1" : line.kind === "del" ? "#fdf6f6" : line.kind === "hunk" ? "#f1f5fb" : "#ffffff" }}
              >
                <span style={{ width: 38, flexShrink: 0, textAlign: "right", marginRight: 14, color: "#bdbdbd", userSelect: "none" }}>
                  {line.lineNo ?? ""}
                </span>
                <span style={{ color: line.kind === "add" ? "#0e700e" : line.kind === "del" ? "#bc2f32" : line.kind === "hunk" ? "#616161" : "#424242" }}>
                  {line.text}
                </span>
              </div>
            ))}
          </pre>
        </div>
      ))}
    </div>
  );
}

function parsePatch(patch: string): { path: string; lines: DiffLine[] }[] {
  const files: { path: string; lines: DiffLine[] }[] = [];
  let current: { path: string; lines: DiffLine[] } | null = null;
  let newLine = 0;
  for (const raw of patch.split("\n")) {
    if (raw.startsWith("diff --git")) {
      const match = /diff --git a\/(\S+) b\//.exec(raw);
      current = { path: match?.[1] ?? raw, lines: [] };
      files.push(current);
      continue;
    }
    if (current === null) continue;
    if (raw.startsWith("--- ") || raw.startsWith("+++ ") || raw.startsWith("index ")) {
      current.lines.push({ kind: "meta", text: raw, lineNo: null });
      continue;
    }
    const hunk = /^@@ -\d+(?:,\d+)? \+(\d+)/.exec(raw);
    if (hunk !== null) {
      newLine = Number(hunk[1]);
      current.lines.push({ kind: "hunk", text: raw, lineNo: null });
      continue;
    }
    if (raw.startsWith("+")) {
      current.lines.push({ kind: "add", text: raw, lineNo: newLine });
      newLine += 1;
    } else if (raw.startsWith("-")) {
      current.lines.push({ kind: "del", text: raw, lineNo: null });
    } else {
      current.lines.push({ kind: "ctx", text: raw || " ", lineNo: newLine });
      newLine += 1;
    }
  }
  return files;
}
