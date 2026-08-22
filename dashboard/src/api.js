const EMPTY = {
    generated_at: "",
    counts: {},
    incidents: [],
    reviews: [],
};
export async function fetchSnapshot(signal) {
    try {
        const response = await fetch("/viz/dashboard", { cache: "no-store", signal });
        if (!response.ok)
            throw new Error(String(response.status));
        return (await response.json());
    }
    catch (error) {
        if (signal?.aborted)
            throw error;
        return EMPTY;
    }
}
export async function fetchReviewDetail(sha) {
    const response = await fetch(`/api/reviews/${sha}`, { cache: "no-store" });
    if (!response.ok)
        return null;
    const body = (await response.json());
    return body.detail;
}
