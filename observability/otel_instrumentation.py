"""
OpenTelemetry Instrumentation — for Sixfold AI Insurance Platform services.

Provides OTEL SDK setup for Python services, with custom instrumentation
for LLM inference calls, async pipeline tracing, and insurance domain
context propagation. Designed for observability of non-deterministic AI systems.
"""

from __future__ import annotations

import functools
import hashlib
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, Generator, List, Optional


class SpanStatus(Enum):
    OK = "ok"
    ERROR = "error"
    UNSET = "unset"


class SpanKind(Enum):
    SERVER = "server"
    CLIENT = "client"
    PRODUCER = "producer"
    CONSUMER = "consumer"
    INTERNAL = "internal"


@dataclass
class SpanContext:
    trace_id: str
    span_id: str
    parent_span_id: Optional[str] = None


@dataclass
class Span:
    """Represents an OpenTelemetry span (simplified for POC)."""
    name: str
    trace_id: str
    span_id: str
    parent_span_id: Optional[str]
    start_time_ns: int
    end_time_ns: Optional[int] = None
    status: SpanStatus = SpanStatus.UNSET
    kind: SpanKind = SpanKind.INTERNAL
    attributes: Dict[str, Any] = field(default_factory=dict)
    events: List[Dict] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def duration_ms(self) -> Optional[float]:
        if self.end_time_ns is None:
            return None
        return (self.end_time_ns - self.start_time_ns) / 1_000_000

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes[key] = value

    def add_event(self, name: str, attributes: Optional[Dict] = None) -> None:
        self.events.append({
            "name": name,
            "timestamp_ns": time.time_ns(),
            "attributes": attributes or {},
        })

    def set_status(self, status: SpanStatus, description: Optional[str] = None) -> None:
        self.status = status
        if description:
            self.attributes["status.description"] = description

    def record_exception(self, exc: Exception) -> None:
        self.error = str(exc)
        self.status = SpanStatus.ERROR
        self.add_event("exception", {
            "exception.type": type(exc).__name__,
            "exception.message": str(exc),
        })

    def end(self) -> None:
        self.end_time_ns = time.time_ns()
        if self.status == SpanStatus.UNSET:
            self.status = SpanStatus.OK

    def to_dict(self) -> Dict:
        return {
            "name": self.name,
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "start_time_ns": self.start_time_ns,
            "end_time_ns": self.end_time_ns,
            "duration_ms": self.duration_ms,
            "status": self.status.value,
            "kind": self.kind.value,
            "attributes": self.attributes,
            "events": self.events,
            "error": self.error,
        }


class InMemorySpanExporter:
    """Exports spans to memory for testing."""

    def __init__(self):
        self._spans: List[Span] = []

    def export(self, span: Span) -> None:
        self._spans.append(span)

    def get_finished_spans(self) -> List[Span]:
        return [s for s in self._spans if s.end_time_ns is not None]

    def clear(self) -> None:
        self._spans.clear()

    @property
    def span_count(self) -> int:
        return len(self._spans)


class OTELTracer:
    """
    Simplified OpenTelemetry tracer for the Sixfold AI platform.

    In production, this wraps the official opentelemetry-sdk TracerProvider
    with OTLP exporter to the OTEL Collector.
    """

    def __init__(self, service_name: str, service_version: str = "1.0.0"):
        self.service_name = service_name
        self.service_version = service_version
        self._exporter = InMemorySpanExporter()
        self._active_span: Optional[Span] = None
        self._span_stack: List[Span] = []

    def _generate_id(self, length: int = 16) -> str:
        import random
        return "".join(random.choices("0123456789abcdef", k=length))

    def start_span(
        self,
        name: str,
        kind: SpanKind = SpanKind.INTERNAL,
        attributes: Optional[Dict] = None,
        parent_span: Optional[Span] = None,
    ) -> Span:
        """Start a new span."""
        if parent_span:
            trace_id = parent_span.trace_id
            parent_id = parent_span.span_id
        elif self._span_stack:
            trace_id = self._span_stack[-1].trace_id
            parent_id = self._span_stack[-1].span_id
        else:
            trace_id = self._generate_id(32)
            parent_id = None

        span = Span(
            name=name,
            trace_id=trace_id,
            span_id=self._generate_id(16),
            parent_span_id=parent_id,
            start_time_ns=time.time_ns(),
            kind=kind,
            attributes=dict(attributes or {}),
        )

        # Add service resource attributes
        span.set_attribute("service.name", self.service_name)
        span.set_attribute("service.version", self.service_version)

        self._span_stack.append(span)
        return span

    def end_span(self, span: Span) -> None:
        """End a span and export it."""
        span.end()
        if span in self._span_stack:
            self._span_stack.remove(span)
        self._exporter.export(span)

    @contextmanager
    def span(
        self,
        name: str,
        kind: SpanKind = SpanKind.INTERNAL,
        attributes: Optional[Dict] = None,
    ) -> Generator[Span, None, None]:
        """Context manager for span lifecycle."""
        s = self.start_span(name, kind, attributes)
        try:
            yield s
        except Exception as exc:
            s.record_exception(exc)
            raise
        finally:
            self.end_span(s)

    def get_finished_spans(self) -> List[Span]:
        return self._exporter.get_finished_spans()

    def clear_spans(self) -> None:
        self._exporter.clear()


