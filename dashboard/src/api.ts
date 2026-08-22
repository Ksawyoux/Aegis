import type { DashboardSnapshot, ReviewDetail } from "./types";

const EMPTY: DashboardSnapshot = {
  generated_at: "",
  counts: {},
  incidents: [],
  reviews: [],
};

export async function fetchSnapshot(signal?: AbortSignal): Promise<DashboardSnapshot> {
  try {
    const response = await fetch("/viz/dashboard", { cache: "no-store", signal });
    if (!response.ok) throw new Error(String(response.status));
    return (await response.json()) as DashboardSnapshot;
  } catch (error) {
    if (signal?.aborted) throw error;
    return EMPTY;
  }
}

export async function fetchReviewDetail(sha: string): Promise<ReviewDetail | null> {
  const response = await fetch(`/api/reviews/${sha}`, { cache: "no-store" });
  if (!response.ok) return null;
  const body = (await response.json()) as { detail: ReviewDetail | null };
  return body.detail;
}
