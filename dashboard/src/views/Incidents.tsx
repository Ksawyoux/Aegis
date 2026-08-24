import type { CSSProperties } from "react";
import type { Incident } from "../types";
import { cardStyle, confChip, MONO, pill, sevColor } from "../ui";

const GRID =
  "100px minmax(130px, 1.1fr) minmax(280px, 2.6fr) 116px 108px 132px 92px";

const gridStyle: CSSProperties = {
  minWidth: 1140,
  display: "grid",
  gridTemplateColumns: GRID,
  gap: 12,
};

export function IncidentsView({
  incidents,
  filter,
  setFilter,
  layout,
  setLayout,
  onOpen,
  selectedId,
  onSelect,
}: {
  incidents: Incident[];
  filter: string;
  setFilter: (f: string) => void;
  layout: "table" | "split";
  setLayout: (l: "table" | "split") => void;
  onOpen: (inc: Incident) => void;
  selectedId: string | null;
  onSelect: (inc: Incident) => void;
}) {
  const visible =
    filter === "all" ? incidents : incidents.filter((i) => i.status === filter);
  const selected = incidents.find((i) => i.id === selectedId) ?? incidents[0];

  return (
    <div style={{ padding: "24px 28px 40px", display: "flex", flexDirection: "column", gap: 20 }}>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 12 }}>
        <StatCard label="OPEN" value={String(incidents.filter((i) => i.status === "open" || i.status === "investigating").length)} note="awaiting or mid-investigation" />
        <StatCard label="SUMMARIZED" value={String(incidents.filter((i) => i.status === "summarized").length)} note="validated summaries written" />
        <StatCard
          label="HIGH CONFIDENCE"
          value={`${incidents.filter((i) => i.confidence === "high").length} / ${incidents.length}`}
          note={`${incidents.filter((i) => i.confidence === "medium").length} medium, ${incidents.filter((i) => i.confidence === "low").length} low`}
        />
        <StatCard label="FAILED RUNS" value={String(incidents.filter((i) => i.status === "failed").length)} note="aborted before a summary" />
      </div>

      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 16 }}>
        <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
          {["all", "open", "investigating", "summarized", "failed"].map((f) => (
            <button key={f} style={pill(filter === f)} onClick={() => setFilter(f)}>
              {f}
            </button>
          ))}
        </div>
        <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
          <span style={{ fontSize: 10, letterSpacing: "0.1em", color: "#616161", marginRight: 4 }}>LAYOUT</span>
          {(["table", "split"] as const).map((l) => (
            <button key={l} style={pill(layout === l)} onClick={() => setLayout(l)}>
              {l}
            </button>
          ))}
        </div>
      </div>

      {layout === "table" ? (
        <IncidentTable incidents={visible} onOpen={onOpen} />
      ) : (
        selected && (
          <SplitView
            incidents={visible}
            selected={selected}
            onSelect={onSelect}
            onOpen={onOpen}
          />
        )
      )}
    </div>
  );
}

function StatCard({ label, value, note }: { label: string; value: string; note: string }) {
  return (
    <div style={{ ...cardStyle, display: "flex", flexDirection: "column", gap: 8 }}>
      <span style={{ fontSize: 10, letterSpacing: "0.1em", color: "#616161" }}>{label}</span>
      <span style={{ fontSize: 24, fontWeight: 500, letterSpacing: "-0.02em", lineHeight: 1 }}>{value}</span>
      <span style={{ fontSize: 12, color: "#616161" }}>{note}</span>
    </div>
  );
}

function IncidentTable({ incidents, onOpen }: { incidents: Incident[]; onOpen: (i: Incident) => void }) {
  return (
    <div style={{ ...cardStyle, overflowX: "auto" }}>
      <div style={{ ...gridStyle, padding: "10px 16px", background: "#fafafa", borderBottom: "1px solid #d1d1d1", fontSize: 10, letterSpacing: "0.11em", color: "#616161" }}>
        <span>INCIDENT</span><span>SERVICE</span><span>ROOT CAUSE</span><span>CONFIDENCE</span><span>STATUS</span><span>WINDOW (UTC)</span>
        <span style={{ textAlign: "right" }}>CITES</span>
      </div>
      {incidents.map((inc) => {
        const c = confChip(inc.confidence);
        return (
          <div
            key={inc.id}
            onClick={() => onOpen(inc)}
            style={{ ...gridStyle, padding: "13px 16px", borderBottom: "1px solid #e0e0e0", alignItems: "center", cursor: "pointer", fontSize: 12 }}
            onMouseEnter={(e) => (e.currentTarget.style.background = "#fafafa")}
            onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
          >
            <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <span style={{ width: 3, height: 24, borderRadius: 2, background: sevColor(inc.severity) }} />
              <span style={{ fontFamily: MONO, fontWeight: 600 }}>{inc.id}</span>
            </span>
            <span style={{ display: "flex", flexDirection: "column", gap: 2, minWidth: 0 }}>
              <span>{inc.service}</span>
              <span style={{ fontSize: 10, color: "#616161" }}>{inc.alert}</span>
            </span>
            <span style={{ color: "#424242", lineHeight: 1.45 }}>
              {inc.root_cause ?? (inc.status === "failed" ? "Run aborted — no summary was written." : "Investigation pending.")}
            </span>
            <span style={{ fontSize: 12, padding: "2px 7px", borderRadius: 2, justifySelf: "start", border: `1px solid ${c.border}`, color: c.color, background: c.bg }}>
              {inc.confidence ?? "—"}
            </span>
            <span style={{ fontSize: 12, color: "#424242" }}>{inc.status}</span>
            <span style={{ fontFamily: MONO, fontSize: 12, color: "#616161" }}>{inc.window}</span>
            <span style={{ fontSize: 12, color: "#616161", textAlign: "right" }}>{inc.cites}</span>
          </div>
        );
      })}
    </div>
  );
}

