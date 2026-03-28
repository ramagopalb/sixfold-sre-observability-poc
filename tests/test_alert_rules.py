"""
Tests for Alert Rule Generator.
Covers: SLO burn-rate alerts, latency alerts, LLM alerts,
        Kubernetes reliability alerts, rule validation, and YAML output.
"""

import pytest
from observability.alert_rules import (
    AlertRuleGenerator,
    AlertRule,
    AlertGroup,
    AlertSeverity,
)


# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def generator():
    return AlertRuleGenerator()


@pytest.fixture
def generator_with_all_rules():
    gen = AlertRuleGenerator()
    gen.generate_all_rules()
    return gen


# ─── AlertRule Tests ──────────────────────────────────────────────────────────

class TestAlertRule:

    def test_alert_rule_to_yaml_contains_name(self):
        rule = AlertRule(
            name="TestAlert",
            expr="up == 0",
            severity=AlertSeverity.CRITICAL,
            summary="Test service is down",
            description="Test service has been down for 5 minutes",
        )
        yaml_str = rule.to_yaml()
        assert "TestAlert" in yaml_str

    def test_alert_rule_yaml_has_severity_label(self):
        rule = AlertRule(
            name="TestCriticalAlert",
            expr="up == 0",
            severity=AlertSeverity.CRITICAL,
            summary="Down",
            description="Service is down",
        )
        yaml_str = rule.to_yaml()
        assert "severity: critical" in yaml_str

    def test_alert_rule_yaml_has_for_duration(self):
        rule = AlertRule(
            name="LongAlert",
            expr="metric > 10",
            severity=AlertSeverity.WARNING,
            summary="High",
            description="Metric is high",
            for_duration="10m",
        )
        yaml_str = rule.to_yaml()
        assert "for: 10m" in yaml_str

    def test_alert_rule_yaml_has_runbook(self):
        rule = AlertRule(
            name="AlertWithRunbook",
            expr="errors > 0",
            severity=AlertSeverity.CRITICAL,
            summary="Errors",
            description="There are errors",
            runbook_url="https://runbooks.example.com/test",
        )
        yaml_str = rule.to_yaml()
        assert "runbook_url" in yaml_str
        assert "runbooks.example.com" in yaml_str

    def test_alert_rule_custom_labels(self):
        rule = AlertRule(
            name="ServiceAlert",
            expr="rate(errors[5m]) > 0",
            severity=AlertSeverity.WARNING,
            summary="Service errors",
            description="Service has errors",
            labels={"service": "my-service", "team": "sre"},
        )
        yaml_str = rule.to_yaml()
        assert "service: my-service" in yaml_str
        assert "team: sre" in yaml_str

    def test_warning_severity_label(self):
        rule = AlertRule(
            name="WarningAlert",
            expr="metric > 5",
            severity=AlertSeverity.WARNING,
            summary="Warning",
            description="A warning condition",
        )
        yaml_str = rule.to_yaml()
        assert "severity: warning" in yaml_str


# ─── AlertGroup Tests ─────────────────────────────────────────────────────────

class TestAlertGroup:

    def test_group_add_rule(self):
        group = AlertGroup(name="test_group", interval="1m")
        rule = AlertRule("TestRule", "up == 0", AlertSeverity.CRITICAL, "Down", "Down")
        group.add_rule(rule)
        assert len(group.rules) == 1

    def test_group_to_yaml_has_name(self):
        group = AlertGroup(name="slo_alerts", interval="1m")
        yaml_str = group.to_yaml()
        assert "slo_alerts" in yaml_str

    def test_group_to_yaml_has_interval(self):
        group = AlertGroup(name="test", interval="30s")
        yaml_str = group.to_yaml()
        assert "30s" in yaml_str


# ─── SLO Burn Rate Alert Tests ────────────────────────────────────────────────

