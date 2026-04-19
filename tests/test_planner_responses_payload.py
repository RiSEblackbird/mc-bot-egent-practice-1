from planner import _build_responses_payload
from planner.models import PlanOut
from planner_config import PlannerConfig


def _make_config() -> PlannerConfig:
    return PlannerConfig(
        model="gpt-5-mini",
        default_temperature=0.3,
        temperature_locked_models={"gpt-5-mini"},
        allowed_verbosity_levels={"low", "medium", "high"},
        allowed_reasoning_effort={"low", "medium", "high"},
        llm_timeout_seconds=30.0,
    )


def test_build_responses_payload_uses_json_schema_for_planout() -> None:
    payload = _build_responses_payload(
        "system",
        "user",
        _make_config(),
        schema_model=PlanOut,
        schema_name="plan_out",
    )

    fmt = payload["text"]["format"]
    assert fmt["type"] == "json_schema"
    assert fmt["name"] == "plan_out"
    assert fmt["strict"] is True
    assert "properties" in fmt["schema"]
    assert "plan" in fmt["schema"]["properties"]


def test_build_responses_payload_falls_back_to_json_object_without_schema() -> None:
    payload = _build_responses_payload("system", "user", _make_config())
    assert payload["text"]["format"] == {"type": "json_object"}
