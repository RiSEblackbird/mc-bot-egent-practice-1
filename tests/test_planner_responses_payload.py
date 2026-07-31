import logging

from planner import _build_responses_payload, compose_barrier_notification
import planner.graph as planner_graph
from planner.graph import build_plan_graph
from planner.models import BarrierNotification, PlanOut, PreActionReview
from planner.priority import PlanPriorityManager
from planner_config import (
    OPENAI_MODEL,
    OPENAI_REASONING_EFFORT,
    OPENAI_VERBOSITY,
    PlannerConfig,
    load_planner_config,
)
import pytest


def _response_call_contexts(caplog: pytest.LogCaptureFixture) -> list[dict[str, object]]:
    return [
        record.structured_context
        for record in caplog.records
        if record.message == "openai_response_call"
        and isinstance(getattr(record, "structured_context", None), dict)
    ]


def _make_config() -> PlannerConfig:
    return PlannerConfig(llm_timeout_seconds=30.0)


def test_planner_config_uses_fixed_luna_high_contract() -> None:
    config = load_planner_config({})

    assert config.model == OPENAI_MODEL == "gpt-5.6-luna"
    assert config.reasoning_effort == OPENAI_REASONING_EFFORT == "high"
    assert config.verbosity == OPENAI_VERBOSITY == "low"


def test_planner_config_rejects_runtime_model_override() -> None:
    with pytest.raises(TypeError):
        PlannerConfig(model="unexpected-model")  # type: ignore[call-arg]


@pytest.mark.parametrize(
    "removed_key",
    [
        "OPENAI_MODEL",
        "OPENAI_REASONING_EFFORT",
        "OPENAI_VERBOSITY",
        "OPENAI_TEMPERATURE",
    ],
)
def test_planner_config_fails_fast_for_removed_model_environment_variables(removed_key: str) -> None:
    with pytest.raises(ValueError, match=removed_key):
        load_planner_config({removed_key: "legacy-value"})


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
    assert fmt["schema"]["additionalProperties"] is False
    assert set(fmt["schema"]["required"]) == set(fmt["schema"]["properties"])
    assert payload["model"] == "gpt-5.6-luna"
    assert payload["reasoning"] == {"effort": "high"}
    assert payload["text"]["verbosity"] == "low"
    assert "temperature" not in payload
    assert "mode" not in payload["reasoning"]


def test_build_responses_payload_falls_back_to_json_object_without_schema() -> None:
    payload = _build_responses_payload("system", "user", _make_config())
    assert payload["text"]["format"] == {"type": "json_object"}


@pytest.mark.parametrize(
    ("schema_model", "schema_name"),
    [
        (PlanOut, "plan_out"),
        (PreActionReview, "pre_action_review"),
        (BarrierNotification, "barrier_notification"),
    ],
)
def test_all_response_schemas_share_the_fixed_model_payload(
    schema_model: type[PlanOut] | type[PreActionReview] | type[BarrierNotification],
    schema_name: str,
) -> None:
    payload = _build_responses_payload(
        "system",
        "user",
        _make_config(),
        schema_model=schema_model,
        schema_name=schema_name,
    )

    assert payload["model"] == "gpt-5.6-luna"
    assert payload["reasoning"] == {"effort": "high"}
    assert payload["text"]["verbosity"] == "low"
    assert payload["text"]["format"]["name"] == schema_name
    assert payload["text"]["format"]["strict"] is True
    assert payload["text"]["format"]["schema"]["additionalProperties"] is False
    assert set(payload["text"]["format"]["schema"]["required"]) == set(
        payload["text"]["format"]["schema"]["properties"]
    )
    assert "temperature" not in payload


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


class _CapturingResponses:
    def __init__(self, responses: list[object]) -> None:
        self.responses = list(responses)
        self.payloads: list[dict[str, object]] = []

    async def create(self, **payload: object) -> object:
        self.payloads.append(dict(payload))
        if not self.responses:
            raise RuntimeError("no fake response available")
        return self.responses.pop(0)


class _CapturingAsyncClient:
    def __init__(self, responses: list[object]) -> None:
        self.responses = _CapturingResponses(responses)


def _response(output_text: str, *, with_usage: bool = False) -> object:
    attrs: dict[str, object] = {"output_text": output_text, "output": []}
    if with_usage:
        attrs["usage"] = type(
            "Usage",
            (),
            {
                "input_tokens": 120,
                "input_tokens_details": type("InputDetails", (), {"cached_tokens": 40})(),
                "output_tokens": 30,
                "output_tokens_details": type("OutputDetails", (), {"reasoning_tokens": 12})(),
            },
        )()
    return type("FakeResponse", (), attrs)()


class _TimeoutResponses:
    async def create(self, **_: object) -> object:
        raise TimeoutError("simulated timeout")


class _TimeoutAsyncClient:
    def __init__(self) -> None:
        self.responses = _TimeoutResponses()