function SplitView({
  incidents,
  selected,
  onSelect,
  onOpen,
}: {
  incidents: Incident[];
  selected: Incident;
  onSelect: (i: Incident) => void;
  onOpen: (i: Incident) => void;
}) {
  const c = confChip(selected.confidence);
  return (
    <div style={{ display: "grid", gridTemplateColumns: "minmax(260px, 380px) minmax(360px, 1fr)", gap: 16, alignItems: "start" }}>
      <div style={{ ...cardStyle, overflow: "hidden" }}>
        <div style={{ padding: "10px 14px", background: "#fafafa", borderBottom: "1px solid #d1d1d1", fontSize: 10, letterSpacing: "0.11em", color: "#616161" }}>
          QUEUE · ORDERED BY OPENED_AT DESC
        </div>
        {incidents.map((inc) => {
          const active = inc.id === selected.id;
          return (
            <div
              key={inc.id}
              onClick={() => onSelect(inc)}
              style={{ padding: "13px 14px", borderBottom: "1px solid #e0e0e0", cursor: "pointer", borderLeft: `2px solid ${active ? "#115ea3" : "transparent"}`, background: active ? "#ebf3fc" : "#ffffff" }}
            >
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
                <span style={{ display: "flex", alignItems: "center", gap: 7, fontSize: 12, fontWeight: 500 }}>
                  <span style={{ width: 6, height: 6, borderRadius: "50%", background: sevColor(inc.severity) }} />
                  {inc.id}
                </span>
                <span style={{ fontSize: 10, color: "#616161" }}>{inc.opened}</span>
              </div>
              <div style={{ fontSize: 12, color: "#424242", marginTop: 5 }}>{inc.service} · {inc.alert}</div>
              <div style={{ fontSize: 12, color: "#616161", marginTop: 4 }}>
                {inc.status} · {inc.confidence ?? "—"} confidence · {inc.cites} cites
              </div>
            </div>
          );
        })}
      </div>

      <div style={{ ...cardStyle, padding: "20px 22px", display: "flex", flexDirection: "column", gap: 18 }}>
        <div style={{ display: "flex", flexWrap: "wrap", alignItems: "flex-start", justifyContent: "space-between", gap: 16 }}>
          <div style={{ display: "flex", flexDirection: "column", gap: 6, minWidth: 0 }}>
            <span style={{ fontSize: 10, letterSpacing: "0.1em", color: "#616161" }}>{selected.id} · {selected.dedup}</span>
            <span style={{ fontSize: 16, fontWeight: 600 }}>{selected.service} · {selected.alert}</span>
            <span style={{ fontSize: 12, color: "#616161" }}>window {selected.window_full} · opened {selected.opened}</span>
          </div>
          <button
            onClick={() => onOpen(selected)}
            style={{ border: "1px solid #0f6cbd", background: "#0f6cbd", color: "#ffffff", fontFamily: "inherit", fontSize: 12, padding: "7px 12px", borderRadius: 4, cursor: "pointer", whiteSpace: "nowrap", flexShrink: 0 }}
          >
            open investigation →
          </button>
        </div>
        <div style={{ border: "1px solid #e0e0e0", borderRadius: 4, padding: "14px 16px", background: "#fafafa", display: "flex", flexDirection: "column", gap: 8 }}>
          <span style={{ fontSize: 10, letterSpacing: "0.1em", color: "#616161" }}>
            ROOT CAUSE · {selected.confidence ?? "—"} CONFIDENCE
          </span>
          <span style={{ fontSize: 14, lineHeight: 1.55 }}>
            {selected.root_cause ?? "No summary yet — the investigation has not completed."}
          </span>
        </div>
        <div style={{ border: "1px solid #e0e0e0", borderRadius: 4, padding: "12px 14px", display: "flex", flexDirection: "column", gap: 5 }}>
          <span style={{ fontSize: 10, letterSpacing: "0.1em", color: "#616161" }}>CONFIDENCE CHIP</span>
          <span style={{ fontSize: 12, padding: "2px 7px", borderRadius: 2, justifySelf: "start", width: "fit-content", border: `1px solid ${c.border}`, color: c.color, background: c.bg }}>
            {selected.confidence ?? "—"}
          </span>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <span style={{ fontSize: 10, letterSpacing: "0.1em", color: "#616161" }}>TIMELINE</span>
          {selected.timeline.length === 0 && (
            <span style={{ fontSize: 12, color: "#616161" }}>No timeline yet.</span>
          )}
          {selected.timeline.map((t, i) => (
            <div key={i} style={{ display: "grid", gridTemplateColumns: "74px minmax(0, 1fr)", gap: 14, fontSize: 12, paddingBottom: 10, borderBottom: "1px solid #f0f0f0" }}>
              <span style={{ fontFamily: MONO, color: "#616161" }}>{String(t.at).slice(11, 19)}Z</span>
              <span style={{ color: "#424242", lineHeight: 1.5 }}>{t.what}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
