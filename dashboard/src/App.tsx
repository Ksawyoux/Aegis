import { useEffect, useMemo, useState } from "react";
import { fetchIncidentDetail, fetchIncidents, fetchReviews } from "./api";
import { AgentRunView } from "./views/AgentRun";
import { DiffView } from "./views/Diff";
import { IncidentsView } from "./views/Incidents";
import { IngestView } from "./views/Ingest";
import { InvestigationView } from "./views/Investigation";
import { ReviewsView } from "./views/Reviews";
import { TelemetryView } from "./views/Telemetry";
import type { Incident, IncidentDetail } from "./types";
import { MONO, navButton } from "./ui";

type Screen =
  | "incidents"
  | "investigation"
  | "telemetry"
  | "diff"
  | "reviews"
  | "agent"
  | "ingest";

export default function App() {
  const [screen, setScreen] = useState<Screen>("incidents");
  const [layout, setLayout] = useState<"table" | "split">("table");
  const [filter, setFilter] = useState("all");
  const [incidents, setIncidents] = useState<Incident[] | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<IncidentDetail | null>(null);
  const [flagged, setFlagged] = useState(0);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [online, setOnline] = useState(true);
  const [clock, setClock] = useState("");

  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();
    const poll = async (): Promise<void> => {
      try {
        const [inc, rev] = await Promise.all([
          fetchIncidents(controller.signal),
          fetchReviews(controller.signal),
        ]);
        if (cancelled) return;
        setIncidents(inc.incidents);
        setFlagged(rev.reviews.filter((r) => r.verdict === "fail").length);
        const liveResponse = await fetch("/viz/live", { cache: "no-store", signal: controller.signal });
        const live = (await liveResponse.json()) as { counts: Record<string, number> };
        setCounts(live.counts);
        setOnline(true);
        setClock(new Date().toISOString().slice(0, 16).replace("T", " ") + " UTC");
      } catch {
        if (!cancelled) setOnline(false);
      }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), 4000);
    return () => {
      cancelled = true;
      controller.abort();
      window.clearInterval(timer);
    };
  }, []);

  useEffect(() => {
    if (screen !== "agent" || selectedId === null) return;
    let cancelled = false;
    void fetchIncidentDetail(selectedId.replace("INC-", "")).then((d) => {
      if (!cancelled) setDetail(d.incident);
    });
    return () => {
      cancelled = true;
    };
  }, [screen, selectedId]);

  const selected = useMemo(
    () =>
      incidents?.find((i) => i.id === selectedId) ??
      incidents?.find((i) => i.status === "summarized") ??
      incidents?.[0] ??
      null,
    [incidents, selectedId],
  );

  const titles: Record<Screen, [string, string]> = {
    incidents: [
      "Incident feed",
      `${incidents?.length ?? 0} incidents tracked · ${incidents?.filter((i) => i.status !== "summarized" && i.status !== "failed").length ?? 0} unresolved by the agent`,
    ],
    investigation: [
      selected ? `${selected.id} · ${selected.service}` : "Investigation",
      selected ? `${selected.alert} · dedup ${selected.dedup} · ${selected.status}` : "",
    ],
    telemetry: [
      "Error telemetry",
      "get_error_telemetry · 60s rollups against the preceding disjoint window",
    ],
    diff: ["Incident diff", "get_incident_diff · commits, deployments and infra changes in the alert window"],
    reviews: ["Code reviews", `${flagged} flagged by the rule engine`],
    agent: ["Agent run trace", "aggregates-only tool access · provenance validated"],
    ingest: ["Ingest health", "watermarks, unresolved events, and per-source rows"],
  };
  const [screenTitle, screenSub] = titles[screen];

  const openInvestigation = (inc: Incident): void => {
    setSelectedId(inc.id);
    setScreen("investigation");
  };

  return (
    <div style={{ display: "grid", gridTemplateColumns: "216px minmax(0, 1fr)", minWidth: 1160, minHeight: "100vh", background: "#f5f5f5" }}>
      <aside style={{ background: "#292929", color: "#d6d6d6", display: "flex", flexDirection: "column", gap: 28, padding: "20px 14px", position: "sticky", top: 0, height: "100vh" }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 6, padding: "0 8px" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <div style={{ width: 10, height: 10, background: "#0f6cbd", borderRadius: 2 }} />
            <span style={{ fontSize: 14, fontWeight: 600, letterSpacing: "0.14em", color: "#ffffff" }}>AEGIS</span>
          </div>
          <span style={{ fontSize: 10, color: "#bdbdbd", letterSpacing: "0.04em" }}>incident engine · v0.3</span>
        </div>

        <nav style={{ display: "flex", flexDirection: "column", gap: 2 }}>
          <div style={{ fontSize: 10, letterSpacing: "0.14em", color: "#bdbdbd", padding: "0 8px 8px" }}>TRIAGE</div>
          <NavButton label="incidents" badge={String(incidents?.length ?? 0)} active={screen === "incidents"} onClick={() => setScreen("incidents")} />
          <NavButton label="investigation" active={screen === "investigation"} onClick={() => selected && setScreen("investigation")} />
          <NavButton label="telemetry" active={screen === "telemetry"} onClick={() => setScreen("telemetry")} />
          <NavButton label="diff" active={screen === "diff"} onClick={() => setScreen("diff")} />
          <NavButton label="code reviews" badge={flagged ? String(flagged) : undefined} active={screen === "reviews"} onClick={() => setScreen("reviews")} />
          <div style={{ fontSize: 10, letterSpacing: "0.14em", color: "#bdbdbd", padding: "18px 8px 8px" }}>ENGINE</div>
          <NavButton label="agent runs" active={screen === "agent"} onClick={() => setScreen("agent")} />
          <NavButton label="ingest" active={screen === "ingest"} onClick={() => setScreen("ingest")} />
        </nav>

        <div style={{ marginTop: "auto", display: "flex", flexDirection: "column", gap: 8, padding: 12, border: "1px solid #424242", borderRadius: 4 }}>
          <div style={{ fontSize: 10, letterSpacing: "0.12em", color: "#bdbdbd" }}>AGENT</div>
          <Row k="model" v="gpt-5.6-luna" />
          <Row k="effort" v="high" />
          <Row k="max turns" v="20" />
        </div>
      </aside>

      <main style={{ display: "flex", flexDirection: "column", minWidth: 0 }}>
        <header style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 24, padding: "16px 28px", background: "#ffffff", borderBottom: "1px solid #d1d1d1", position: "sticky", top: 0, zIndex: 5 }}>
          <div style={{ display: "flex", flexDirection: "column", gap: 3, minWidth: 0 }}>
            <h1 style={{ margin: 0, fontSize: 16, fontWeight: 600, letterSpacing: "-0.01em" }}>{screenTitle}</h1>
            <span style={{ fontSize: 12, color: "#616161" }}>{screenSub}</span>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 7, fontSize: 12, color: online ? "#616161" : "#bc2f32", padding: "5px 9px", border: "1px solid #e0e0e0", borderRadius: 4, background: "#fafafa" }}>
              <span style={{ width: 6, height: 6, borderRadius: "50%", background: online ? "#107c10" : "#bc2f32" }} />
              <span>{online ? "ingest live · lag 4s" : "api offline"}</span>
            </div>
            <div style={{ fontSize: 12, color: "#616161", padding: "5px 9px", border: "1px solid #e0e0e0", borderRadius: 4, background: "#fafafa", fontFamily: MONO }}>
              {clock}
            </div>
          </div>
        </header>

        {incidents === null && (
          <div style={{ padding: "24px 28px", color: "#616161" }}>loading…</div>
        )}
        {incidents !== null && screen === "incidents" && (
          <IncidentsView
            incidents={incidents}
            filter={filter}
            setFilter={setFilter}
            layout={layout}
            setLayout={setLayout}
            onOpen={openInvestigation}
            selectedId={selected?.id ?? null}
            onSelect={(inc) => setSelectedId(inc.id)}
          />
        )}
        {incidents !== null && screen === "investigation" && selected && (
          <InvestigationView incident={selected} />
        )}
        {screen === "telemetry" && <TelemetryView incident={selected} />}
        {screen === "diff" && <DiffView incident={selected} />}
        {screen === "reviews" && <ReviewsView flagged={flagged} />}
        {screen === "agent" && <AgentRunView detail={detail} />}
        {screen === "ingest" && <IngestView counts={counts} />}
      </main>
    </div>
  );
}

function NavButton({
  label,
  badge,
  active,
  onClick,
}: {
  label: string;
  badge?: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button style={navButton(active)} onClick={onClick}>
      <span>{label}</span>
      {badge !== undefined && (
        <span style={{ fontSize: 10, padding: "1px 5px", borderRadius: 2, background: "#424242", color: "#f5f5f5" }}>
          {badge}
        </span>
      )}
    </button>
  );
}

function Row({ k, v }: { k: string; v: string }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12 }}>
      <span style={{ color: "#bdbdbd" }}>{k}</span>
      <span style={{ color: "#f5f5f5" }}>{v}</span>
    </div>
  );
}