class TestSLOBurnRateAlerts:

    def test_generates_four_window_rules(self, generator):
        rules = generator.generate_slo_burn_rate_alerts(
            "risk-score-api", 0.001, "risk_score_api"
        )
        assert len(rules) == 4

    def test_first_rule_is_critical(self, generator):
        rules = generator.generate_slo_burn_rate_alerts(
            "risk-score-api", 0.001, "risk_score_api"
        )
        assert rules[0].severity == AlertSeverity.CRITICAL

    def test_last_rule_is_warning(self, generator):
        rules = generator.generate_slo_burn_rate_alerts(
            "risk-score-api", 0.001, "risk_score_api"
        )
        assert rules[-1].severity == AlertSeverity.WARNING

    def test_1h_window_rule_name(self, generator):
        rules = generator.generate_slo_burn_rate_alerts(
            "risk-score-api", 0.001, "risk_score_api"
        )
        names = [r.name for r in rules]
        assert any("1h" in name for name in names)

    def test_burn_rate_threshold_in_expr(self, generator):
        # 0.001 budget * 14.4 burn rate = 0.0144 threshold for 1h window
        rules = generator.generate_slo_burn_rate_alerts(
            "risk-score-api", 0.001, "risk_score_api"
        )
        one_hour_rule = next(r for r in rules if "1h" in r.name)
        assert "0.0144" in one_hour_rule.expr

    def test_dual_window_expr_structure(self, generator):
        # Multi-window alerts must check BOTH long and short windows
        rules = generator.generate_slo_burn_rate_alerts(
            "test-service", 0.001, "test_service"
        )
        for rule in rules:
            assert " and " in rule.expr

    def test_rules_have_service_label(self, generator):
        rules = generator.generate_slo_burn_rate_alerts(
            "underwriting-api", 0.001, "underwriting_api"
        )
        for rule in rules:
            assert rule.labels.get("service") == "underwriting-api"

    def test_rules_have_runbook_url(self, generator):
        rules = generator.generate_slo_burn_rate_alerts(
            "risk-score-api", 0.001, "risk_score_api"
        )
        for rule in rules:
            assert rule.runbook_url is not None


# ─── Latency Alert Tests ──────────────────────────────────────────────────────

class TestLatencyAlerts:

    def test_generates_two_latency_rules(self, generator):
        rules = generator.generate_latency_alerts("underwriting-api", 1000.0)
        assert len(rules) == 2

    def test_p99_rule_is_critical(self, generator):
        rules = generator.generate_latency_alerts("underwriting-api", 1000.0)
        p99_rule = next(r for r in rules if "P99" in r.name)
        assert p99_rule.severity == AlertSeverity.CRITICAL

    def test_p95_rule_is_warning(self, generator):
        rules = generator.generate_latency_alerts("underwriting-api", 1000.0)
        p95_rule = next(r for r in rules if "P95" in r.name)
        assert p95_rule.severity == AlertSeverity.WARNING

    def test_threshold_in_p99_expr(self, generator):
        rules = generator.generate_latency_alerts("underwriting-api", 1000.0)
        p99_rule = next(r for r in rules if "P99" in r.name)
        assert "1000" in p99_rule.expr

    def test_p95_threshold_lower_than_p99(self, generator):
        rules = generator.generate_latency_alerts("underwriting-api", 2000.0)
        p99_rule = next(r for r in rules if "P99" in r.name)
        p95_rule = next(r for r in rules if "P95" in r.name)
        # P95 threshold should be 75% of P99 threshold
        assert "1500" in p95_rule.expr

    def test_latency_rules_have_percentile_label(self, generator):
        rules = generator.generate_latency_alerts("risk-score-api", 3000.0)
        for rule in rules:
            assert "percentile" in rule.labels


# ─── LLM-Specific Alert Tests ─────────────────────────────────────────────────

