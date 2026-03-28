"""
Tests for OpenTelemetry Instrumentation.
Covers: span lifecycle, LLM instrumentation, async pipeline tracing,
        attribute propagation, error handling, and OTEL collector config.
"""

import pytest
import time
from observability.otel_instrumentation import (
    OTELTracer,
    Span,
    SpanStatus,
    SpanKind,
    LLMInstrumentor,
    AsyncPipelineTracer,
    InMemorySpanExporter,
    build_otel_collector_config,
)


# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def tracer():
    return OTELTracer("risk-score-api", "1.0.0")


@pytest.fixture
def instrumentor(tracer):
    return LLMInstrumentor(tracer)


@pytest.fixture
def pipeline_tracer(tracer):
    return AsyncPipelineTracer(tracer)


# ─── Span Tests ───────────────────────────────────────────────────────────────

class TestSpan:

    def test_span_has_trace_id(self, tracer):
        span = tracer.start_span("test.operation")
        assert span.trace_id is not None
        assert len(span.trace_id) > 0
        tracer.end_span(span)

    def test_span_has_span_id(self, tracer):
        span = tracer.start_span("test.operation")
        assert span.span_id is not None
        tracer.end_span(span)

    def test_span_duration_after_end(self, tracer):
        span = tracer.start_span("test.operation")
        time.sleep(0.01)
        tracer.end_span(span)
        assert span.duration_ms is not None
        assert span.duration_ms >= 0

    def test_span_status_ok_after_end(self, tracer):
        span = tracer.start_span("test.operation")
        tracer.end_span(span)
        assert span.status == SpanStatus.OK

    def test_span_set_attribute(self, tracer):
        span = tracer.start_span("test.op")
        span.set_attribute("key", "value")
        assert span.attributes["key"] == "value"
        tracer.end_span(span)

    def test_span_add_event(self, tracer):
        span = tracer.start_span("test.op")
        span.add_event("cache.hit", {"cache_key": "abc"})
        assert len(span.events) == 1
        assert span.events[0]["name"] == "cache.hit"
        tracer.end_span(span)

    def test_span_record_exception(self, tracer):
        span = tracer.start_span("test.op")
        exc = ValueError("test error")
        span.record_exception(exc)
        assert span.status == SpanStatus.ERROR
        assert span.error == "test error"
        tracer.end_span(span)

    def test_span_to_dict(self, tracer):
        span = tracer.start_span("test.op")
        tracer.end_span(span)
        d = span.to_dict()
        assert "name" in d
        assert "trace_id" in d
        assert "duration_ms" in d
        assert "status" in d

    def test_span_service_attributes_added(self, tracer):
        span = tracer.start_span("test.op")
        assert span.attributes.get("service.name") == "risk-score-api"
        assert span.attributes.get("service.version") == "1.0.0"
        tracer.end_span(span)


# ─── OTELTracer Tests ─────────────────────────────────────────────────────────

class TestOTELTracer:

    def test_context_manager_creates_and_ends_span(self, tracer):
        with tracer.span("test.op") as span:
            assert span is not None
        assert span.end_time_ns is not None

    def test_context_manager_sets_ok_status(self, tracer):
        with tracer.span("test.op") as span:
            pass
        assert span.status == SpanStatus.OK

    def test_context_manager_records_exception(self, tracer):
        with pytest.raises(RuntimeError):
            with tracer.span("test.op") as span:
                raise RuntimeError("test failure")
        assert span.status == SpanStatus.ERROR

    def test_nested_spans_share_trace_id(self, tracer):
        with tracer.span("outer.op") as outer:
            with tracer.span("inner.op") as inner:
                pass
        assert outer.trace_id == inner.trace_id

    def test_nested_spans_parent_child_relationship(self, tracer):
        with tracer.span("outer.op") as outer:
            with tracer.span("inner.op") as inner:
                pass
        assert inner.parent_span_id == outer.span_id

    def test_root_span_has_no_parent(self, tracer):
        tracer.clear_spans()
        with tracer.span("root.op") as span:
            pass
        assert span.parent_span_id is None

    def test_finished_spans_in_exporter(self, tracer):
        tracer.clear_spans()
        with tracer.span("test.op"):
            pass
        spans = tracer.get_finished_spans()
        assert len(spans) == 1

    def test_span_with_attributes(self, tracer):
        with tracer.span("test.op", attributes={"key": "value"}) as span:
            pass
        assert span.attributes["key"] == "value"

    def test_span_kind_client(self, tracer):
        span = tracer.start_span("db.query", kind=SpanKind.CLIENT)
        assert span.kind == SpanKind.CLIENT
        tracer.end_span(span)

    def test_clear_spans(self, tracer):
        with tracer.span("op1"):
            pass
        with tracer.span("op2"):
            pass
        tracer.clear_spans()
        assert len(tracer.get_finished_spans()) == 0


