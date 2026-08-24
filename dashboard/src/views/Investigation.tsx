import { useState, type CSSProperties } from "react";
import type { Incident } from "../types";
import { cardStyle, KIND, MONO } from "../ui";

export function InvestigationView({ incident }: { incident: Incident }) {
  const [expanded, setExpanded] = useState<number>(-1);

  const c = confidenceColors(incident.confidence);

  return (
    <div style={{ padding: "24px 28px 40px", display: "grid", gridTemplateColumns: "minmax(420px, 1fr) minmax(260px, 320px)", gap: 18, alignItems: "start" }}>
      <div style={{ display: "flex", flexDirection: "column", gap: 16, minWidth: 0 }}>
        <div style={{ ...cardStyle, padding: "20px 22px", display: "flex", flexDirection: "column", gap: 16 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
            <span style={chipStyle("#fdf6f6", "#bc2f32", "#f1bbbc")}>{incident.severity}</span>
            <span style={chipStyle("#fafafa", "#424242", "#e0e0e0")}>status: {incident.status}</span>
            {incident.confidence && (
              <span style={chipStyle(c.bg, c.color, c.border)}>confidence: {incident.confidence}</span>
            )}
            <span style={{ fontSize: 12, color: "#616161" }}>
              {incident.provenance_ok ? "provenance validated" : "no run recorded"} · {incident.cites} citations captured this run
            </span>
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 9 }}>
            <span style={{ fontSize: 10, letterSpacing: "0.1em", color: "#616161" }}>ROOT CAUSE</span>
            <p style={{ margin: 0, fontSize: 16, lineHeight: 1.6 }}>
              {incident.root_cause ?? "No summary was written for this incident."}
            </p>
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
              {incident.root_cites.map((cite) => (
                <button
                  key={cite}
                  onClick={() => setExpanded(indexOfCite(incident.evidence, cite))}
                  style={{ fontFamily: MONO, fontSize: 10, color: "#115ea3", background: "#ebf3fc", border: "1px solid #b4d6fa", borderRadius: 2, padding: "3px 7px", cursor: "pointer" }}
                >
                  {cite.length > 34 ? cite.slice(0, 34) + "…" : cite}
                </button>
              ))}
            </div>
          </div>
          {incident.action && (
            <div style={{ display: "flex", flexDirection: "column", gap: 8, borderTop: "1px solid #e0e0e0", paddingTop: 16 }}>
              <span style={{ fontSize: 10, letterSpacing: "0.1em", color: "#616161" }}>RECOMMENDED ACTION</span>
              <p style={{ margin: 0, fontSize: 14, lineHeight: 1.6, color: "#424242" }}>{incident.action}</p>
            </div>
          )}
        </div>

        <div style={{ ...cardStyle, padding: "20px 22px", display: "flex", flexDirection: "column", gap: 14 }}>
          <span style={{ fontSize: 10, letterSpacing: "0.1em", color: "#616161" }}>TIMELINE</span>
          {incident.timeline.length === 0 && (
            <span style={{ fontSize: 12, color: "#616161" }}>No timeline entries.</span>
          )}
          {incident.timeline.map((t, i) => (
            <div key={i} style={{ display: "grid", gridTemplateColumns: "92px minmax(0, 1fr)", gap: 16, paddingBottom: 14, borderBottom: "1px solid #f0f0f0" }}>
              <span style={{ fontFamily: MONO, fontSize: 12, color: "#616161" }}>{String(t.at).slice(11, 19)}Z</span>
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                <span style={{ fontSize: 14, lineHeight: 1.5 }}>{t.what}</span>
                <div style={{ display: "flex", gap: 5, flexWrap: "wrap" }}>
                  {(t.cites ?? []).map((cite) => (
                    <span key={cite} style={{ fontFamily: MONO, fontSize: 10, color: "#616161", background: "#fafafa", border: "1px solid #e0e0e0", borderRadius: 2, padding: "2px 6px" }}>
                      {shortCite(cite)}
                    </span>
                  ))}
                </div>
              </div>
            </div>
          ))}
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
          <MiniList title="RULED OUT" rows={incident.ruled_out} />
          <MiniList title="SIMILAR INCIDENTS" rows={incident.similar} />
        </div>
      </div>

      <div style={{ ...cardStyle, position: "sticky", top: 88, display: "flex", flexDirection: "column", overflow: "hidden", maxHeight: "calc(100vh - 120px)" }}>
        <div style={{ padding: "13px 16px", borderBottom: "1px solid #d1d1d1", background: "#fafafa", display: "flex", flexDirection: "column", gap: 3 }}>
          <span style={{ fontSize: 10, letterSpacing: "0.11em", color: "#616161" }}>EVIDENCE</span>
          <span style={{ fontSize: 12, color: "#616161" }}>
            {incident.evidence.length} citations captured · every claim resolves here
          </span>
        </div>
        <div style={{ overflowY: "auto" }}>
          {incident.evidence.map((e, i) => {
            const k = KIND[e.kind] ?? { bg: "#fafafa", color: "#424242" };
            const isOpen = expanded === i;
            return (
              <div
                key={e.uid + String(i)}
                onClick={() => setExpanded(isOpen ? -1 : i)}
                style={{ padding: "13px 16px", borderBottom: "1px solid #e0e0e0", cursor: "pointer", background: isOpen ? "#fafafa" : "#ffffff" }}
              >
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <span style={{ fontSize: 10, letterSpacing: "0.08em", padding: "2px 5px", borderRadius: 2, background: k.bg, color: k.color }}>{e.kind}</span>
                  <span style={{ fontFamily: MONO, fontSize: 12, color: "#424242", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {e.uid.length > 30 ? e.uid.slice(0, 30) + "…" : e.uid}
                  </span>
                </div>
                <div style={{ fontSize: 12, color: "#616161", marginTop: 6, lineHeight: 1.5 }}>{e.label}</div>
                {isOpen && (
                  <pre style={{ margin: "10px 0 0", fontFamily: MONO, fontSize: 10, lineHeight: 1.6, color: "#424242", background: "#f5f5f5", border: "1px solid #e0e0e0", borderRadius: 2, padding: 10, whiteSpace: "pre-wrap", overflowX: "auto" }}>
                    {e.raw}
                  </pre>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function MiniList({ title, rows }: { title: string; rows: { statement: string; cites: string[] }[] }) {
  return (
    <div style={{ ...cardStyle, padding: "18px 20px", display: "flex", flexDirection: "column", gap: 12 }}>
      <span style={{ fontSize: 10, letterSpacing: "0.1em", color: "#616161" }}>{title}</span>
      {rows.length === 0 && <span style={{ fontSize: 12, color: "#616161" }}>Nothing recorded.</span>}
      {rows.map((r, i) => (
        <div key={i} style={{ display: "flex", flexDirection: "column", gap: 5 }}>
          <span style={{ fontSize: 12, lineHeight: 1.5, color: "#424242" }}>{r.statement}</span>
          {(r.cites ?? []).length > 0 && (
            <span style={{ fontFamily: MONO, fontSize: 10, color: "#707070" }}>
              {(r.cites ?? []).map(shortCite).join(" · ")}
            </span>
          )}
        </div>
      ))}
    </div>
  );
}

function confidenceColors(confidence: string | null): { color: string; bg: string; border: string } {
  if (confidence === "high") return { color: "#0e700e", bg: "#f1faf1", border: "#9fd89f" };
  if (confidence === "medium") return { color: "#c43501", bg: "#fdf6f3", border: "#f4bfab" };
  return { color: "#616161", bg: "#fafafa", border: "#e0e0e0" };
}

function chipStyle(bg: string, color: string, border: string): CSSProperties {
  return { fontSize: 12, padding: "3px 8px", borderRadius: 2, background: bg, color, border: `1px solid ${border}` };
}

function indexOfCite(evidence: { uid: string }[], cite: string): number {
  const idx = evidence.findIndex((e) => e.uid === cite);
  return idx;
}

function shortCite(cite: string): string {
  const [prefix, rest] = cite.split(":", 2);
  if (!rest) return cite;
  return `${prefix}:${rest.length > 16 ? rest.slice(0, 16) + "…" : rest}`;
}
