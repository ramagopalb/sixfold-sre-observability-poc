"""
Tests for Incident Manager.
Covers: incident creation, severity classification, lifecycle management,
        RCA generation, DR runbooks, customer communication, and statistics.
"""

import pytest
from incident.incident_manager import (
    IncidentManager,
    IncidentSeverity,
    IncidentStatus,
    IncidentType,
    SeverityClassifier,
    RCAGenerator,
    DisasterRecoveryRunner,
    ActionItem,
    IncidentTimeline,
)


# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def manager():
    return IncidentManager()


@pytest.fixture
def classifier():
    return SeverityClassifier()


@pytest.fixture
def rca_gen():
    return RCAGenerator()


@pytest.fixture
def dr_runner():
    runner = DisasterRecoveryRunner()
    runner.register_runbook(
        "test-runbook",
        steps=["Step 1: Check status", "Step 2: Restart service", "Step 3: Verify"],
        rto_minutes=15,
        rpo_minutes=5,
    )
    return runner


# ─── SeverityClassifier Tests ─────────────────────────────────────────────────

class TestSeverityClassifier:

    def test_high_error_rate_is_p1(self, classifier):
        sev = classifier.classify(error_rate=0.06)
        assert sev == IncidentSeverity.P1

    def test_moderate_error_rate_is_p2(self, classifier):
        sev = classifier.classify(error_rate=0.02)
        assert sev == IncidentSeverity.P2

    def test_no_signals_is_p4(self, classifier):
        sev = classifier.classify()
        assert sev == IncidentSeverity.P4

    def test_security_incident_is_p1(self, classifier):
        sev = classifier.classify(is_security_incident=True)
        assert sev == IncidentSeverity.P1

    def test_complete_outage_with_enterprise_customers_is_p1(self, classifier):
        sev = classifier.classify(is_complete_outage=True, affected_enterprise_customers=3)
        assert sev == IncidentSeverity.P1

    def test_high_latency_multiplier_is_p1(self, classifier):
        sev = classifier.classify(latency_multiplier=6.0)
        assert sev == IncidentSeverity.P1

    def test_moderate_latency_multiplier_is_p2(self, classifier):
        sev = classifier.classify(latency_multiplier=4.0)
        assert sev == IncidentSeverity.P2

    def test_enterprise_customer_affected_is_p3(self, classifier):
        sev = classifier.classify(affected_enterprise_customers=1)
        assert sev == IncidentSeverity.P3

    def test_p1_response_target_5_minutes(self, classifier):
        target = classifier.get_response_target_minutes(IncidentSeverity.P1)
        assert target == 5

    def test_p4_response_target_24_hours(self, classifier):
        target = classifier.get_response_target_minutes(IncidentSeverity.P4)
        assert target == 1440

    def test_error_rate_exactly_at_p1_threshold(self, classifier):
        sev = classifier.classify(error_rate=0.05)
        assert sev == IncidentSeverity.P1

    def test_error_rate_just_below_p1_threshold(self, classifier):
        sev = classifier.classify(error_rate=0.04)
        assert sev == IncidentSeverity.P2


# ─── IncidentManager Creation Tests ──────────────────────────────────────────

class TestIncidentCreation:

    def test_create_incident_returns_incident(self, manager):
        incident = manager.create_incident(
            title="Risk scoring API down",
            incident_type=IncidentType.AVAILABILITY,
            affected_services=["risk-score-api"],
            detected_by="prometheus-alert",
            error_rate=0.5,
        )
        assert incident is not None
        assert incident.id.startswith("INC-")

    def test_incident_id_is_unique(self, manager):
        i1 = manager.create_incident(
            "Test 1", IncidentType.ERROR_RATE, ["svc-a"], "alert", error_rate=0.1
        )
        i2 = manager.create_incident(
            "Test 2", IncidentType.LATENCY, ["svc-b"], "alert", error_rate=0.1
        )
        assert i1.id != i2.id

    def test_high_error_rate_auto_classified_p1(self, manager):
        incident = manager.create_incident(
            "API errors spike",
            IncidentType.ERROR_RATE,
            ["risk-score-api"],
            "prometheus",
            error_rate=0.08,
        )
        assert incident.severity == IncidentSeverity.P1

    def test_incident_initial_status_detected(self, manager):
        incident = manager.create_incident(
            "Test incident", IncidentType.LATENCY, ["api"], "grafana"
        )
        assert incident.status == IncidentStatus.DETECTED

    def test_incident_has_detection_timestamp(self, manager):
        incident = manager.create_incident(
            "Test incident", IncidentType.AVAILABILITY, ["api"], "cloudwatch"
        )
        assert incident.detected_at is not None

    def test_incident_timeline_has_detection_event(self, manager):
        incident = manager.create_incident(
            "Test incident", IncidentType.LLM_DEGRADATION, ["llm-inference"], "alert"
        )
        assert len(incident.timeline.events) >= 1
        assert any("detected" in e["action"] for e in incident.timeline.events)

    def test_incident_retrieval_by_id(self, manager):
        incident = manager.create_incident(
            "Retrievable incident", IncidentType.INFRASTRUCTURE, ["eks-cluster"], "cloudwatch"
        )
        retrieved = manager.get_incident(incident.id)
        assert retrieved is not None
        assert retrieved.title == "Retrievable incident"

    def test_nonexistent_incident_returns_none(self, manager):
        result = manager.get_incident("INC-NONEXISTENT")
        assert result is None


