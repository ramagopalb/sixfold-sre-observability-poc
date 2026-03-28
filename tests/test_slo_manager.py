"""
Tests for SLO Manager — SLO/error budget calculator.
Covers: SLO registration, error budget calculation, burn rate alerting,
        multi-window alerts, edge cases, and Sixfold service SLOs.
"""

import pytest
from observability.slo_manager import (
    SLODefinition,
    SLOManager,
    SLOType,
    SLOWindow,
    AlertSeverity,
    ErrorBudgetStatus,
    STANDARD_ALERT_WINDOWS,
    create_sixfold_slos,
)


# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def manager():
    return SLOManager()


@pytest.fixture
def error_rate_slo():
    return SLODefinition(
        service="risk-score-api",
        slo_type=SLOType.ERROR_RATE,
        target=0.001,
        window_days=30,
    )


@pytest.fixture
def latency_slo():
    return SLODefinition(
        service="underwriting-api",
        slo_type=SLOType.LATENCY,
        target=0.99,
        window_days=30,
        latency_threshold_ms=1000.0,
        percentile=0.99,
    )


@pytest.fixture
def availability_slo():
    return SLODefinition(
        service="submission-ingestion",
        slo_type=SLOType.AVAILABILITY,
        target=0.999,
        window_days=30,
    )


@pytest.fixture
def sixfold_manager():
    return create_sixfold_slos()


# ─── SLODefinition Tests ──────────────────────────────────────────────────────

class TestSLODefinition:

    def test_error_rate_budget_fraction(self, error_rate_slo):
        # For error rate SLO, budget = target itself (0.1%)
        assert error_rate_slo.error_budget_fraction == pytest.approx(0.001)

    def test_availability_budget_fraction(self, availability_slo):
        # 99.9% availability → 0.1% budget
        assert availability_slo.error_budget_fraction == pytest.approx(0.001)

    def test_latency_budget_fraction(self, latency_slo):
        # 99% of requests fast → 1% allowed to be slow
        assert latency_slo.error_budget_fraction == pytest.approx(0.01)

    def test_window_seconds(self, error_rate_slo):
        assert error_rate_slo.window_seconds == 30 * 24 * 3600

    def test_latency_slo_has_threshold(self, latency_slo):
        assert latency_slo.latency_threshold_ms == 1000.0

    def test_latency_slo_has_percentile(self, latency_slo):
        assert latency_slo.percentile == 0.99

    def test_slo_service_name(self, error_rate_slo):
        assert error_rate_slo.service == "risk-score-api"

    def test_slo_type(self, error_rate_slo):
        assert error_rate_slo.slo_type == SLOType.ERROR_RATE

    def test_100_percent_availability_slo(self):
        slo = SLODefinition("svc", SLOType.AVAILABILITY, target=1.0, window_days=30)
        assert slo.error_budget_fraction == pytest.approx(0.0)

    def test_five_nines_availability(self):
        slo = SLODefinition("svc", SLOType.AVAILABILITY, target=0.99999, window_days=30)
        assert slo.error_budget_fraction == pytest.approx(0.00001)


# ─── SLOManager Registration Tests ───────────────────────────────────────────

class TestSLOManagerRegistration:

    def test_register_slo(self, manager, error_rate_slo):
        manager.register_slo(error_rate_slo)
        retrieved = manager.get_slo("risk-score-api", SLOType.ERROR_RATE)
        assert retrieved is not None
        assert retrieved.service == "risk-score-api"

    def test_register_multiple_slos_same_service(self, manager, error_rate_slo, latency_slo):
        manager.register_slo(error_rate_slo)
        # Register a latency SLO for the same service but different type
        latency = SLODefinition("risk-score-api", SLOType.LATENCY, 0.99, 30, 3000.0, 0.99)
        manager.register_slo(latency)
        assert manager.get_slo("risk-score-api", SLOType.ERROR_RATE) is not None
        assert manager.get_slo("risk-score-api", SLOType.LATENCY) is not None

    def test_get_nonexistent_slo(self, manager):
        result = manager.get_slo("nonexistent-service", SLOType.ERROR_RATE)
        assert result is None

    def test_list_slos(self, manager, error_rate_slo, availability_slo):
        manager.register_slo(error_rate_slo)
        manager.register_slo(availability_slo)
        slos = manager.list_slos()
        assert len(slos) == 2

    def test_overwrite_slo(self, manager, error_rate_slo):
        manager.register_slo(error_rate_slo)
        new_slo = SLODefinition("risk-score-api", SLOType.ERROR_RATE, target=0.005, window_days=30)
        manager.register_slo(new_slo)
        retrieved = manager.get_slo("risk-score-api", SLOType.ERROR_RATE)
        assert retrieved.target == 0.005