async def _invoke_graph_with_output_state(
    output_text: str,
    output: list[object] | None = None,
    response_attrs: dict[str, object] | None = None,
) -> dict[str, object]:
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
    return result


async def _invoke_graph_with_output(
    output_text: str,
    output: list[object] | None = None,
    response_attrs: dict[str, object] | None = None,
) -> PlanOut:
    result = await _invoke_graph_with_output_state(output_text, output, response_attrs)
    return result["plan_out"]


@pytest.mark.anyio
async def test_replan_call_records_fixed_contract_usage_and_depth(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="llm.client")
    config = _make_config()
    response = _response(
        '{"plan":["別経路へ移動"],"resp":"再計画します","confidence":0.9}',
        with_usage=True,
    )
    client = _CapturingAsyncClient([response])
    graph = build_plan_graph(
        config,
        priority_manager=PlanPriorityManager(config),
        async_client_factory=lambda: client,
        payload_builder=lambda system_prompt, user_prompt: _build_responses_payload(
            system_prompt,
            user_prompt,
            config,
            schema_model=PlanOut,
            schema_name="plan_out",
        ),
    )

    result = await graph.ainvoke(
        {
            "user_msg": "失敗後に別案を作る",
            "context": {"_replan_depth": 2},
            "structured_events": [],
        }
    )

    observation = result["llm_observation"]
    assert observation == {
        "call_purpose": "replan",
        "model": "gpt-5.6-luna",
        "reasoning_effort": "high",
        "verbosity": "low",
        "input_tokens": 120,
        "cached_input_tokens": 40,
        "output_tokens": 30,
        "reasoning_tokens": 12,
        "latency_ms": observation["latency_ms"],
        "replan_depth": 2,
    }
    assert observation["latency_ms"] >= 0
    assert client.responses.payloads[0]["model"] == "gpt-5.6-luna"
    assert any(
        context["outcome"] == "success"
        and context["call_purpose"] == "replan"
        and context["reasoning_tokens"] == 12
        for context in _response_call_contexts(caplog)
    )


@pytest.mark.anyio
async def test_pre_action_review_uses_dedicated_schema() -> None:
    config = _make_config()
    client = _CapturingAsyncClient(
        [
            _response('{"plan":["周囲を確認"],"resp":"確認します","confidence":0.2}'),
            _response('{"message":"危険物の有無を教えてください。"}'),
        ]
    )
    graph = build_plan_graph(
        config,
        priority_manager=PlanPriorityManager(config),
        async_client_factory=lambda: client,
        payload_builder=lambda system_prompt, user_prompt: _build_responses_payload(
            system_prompt,
            user_prompt,
            config,
            schema_model=PlanOut,
            schema_name="plan_out",
        ),
        review_payload_builder=lambda system_prompt, user_prompt: _build_responses_payload(
            system_prompt,
            user_prompt,
            config,
            schema_model=PreActionReview,
            schema_name="pre_action_review",
        ),
    )

    result = await graph.ainvoke(
        {"user_msg": "安全を確認して", "context": {}, "structured_events": []}
    )

    assert result["plan_out"].resp == "危険物の有無を教えてください。"
    review_format = client.responses.payloads[1]["text"]["format"]
    assert review_format["name"] == "pre_action_review"
    assert set(review_format["schema"]["properties"]) == {"message"}


@pytest.mark.anyio
async def test_barrier_notification_uses_fixed_contract_and_schema() -> None:
    client = _CapturingAsyncClient(
        [_response('{"message":"進路が塞がれています。別の経路を指定してください。"}')]
    )

    message = await compose_barrier_notification(
        "北へ移動",
        "進路が塞がれている",
        {"_replan_depth": 1},
        client_factory=lambda: client,
    )

    payload = client.responses.payloads[0]
    assert message == "進路が塞がれています。別の経路を指定してください。"
    assert payload["model"] == "gpt-5.6-luna"
    assert payload["reasoning"] == {"effort": "high"}
    assert payload["text"]["verbosity"] == "low"
    assert payload["text"]["format"]["name"] == "barrier_notification"
    assert "temperature" not in payload


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("user_message", "response_json", "expected_intent", "expected_quantity"),
    [
        (
            "X=12, Y=64, Z=-8へ移動して",
            '{"plan":["指定座標へ移動"],"resp":"移動します",'
            '"intent":"move","arguments":{"coordinates":{"x":12,"y":64,"z":-8}},'
            '"confidence":0.9}',
            "move",
            None,
        ),
        (
            "丸石を16個採掘して",
            '{"plan":["丸石を16個採掘"],"resp":"採掘します",'
            '"intent":"mine","arguments":{"target":"cobblestone","quantity":16},'
            '"confidence":0.9}',
            "mine",
            16,
        ),
    ],
)
async def test_representative_plan_scenarios_keep_structured_contract(
    user_message: str,
    response_json: str,
    expected_intent: str,
    expected_quantity: int | None,
) -> None:
    config = _make_config()
    client = _CapturingAsyncClient([_response(response_json, with_usage=True)])
    graph = build_plan_graph(
        config,
        priority_manager=PlanPriorityManager(config),
        async_client_factory=lambda: client,
        payload_builder=lambda system_prompt, user_prompt: _build_responses_payload(
            system_prompt,
            user_prompt,
            config,
            schema_model=PlanOut,
            schema_name="plan_out",
        ),
    )

    result = await graph.ainvoke(
        {"user_msg": user_message, "context": {}, "structured_events": []}
    )

    plan_out = result["plan_out"]
    assert plan_out.intent == expected_intent
    assert plan_out.arguments.quantity == expected_quantity
    assert result["llm_observation"]["call_purpose"] == "plan"
    assert result["llm_observation"]["input_tokens"] == 120
    assert result["llm_observation"]["latency_ms"] >= 0


