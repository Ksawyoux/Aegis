import { useEffect, useState } from "react";
import { fetchIngestStats } from "../api";
import type { IngestStats } from "../types";
import { cardStyle, MONO } from "../ui";

export function IngestView({ counts }: { counts: Record<string, number> }) {
  const [stats, setStats] = useState<IngestStats | null>(null);

  useEffect(() => {
    let cancelled = false;
    void fetchIngestStats().then((s) => {
      if (!cancelled) setStats(s);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  if (!stats) {
    return <div style={{ padding: "24px 28px", color: "#616161" }}>loading ingest health…</div>;
  }

  const totalReasons = stats.reasons.reduce((a, r) => a + r.count, 0) || 1;
  const sources = [
    { source: "logs/*", inserted: counts["log_events"] ?? 0, unresolved: stats.total_unresolved, cursor: stats.watermarks.find((w) => w.source.startsWith("logs"))?.last_cursor ?? "—" },
    { source: "git/*", inserted: counts["commits"] ?? 0, unresolved: 0, cursor: stats.watermarks.find((w) => w.source.startsWith("git"))?.last_cursor ?? "—" },
    { source: "terraform/*", inserted: counts["infra_changes"] ?? 0, unresolved: 0, cursor: "—" },
    { source: "postmortems/*", inserted: counts["postmortem_chunks"] ?? 0, unresolved: 0, cursor: `${counts["postmortems"] ?? 0} docs` },
  ];

  return (
    <div style={{ padding: "24px 28px 40px", display: "flex", flexDirection: "column", gap: 16 }}>
      <div style={{ ...cardStyle, overflow: "hidden" }}>
        <div style={{ display: "grid", gridTemplateColumns: "2fr 86px 1fr", gap: 10, padding: "10px 16px", background: "#fafafa", borderBottom: "1px solid #d1d1d1", fontSize: 10, letterSpacing: "0.1em", color: "#616161" }}>
          <span>SOURCE</span>
          <span style={{ textAlign: "right" }}>ROWS</span>
          <span style={{ textAlign: "right" }}>WATERMARK</span>
        </div>
        {sources.map((s) => (
          <div key={s.source} style={{ display: "grid", gridTemplateColumns: "2fr 86px 1fr", gap: 10, padding: "12px 16px", borderBottom: "1px solid #f0f0f0", fontSize: 12, alignItems: "center" }}>
            <span style={{ fontFamily: MONO, color: "#242424" }}>{s.source}</span>
            <span style={{ textAlign: "right", color: "#424242" }}>{s.inserted.toLocaleString()}</span>
            <span style={{ fontFamily: MONO, textAlign: "right", color: "#616161" }}>
              {s.cursor.length > 24 ? s.cursor.slice(0, 24) + "…" : s.cursor}
            </span>
          </div>
        ))}
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "minmax(240px, 320px) minmax(0, 1fr)", gap: 16, alignItems: "start" }}>
        <div style={{ ...cardStyle, padding: "18px 20px", display: "flex", flexDirection: "column", gap: 13 }}>
          <span style={{ fontSize: 10, letterSpacing: "0.1em", color: "#616161" }}>UNRESOLVED BY REASON</span>
          {stats.reasons.length === 0 && <span style={{ fontSize: 12, color: "#616161" }}>Nothing unresolved.</span>}
          {stats.reasons.map((r) => (
            <div key={r.reason} style={{ display: "flex", flexDirection: "column", gap: 5 }}>
              <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12 }}>
                <span style={{ color: "#424242" }}>{r.reason}</span>
                <span style={{ color: "#616161" }}>{r.count}</span>
              </div>
              <div style={{ height: 5, background: "#f0f0f0", borderRadius: 2, overflow: "hidden" }}>
                <div style={{ height: "100%", background: "#ca5010", width: `${((r.count / totalReasons) * 100).toFixed(0)}%` }} />
              </div>
            </div>
          ))}
        </div>
        <div style={{ ...cardStyle, overflow: "hidden" }}>
          <div style={{ padding: "12px 16px", background: "#fafafa", borderBottom: "1px solid #d1d1d1", fontSize: 10, letterSpacing: "0.11em", color: "#616161" }}>
            RECENT UNRESOLVED EVENTS
          </div>
          {stats.recent.length === 0 && (
            <div style={{ padding: "12px 16px", fontSize: 12, color: "#616161" }}>Every line was attributed.</div>
          )}
          {stats.recent.map((u, i) => (
            <div key={i} style={{ padding: "12px 16px", borderBottom: "1px solid #f0f0f0", display: "flex", flexDirection: "column", gap: 5 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 9 }}>
                <span style={{ fontSize: 10, padding: "2px 6px", borderRadius: 2, background: "#fdf6f3", color: "#c43501" }}>{u.reason}</span>
                <span style={{ fontSize: 12, color: "#616161" }}>{u.source_file} @ {u.source_offset}</span>
              </div>
              <span style={{ fontFamily: MONO, fontSize: 12, color: "#424242", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                {u.raw}
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
