# -*- coding: utf-8 -*-
"""Node/Python 間 transport の共通 envelope を扱うユーティリティ。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Dict, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

EnvelopeKind = Literal["command", "event", "status", "error"]
CURRENT_TRANSPORT_VERSION = "v1"


class TransportEnvelope(BaseModel):
    """contracts/transport-envelope.schema.json と対応する envelope。"""

    model_config = ConfigDict(extra="forbid")

    version: str = Field(min_length=2)
    trace_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    message_id: str = Field(min_length=1)
    timestamp: datetime
    source: str = Field(min_length=1)
    kind: EnvelopeKind
    name: str = Field(min_length=1)
    body: Dict[str, Any]
    auth: Dict[str, Any] | None = None


def make_transport_envelope(
    *,
    source: str,
    kind: EnvelopeKind,
    name: str,
    body: Dict[str, Any],
    trace_id: str | None = None,
    run_id: str | None = None,
    message_id: str | None = None,
) -> Dict[str, Any]:
    """送信時に利用する envelope を生成して dict で返す。"""

    envelope = TransportEnvelope(
        version=CURRENT_TRANSPORT_VERSION,
        trace_id=trace_id or uuid4().hex,
        run_id=run_id or uuid4().hex,
        message_id=message_id or uuid4().hex,
        timestamp=datetime.now(UTC),
        source=source,
        kind=kind,
        name=name,
        body=body,
    )
    return envelope.model_dump(mode="json")


def validate_transport_envelope(payload: Dict[str, Any]) -> TransportEnvelope:
    """受信 payload を検証し、型安全な envelope として返す。"""

    return TransportEnvelope.model_validate(payload)


__all__ = [
    "CURRENT_TRANSPORT_VERSION",
    "TransportEnvelope",
    "make_transport_envelope",
    "validate_transport_envelope",
]