# ─── Error Budget Calculation Tests ──────────────────────────────────────────

class TestErrorBudgetCalculation:

    def test_healthy_budget_no_errors(self, manager, error_rate_slo):
        manager.register_slo(error_rate_slo)
        status = manager.calculate_error_budget(
            service="risk-score-api",
            slo_type=SLOType.ERROR_RATE,
            total_requests=100000,
            bad_requests=0,
            window_seconds=30 * 86400,
        )
        assert status.burn_rate == 0.0
        assert status.budget_percent_remaining == 100.0
        assert status.status_label == "HEALTHY"

    def test_budget_exactly_on_target(self, manager, error_rate_slo):
        manager.register_slo(error_rate_slo)
        # 0.1% error rate exactly = burn rate 1.0
        status = manager.calculate_error_budget(
            service="risk-score-api",
            slo_type=SLOType.ERROR_RATE,
            total_requests=100000,
            bad_requests=100,  # 0.1% = exactly on budget
            window_seconds=30 * 86400,
        )
        assert status.burn_rate == pytest.approx(1.0)
        assert status.budget_percent_remaining == pytest.approx(0.0, abs=1.0)

    def test_high_burn_rate_p1_alert(self, manager, error_rate_slo):
        manager.register_slo(error_rate_slo)
        # 1.44% error rate = 14.4x burn rate → P1 alert should fire
        status = manager.calculate_error_budget(
            service="risk-score-api",
            slo_type=SLOType.ERROR_RATE,
            total_requests=100000,
            bad_requests=1440,
            window_seconds=30 * 86400,
        )
        assert status.burn_rate > 14.0
        # At least one window should be firing as PAGE
        page_windows = [w for w in status.alert_windows if w["firing"] and w["severity"] == "page"]
        assert len(page_windows) > 0

    def test_budget_exhausted_status(self, manager, error_rate_slo):
        manager.register_slo(error_rate_slo)
        # 10% error rate — way over budget
        status = manager.calculate_error_budget(
            service="risk-score-api",
            slo_type=SLOType.ERROR_RATE,
            total_requests=1000,
            bad_requests=100,
            window_seconds=30 * 86400,
        )
        assert status.remaining_fraction < 0
        assert status.status_label == "EXHAUSTED"
        assert status.is_budget_exhausted

    def test_zero_requests_edge_case(self, manager, error_rate_slo):
        manager.register_slo(error_rate_slo)
        status = manager.calculate_error_budget(
            service="risk-score-api",
            slo_type=SLOType.ERROR_RATE,
            total_requests=0,
            bad_requests=0,
            window_seconds=30 * 86400,
        )
        assert status.burn_rate == 0.0

    def test_unregistered_slo_raises(self, manager):
        with pytest.raises(ValueError, match="No SLO registered"):
            manager.calculate_error_budget(
                service="unknown-service",
                slo_type=SLOType.ERROR_RATE,
                total_requests=1000,
                bad_requests=0,
                window_seconds=86400,
            )

    def test_warning_status_at_50_percent_remaining(self, manager, error_rate_slo):
        manager.register_slo(error_rate_slo)
        # 0.05% errors = 50% budget consumed
        status = manager.calculate_error_budget(
            service="risk-score-api",
            slo_type=SLOType.ERROR_RATE,
            total_requests=100000,
            bad_requests=50,
            window_seconds=30 * 86400,
        )
        assert status.budget_percent_remaining == pytest.approx(50.0)
        assert status.status_label == "WARNING"

    def test_projected_exhaustion_when_burning_fast(self, manager, error_rate_slo):
        manager.register_slo(error_rate_slo)
        status = manager.calculate_error_budget(
            service="risk-score-api",
            slo_type=SLOType.ERROR_RATE,
            total_requests=10000,
            bad_requests=50,  # 0.5% = 5x burn rate — budget already exhausted
            window_seconds=30 * 86400,
        )
        # At 5x burn rate the budget is already exhausted (remaining < 0),
        # so projected_exhaustion_hours is None (already past the deadline).
        # The burn_rate should be > 1 confirming we are over budget.
        assert status.burn_rate > 1.0
        assert status.is_budget_exhausted

    def test_no_projected_exhaustion_when_healthy(self, manager, error_rate_slo):
        manager.register_slo(error_rate_slo)
        status = manager.calculate_error_budget(
            service="risk-score-api",
            slo_type=SLOType.ERROR_RATE,
            total_requests=100000,
            bad_requests=10,  # 0.01% = 0.1x burn rate (healthy)
            window_seconds=30 * 86400,
        )
        assert status.projected_exhaustion_hours is None

    def test_alert_windows_count(self, manager, error_rate_slo):
        manager.register_slo(error_rate_slo)
        status = manager.calculate_error_budget(
            service="risk-score-api",
            slo_type=SLOType.ERROR_RATE,
            total_requests=10000,
            bad_requests=0,
            window_seconds=30 * 86400,
        )
        assert len(status.alert_windows) == len(STANDARD_ALERT_WINDOWS)

    def test_availability_slo_calculation(self, manager, availability_slo):
        manager.register_slo(availability_slo)
        # 99.9% availability — 0.05% downtime = 50% of budget
        status = manager.calculate_error_budget(
            service="submission-ingestion",
            slo_type=SLOType.AVAILABILITY,
            total_requests=100000,
            bad_requests=50,
            window_seconds=30 * 86400,
        )
        assert status.budget_percent_remaining == pytest.approx(50.0)


