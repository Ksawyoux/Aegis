import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
export function ConfidenceChip({ level }) {
    if (level === null)
        return _jsx("span", { className: "chip dim", children: "\u2014" });
    return _jsx("span", { className: `chip ${level}`, children: level });
}
export function StatusChip({ status }) {
    return _jsx("span", { className: `status ${status}`, children: status });
}
export function VerdictChip({ verdict }) {
    return (_jsx("span", { className: `chip verdict-${verdict}`, children: verdict === "fail" ? "flagged" : verdict }));
}
export function LiveBadge({ online, generatedAt }) {
    return (_jsxs("div", { className: "livegroup", children: [_jsxs("span", { className: `livebadge ${online ? "" : "off"}`, children: [_jsx("span", { className: "dot" }), online ? "ingest live · lag 4s" : "api offline"] }), generatedAt && _jsxs("span", { className: "utcclock", children: [generatedAt.slice(0, 19), " UTC"] })] }));
}
