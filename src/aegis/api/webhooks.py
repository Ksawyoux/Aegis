"""Atomic alert ingestion and background investigation scheduling."""

from __future__ import annotations

import hashlib
import time
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import Engine, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from aegis.app.investigate import InvestigationRequest
from aegis.app.runner import run_incident
from aegis.config import Settings
from aegis.db.models import Incident, Service

router = APIRouter()


class AlertWebhook(BaseModel):
    """The operational alert fields plus provider-specific extras."""

    model_config = ConfigDict(extra="allow")

    service: str
    alert_name: str
    fired_at: datetime
    payload: dict[str, Any]
    window_start: datetime
    window_end: datetime


def derive_dedup_key(payload: dict[str, Any]) -> tuple[str, str]:
    """Return the provider key when supplied, otherwise a five-minute fingerprint."""
    event = payload.get("event")
    if isinstance(event, dict):
        data = event.get("data")
        if isinstance(data, dict) and isinstance(data.get("id"), str):
            return data["id"], "provider"
    if isinstance(payload.get("dedup_key"), str):
        return payload["dedup_key"], "provider"
    fired_at = _parse_fired_at(payload.get("fired_at"))
    bucket = fired_at.replace(second=0, microsecond=0) - timedelta(minutes=fired_at.minute % 5)
    material = f"{payload.get('service')}|{payload.get('alert_name')}|{bucket.isoformat()}"
    return "fp:" + hashlib.sha256(material.encode()).hexdigest()[:32], "fingerprint"


@router.post("/webhooks/alert", status_code=202)
def receive_alert(
    alert: AlertWebhook, background: BackgroundTasks, request: Request
) -> dict[str, Any]:
    """Insert one incident atomically and schedule only the conflict winner."""
    raw_payload = alert.model_dump(mode="json")
    dedup_key, source = derive_dedup_key(raw_payload)
    raw_payload["dedup_source"] = source
    engine: Engine = request.app.state.engine
    settings: Settings = request.app.state.settings
    investigation = InvestigationRequest.model_validate(alert.model_dump())

    with Session(engine) as session, session.begin():
        service_id = session.scalar(select(Service.id).where(Service.name == alert.service))
        statement = (
            insert(Incident)
            .values(
                dedup_key=dedup_key,
                service_id=service_id,
                opened_at=alert.fired_at,
                window_start=alert.window_start,
                window_end=alert.window_end,
                alert_payload=raw_payload,
                status="open",
            )
            .on_conflict_do_nothing(index_elements=[Incident.dedup_key])
            .returning(Incident.id)
        )
        incident_id = session.scalar(statement)

    if incident_id is not None:
        background.add_task(run_incident, incident_id, investigation, settings, engine)
        return {
            "incident_id": incident_id,
            "dedup_key": dedup_key,
            "created": True,
            "run_scheduled": True,
        }

    existing_id = _existing_incident_id(engine, dedup_key)
    if existing_id is not None:
        return {
            "incident_id": existing_id,
            "dedup_key": dedup_key,
            "created": False,
            "run_scheduled": False,
        }
    return {
        "incident_id": None,
        "dedup_key": dedup_key,
        "created": False,
        "run_scheduled": False,
    }


def _existing_incident_id(engine: Engine, dedup_key: str) -> int | None:
    for _attempt in range(2):
        with Session(engine) as session:
            incident_id = session.scalar(select(Incident.id).where(Incident.dedup_key == dedup_key))
        if incident_id is not None:
            return incident_id
        time.sleep(0.05)
    return None


def _parse_fired_at(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail="fired_at must be an ISO-8601 timestamp"
            ) from exc
    raise HTTPException(status_code=422, detail="fired_at is required")
