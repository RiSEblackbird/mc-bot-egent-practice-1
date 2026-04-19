from planner import _build_responses_payload
from planner.graph import build_plan_graph
from planner.models import PlanOut
from planner.priority import PlanPriorityManager
from planner_config import PlannerConfig
import pytest


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


class _FakeResponses:
    def __init__(
        self,
        output_text: str,
        output: list[object] | None = None,
        response_attrs: dict[str, object] | None = None,
    ) -> None:
        self._output_text = output_text
        self._output = output or []
        self._response_attrs = response_attrs or {}

    async def create(self, **_: object) -> object:
        attrs = {"output_text": self._output_text, "output": self._output}
        attrs.update(self._response_attrs)
        return type("FakeResponse", (), attrs)()


class _FakeAsyncClient:
    def __init__(
        self,
        output_text: str,
        output: list[object] | None = None,
        response_attrs: dict[str, object] | None = None,
    ) -> None:
        self.responses = _FakeResponses(output_text, output, response_attrs)


async def _invoke_graph_with_output(
    output_text: str,
    output: list[object] | None = None,
    response_attrs: dict[str, object] | None = None,
) -> PlanOut:
    config = _make_config()
    graph = build_plan_graph(
        config,
        priority_manager=PlanPriorityManager(config),
        async_client_factory=lambda: _FakeAsyncClient(output_text, output, response_attrs),
        payload_builder=lambda system_prompt, user_prompt: {
            "model": config.model,
            "input": [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
            "text": {"format": {"type": "json_schema"}},
        },
    )

    result = await graph.ainvoke({"user_msg": "test", "context": {}, "structured_events": []})
    assert isinstance(result.get("plan_out"), PlanOut)
    return result["plan_out"]


@pytest.mark.anyio
async def test_plan_graph_handles_empty_plan_as_controlled_chat_fallback() -> None:
    plan_out = await _invoke_graph_with_output('{"plan":[],"resp":"", "intent":"move"}')
    assert plan_out.next_action == "chat"
    assert plan_out.blocking is True
    assert plan_out.clarification_needed == "data_gap"
    assert any(item.get("label") == "plan_empty" for item in plan_out.backlog)


@pytest.mark.anyio
async def test_plan_graph_returns_safe_fallback_on_invalid_json() -> None:
    plan_out = await _invoke_graph_with_output("not-json")
    assert plan_out.plan == []
    assert plan_out.resp == "了解しました。"


@pytest.mark.anyio
async def test_plan_graph_returns_safe_fallback_on_empty_output_text() -> None:
    plan_out = await _invoke_graph_with_output("")
    assert plan_out.plan == []
    assert plan_out.resp == "了解しました。"


@pytest.mark.anyio
async def test_plan_graph_returns_safe_fallback_when_required_fields_are_missing() -> None:
    plan_out = await _invoke_graph_with_output('{"intent":"move"}')
    assert plan_out.plan == []
    assert plan_out.resp == "手順が生成できませんでした。もう少し具体的に指示してください。"
    assert plan_out.next_action == "chat"
    assert plan_out.clarification_needed == "data_gap"


@pytest.mark.anyio
async def test_plan_graph_uses_refusal_message_as_controlled_chat_fallback() -> None:
    refusal_content = type("FakeRefusalContent", (), {"type": "refusal", "refusal": "危険な操作のため確認が必要です"})()
    refusal_message = type("FakeMessage", (), {"type": "message", "content": [refusal_content]})()
    plan_out = await _invoke_graph_with_output("", output=[refusal_message])
    assert plan_out.plan == []
    assert plan_out.resp == "危険な操作のため確認が必要です"
    assert plan_out.next_action == "chat"
    assert plan_out.blocking is True
    assert plan_out.clarification_needed == "confirmation"
    assert any(item.get("label") == "plan_refusal" for item in plan_out.backlog)


@pytest.mark.anyio
async def test_plan_graph_legacy_normalize_coerces_arguments_shape() -> None:
    plan_out = await _invoke_graph_with_output(
        '{"plan":["x=10,z=20へ移動"],"resp":"了解","intent":"move",'
        '"arguments":{"coordinates":{"x":"10","y":"64.0","z":"oops"},'
        '"notes":"橋の近く","clarification_needed":"unknown"}}'
    )
    assert plan_out.plan == ["x=10,z=20へ移動"]
    assert plan_out.arguments.coordinates == {"x": 10, "y": 64}
    assert plan_out.arguments.notes == {"text": "橋の近く"}
    assert plan_out.arguments.clarification_needed == "data_gap"


@pytest.mark.anyio
async def test_plan_graph_legacy_normalize_coerces_top_level_clarification_enum() -> None:
    plan_out = await _invoke_graph_with_output(
        '{"plan":["周辺を確認"],"resp":"確認します","intent":"survey",'
        '"clarification_needed":"manual_review"}'
    )
    assert plan_out.plan == ["周辺を確認"]
    assert plan_out.clarification_needed == "data_gap"


@pytest.mark.anyio
async def test_plan_graph_prefers_structured_output_without_legacy_normalize() -> None:
    plan_out = await _invoke_graph_with_output(
        "not-json",
        response_attrs={
            "output_parsed": {
                "plan": ["丸石を10個掘る"],
                "resp": "掘ります",
                "intent": "mine",
                "clarification_needed": "none",
            }
        },
    )
    assert plan_out.plan == ["丸石を10個掘る"]
    assert plan_out.intent == "mine"
    assert plan_out.resp != "了解しました。"
