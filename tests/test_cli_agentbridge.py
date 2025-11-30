# -*- coding: utf-8 -*-
"""Unit tests for AgentBridge CLI helpers."""

from __future__ import annotations

from python.cli import _format_bridge_event, _should_emit_event


def test_should_emit_event_filters_by_job_and_level():
    event = {"job_id": "demo", "event_level": "info"}
    assert _should_emit_event(event, job_id="demo", danger_only=False)
    assert not _should_emit_event(event, job_id="other", danger_only=False)

    warning_event = {"job_id": "demo", "event_level": "warning"}
    assert _should_emit_event(warning_event, job_id="demo", danger_only=True)
    info_event = {"job_id": "demo", "event_level": "info"}
    assert not _should_emit_event(info_event, job_id="demo", danger_only=True)


def test_format_bridge_event_supports_text_and_json():
    event = {
        "event_level": "warning",
        "job_id": "abc",
        "message": "liquid_detected",
        "region": "quarry-1",
        "block_pos": {"x": 10, "y": 60, "z": -2},
    }
    text_output = _format_bridge_event(event, "text")
    assert "[WARNING]" in text_output
    assert "job=abc" in text_output
    assert "region=quarry-1" in text_output
    assert "pos=" in text_output

    json_output = _format_bridge_event(event, "json")
    assert '"job_id": "abc"' in json_output