class LLMInstrumentor:
    """
    Instruments LLM inference calls with OpenTelemetry spans.

    Captures model name, token counts, prompt hash (not the prompt itself),
    latency percentiles, and error details for AI/LLM observability.
    """

    # OTEL semantic conventions for LLM (GenAI conventions draft)
    ATTR_LLM_SYSTEM = "gen_ai.system"
    ATTR_LLM_REQUEST_MODEL = "gen_ai.request.model"
    ATTR_LLM_USAGE_INPUT_TOKENS = "gen_ai.usage.input_tokens"
    ATTR_LLM_USAGE_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
    ATTR_LLM_RESPONSE_FINISH_REASON = "gen_ai.response.finish_reason"

    # Sixfold custom attributes
    ATTR_PROMPT_HASH = "sixfold.prompt_hash"
    ATTR_INSURANCE_LINE = "sixfold.insurance_line"
    ATTR_SUBMISSION_ID = "sixfold.submission_id"
    ATTR_RISK_SCORE = "sixfold.risk_score"

    def __init__(self, tracer: OTELTracer):
        self.tracer = tracer

    def hash_prompt(self, prompt: str) -> str:
        """Hash a prompt for observability without storing PII."""
        return hashlib.sha256(prompt.encode()).hexdigest()[:16]

    @contextmanager
    def instrument_llm_call(
        self,
        model: str,
        prompt: str,
        insurance_line: Optional[str] = None,
        submission_id: Optional[str] = None,
    ) -> Generator[Span, None, None]:
        """
        Instrument an LLM inference call.

        Usage:
            with instrumentor.instrument_llm_call("gpt-4", prompt, "property") as span:
                response = await llm_client.complete(prompt)
                span.set_attribute(instrumentor.ATTR_LLM_USAGE_OUTPUT_TOKENS, len(response.tokens))
        """
        attributes = {
            self.ATTR_LLM_SYSTEM: "openai",
            self.ATTR_LLM_REQUEST_MODEL: model,
            self.ATTR_PROMPT_HASH: self.hash_prompt(prompt),
            self.ATTR_LLM_USAGE_INPUT_TOKENS: len(prompt.split()),  # approximation
        }
        if insurance_line:
            attributes[self.ATTR_INSURANCE_LINE] = insurance_line
        if submission_id:
            attributes[self.ATTR_SUBMISSION_ID] = submission_id

        with self.tracer.span("llm.inference", SpanKind.CLIENT, attributes) as span:
            yield span

    def instrument_function(
        self,
        span_name: Optional[str] = None,
        attributes: Optional[Dict] = None,
    ) -> Callable:
        """Decorator to instrument any function with a span."""
        def decorator(func: Callable) -> Callable:
            name = span_name or f"{func.__module__}.{func.__qualname__}"

            @functools.wraps(func)
            def wrapper(*args, **kwargs):
                with self.tracer.span(name, attributes=attributes or {}) as span:
                    result = func(*args, **kwargs)
                    return result

            return wrapper
        return decorator