# ─── Incident Lifecycle Tests ─────────────────────────────────────────────────

class TestIncidentLifecycle:

    def test_acknowledge_incident(self, manager):
        incident = manager.create_incident(
            "LLM timeout", IncidentType.LLM_DEGRADATION, ["llm-inference"], "alert", error_rate=0.06
        )
        incident.acknowledge("ram.basireddy@sixfold.ai")
        assert incident.status == IncidentStatus.ACKNOWLEDGED
        assert incident.incident_commander == "ram.basireddy@sixfold.ai"

    def test_acknowledge_adds_timeline_event(self, manager):
        incident = manager.create_incident(
            "Test", IncidentType.ERROR_RATE, ["api"], "alert", error_rate=0.06
        )
        initial_events = len(incident.timeline.events)
        incident.acknowledge("engineer@sixfold.ai")
        assert len(incident.timeline.events) > initial_events

    def test_escalate_adds_responder(self, manager):
        incident = manager.create_incident(
            "Test", IncidentType.AVAILABILITY, ["api"], "alert"
        )
        incident.escalate("backend-lead@sixfold.ai", "Need backend expertise")
        assert "backend-lead@sixfold.ai" in incident.responders

    def test_resolve_incident(self, manager):
        incident = manager.create_incident(
            "Resolved incident", IncidentType.ERROR_RATE, ["api"], "alert", error_rate=0.06
        )
        incident.update_status(IncidentStatus.RESOLVED, "on-call-engineer", "Fix deployed")
        assert incident.status == IncidentStatus.RESOLVED
        assert incident.resolved_at is not None

    def test_close_incident(self, manager):
        incident = manager.create_incident(
            "Closed incident", IncidentType.LATENCY, ["api"], "alert"
        )
        incident.update_status(IncidentStatus.CLOSED, "sre-team")
        assert incident.status == IncidentStatus.CLOSED
        assert incident.closed_at is not None

    def test_is_resolved_after_resolution(self, manager):
        incident = manager.create_incident(
            "Test", IncidentType.DATA, ["db"], "cloudwatch"
        )
        assert not incident.is_resolved
        incident.update_status(IncidentStatus.RESOLVED, "engineer")
        assert incident.is_resolved

    def test_add_action_item(self, manager):
        incident = manager.create_incident(
            "Test", IncidentType.ERROR_RATE, ["api"], "alert", error_rate=0.06
        )
        action = ActionItem(
            title="Add retry logic",
            owner="backend-team",
            due_date="2026-04-15",
            priority="high",
            description="Implement exponential backoff"
        )
        incident.add_action_item(action)
        assert len(incident.action_items) == 1
        assert incident.action_items[0].title == "Add retry logic"

    def test_list_open_incidents(self, manager):
        manager.create_incident("Open 1", IncidentType.LATENCY, ["api"], "alert")
        i2 = manager.create_incident("Open 2", IncidentType.ERROR_RATE, ["api"], "alert", error_rate=0.06)
        i2.update_status(IncidentStatus.RESOLVED, "engineer")

        open_incidents = manager.list_open_incidents()
        # i2 is resolved, only "Open 1" should be open
        open_titles = [i.title for i in open_incidents]
        assert "Open 1" in open_titles
        assert "Open 2" not in open_titles


