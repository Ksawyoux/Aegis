import { useEffect, useState } from "react";
import { fetchReviewDetail } from "../api";
import type { ReviewDetail, ReviewFinding } from "../types";
import { VerdictChip } from "./Chips";

interface DiffFile {
  path: string;
  lines: { kind: "add" | "del" | "hunk" | "ctx" | "meta"; text: string; a: number | null; b: number | null }[];
}

function parsePatch(patch: string): DiffFile[] {
  const files: DiffFile[] = [];
  let current: DiffFile | null = null;
  let a = 0;
  let b = 0;
  for (const raw of patch.split("\n")) {
    if (raw.startsWith("diff --git")) {
      current = { path: raw.replace(/^diff --git a\/(\S+) b\/.*/, "$1"), lines: [] };
      files.push(current);
      continue;
    }
    if (current === null) continue;
    if (raw.startsWith("--- ") || raw.startsWith("+++ ") || raw.startsWith("index ")) {
      current.lines.push({ kind: "meta", text: raw, a: null, b: null });
      continue;
    }
    const hunk = /^@@ -\d+(?:,\d+)? \+(\d+)/.exec(raw);
    if (hunk !== null) {
      b = Number(hunk[1]);
      a = Number(/^@@ -(\d+)/.exec(raw)?.[1] ?? "0");
      current.lines.push({ kind: "hunk", text: raw, a: null, b: null });
      continue;
    }
    if (raw.startsWith("+")) {
      current.lines.push({ kind: "add", text: raw, a: null, b });
      b += 1;
    } else if (raw.startsWith("-")) {
      current.lines.push({ kind: "del", text: raw, a, b: null });
      a += 1;
    } else {
      current.lines.push({ kind: "ctx", text: raw || " ", a, b });
      a += 1;
      b += 1;
    }
  }
  return files.filter((f) => f.path !== "");
}

function FindingCard({ finding }: { finding: ReviewFinding }) {
  return (
    <div className={`finding sev-${finding.severity}`}>
      <div className="findinghead">
        <span className={`sevchip ${finding.severity}`}>{finding.severity}</span>
        <span className="mono ruleid">{finding.rule_id}</span>
        <span className="mono loc">
          {finding.path}#L{finding.line}
        </span>
      </div>
      <p className="findingmsg">{finding.message}</p>
      <pre className="evidence">{finding.evidence}</pre>
      <div className="fixbox">
        <span className="fixlabel">How to fix</span> {finding.remediation}
      </div>
    </div>
  );
}

export function ReviewDetailPanel({ sha, onClose }: { sha: string; onClose: () => void }) {
  const [detail, setDetail] = useState<ReviewDetail | null>(null);
  const [tab, setTab] = useState<"findings" | "diff">("findings");

  useEffect(() => {
    setDetail(null);
    void fetchReviewDetail(sha).then((d) => setDetail(d));
  }, [sha]);

  const files = detail?.patch ? parsePatch(detail.patch) : [];

  return (
    <div className="modalbackdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <header className="modalhead">
          <div>
            <div className="modalttl mono">{sha.slice(0, 12)}</div>
            <div className="sub">
              {detail
                ? `${detail.source}${detail.pr_number ? ` · PR #${detail.pr_number}` : ""} · ${detail.service || "unattributed"}`
                : "loading…"}
            </div>
          </div>
          {detail && <VerdictChip verdict={detail.verdict} />}
          <button className="closebtn" onClick={onClose}>
            ✕
          </button>
        </header>

        {detail === null && <div className="empty">loading…</div>}
        {detail !== null && (
          <>
            <div className="tabs">
              <button className={tab === "findings" ? "on" : ""} onClick={() => setTab("findings")}>
                Findings ({detail.findings.length})
              </button>
              <button className={tab === "diff" ? "on" : ""} onClick={() => setTab("diff")}>
                File changes ({files.length})
              </button>
            </div>

            {tab === "findings" &&
              (detail.findings.length === 0 ? (
                <div className="empty">No rule matched any added line — clean review.</div>
              ) : (
                detail.findings.map((f, i) => <FindingCard key={i} finding={f} />)
              ))}

            {tab === "diff" && (
              <div>
                {detail.patch === null && (
                  <div className="empty">Diff was not stored for this review.</div>
                )}
                {detail.patch !== null && files.length === 0 && (
                  <div className="empty">No textual changes in this commit.</div>
                )}
                {files.map((file) => (
                  <div key={file.path} className="fileblock">
                    <div className="filepath mono">{file.path}</div>
                    <pre className="diffview">
                      {file.lines.map((line, i) => (
                        <div key={i} className={`dl ${line.kind}`}>
                          <span className="lnum">{line.b !== null ? String(line.b) : ""}</span>
                          <span>{line.text}</span>
                        </div>
                      ))}
                    </pre>
                  </div>
                ))}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
