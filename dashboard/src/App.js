import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { useEffect, useMemo, useState } from "react";
import { fetchSnapshot } from "./api";
import { LiveBadge } from "./components/Chips";
import { ReviewDetailPanel } from "./components/ReviewDetail";
import { IncidentsTable, ReviewsTable } from "./components/Tables";
export default function App() {
    const [snapshot, setSnapshot] = useState({
        generated_at: "",
        counts: {},
        incidents: [],
        reviews: [],
    });
    const [online, setOnline] = useState(false);
    const [view, setView] = useState("incidents");
    const [statusFilter, setStatusFilter] = useState("all");
    const [selectedSha, setSelectedSha] = useState(null);
    useEffect(() => {
        let cancelled = false;
        const controller = new AbortController();
        const poll = async () => {
            const data = await fetchSnapshot(controller.signal);
            if (cancelled)
                return;
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
    const visibleIncidents = useMemo(() => statusFilter === "all"
        ? snapshot.incidents
        : snapshot.incidents.filter((i) => i.status === statusFilter), [snapshot.incidents, statusFilter]);
    const statuses = ["all", "open", "investigating", "summarized", "failed"];
    return (_jsxs("div", { className: "shell", children: [_jsxs("aside", { className: "sidebar", children: [_jsxs("div", { className: "brand", children: [_jsx("span", { className: "brandmark" }), " AEGIS", _jsx("div", { className: "brandsub", children: "incident engine \u00B7 v0.3" })] }), _jsxs("nav", { children: [_jsx("div", { className: "navsection", children: "TRIAGE" }), _jsxs("button", { className: view === "incidents" ? "on" : "", onClick: () => setView("incidents"), children: ["incidents", _jsx("span", { className: "count", children: snapshot.incidents.length })] }), _jsxs("button", { className: view === "reviews" ? "on" : "", onClick: () => setView("reviews"), children: ["code reviews", _jsx("span", { className: "count warn", children: stats.flaggedReviews || "" })] }), _jsx("div", { className: "navsection", children: "ENGINE" }), _jsx("button", { disabled: true, children: "agent runs" }), _jsxs("button", { disabled: true, children: ["ingest", _jsx("span", { className: "count", children: stats.logEvents })] })] }), _jsxs("div", { className: "sidefooter", children: [_jsx("div", { children: "model gpt-5.6-luna" }), _jsx("div", { children: "effort high \u00B7 max turns 20" }), _jsxs("div", { children: ["rollups ", stats.rollups] })] })] }), _jsxs("main", { children: [_jsxs("header", { children: [_jsx("h1", { children: view === "incidents" ? "Incident feed" : "Code reviews" }), _jsx("p", { className: "tagline", children: view === "incidents"
                                    ? `${snapshot.incidents.length} incidents tracked · ${stats.summarized} summarized`
                                    : `${stats.totalReviews} reviews · ${stats.flaggedReviews} flagged by the rule engine` }), _jsx(LiveBadge, { online: online, generatedAt: snapshot.generated_at })] }), _jsxs("section", { className: "cards", children: [_jsx(Card, { label: "OPEN / IN FLIGHT", value: String(stats.open), sub: "alerts awaiting a summary" }), _jsx(Card, { label: "HIGH CONFIDENCE", value: `${stats.highConfidence}/${snapshot.incidents.length || 0}`, sub: "cited root causes" }), _jsx(Card, { label: "FAILED RUNS", value: String(stats.failed), sub: "provenance or turn-limit aborts" }), _jsx(Card, { label: "FLAGGED COMMITS", value: String(stats.flaggedReviews), sub: `${stats.totalReviews} reviewed by rules` })] }), view === "incidents" && (_jsxs(_Fragment, { children: [_jsx("div", { className: "filters", children: statuses.map((s) => (_jsx("button", { className: `pill ${statusFilter === s ? "on" : ""}`, onClick: () => setStatusFilter(s), children: s }, s))) }), _jsx(IncidentsTable, { incidents: visibleIncidents })] })), view === "reviews" && (_jsx(ReviewsTable, { reviews: snapshot.reviews, onSelect: (sha) => setSelectedSha(sha) }))] }), selectedSha !== null && (_jsx(ReviewDetailPanel, { sha: selectedSha, onClose: () => setSelectedSha(null) }))] }));
}
function Card({ label, value, sub }) {
    return (_jsxs("div", { className: "card", children: [_jsx("div", { className: "cardlabel", children: label }), _jsx("div", { className: "cardvalue", children: value }), _jsx("div", { className: "cardsub", children: sub })] }));
}
