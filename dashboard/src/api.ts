import type { DashboardSnapshot, Incident, IncidentDetail, IngestStats, ReviewDetail } from "./types";

const jsonFetch = async <T,>(url: string, signal?: AbortSignal): Promise<T> => {
  const response = await fetch(url, { cache: "no-store", signal });
  if (!response.ok) throw new Error(String(response.status));
  return (await response.json()) as T;
};

export const fetchIncidents = (signal?: AbortSignal): Promise<{ incidents: Incident[] }> =>
  jsonFetch("/api/incidents", signal);

export const fetchIncidentDetail = (
  id: string,
  signal?: AbortSignal,
): Promise<{ incident: IncidentDetail | null }> => jsonFetch(`/api/incidents/${id}`, signal);

export const fetchTelemetry = <T,>(service: string, signal?: AbortSignal): Promise<{ telemetry: T | null }> =>
  jsonFetch(`/api/telemetry?service=${encodeURIComponent(service)}`, signal);

export const fetchDiff = <T,>(service: string, signal?: AbortSignal): Promise<{ diff: T | null }> =>
  jsonFetch(`/api/diff?service=${encodeURIComponent(service)}`, signal);

export const fetchIngestStats = (signal?: AbortSignal): Promise<IngestStats> =>
  jsonFetch("/api/ingest-stats", signal);

export const fetchReviews = (signal?: AbortSignal): Promise<{ reviews: DashboardSnapshot["reviews"] }> =>
  jsonFetch("/api/reviews", signal);

export const fetchReviewDetail = (sha: string): Promise<{ detail: ReviewDetail | null }> =>
  jsonFetch(`/api/reviews/${sha}`);
