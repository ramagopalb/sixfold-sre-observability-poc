"""
Shared fixtures for Sixfold SRE Observability POC test suite.
"""
import sys
import os

# Add POC_Project root to sys.path so imports work from tests/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from observability.slo_manager import (
    SLOManager, SLODefinition, SLOType, create_sixfold_slos
)
from observability.alert_rules import AlertRuleGenerator, AlertSeverity, AlertRule
from observability.otel_instrumentation import OTELTracer, LLMInstrumentor, AsyncPipelineTracer
from incident.incident_manager import (
    IncidentManager, IncidentType, IncidentSeverity, SeverityClassifier, RCAGenerator, Incident
)


@pytest.fixture
def slo_manager():
    return create_sixfold_slos()


@pytest.fixture
def empty_slo_manager():
    return SLOManager()


@pytest.fixture
def alert_generator():
    return AlertRuleGenerator()


@pytest.fixture
def tracer():
    t = OTELTracer("sixfold-test-service", "1.0.0")
    return t


@pytest.fixture
def llm_instrumentor(tracer):
    return LLMInstrumentor(tracer)


@pytest.fixture
def pipeline_tracer(tracer):
    return AsyncPipelineTracer(tracer)


@pytest.fixture
def incident_manager():
    return IncidentManager()


@pytest.fixture
def severity_classifier():
    return SeverityClassifier()


@pytest.fixture
def rca_generator():
    return RCAGenerator()
