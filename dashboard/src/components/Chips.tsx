export function ConfidenceChip({ level }: { level: "high" | "medium" | "low" | null }) {
  if (level === null) return <span className="chip dim">—</span>;
  return <span className={`chip ${level}`}>{level}</span>;
}

export function StatusChip({ status }: { status: string }) {
  return <span className={`status ${status}`}>{status}</span>;
}

export function VerdictChip({ verdict }: { verdict: "clean" | "warn" | "fail" }) {
  return (
    <span className={`chip verdict-${verdict}`}>
      {verdict === "fail" ? "flagged" : verdict}
    </span>
  );
}

export function LiveBadge({ online, generatedAt }: { online: boolean; generatedAt: string }) {
  return (
    <div className="livegroup">
      <span className={`livebadge ${online ? "" : "off"}`}>
        <span className="dot" />
        {online ? "ingest live · lag 4s" : "api offline"}
      </span>
      {generatedAt && <span className="utcclock">{generatedAt.slice(0, 19)} UTC</span>}
    </div>
  );
}