# ─── RCA Generation Tests ─────────────────────────────────────────────────────

class TestRCAGeneration:

    def test_generate_rca_returns_string(self, manager):
        incident = manager.create_incident(
            "RCA test", IncidentType.LLM_DEGRADATION, ["llm-inference"], "alert", error_rate=0.06
        )
        rca = manager.generate_rca(incident.id)
        assert isinstance(rca, str)
        assert len(rca) > 100

    def test_rca_contains_incident_id(self, manager):
        incident = manager.create_incident(
            "RCA test", IncidentType.ERROR_RATE, ["api"], "alert", error_rate=0.06
        )
        rca = manager.generate_rca(incident.id)
        assert incident.id in rca

    def test_rca_contains_affected_services(self, manager):
        incident = manager.create_incident(
            "RCA test", IncidentType.AVAILABILITY, ["risk-score-api"], "alert"
        )
        rca = manager.generate_rca(incident.id)
        assert "risk-score-api" in rca

    def test_rca_for_nonexistent_incident_returns_none(self, manager):
        rca = manager.generate_rca("INC-NONEXISTENT")
        assert rca is None

    def test_rca_contains_action_items(self, manager):
        incident = manager.create_incident(
            "RCA with actions", IncidentType.INFRASTRUCTURE, ["eks"], "cloudwatch"
        )
        incident.add_action_item(ActionItem(
            title="Fix memory leak", owner="sre-team", due_date="2026-04-01", priority="high"
        ))
        rca = manager.generate_rca(incident.id)
        assert "Fix memory leak" in rca

    def test_customer_communication_initial(self, manager):
        incident = manager.create_incident(
            "Customer comm test", IncidentType.AVAILABILITY, ["api"], "alert"
        )
        comm = manager.generate_customer_communication(incident.id, "initial")
        assert comm is not None
        assert "investigating" in comm.lower()

    def test_customer_communication_resolution(self, manager):
        incident = manager.create_incident(
            "Resolved comm test", IncidentType.ERROR_RATE, ["api"], "alert", error_rate=0.06
        )
        incident.update_status(IncidentStatus.RESOLVED, "engineer")
        comm = manager.generate_customer_communication(incident.id, "resolution")
        assert comm is not None
        assert "resolved" in comm.lower()


# ─── DR Runbook Tests ─────────────────────────────────────────────────────────

class TestDRRunbooks:

    def test_execute_runbook_dry_run(self, manager):
        result = manager.execute_dr_runbook("rds-failover", dry_run=True)
        assert result["success"] is True
        assert result["dry_run"] is True

    def test_execute_runbook_all_steps_completed(self, manager):
        result = manager.execute_dr_runbook("rds-failover", dry_run=True)
        assert len(result["steps_completed"]) > 0

    def test_execute_nonexistent_runbook(self, manager):
        result = manager.execute_dr_runbook("nonexistent-runbook", dry_run=True)
        assert result["success"] is False
        assert "not found" in result["error"]

    def test_standard_runbooks_registered(self, manager):
        # rds-failover, eks-node-group-recovery, llm-inference-recovery should be present
        result_rds = manager.execute_dr_runbook("rds-failover", dry_run=True)
        assert result_rds["success"] is True

        result_eks = manager.execute_dr_runbook("eks-node-group-recovery", dry_run=True)
        assert result_eks["success"] is True

        result_llm = manager.execute_dr_runbook("llm-inference-recovery", dry_run=True)
        assert result_llm["success"] is True


# ─── Incident Statistics Tests ────────────────────────────────────────────────

class TestIncidentStatistics:

    def test_stats_total_count(self, manager):
        manager.create_incident("I1", IncidentType.ERROR_RATE, ["api"], "alert", error_rate=0.06)
        manager.create_incident("I2", IncidentType.LATENCY, ["api"], "alert")
        stats = manager.get_incident_stats()
        assert stats["total"] >= 2

    def test_stats_by_severity(self, manager):
        manager.create_incident("P1 incident", IncidentType.ERROR_RATE, ["api"], "alert", error_rate=0.08)
        stats = manager.get_incident_stats()
        assert "P1" in stats["by_severity"]

    def test_stats_dr_runbooks_count(self, manager):
        stats = manager.get_incident_stats()
        assert stats["dr_runbooks_available"] >= 3  # At least the 3 standard runbooks