class TestLLMAlerts:

    def test_generates_five_llm_rules(self, generator):
        rules = generator.generate_llm_specific_alerts()
        assert len(rules) == 5

    def test_token_throughput_drop_is_warning(self, generator):
        rules = generator.generate_llm_specific_alerts()
        throughput_rule = next(r for r in rules if "Throughput" in r.name)
        assert throughput_rule.severity == AlertSeverity.WARNING

    def test_inference_timeout_is_critical(self, generator):
        rules = generator.generate_llm_specific_alerts()
        timeout_rule = next(r for r in rules if "Timeout" in r.name)
        assert timeout_rule.severity == AlertSeverity.CRITICAL

    def test_model_error_rate_is_critical(self, generator):
        rules = generator.generate_llm_specific_alerts()
        error_rule = next(r for r in rules if "Error" in r.name)
        assert error_rule.severity == AlertSeverity.CRITICAL

    def test_queue_depth_alert_exists(self, generator):
        rules = generator.generate_llm_specific_alerts()
        queue_rule = next((r for r in rules if "Queue" in r.name), None)
        assert queue_rule is not None

    def test_token_cost_anomaly_exists(self, generator):
        rules = generator.generate_llm_specific_alerts()
        cost_rule = next((r for r in rules if "Cost" in r.name), None)
        assert cost_rule is not None

    def test_all_llm_rules_have_service_label(self, generator):
        rules = generator.generate_llm_specific_alerts()
        for rule in rules:
            assert rule.labels.get("service") == "llm-inference"


# ─── Kubernetes Alert Tests ───────────────────────────────────────────────────

class TestKubernetesAlerts:

    def test_generates_five_k8s_rules(self, generator):
        rules = generator.generate_kubernetes_reliability_alerts()
        assert len(rules) == 5

    def test_crash_loop_is_critical(self, generator):
        rules = generator.generate_kubernetes_reliability_alerts()
        crash_rule = next(r for r in rules if "CrashLoop" in r.name)
        assert crash_rule.severity == AlertSeverity.CRITICAL

    def test_node_memory_is_critical(self, generator):
        rules = generator.generate_kubernetes_reliability_alerts()
        memory_rule = next(r for r in rules if "Memory" in r.name)
        assert memory_rule.severity == AlertSeverity.CRITICAL

    def test_pvc_storage_is_warning(self, generator):
        rules = generator.generate_kubernetes_reliability_alerts()
        pvc_rule = next(r for r in rules if "PVC" in r.name)
        assert pvc_rule.severity == AlertSeverity.WARNING

    def test_hpa_max_replicas_is_warning(self, generator):
        rules = generator.generate_kubernetes_reliability_alerts()
        hpa_rule = next(r for r in rules if "HPA" in r.name)
        assert hpa_rule.severity == AlertSeverity.WARNING

    def test_rollout_stuck_is_warning(self, generator):
        rules = generator.generate_kubernetes_reliability_alerts()
        rollout_rule = next(r for r in rules if "Rollout" in r.name)
        assert rollout_rule.severity == AlertSeverity.WARNING


# ─── Full Rule Generation Tests ───────────────────────────────────────────────

class TestFullRuleGeneration:

    def test_generate_all_rules_returns_yaml(self, generator):
        yaml_output = generator.generate_all_rules()
        assert isinstance(yaml_output, str)
        assert "groups:" in yaml_output

    def test_generate_all_rules_has_slo_group(self, generator):
        yaml_output = generator.generate_all_rules()
        assert "sixfold_slo_alerts" in yaml_output

    def test_generate_all_rules_has_llm_group(self, generator):
        yaml_output = generator.generate_all_rules()
        assert "sixfold_llm_alerts" in yaml_output

    def test_generate_all_rules_has_k8s_group(self, generator):
        yaml_output = generator.generate_all_rules()
        assert "sixfold_kubernetes_alerts" in yaml_output

    def test_total_rule_count(self, generator_with_all_rules):
        count = generator_with_all_rules.get_rule_count()
        assert count >= 25  # Minimum expected rule count

    def test_get_rules_by_severity_critical(self, generator_with_all_rules):
        critical_rules = generator_with_all_rules.get_rules_by_severity(AlertSeverity.CRITICAL)
        assert len(critical_rules) > 0

    def test_get_rules_by_severity_warning(self, generator_with_all_rules):
        warning_rules = generator_with_all_rules.get_rules_by_severity(AlertSeverity.WARNING)
        assert len(warning_rules) > 0

    def test_validation_passes_for_generated_rules(self, generator_with_all_rules):
        errors = generator_with_all_rules.validate_rules()
        assert len(errors) == 0, f"Validation errors: {errors}"

    def test_no_duplicate_rule_names(self, generator_with_all_rules):
        errors = generator_with_all_rules.validate_rules()
        duplicate_errors = [e for e in errors if "Duplicate" in e]
        assert len(duplicate_errors) == 0