@pytest.mark.anyio
async def test_plan_graph_handles_empty_plan_as_controlled_chat_fallback() -> None:
    plan_out = await _invoke_graph_with_output('{"plan":[],"resp":"", "intent":"move"}')
    assert plan_out.next_action == "chat"
    assert plan_out.blocking is True
    assert plan_out.clarification_needed == "data_gap"
    assert any(item.get("label") == "plan_empty" for item in plan_out.backlog)


@pytest.mark.anyio
async def test_plan_graph_returns_safe_fallback_on_invalid_json(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="llm.client")
    plan_out = await _invoke_graph_with_output("not-json")
    assert plan_out.plan == []
    assert plan_out.resp == "了解しました。"
    assert any(
        context["outcome"] == "schema_validation_failure"
        for context in _response_call_contexts(caplog)
    )


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
async def test_plan_graph_uses_refusal_message_as_controlled_chat_fallback(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="llm.client")
    refusal_content = type("FakeRefusalContent", (), {"type": "refusal", "refusal": "危険な操作のため確認が必要です"})()
    refusal_message = type("FakeMessage", (), {"type": "message", "content": [refusal_content]})()
    plan_out = await _invoke_graph_with_output("", output=[refusal_message])
    assert plan_out.plan == []
    assert plan_out.resp == "危険な操作のため確認が必要です"
    assert plan_out.next_action == "chat"
    assert plan_out.blocking is True
    assert plan_out.clarification_needed == "confirmation"
    assert any(item.get("label") == "plan_refusal" for item in plan_out.backlog)
    assert any(
        context["outcome"] == "refusal"
        for context in _response_call_contexts(caplog)
    )


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


@pytest.mark.anyio
async def test_plan_graph_sets_structured_parse_error_code_on_schema_mismatch() -> None:
    result = await _invoke_graph_with_output_state(
        "",
        response_attrs={"output_parsed": {"plan": "move", "resp": "了解"}},
    )
    assert result.get("parse_error_code") == "structured_output_schema_mismatch"
    assert isinstance(result.get("plan_out"), PlanOut)
    assert result["plan_out"].resp == "了解しました。"


@pytest.mark.anyio
async def test_plan_graph_sets_json_parse_error_code_on_invalid_output() -> None:
    result = await _invoke_graph_with_output_state("not-json")
    assert result.get("parse_error_code") == "plan_json_decode_failed"
    assert isinstance(result.get("plan_out"), PlanOut)
    assert result["plan_out"].resp == "了解しました。"


@pytest.mark.anyio
async def test_plan_graph_skips_legacy_normalize_for_non_json_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise_if_called(_: str) -> str:
        raise AssertionError("legacy normalize should not run for non-JSON payload")

    monkeypatch.setattr(planner_graph, "_normalize_plan_json", _raise_if_called)
    result = await _invoke_graph_with_output_state("not-json")
    assert result.get("parse_error_code") == "plan_json_decode_failed"


@pytest.mark.anyio
async def test_plan_graph_skips_legacy_normalize_for_missing_required_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise_if_called(_: str) -> str:
        raise AssertionError("legacy normalize should not run for missing required fields")

    monkeypatch.setattr(planner_graph, "_normalize_plan_json", _raise_if_called)
    result = await _invoke_graph_with_output_state('{"intent":"move"}')
    assert result.get("parse_error_code") is None
    assert isinstance(result.get("plan_out"), PlanOut)
    assert result["plan_out"].next_action == "chat"


@pytest.mark.anyio
async def test_plan_graph_sets_llm_timeout_error_code_when_call_times_out(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="llm.client")
    config = _make_config()
    graph = build_plan_graph(
        config,
        priority_manager=PlanPriorityManager(config),
        async_client_factory=lambda: _TimeoutAsyncClient(),
        payload_builder=lambda system_prompt, user_prompt: {
            "model": config.model,
            "input": [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
            "text": {"format": {"type": "json_schema"}},
        },
    )
    result = await graph.ainvoke({"user_msg": "test", "context": {}, "structured_events": []})
    assert result.get("parse_error_code") == "llm_timeout"
    assert any(
        context["outcome"] == "timeout"
        for context in _response_call_contexts(caplog)
    )