class AsyncPipelineTracer:
    """
    Traces async AI pipeline stages with context propagation.

    The Sixfold platform processes insurance submissions through multiple
    async stages: ingestion → risk extraction → LLM scoring → decision.
    This tracer maintains trace context across async boundaries.
    """

    def __init__(self, tracer: OTELTracer):
        self.tracer = tracer
        self._pipeline_spans: Dict[str, Span] = {}

    def start_pipeline(self, pipeline_id: str, pipeline_type: str) -> Span:
        """Start a root span for an entire pipeline run."""
        span = self.tracer.start_span(
            f"pipeline.{pipeline_type}",
            SpanKind.SERVER,
            attributes={
                "pipeline.id": pipeline_id,
                "pipeline.type": pipeline_type,
            }
        )
        self._pipeline_spans[pipeline_id] = span
        return span

    def start_stage(self, pipeline_id: str, stage_name: str, stage_attrs: Optional[Dict] = None) -> Optional[Span]:
        """Start a child span for a pipeline stage."""
        parent = self._pipeline_spans.get(pipeline_id)
        if not parent:
            return None

        attrs = {"pipeline.id": pipeline_id, "pipeline.stage": stage_name}
        if stage_attrs:
            attrs.update(stage_attrs)

        span = self.tracer.start_span(
            f"pipeline.stage.{stage_name}",
            SpanKind.INTERNAL,
            attributes=attrs,
            parent_span=parent,
        )
        return span

    def end_stage(self, span: Span, success: bool = True, error: Optional[str] = None) -> None:
        """End a pipeline stage span."""
        span.set_attribute("stage.success", success)
        if error:
            span.set_attribute("stage.error", error)
            span.set_status(SpanStatus.ERROR, error)
        self.tracer.end_span(span)

    def end_pipeline(self, pipeline_id: str, success: bool = True) -> Optional[Span]:
        """End a pipeline root span."""
        span = self._pipeline_spans.pop(pipeline_id, None)
        if span:
            span.set_attribute("pipeline.success", success)
            self.tracer.end_span(span)
        return span

    def get_pipeline_trace_id(self, pipeline_id: str) -> Optional[str]:
        """Get the trace ID for a pipeline (for log correlation)."""
        span = self._pipeline_spans.get(pipeline_id)
        return span.trace_id if span else None


def build_otel_collector_config() -> Dict:
    """
    Build the OpenTelemetry Collector configuration for Sixfold.

    Exports to:
    - Prometheus (metrics)
    - Grafana Tempo (traces)
    - Loki (logs via OTLP)
    """
    return {
        "receivers": {
            "otlp": {
                "protocols": {
                    "grpc": {"endpoint": "0.0.0.0:4317"},
                    "http": {"endpoint": "0.0.0.0:4318"},
                }
            }
        },
        "processors": {
            "batch": {
                "timeout": "10s",
                "send_batch_size": 1024,
            },
            "resource": {
                "attributes": [
                    {"action": "insert", "key": "deployment.environment", "value": "production"},
                    {"action": "insert", "key": "service.namespace", "value": "sixfold"},
                ]
            },
            "filter/drop_health_spans": {
                "traces": {
                    "span": ['attributes["http.target"] == "/healthz"']
                }
            },
        },
        "exporters": {
            "prometheus": {
                "endpoint": "0.0.0.0:8889",
                "namespace": "sixfold",
            },
            "otlp/tempo": {
                "endpoint": "tempo:4317",
                "tls": {"insecure": True},
            },
            "loki": {
                "endpoint": "http://loki:3100/loki/api/v1/push",
                "labels": {
                    "resource": {
                        "service.name": "service_name",
                        "deployment.environment": "env",
                    }
                },
            },
        },
        "service": {
            "pipelines": {
                "traces": {
                    "receivers": ["otlp"],
                    "processors": ["resource", "filter/drop_health_spans", "batch"],
                    "exporters": ["otlp/tempo"],
                },
                "metrics": {
                    "receivers": ["otlp"],
                    "processors": ["resource", "batch"],
                    "exporters": ["prometheus"],
                },
                "logs": {
                    "receivers": ["otlp"],
                    "processors": ["resource", "batch"],
                    "exporters": ["loki"],
                },
            }
        },
    }
