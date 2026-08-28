import { useMemo } from "react";
import type { IncidentDetail } from "../types";
import { cardStyle, MONO } from "../ui";

interface Turn {
  n: string;
  kind: "tool" | "turn" | "terminal";
  name: string;
  meta: string;
  detail: string;
}

export function AgentRunView({ detail }: { detail: IncidentDetail | null }) {
  const turns = useMemo(() => buildTurns(detail), [detail]);
  const toolTally = useMemo(() => {
    const tally = new Map<string, number>();
    for (const t of turns) {
      if (t.kind === "tool") tally.set(t.name, (tally.get(t.name) ?? 0) + 1);
    }
    return [...tally.entries()].map(([name, n]) => ({ name, n }));
  }, [turns]);

  if (!detail) {
    return <div style={{ padding: "24px 28px", color: "#616161" }}>No run recorded for this incident yet.</div>;
  }

  return (
    <div style={{ padding: "24px 28px 40px", display: "grid", gridTemplateColumns: "minmax(0, 1fr) minmax(240px, 300px)", gap: 16, alignItems: "start" }}>
      <div style={{ ...cardStyle, overflow: "hidden" }}>
        <div style={{ padding: "12px 16px", background: "#fafafa", borderBottom: "1px solid #d1d1d1", display: "flex", justifyContent: "space-between" }}>
          <span style={{ fontSize: 10, letterSpacing: "0.11em", color: "#616161" }}>
            RUN {(detail.run_id ?? "—").slice(0, 6)} · {detail.id}
          </span>
          <span style={{ fontSize: 12, color: "#616161" }}>{turns.length} trace events</span>
        </div>
        {turns.map((t) => {
          const colors = t.kind === "tool"
            ? { bg: "#ebf3fc", color: "#115ea3" }
            : t.kind === "terminal"
              ? { bg: "#f1faf1", color: "#0e700e" }
              : { bg: "#fafafa", color: "#424242" };
          return (
            <div key={t.n} style={{ padding: "14px 16px", borderBottom: "1px solid #f0f0f0", display: "grid", gridTemplateColumns: "40px minmax(0, 1fr)", gap: 14 }}>
              <span style={{ fontFamily: MONO, fontSize: 12, color: "#bdbdbd" }}>{t.n}</span>
              <div style={{ display: "flex", flexDirection: "column", gap: 7, minWidth: 0 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 9, flexWrap: "wrap" }}>
                  <span style={{ fontSize: 10, letterSpacing: "0.08em", padding: "2px 6px", borderRadius: 2, background: colors.bg, color: colors.color }}>{t.kind}</span>
                  <span style={{ fontSize: 12, color: "#242424" }}>{t.name}</span>
                  {t.meta && <span style={{ fontSize: 12, color: "#616161" }}>{t.meta}</span>}
                </div>
                {t.detail && (
                  <span style={{ fontSize: 12, color: "#424242", lineHeight: 1.55, wordBreak: "break-all" }}>{t.detail}</span>
                )}
              </div>
            </div>
          );
        })}
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
        <div style={{ ...cardStyle, padding: "18px 20px", display: "flex", flexDirection: "column", gap: 11 }}>
          <span style={{ fontSize: 10, letterSpacing: "0.1em", color: "#616161" }}>PROVENANCE</span>
          <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12, color: detail.provenance_ok ? "#0e700e" : "#bc2f32" }}>
            <span style={{ width: 6, height: 6, borderRadius: "50%", background: detail.provenance_ok ? "#107c10" : "#bc2f32" }} />
            {detail.provenance_ok ? "validated" : "no run"}
          </div>
          <span style={{ fontSize: 12, color: "#616161", lineHeight: 1.55 }}>
            {detail.cites} citations captured across {turns.filter((t) => t.kind === "tool").length} tool calls. Every summary citation was matched against this run's captured set.
          </span>
        </div>
        <div style={{ ...cardStyle, padding: "18px 20px", display: "flex", flexDirection: "column", gap: 11 }}>
          <span style={{ fontSize: 10, letterSpacing: "0.1em", color: "#616161" }}>TOOL CALLS</span>
          {toolTally.map((c) => (
            <div key={c.name} style={{ display: "flex", justifyContent: "space-between", fontSize: 12 }}>
              <span style={{ color: "#424242" }}>{c.name}</span>
              <span style={{ color: "#616161" }}>{c.n}</span>
            </div>
          ))}
          {toolTally.length === 0 && <span style={{ fontSize: 12, color: "#616161" }}>None recorded.</span>}
        </div>
      </div>
    </div>
  );
}

function buildTurns(detail: IncidentDetail | null): Turn[] {
  if (!detail) return [];
  const turns: Turn[] = [];
  let n = 0;
  for (const event of detail.trace) {
    n += 1;
    const payload = event.payload as {
      turn?: number;
      function_calls?: string[];
      tool?: string;
      args?: Record<string, unknown>;
    };
    if (event.kind === "agent_turn") {
      const calls = payload.function_calls ?? [];
      turns.push({
        n: String(n).padStart(2, "0"),
        kind: calls.length ? "tool" : "turn",
        name: calls.length ? calls.join(", ") : "model turn",
        meta: "",
        detail: calls.length ? "" : "No tool calls this turn.",
      });
    } else if (event.kind === "tool_result") {
      const cites = countCites(event.payload as { result?: unknown });
      turns.push({
        n: String(n).padStart(2, "0"),
        kind: "tool",
        name: "→ " + (payload.tool ?? "result"),
        meta: `${cites} citations`,
        detail: JSON.stringify(payload.args ?? {}),
      });
    } else if (event.kind === "terminal") {
      turns.push({
        n: String(n).padStart(2, "0"),
        kind: "terminal",
        name: "terminal",
        meta: String((event.payload as { status?: string }).status ?? ""),
        detail: "",
      });
    }
  }
  return turns;
}

function countCites(payload: { result?: unknown }): number {
  let count = 0;
  const visit = (node: unknown): void => {
    if (Array.isArray(node)) {
      node.forEach(visit);
    } else if (node && typeof node === "object") {
      for (const [key, value] of Object.entries(node as Record<string, unknown>)) {
        if (key === "cite" && typeof value === "string") count += 1;
        else if (Array.isArray(value) && ["cites", "source_cites", "baseline_cites"].includes(key))
          count += value.filter((v) => typeof v === "string").length;
        else visit(value);
      }
    }
  };
  visit(payload.result);
  return count;
}
