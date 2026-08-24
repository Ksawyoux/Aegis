import { useEffect, useState } from "react";
import { fetchDiff } from "../api";
import type { DiffPayload, Incident } from "../types";
import { cardStyle, MONO } from "../ui";

export function DiffView({ incident }: { incident: Incident | null }) {
  const service = incident?.service || "checkout-api";
  const [diff, setDiff] = useState<DiffPayload | null>(null);
  const [missing, setMissing] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setDiff(null);
    void fetchDiff<DiffPayload>(service).then((d) => {
      if (cancelled) return;
      setDiff(d.diff);
      setMissing(d.diff ? null : (d as { detail?: string }).detail ?? "unavailable");
    });
    return () => {
      cancelled = true;
    };
  }, [service]);

  if (missing !== null) {
    return <div style={{ padding: "24px 28px" }}><div style={cardStyle}>{missing}</div></div>;
  }
  if (!diff) {
    return <div style={{ padding: "24px 28px", color: "#616161" }}>loading diff…</div>;
  }

  return (
    <div style={{ padding: "24px 28px 40px", display: "flex", flexDirection: "column", gap: 16 }}>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 12 }}>
        <CountCard label="COMMITS" value={diff.counts.commits} />
        <CountCard label="DEPLOYMENTS" value={diff.counts.deployments} />
        <CountCard label="INFRA CHANGES" value={diff.counts.infra_changes} />
      </div>

      <div style={{ ...cardStyle, padding: "20px 22px", display: "flex", flexDirection: "column", gap: 16 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <span style={{ fontSize: 12, fontWeight: 600 }}>focus · {diff.focus.service}</span>
          <span style={{ fontSize: 12, color: "#616161" }}>
            window {diff.window.start.slice(11, 16)} → {diff.window.end.slice(11, 16)}Z
          </span>
        </div>

        {diff.focus.deployments.length === 0 && (
          <span style={{ fontSize: 12, color: "#616161" }}>No deployments in the window.</span>
        )}
        {diff.focus.deployments.map((dep) => (
          <div key={dep.cite} style={{ border: "1px solid #e0e0e0", borderRadius: 4, overflow: "hidden" }}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, padding: "12px 14px", background: "#fafafa", borderBottom: "1px solid #e0e0e0" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                <span style={{ fontSize: 10, letterSpacing: "0.08em", padding: "2px 6px", borderRadius: 2, background: "#f0fafa", color: "#037679" }}>deploy</span>
                <span style={{ fontSize: 12 }}>{dep.environment}</span>
                <span style={{ fontSize: 12, color: "#616161" }}>{dep.status} · {String(dep.started_at).slice(11, 19)}Z</span>
              </div>
              <span style={{ fontFamily: MONO, fontSize: 10, color: "#707070" }}>
                {dep.cite.length > 28 ? dep.cite.slice(0, 28) + "…" : dep.cite}
              </span>
            </div>
            <div style={{ padding: 14, display: "flex", flexDirection: "column", gap: 10 }}>
              {dep.commit && (
                <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
                  <span style={{ fontFamily: MONO, fontSize: 12, color: "#115ea3" }}>{dep.commit.sha.slice(0, 7)}</span>
                  <span style={{ fontSize: 12, color: "#242424" }}>{dep.commit.message}</span>
                </div>
              )}
              {dep.commit?.files_changed.map((file) => (
                <div key={file.path} style={{ border: "1px solid #e0e0e0", borderRadius: 2, overflow: "hidden" }}>
                  <div style={{ display: "flex", justifyContent: "space-between", padding: "8px 11px", background: "#fafafa", borderBottom: "1px solid #e0e0e0", fontSize: 12 }}>
                    <span style={{ color: "#424242" }}>{file.path}</span>
                    <span style={{ color: "#616161" }}>
                      {file.hunks_omitted ? `hunks omitted (${file.hunks_omitted})` : file.status}
                    </span>
                  </div>
                  {parseHunks(file.hunks).map((h, i) => {
                    const added = h.text.startsWith("+");
                    const removed = h.text.startsWith("-");
                    return (
                      <div key={i} style={{ display: "grid", gridTemplateColumns: "44px 1fr", fontSize: 12, lineHeight: 1.7, background: added ? "#f1faf1" : removed ? "#fdf6f6" : "#ffffff" }}>
                        <span style={{ fontFamily: MONO, color: "#bdbdbd", textAlign: "right", paddingRight: 10, borderRight: "1px solid #e0e0e0" }}>
                          {h.lineNo}
                        </span>
                        <span style={{ fontFamily: MONO, color: added ? "#0e700e" : removed ? "#bc2f32" : "#424242", paddingLeft: 10, whiteSpace: "pre" }}>
                          {h.text}
                        </span>
                      </div>
                    );
                  })}
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) minmax(0, 1fr)", gap: 16, alignItems: "start" }}>
        <div style={{ ...cardStyle, padding: "18px 20px", display: "flex", flexDirection: "column", gap: 12 }}>
          <span style={{ fontSize: 10, letterSpacing: "0.1em", color: "#616161" }}>OTHER SERVICES IN WINDOW</span>
          {Object.keys(diff.other_services).length === 0 && (
            <span style={{ fontSize: 12, color: "#616161" }}>No peer activity.</span>
          )}
          {Object.entries(diff.other_services).map(([svc, info]) => (
            <div key={svc} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", paddingBottom: 10, borderBottom: "1px solid #f0f0f0", fontSize: 12 }}>
              <span>{svc}</span>
              <span style={{ fontSize: 12, color: "#616161" }}>{info.commits} commits · {info.deployments} deploys</span>
            </div>
          ))}
        </div>
        <div style={{ ...cardStyle, padding: "18px 20px", display: "flex", flexDirection: "column", gap: 12 }}>
          <span style={{ fontSize: 10, letterSpacing: "0.1em", color: "#616161" }}>UNATTRIBUTED INFRA CHANGES</span>
          {diff.unattributed.length === 0 && (
            <span style={{ fontSize: 12, color: "#616161" }}>None in the window.</span>
          )}
          {diff.unattributed.map((i) => (
            <div key={i.uid} style={{ display: "flex", flexDirection: "column", gap: 4, paddingBottom: 10, borderBottom: "1px solid #f0f0f0" }}>
              <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12 }}>
                <span>{i.resource_name}</span>
                <span style={{ color: "#616161", fontSize: 12 }}>{i.action} · {String(i.applied_at).slice(11, 19)}Z</span>
              </div>
              <span style={{ fontFamily: MONO, fontSize: 10, color: "#707070" }}>
                {JSON.stringify(i.attribute_diff).slice(0, 110)}
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function CountCard({ label, value }: { label: string; value: number }) {
  return (
    <div style={{ ...cardStyle, padding: "14px 16px", display: "flex", alignItems: "baseline", justifyContent: "space-between" }}>
      <span style={{ fontSize: 10, letterSpacing: "0.1em", color: "#616161" }}>{label}</span>
      <span style={{ fontSize: 20, fontWeight: 500 }}>{value}</span>
    </div>
  );
}


function parseHunks(hunks: string | null): { text: string; lineNo: number | null }[] {
  if (!hunks) return [];
  let lineNo: number | null = null;
  const out: { text: string; lineNo: number | null }[] = [];
  for (const raw of hunks.split("\n")) {
    if (raw === "") continue;
    const hunk = /^@@ -\d+(?:,\d+)? \+(\d+)/.exec(raw);
    if (hunk !== null) {
      lineNo = Number(hunk[1]);
      out.push({ text: raw, lineNo: null });
      continue;
    }
    if (raw.startsWith("+")) {
      out.push({ text: raw, lineNo });
      lineNo = lineNo === null ? null : lineNo + 1;
    } else if (raw.startsWith("-")) {
      out.push({ text: raw, lineNo: null });
    } else {
      out.push({ text: raw, lineNo });
      lineNo = lineNo === null ? null : lineNo + 1;
    }
  }
  return out;
}
