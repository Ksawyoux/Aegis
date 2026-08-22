"""Generate visualization/index.html from the committed corpus and recorded run data.

Inputs (all relative to the repository root):
  corpus/**                       evidence fixtures, read directly
  visualization/data/*.json       metrics and a recorded investigation trace

``mask`` and ``template_hash`` are imported from the production ingest code,
so every masked example on the page is what the pipeline actually computes.

Usage: uv run python visualization/generate.py
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from aegis.ingest.templates import mask, template_hash

ROOT = Path(__file__).parents[1]
OUT = Path(__file__).parent / "index.html"
DATA_DIR = Path(__file__).parent / "data"


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def source_samples() -> dict[str, Any]:
    checkout_git = load_json(ROOT / "corpus/git/checkout.json")
    commit = checkout_git["commits"][0]
    deploy = checkout_git["deploys"][0]
    log_line = (
        ROOT / "corpus/logs/checkout-api.log"
    ).read_text(encoding="utf-8").splitlines()[61]
    record = json.loads(log_line)
    message = record["msg"]
    services = yaml.safe_load((ROOT / "corpus/services.yaml").read_text(encoding="utf-8"))
    checkout_service = next(s for s in services if s["name"] == "checkout-api")
    applies = load_json(ROOT / "corpus/terraform/applies.json")
    pods = load_json(ROOT / "corpus/k8s/pod-status.json")
    events = load_json(ROOT / "corpus/k8s/events.json")

    return {
        "git": {
            "sha": commit["sha"],
            "author": commit["author"],
            "message": commit["message"],
            "hunk": commit["files_changed"][0]["hunks"].strip(),
            "deploy": deploy,
        },
        "logs": {
            "raw_line": log_line,
            "msg_raw": message,
            "msg_masked": mask(message),
            "template_hash": template_hash(message),
            "ts": record["ts"],
            "status": record["status"],
        },
        "resolve": {
            "name": checkout_service["name"],
            "repo": checkout_service["repo"],
            "log_keys": checkout_service["log_keys"],
            "k8s_names": checkout_service["k8s_names"],
        },
        "terraform": {
            "plan": "plan-payments-pool.json",
            "apply_id": applies[0]["apply_id"],
            "applied_at": applies[0]["applied_at"],
            "status": applies[0]["status"],
        },
        "k8s": {"pods": len(pods), "events": len(events),
                 "event_message": events[0]["message"], "event_count": events[0]["count"]},
    }


def build_data() -> dict[str, Any]:
    metrics = load_json(DATA_DIR / "metrics.json")["metrics"]
    investigation = load_json(DATA_DIR / "investigation.json")

    captured = set(investigation["captured_cites"])
    cited: list[str] = list(investigation["summary"]["root_cause"]["cites"])
    for entry in investigation["summary"].get("timeline", []):
        cited.extend(entry.get("cites", []))
    provenance_ok = all(cite in captured for cite in cited) and bool(cited)

    return {
        "metrics": metrics,
        "sources": source_samples(),
        "investigation": {
            "scenario": investigation["scenario"],
            "alert": investigation["alert"],
            "brief": investigation["brief"],
            "trace": investigation["trace"],
            "summary": investigation["summary"],
            "captured_count": len(captured),
            "captured_sample": sorted(captured)[:12],
            "cited": cited,
            "provenance_ok": provenance_ok,
        },
    }


TEMPLATE = Path(__file__).parent / "template.html"


def main() -> None:
    data = build_data()
    html = TEMPLATE.read_text(encoding="utf-8").replace(
        "__VIZ_DATA__", json.dumps(data).replace("</", "<\\/")
    )
    OUT.write_text(html, encoding="utf-8")
    print(f"wrote {OUT} ({len(html) // 1024} KiB)")


if __name__ == "__main__":
    main()