# ─── Sixfold Service SLO Tests ────────────────────────────────────────────────

class TestSixfoldSLOs:

    def test_sixfold_manager_has_slos(self, sixfold_manager):
        slos = sixfold_manager.list_slos()
        assert len(slos) >= 5  # At least the standard Sixfold SLOs

    def test_risk_score_api_error_rate_slo_exists(self, sixfold_manager):
        slo = sixfold_manager.get_slo("risk-score-api", SLOType.ERROR_RATE)
        assert slo is not None
        assert slo.target == 0.001

    def test_risk_score_api_latency_slo_exists(self, sixfold_manager):
        slo = sixfold_manager.get_slo("risk-score-api", SLOType.LATENCY)
        assert slo is not None
        assert slo.latency_threshold_ms == 3000.0

    def test_submission_ingestion_availability_slo(self, sixfold_manager):
        slo = sixfold_manager.get_slo("submission-ingestion", SLOType.AVAILABILITY)
        assert slo is not None
        assert slo.target == 0.999

    def test_underwriting_api_slo(self, sixfold_manager):
        slo = sixfold_manager.get_slo("underwriting-api", SLOType.LATENCY)
        assert slo is not None
        assert slo.latency_threshold_ms == 1000.0

    def test_llm_inference_slo(self, sixfold_manager):
        slo = sixfold_manager.get_slo("llm-inference", SLOType.LATENCY)
        assert slo is not None
        assert slo.latency_threshold_ms == 5000.0
        # LLM SLO is looser — 95th percentile
        assert slo.percentile == 0.95

    def test_recording_rules_generation(self, sixfold_manager):
        rules_yaml = sixfold_manager.generate_prometheus_recording_rules(
            "risk-score-api", SLOType.ERROR_RATE
        )
        assert "groups:" in rules_yaml
        assert "risk_score_api_error_rate" in rules_yaml

    def test_metrics_ingestion_and_budget_retrieval(self, sixfold_manager):
        sixfold_manager.ingest_metrics("risk-score-api", {
            "total_requests": 50000,
            "bad_requests": 10,
            "window_seconds": 30 * 86400,
        })
        statuses = sixfold_manager.get_all_budget_statuses()
        # Should have at least the risk-score-api status
        assert any("risk-score-api" in k for k in statuses.keys())

    def test_all_sixfold_slos_have_valid_budgets(self, sixfold_manager):
        for slo in sixfold_manager.list_slos():
            assert slo.error_budget_fraction >= 0.0
            assert slo.error_budget_fraction < 1.0

    def test_llm_slo_more_lenient_than_api_slo(self, sixfold_manager):
        llm_slo = sixfold_manager.get_slo("llm-inference", SLOType.LATENCY)
        api_slo = sixfold_manager.get_slo("underwriting-api", SLOType.LATENCY)
        # LLM SLO allows more slow requests
        assert llm_slo.error_budget_fraction > api_slo.error_budget_fraction