# ─── LLM Instrumentation Tests ───────────────────────────────────────────────

class TestLLMInstrumentor:

    def test_llm_call_creates_span(self, instrumentor, tracer):
        tracer.clear_spans()
        with instrumentor.instrument_llm_call("gpt-4", "What is the risk score?") as span:
            pass
        spans = tracer.get_finished_spans()
        assert any("llm" in s.name for s in spans)

    def test_llm_span_has_model_attribute(self, instrumentor, tracer):
        tracer.clear_spans()
        with instrumentor.instrument_llm_call("gpt-4", "Analyze risk") as span:
            pass
        assert span.attributes.get(LLMInstrumentor.ATTR_LLM_REQUEST_MODEL) == "gpt-4"

    def test_llm_span_has_prompt_hash_not_prompt(self, instrumentor, tracer):
        tracer.clear_spans()
        prompt = "Classify the property risk for this submission"
        with instrumentor.instrument_llm_call("gpt-4", prompt) as span:
            pass
        # Should have hash, not the prompt text itself
        assert LLMInstrumentor.ATTR_PROMPT_HASH in span.attributes
        assert prompt not in str(span.attributes)

    def test_llm_span_has_insurance_line(self, instrumentor, tracer):
        tracer.clear_spans()
        with instrumentor.instrument_llm_call(
            "gpt-4", "Assess risk", insurance_line="property"
        ) as span:
            pass
        assert span.attributes.get(LLMInstrumentor.ATTR_INSURANCE_LINE) == "property"

    def test_llm_span_has_submission_id(self, instrumentor, tracer):
        tracer.clear_spans()
        with instrumentor.instrument_llm_call(
            "gpt-4", "Assess risk", submission_id="SUB-12345"
        ) as span:
            pass
        assert span.attributes.get(LLMInstrumentor.ATTR_SUBMISSION_ID) == "SUB-12345"

    def test_prompt_hash_is_deterministic(self, instrumentor):
        prompt = "Same prompt every time"
        hash1 = instrumentor.hash_prompt(prompt)
        hash2 = instrumentor.hash_prompt(prompt)
        assert hash1 == hash2

    def test_different_prompts_have_different_hashes(self, instrumentor):
        hash1 = instrumentor.hash_prompt("Prompt A")
        hash2 = instrumentor.hash_prompt("Prompt B")
        assert hash1 != hash2

    def test_prompt_hash_length(self, instrumentor):
        h = instrumentor.hash_prompt("test prompt")
        assert len(h) == 16

    def test_llm_span_records_input_token_estimate(self, instrumentor, tracer):
        tracer.clear_spans()
        with instrumentor.instrument_llm_call("gpt-4", "word1 word2 word3") as span:
            pass
        token_count = span.attributes.get(LLMInstrumentor.ATTR_LLM_USAGE_INPUT_TOKENS)
        assert token_count is not None
        assert token_count > 0

    def test_llm_exception_captured(self, instrumentor, tracer):
        tracer.clear_spans()
        with pytest.raises(RuntimeError):
            with instrumentor.instrument_llm_call("gpt-4", "test") as span:
                raise RuntimeError("Model timeout")
        assert span.status == SpanStatus.ERROR


# ─── Async Pipeline Tracer Tests ─────────────────────────────────────────────

