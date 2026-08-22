import { useEffect, useMemo, useState } from "react";
import { fetchSnapshot } from "./api";
import { LiveBadge } from "./components/Chips";
import { IncidentsTable, ReviewsTable } from "./components/Tables";
import type { DashboardSnapshot } from "./types";

type View = "incidents" | "reviews";

export default function App() {
  const [snapshot, setSnapshot] = useState<DashboardSnapshot>({
    generated_at: "",
    counts: {},
    incidents: [],
    reviews: [],
  });
  const [online, setOnline] = useState(false);
  const [view, setView] = useState<View>("incidents");
  const [statusFilter, setStatusFilter] = useState<string>("all");

  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();
    const poll = async (): Promise<void> => {
      const data = await fetchSnapshot(controller.signal);
      if (cancelled) return;
      setSnapshot(data);
      setOnline(data.generated_at !== "");
    };
    void poll();
    const timer = window.setInterval(() => void poll(), 4000);
    return () => {
      cancelled = true;
      controller.abort();
      window.clearInterval(timer);
    };
  }, []);

  const stats = useMemo(() => {
    const incidents = snapshot.incidents;
    return {
      open: incidents.filter((i) => i.status === "open" || i.status === "investigating").length,
      summarized: incidents.filter((i) => i.status === "summarized").length,
      highConfidence: incidents.filter((i) => i.confidence === "high").length,
      failed: incidents.filter((i) => i.status === "failed").length,
      flaggedReviews: snapshot.reviews.filter((r) => r.verdict === "fail").length,
      totalReviews: snapshot.reviews.length,
      logEvents: snapshot.counts["log_events"] ?? 0,
      rollups: snapshot.counts["error_rollups"] ?? 0,
    };
  }, [snapshot]);

  const visibleIncidents = useMemo(
    () =>
      statusFilter === "all"
        ? snapshot.incidents
        : snapshot.incidents.filter((i) => i.status === statusFilter),
    [snapshot.incidents, statusFilter],
  );

  const statuses = ["all", "open", "investigating", "summarized", "failed"] as const;

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brandmark" /> AEGIS
          <div className="brandsub">incident engine · v0.3</div>
        </div>
        <nav>
          <div className="navsection">TRIAGE</div>
          <button className={view === "incidents" ? "on" : ""} onClick={() => setView("incidents")}>
            incidents
            <span className="count">{snapshot.incidents.length}</span>
          </button>
          <button className={view === "reviews" ? "on" : ""} onClick={() => setView("reviews")}>
            code reviews
            <span className="count warn">{stats.flaggedReviews || ""}</span>
          </button>
          <div className="navsection">ENGINE</div>
          <button disabled>agent runs</button>
          <button disabled>
            ingest
            <span className="count">{stats.logEvents}</span>
          </button>
        </nav>
        <div className="sidefooter">
          <div>model gpt-5.6-luna</div>
          <div>effort high · max turns 20</div>
          <div>rollups {stats.rollups}</div>
        </div>
      </aside>

      <main>
        <header>
          <h1>{view === "incidents" ? "Incident feed" : "Code reviews"}</h1>
          <p className="tagline">
            {view === "incidents"
              ? `${snapshot.incidents.length} incidents tracked · ${stats.summarized} summarized`
              : `${stats.totalReviews} reviews · ${stats.flaggedReviews} flagged by the rule engine`}
          </p>
          <LiveBadge online={online} generatedAt={snapshot.generated_at} />
        </header>

        <section className="cards">
          <Card label="OPEN / IN FLIGHT" value={String(stats.open)} sub="alerts awaiting a summary" />
          <Card
            label="HIGH CONFIDENCE"
            value={`${stats.highConfidence}/${snapshot.incidents.length || 0}`}
            sub="cited root causes"
          />
          <Card label="FAILED RUNS" value={String(stats.failed)} sub="provenance or turn-limit aborts" />
          <Card
            label="FLAGGED COMMITS"
            value={String(stats.flaggedReviews)}
            sub={`${stats.totalReviews} reviewed by rules`}
          />
        </section>

        {view === "incidents" && (
          <>
            <div className="filters">
              {statuses.map((s) => (
                <button
                  key={s}
                  className={`pill ${statusFilter === s ? "on" : ""}`}
                  onClick={() => setStatusFilter(s)}
                >
                  {s}
                </button>
              ))}
            </div>
            <IncidentsTable incidents={visibleIncidents} />
          </>
        )}
        {view === "reviews" && <ReviewsTable reviews={snapshot.reviews} />}
      </main>
    </div>
  );
}

function Card({ label, value, sub }: { label: string; value: string; sub: string }) {
  return (
    <div className="card">
      <div className="cardlabel">{label}</div>
      <div className="cardvalue">{value}</div>
      <div className="cardsub">{sub}</div>
    </div>
  );
}
