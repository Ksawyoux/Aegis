/* Design tokens and shared inline styles, mirrored from the reference dashboard. */

import type { CSSProperties } from "react";

export const SEV: Record<string, string> = {
  critical: "#bc2f32",
  high: "#da3b01",
  medium: "#c43501",
  low: "#707070",
};

export const CONF: Record<string, { color: string; bg: string; border: string }> = {
  high: { color: "#0e700e", bg: "#f1faf1", border: "#9fd89f" },
  medium: { color: "#c43501", bg: "#fdf6f3", border: "#f4bfab" },
  low: { color: "#616161", bg: "#fafafa", border: "#e0e0e0" },
};

export const KIND: Record<string, { bg: string; color: string }> = {
  rollup: { bg: "#ebf3fc", color: "#115ea3" },
  commit: { bg: "#f1faf1", color: "#0e700e" },
  deploy: { bg: "#f0fafa", color: "#037679" },
  log: { bg: "#fafafa", color: "#424242" },
  infra: { bg: "#fdf6f3", color: "#c43501" },
  postmortem: { bg: "#fdf6f6", color: "#bc2f32" },
};

export const CARD =
  "background: #ffffff; border: 1px solid #d1d1d1; border-radius: 4px; box-shadow: 0 0 2px rgba(0,0,0,0.12), 0 2px 4px rgba(0,0,0,0.14);";
export const MONO = "Consolas, 'Courier New', Courier, monospace";
export const LABEL = "font-size: 10px; letter-spacing: 0.1em; color: #616161;";

export const pill = (active: boolean): CSSProperties => ({
  fontFamily: "inherit",
  fontSize: 12,
  padding: "6px 11px",
  borderRadius: 4,
  cursor: "pointer",
  border: `1px solid ${active ? "#115ea3" : "#d1d1d1"}`,
  background: active ? "#ebf3fc" : "#ffffff",
  color: active ? "#115ea3" : "#424242",
});

export const navButton = (active: boolean): CSSProperties => ({
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: 8,
  width: "100%",
  textAlign: "left",
  fontFamily: "inherit",
  fontSize: 12,
  padding: "7px 8px",
  border: 0,
  borderRadius: 4,
  cursor: "pointer",
  background: active ? "#3d3d3d" : "transparent",
  color: active ? "#ffffff" : "#d6d6d6",
});

export const kindChip = (
  kind: string,
): { bg: string; color: string } => KIND[kind] ?? { bg: "#fafafa", color: "#424242" };

export const confChip = (
  confidence: string | null,
): { color: string; bg: string; border: string } =>
  CONF[confidence ?? "low"] ?? {
    color: "#616161",
    bg: "#fafafa",
    border: "#e0e0e0",
  };

export const sevColor = (severity: string): string =>
  SEV[severity] ?? "#707070";

export const cardStyle = {
  background: "#ffffff",
  border: "1px solid #d1d1d1",
  borderRadius: 4,
  boxShadow: "0 0 2px rgba(0,0,0,0.12), 0 2px 4px rgba(0,0,0,0.14)",
} as const;
