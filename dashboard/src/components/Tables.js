import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { ConfidenceChip, StatusChip, VerdictChip } from "./Chips";
export function IncidentsTable({ incidents }) {
    if (incidents.length === 0) {
        return _jsx("div", { className: "empty", children: "No incidents yet \u2014 POST /webhooks/alert fires one." });
    }
    return (_jsxs("table", { children: [_jsx("thead", { children: _jsxs("tr", { children: [_jsx("th", { children: "INCIDENT" }), _jsx("th", { children: "SERVICE" }), _jsx("th", { children: "ROOT CAUSE" }), _jsx("th", { children: "CONFIDENCE" }), _jsx("th", { children: "STATUS" }), _jsx("th", { children: "WINDOW (UTC)" })] }) }), _jsx("tbody", { children: incidents.map((incident) => (_jsxs("tr", { children: [_jsxs("td", { className: "mono", children: ["INC-", 1000 + incident.id] }), _jsxs("td", { children: [_jsx("div", { className: "svc", children: incident.service || "—" }), incident.alert_name && _jsx("div", { className: "sub", children: incident.alert_name })] }), _jsx("td", { className: "cause", children: incident.root_cause ??
                                (incident.status === "failed"
                                    ? "Run aborted — no summary was written."
                                    : "Investigation pending.") }), _jsx("td", { children: _jsx(ConfidenceChip, { level: incident.confidence }) }), _jsx("td", { children: _jsx(StatusChip, { status: incident.status }) }), _jsxs("td", { className: "mono window", children: [incident.window_start.slice(11, 16), " \u2192 ", incident.window_end.slice(11, 16)] })] }, incident.id))) })] }));
}
export function ReviewsTable({ reviews }) {
    if (reviews.length === 0) {
        return (_jsx("div", { className: "empty", children: "No reviews yet \u2014 push to a registered repo and the engine reviews it." }));
    }
    return (_jsxs("table", { children: [_jsx("thead", { children: _jsxs("tr", { children: [_jsx("th", { children: "COMMIT / PR" }), _jsx("th", { children: "SERVICE" }), _jsx("th", { children: "DIFF" }), _jsx("th", { children: "FINDINGS" }), _jsx("th", { children: "VERDICT" }), _jsx("th", { children: "REVIEWED AT (UTC)" })] }) }), _jsx("tbody", { children: reviews.map((review) => {
                    const label = review.source === "pull_request" && review.pr_number !== null
                        ? `PR #${String(review.pr_number)} · ${review.sha.slice(0, 8)}`
                        : review.sha.slice(0, 10);
                    return (_jsxs("tr", { className: `row-${review.verdict}`, children: [_jsx("td", { className: "mono", children: label }), _jsx("td", { children: review.service || "—" }), _jsxs("td", { className: "mono diffstat", children: [review.files_changed, "f", " ", _jsxs("span", { className: "add", children: ["+", review.additions] }), " ", _jsxs("span", { className: "del", children: ["\u2212", review.deletions] })] }), _jsx("td", { className: "mono", children: review.findings }), _jsx("td", { children: _jsx(VerdictChip, { verdict: review.verdict }) }), _jsx("td", { className: "mono", children: review.created_at.slice(0, 19) })] }, `${review.sha}-${review.source}`));
                }) })] }));
}
