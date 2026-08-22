import type { CodeReviewRow, Incident } from "../types";
import { ConfidenceChip, StatusChip, VerdictChip } from "./Chips";

export function IncidentsTable({ incidents }: { incidents: Incident[] }) {
  if (incidents.length === 0) {
    return <div className="empty">No incidents yet — POST /webhooks/alert fires one.</div>;
  }
  return (
    <table>
      <thead>
        <tr>
          <th>INCIDENT</th>
          <th>SERVICE</th>
          <th>ROOT CAUSE</th>
          <th>CONFIDENCE</th>
          <th>STATUS</th>
          <th>WINDOW (UTC)</th>
        </tr>
      </thead>
      <tbody>
        {incidents.map((incident) => (
          <tr key={incident.id}>
            <td className="mono">INC-{1000 + incident.id}</td>
            <td>
              <div className="svc">{incident.service || "—"}</div>
              {incident.alert_name && <div className="sub">{incident.alert_name}</div>}
            </td>
            <td className="cause">
              {incident.root_cause ??
                (incident.status === "failed"
                  ? "Run aborted — no summary was written."
                  : "Investigation pending.")}
            </td>
            <td><ConfidenceChip level={incident.confidence} /></td>
            <td><StatusChip status={incident.status} /></td>
            <td className="mono window">
              {incident.window_start.slice(11, 16)} → {incident.window_end.slice(11, 16)}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function ReviewsTable({
  reviews,
  onSelect,
}: {
  reviews: CodeReviewRow[];
  onSelect: (sha: string) => void;
}) {
  if (reviews.length === 0) {
    return (
      <div className="empty">
        No reviews yet — push to a registered repo and the engine reviews it.
      </div>
    );
  }
  return (
    <table>
      <thead>
        <tr>
          <th>COMMIT / PR</th>
          <th>SERVICE</th>
          <th>DIFF</th>
          <th>FINDINGS</th>
          <th>VERDICT</th>
          <th>REVIEWED AT (UTC)</th>
        </tr>
      </thead>
      <tbody>
        {reviews.map((review) => {
          const label =
            review.source === "pull_request" && review.pr_number !== null
              ? `PR #${String(review.pr_number)} · ${review.sha.slice(0, 8)}`
              : review.sha.slice(0, 10);
          return (
            <tr
              key={`${review.sha}-${review.source}`}
              className={`row-${review.verdict} clickable`}
              onClick={() => onSelect(review.sha)}
            >
              <td className="mono">{label}</td>
              <td>{review.service || "—"}</td>
              <td className="mono diffstat">
                {review.files_changed}f{" "}
                <span className="add">+{review.additions}</span>{" "}
                <span className="del">−{review.deletions}</span>
              </td>
              <td className="mono">{review.findings}</td>
              <td><VerdictChip verdict={review.verdict} /></td>
              <td className="mono">{review.created_at.slice(0, 19)}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
