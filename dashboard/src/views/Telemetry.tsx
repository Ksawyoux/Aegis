import { useEffect, useMemo, useState, type CSSProperties } from "react";
import { fetchTelemetry } from "../api";
import type { Incident, Telemetry } from "../types";
import { cardStyle, MONO } from "../ui";

const STATUS_COLORS: Record<string, string> = {
  "5xx": "#bc2f32",
  "4xx": "#eaa300",
  "3xx": "#c4a300",
  "2xx": "#d1d1d1",
  none: "#707070",
};

export function TelemetryView({ incident }: { incident: Incident | null }) {
  const service = incident?.service || "checkout-api";
  const [telemetry, setTelemetry] = useState<Telemetry | null>(null);
  const [missing, setMissing] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setTelemetry(null);
    void fetchTelemetry<Telemetry>(service).then((d) => {
      if (cancelled) return;
      setTelemetry(d.telemetry);
      setMissing(d.telemetry ? null : (d as { detail?: string }).detail ?? "unavailable");
    });
    return () => {
      cancelled = true;
    };
  }, [service]);

  const buckets = useMemo(() => {
    if (!telemetry) return [];
    const byBucket = new Map<string, Record<string, number>>();
    for (const point of telemetry.series) {
      const key = point.bucket_start;
      const row = byBucket.get(key) ?? {};
      row[point.status_class] = (row[point.status_class] ?? 0) + point.count;
      byBucket.set(key, row);
    }
    const max = Math.max(
      1,
      ...[...byBucket.values()].map((r) => Object.values(r).reduce((a, b) => a + b, 0)),
    );
    return [...byBucket.entries()].map(([time, row]) => ({
      time,
      row,
      total: Object.values(row).reduce((a, b) => a + b, 0),
      max,
    }));
  }, [telemetry]);

  if (missing !== null) {
    return <div style={{ padding: "24px 28px" }}><div style={cardStyle} className="pad">{missing}</div></div>;
  }
  if (!telemetry) {
    return <div style={{ padding: "24px 28px", color: "#616161" }}>loading telemetry…</div>;
  }

  const legendClasses = [...new Set(telemetry.series.map((p) => p.status_class))];

  return (
    <div style={{ padding: "24px 28px 40px", display: "flex", flexDirection: "column", gap: 16 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <span style={windowChip()}>
          effective {telemetry.effective_window.start.slice(11, 19)} → {telemetry.effective_window.end.slice(11, 19)}Z
        </span>
        <span style={windowChip()}>
          baseline {telemetry.baseline_window.start.slice(11, 19)} → {telemetry.baseline_window.end.slice(11, 19)}Z
        </span>
        <span style={windowChip("#616161")}>snapped to 60s buckets</span>
        <span style={windowChip(telemetry.baseline_sparse ? "#c43501" : "#0e700e", telemetry.baseline_sparse ? "#fdf6f3" : "#f1faf1", telemetry.baseline_sparse ? "#f4bfab" : "#9fd89f")}>
          baseline_sparse: {String(telemetry.baseline_sparse)}
        </span>
      </div>

      <div style={{ ...cardStyle, padding: "20px 22px", display: "flex", flexDirection: "column", gap: 16 }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <span style={{ fontSize: 10, letterSpacing: "0.1em", color: "#616161" }}>
            EVENTS PER 60s BUCKET · {service}
          </span>
          <div style={{ display: "flex", gap: 14 }}>
            {legendClasses.map((cls) => (
              <span key={cls} style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, color: "#616161" }}>
                <span style={{ width: 9, height: 9, borderRadius: 2, background: STATUS_COLORS[cls] ?? "#d1d1d1" }} />
                {cls}
              </span>
            ))}
          </div>
        </div>
        <div style={{ display: "flex", alignItems: "flex-end", gap: 3, height: 188, paddingTop: 8, borderBottom: "1px solid #d1d1d1" }}>
          {buckets.map((bucket) => (
            <div
              key={bucket.time}
              title={`${bucket.time.slice(11, 16)}Z · ${Object.entries(bucket.row).map(([k, v]) => `${k} ${v}`).join(" · ")}`}
              style={{ flex: 1, display: "flex", flexDirection: "column", justifyContent: "flex-end", gap: 1, height: "100%" }}
            >
              {legendClasses.map((cls) => {
                const count = bucket.row[cls] ?? 0;
                return (
                  <div
                    key={cls}
                    style={{
                      width: "100%",
                      background: STATUS_COLORS[cls] ?? "#d1d1d1",
                      height: `${((count / bucket.max) * 100).toFixed(1)}%`,
                    }}
                  />
                );
              })}
            </div>
          ))}
        </div>
        <div style={{ display: "flex", justifyContent: "space-between", fontSize: 10, color: "#616161" }}>
          <span>{buckets[0]?.time.slice(11, 16)}</span>
          <span>{buckets[Math.floor(buckets.length / 2)]?.time.slice(11, 16)}</span>
          <span>{buckets[buckets.length - 1]?.time.slice(11, 16)}</span>
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) minmax(240px, 300px)", gap: 16, alignItems: "start" }}>
        <div style={{ ...cardStyle, overflow: "hidden" }}>
          <div style={{ padding: "12px 16px", background: "#fafafa", borderBottom: "1px solid #d1d1d1", fontSize: 10, letterSpacing: "0.11em", color: "#616161" }}>
            TOP TEMPLATES · ERROR POOL FIRST, THEN DELTA DESC
          </div>
          <div style={templateHeaderStyle}>
            <span>TEMPLATE</span><span>STATUS</span><span>LEVEL</span>
            <span style={{ textAlign: "right" }}>COUNT</span>
            <span style={{ textAlign: "right" }}>BASELINE</span>
            <span style={{ textAlign: "right" }}>DELTA</span>
          </div>
          {telemetry.top_templates.map((t) => (
            <div key={t.template_hash + t.level} style={{ ...templateHeaderStyle, padding: "12px 16px", borderBottom: "1px solid #f0f0f0", fontSize: 12, alignItems: "center", color: "#242424" }}>
              <span style={{ display: "flex", flexDirection: "column", gap: 3, minWidth: 0 }}>
                <span style={{ fontFamily: MONO, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {t.exemplar?.message ?? t.template_hash}
                </span>
                <span style={{ fontFamily: MONO, fontSize: 10, color: "#707070" }}>{t.template_hash}</span>
              </span>
              <span style={{ color: "#424242" }}>—</span>
              <span style={{ color: t.level === "error" ? "#bc2f32" : "#616161" }}>{t.level}</span>
              <span style={{ textAlign: "right", color: "#424242" }}>{t.count}</span>
              <span style={{ textAlign: "right", color: "#616161" }}>{t.baseline}</span>
              <span style={{ textAlign: "right", color: t.delta > 0 ? "#bc2f32" : "#0e700e" }}>
                {t.delta > 0 ? `+${t.delta}` : String(t.delta)}
              </span>
            </div>
          ))}
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <div style={{ ...cardStyle, padding: "18px 20px", display: "flex", flexDirection: "column", gap: 12 }}>
            <span style={{ fontSize: 10, letterSpacing: "0.1em", color: "#616161" }}>STATUS BREAKDOWN</span>
            {telemetry.status_breakdown.map((b) => {
              const total = telemetry.status_breakdown.reduce((a, x) => a + x.count, 0) || 1;
              return (
                <div key={b.status_class} style={{ display: "flex", flexDirection: "column", gap: 5 }}>
                  <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12 }}>
                    <span style={{ color: "#424242" }}>{b.status_class}</span>
                    <span style={{ color: "#616161" }}>{b.count}</span>
                  </div>
                  <div style={{ height: 5, background: "#f0f0f0", borderRadius: 2, overflow: "hidden" }}>
                    <div style={{ height: "100%", background: STATUS_COLORS[b.status_class] ?? "#d1d1d1", width: `${((b.count / total) * 100).toFixed(0)}%` }} />
                  </div>
                </div>
              );
            })}
          </div>
          <div style={{ ...cardStyle, padding: "18px 20px", display: "flex", flexDirection: "column", gap: 10 }}>
            <span style={{ fontSize: 10, letterSpacing: "0.1em", color: "#616161" }}>SAMPLE TRACE IDS</span>
            {telemetry.sample_trace_ids.map((t) => (
              <span key={t} style={{ fontFamily: MONO, fontSize: 12, color: "#115ea3" }}>{t}</span>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

const templateHeaderStyle: CSSProperties = {
  display: "grid",
  gridTemplateColumns: "1fr 68px 72px 70px 82px 78px",
  gap: 10,
  padding: "9px 16px",
  borderBottom: "1px solid #e0e0e0",
  fontSize: 10,
  letterSpacing: "0.09em",
  color: "#616161",
};

function windowChip(color = "#424242", bg = "#ffffff", border = "#e0e0e0"): CSSProperties {
  return { fontSize: 12, padding: "4px 9px", border: `1px solid ${border}`, borderRadius: 4, background: bg, color };
}
