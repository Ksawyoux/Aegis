import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { useEffect, useState } from "react";
import { fetchReviewDetail } from "../api";
import { VerdictChip } from "./Chips";
function parsePatch(patch) {
    const files = [];
    let current = null;
    let a = 0;
    let b = 0;
    for (const raw of patch.split("\n")) {
        if (raw.startsWith("diff --git")) {
            current = { path: raw.replace(/^diff --git a\/(\S+) b\/.*/, "$1"), lines: [] };
            files.push(current);
            continue;
        }
        if (current === null)
            continue;
        if (raw.startsWith("--- ") || raw.startsWith("+++ ") || raw.startsWith("index ")) {
            current.lines.push({ kind: "meta", text: raw, a: null, b: null });
            continue;
        }
        const hunk = /^@@ -\d+(?:,\d+)? \+(\d+)/.exec(raw);
        if (hunk !== null) {
            b = Number(hunk[1]);
            a = Number(/^@@ -(\d+)/.exec(raw)?.[1] ?? "0");
            current.lines.push({ kind: "hunk", text: raw, a: null, b: null });
            continue;
        }
        if (raw.startsWith("+")) {
            current.lines.push({ kind: "add", text: raw, a: null, b });
            b += 1;
        }
        else if (raw.startsWith("-")) {
            current.lines.push({ kind: "del", text: raw, a, b: null });
            a += 1;
        }
        else {
            current.lines.push({ kind: "ctx", text: raw || " ", a, b });
            a += 1;
            b += 1;
        }
    }
    return files.filter((f) => f.path !== "");
}
function FindingCard({ finding }) {
    return (_jsxs("div", { className: `finding sev-${finding.severity}`, children: [_jsxs("div", { className: "findinghead", children: [_jsx("span", { className: `sevchip ${finding.severity}`, children: finding.severity }), _jsx("span", { className: "mono ruleid", children: finding.rule_id }), _jsxs("span", { className: "mono loc", children: [finding.path, "#L", finding.line] })] }), _jsx("p", { className: "findingmsg", children: finding.message }), _jsx("pre", { className: "evidence", children: finding.evidence }), _jsxs("div", { className: "fixbox", children: [_jsx("span", { className: "fixlabel", children: "How to fix" }), " ", finding.remediation] })] }));
}
export function ReviewDetailPanel({ sha, onClose }) {
    const [detail, setDetail] = useState(null);
    const [tab, setTab] = useState("findings");
    useEffect(() => {
        setDetail(null);
        void fetchReviewDetail(sha).then((d) => setDetail(d));
    }, [sha]);
    const files = detail?.patch ? parsePatch(detail.patch) : [];
    return (_jsx("div", { className: "modalbackdrop", onClick: onClose, children: _jsxs("div", { className: "modal", onClick: (e) => e.stopPropagation(), children: [_jsxs("header", { className: "modalhead", children: [_jsxs("div", { children: [_jsx("div", { className: "modalttl mono", children: sha.slice(0, 12) }), _jsx("div", { className: "sub", children: detail
                                        ? `${detail.source}${detail.pr_number ? ` · PR #${detail.pr_number}` : ""} · ${detail.service || "unattributed"}`
                                        : "loading…" })] }), detail && _jsx(VerdictChip, { verdict: detail.verdict }), _jsx("button", { className: "closebtn", onClick: onClose, children: "\u2715" })] }), detail === null && _jsx("div", { className: "empty", children: "loading\u2026" }), detail !== null && (_jsxs(_Fragment, { children: [_jsxs("div", { className: "tabs", children: [_jsxs("button", { className: tab === "findings" ? "on" : "", onClick: () => setTab("findings"), children: ["Findings (", detail.findings.length, ")"] }), _jsxs("button", { className: tab === "diff" ? "on" : "", onClick: () => setTab("diff"), children: ["File changes (", files.length, ")"] })] }), tab === "findings" &&
                            (detail.findings.length === 0 ? (_jsx("div", { className: "empty", children: "No rule matched any added line \u2014 clean review." })) : (detail.findings.map((f, i) => _jsx(FindingCard, { finding: f }, i)))), tab === "diff" && (_jsxs("div", { children: [detail.patch === null && (_jsx("div", { className: "empty", children: "Diff was not stored for this review." })), detail.patch !== null && files.length === 0 && (_jsx("div", { className: "empty", children: "No textual changes in this commit." })), files.map((file) => (_jsxs("div", { className: "fileblock", children: [_jsx("div", { className: "filepath mono", children: file.path }), _jsx("pre", { className: "diffview", children: file.lines.map((line, i) => (_jsxs("div", { className: `dl ${line.kind}`, children: [_jsx("span", { className: "lnum", children: line.b !== null ? String(line.b) : "" }), _jsx("span", { children: line.text })] }, i))) })] }, file.path)))] }))] }))] }) }));
}