class TestAsyncPipelineTracer:

    def test_start_pipeline_creates_root_span(self, pipeline_tracer, tracer):
        tracer.clear_spans()
        span = pipeline_tracer.start_pipeline("pipe-001", "submission-processing")
        assert span is not None
        assert span.trace_id is not None
        pipeline_tracer.end_pipeline("pipe-001")

    def test_pipeline_span_has_type_attribute(self, pipeline_tracer, tracer):
        tracer.clear_spans()
        span = pipeline_tracer.start_pipeline("pipe-002", "risk-scoring")
        assert span.attributes.get("pipeline.type") == "risk-scoring"
        pipeline_tracer.end_pipeline("pipe-002")

    def test_stage_span_is_child_of_pipeline(self, pipeline_tracer, tracer):
        tracer.clear_spans()
        pipeline_span = pipeline_tracer.start_pipeline("pipe-003", "submission-processing")
        stage_span = pipeline_tracer.start_stage("pipe-003", "risk-extraction")
        assert stage_span is not None
        assert stage_span.trace_id == pipeline_span.trace_id
        assert stage_span.parent_span_id == pipeline_span.span_id
        pipeline_tracer.end_stage(stage_span)
        pipeline_tracer.end_pipeline("pipe-003")

    def test_end_pipeline_returns_span(self, pipeline_tracer, tracer):
        tracer.clear_spans()
        pipeline_tracer.start_pipeline("pipe-004", "test")
        returned = pipeline_tracer.end_pipeline("pipe-004")
        assert returned is not None

    def test_get_trace_id_for_pipeline(self, pipeline_tracer, tracer):
        tracer.clear_spans()
        pipeline_tracer.start_pipeline("pipe-005", "test")
        trace_id = pipeline_tracer.get_pipeline_trace_id("pipe-005")
        assert trace_id is not None
        pipeline_tracer.end_pipeline("pipe-005")

    def test_get_trace_id_for_unknown_pipeline(self, pipeline_tracer):
        trace_id = pipeline_tracer.get_pipeline_trace_id("nonexistent")
        assert trace_id is None

    def test_stage_end_with_error(self, pipeline_tracer, tracer):
        tracer.clear_spans()
        pipeline_tracer.start_pipeline("pipe-006", "test")
        stage = pipeline_tracer.start_stage("pipe-006", "risk-scoring")
        pipeline_tracer.end_stage(stage, success=False, error="Timeout")
        assert stage.status == SpanStatus.ERROR
        assert stage.attributes.get("stage.error") == "Timeout"
        pipeline_tracer.end_pipeline("pipe-006")

    def test_stage_missing_pipeline_returns_none(self, pipeline_tracer):
        stage = pipeline_tracer.start_stage("nonexistent-pipe", "test-stage")
        assert stage is None

    def test_multiple_stages_share_trace(self, pipeline_tracer, tracer):
        tracer.clear_spans()
        pipeline_tracer.start_pipeline("pipe-007", "end-to-end")
        s1 = pipeline_tracer.start_stage("pipe-007", "ingestion")
        s2 = pipeline_tracer.start_stage("pipe-007", "risk-scoring")
        assert s1.trace_id == s2.trace_id
        pipeline_tracer.end_stage(s1)
        pipeline_tracer.end_stage(s2)
        pipeline_tracer.end_pipeline("pipe-007")


# ─── OTEL Collector Config Tests ─────────────────────────────────────────────

class TestOTELCollectorConfig:

    def test_config_has_receivers(self):
        config = build_otel_collector_config()
        assert "receivers" in config

    def test_config_has_otlp_receiver(self):
        config = build_otel_collector_config()
        assert "otlp" in config["receivers"]

    def test_config_has_grpc_and_http(self):
        config = build_otel_collector_config()
        protocols = config["receivers"]["otlp"]["protocols"]
        assert "grpc" in protocols
        assert "http" in protocols

    def test_config_has_prometheus_exporter(self):
        config = build_otel_collector_config()
        assert "prometheus" in config["exporters"]

    def test_config_has_tempo_exporter(self):
        config = build_otel_collector_config()
        assert "otlp/tempo" in config["exporters"]

    def test_config_has_loki_exporter(self):
        config = build_otel_collector_config()
        assert "loki" in config["exporters"]

    def test_config_has_three_pipelines(self):
        config = build_otel_collector_config()
        pipelines = config["service"]["pipelines"]
        assert "traces" in pipelines
        assert "metrics" in pipelines
        assert "logs" in pipelines

    def test_config_prometheus_namespace(self):
        config = build_otel_collector_config()
        assert config["exporters"]["prometheus"]["namespace"] == "sixfold"
