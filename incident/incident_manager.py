"""
Incident Manager — Structured incident lifecycle management for Sixfold SRE.

Handles incident detection, triage, severity classification, escalation,
RCA generation, post-mortem facilitation, and disaster recovery execution.
Implements the blameless SRE culture with systematic learning.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional


class IncidentSeverity(Enum):
    P1 = "P1"   # Critical — customer-impacting, immediate response
    P2 = "P2"   # High — significant degradation, respond within 30m
    P3 = "P3"   # Medium — partial degradation, respond within 4h
    P4 = "P4"   # Low — minor issue, respond within 24h


class IncidentStatus(Enum):
    DETECTED = "detected"
    ACKNOWLEDGED = "acknowledged"
    INVESTIGATING = "investigating"
    MITIGATING = "mitigating"
    RESOLVED = "resolved"
    CLOSED = "closed"


class IncidentType(Enum):
    AVAILABILITY = "availability"
    LATENCY = "latency"
    ERROR_RATE = "error_rate"
    DATA = "data"
    SECURITY = "security"
    LLM_DEGRADATION = "llm_degradation"
    INFRASTRUCTURE = "infrastructure"


@dataclass
class IncidentTimeline:
    """Chronological events in an incident."""
    events: List[Dict] = field(default_factory=list)

    def add_event(self, actor: str, action: str, details: Optional[str] = None) -> None:
        self.events.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "timestamp_unix": time.time(),
            "actor": actor,
            "action": action,
            "details": details,
        })

    def get_duration_minutes(self) -> Optional[float]:
        """Duration from first to last event in minutes."""
        if len(self.events) < 2:
            return None
        start = self.events[0]["timestamp_unix"]
        end = self.events[-1]["timestamp_unix"]
        return (end - start) / 60.0


@dataclass
class ActionItem:
    """A follow-up action item from an incident post-mortem."""
    title: str
    owner: str
    due_date: str
    priority: str  # high/medium/low
    status: str = "open"
    description: Optional[str] = None


@dataclass
class Incident:
    """Represents a single incident lifecycle."""
    id: str
    title: str
    severity: IncidentSeverity
    incident_type: IncidentType
    affected_services: List[str]
    detected_at: str
    detected_by: str
    status: IncidentStatus = IncidentStatus.DETECTED

    # Timeline tracking
    timeline: IncidentTimeline = field(default_factory=IncidentTimeline)

    # Resolution tracking
    acknowledged_at: Optional[str] = None
    resolved_at: Optional[str] = None
    closed_at: Optional[str] = None

    # Responders
    incident_commander: Optional[str] = None
    responders: List[str] = field(default_factory=list)

    # Impact
    customer_impact: Optional[str] = None
    error_budget_impact_percent: Optional[float] = None

    # Root cause
    root_cause: Optional[str] = None
    contributing_factors: List[str] = field(default_factory=list)

    # Action items
    action_items: List[ActionItem] = field(default_factory=list)

    @property
    def time_to_acknowledge_minutes(self) -> Optional[float]:
        if not self.acknowledged_at:
            return None
        # Simplified — in production parse ISO timestamps
        return None

    @property
    def is_resolved(self) -> bool:
        return self.status in (IncidentStatus.RESOLVED, IncidentStatus.CLOSED)

    def acknowledge(self, commander: str) -> None:
        self.incident_commander = commander
        self.acknowledged_at = datetime.now(timezone.utc).isoformat()
        self.status = IncidentStatus.ACKNOWLEDGED
        self.timeline.add_event(commander, "acknowledged", f"Incident commander: {commander}")

    def escalate(self, responder: str, reason: str) -> None:
        self.responders.append(responder)
        self.timeline.add_event(responder, "escalated", reason)

    def update_status(self, status: IncidentStatus, actor: str, details: Optional[str] = None) -> None:
        self.status = status
        if status == IncidentStatus.RESOLVED:
            self.resolved_at = datetime.now(timezone.utc).isoformat()
        elif status == IncidentStatus.CLOSED:
            self.closed_at = datetime.now(timezone.utc).isoformat()
        self.timeline.add_event(actor, f"status_change:{status.value}", details)

    def add_action_item(self, action: ActionItem) -> None:
        self.action_items.append(action)


class SeverityClassifier:
    """
    Classifies incident severity based on impact signals.

    Sixfold severity matrix:
    - P1: Risk scoring down, >5% error rate, >10min outage for enterprise customers
    - P2: >1% error rate or >3x latency increase for any production service
    - P3: Single tenant affected, elevated error rate <1%, non-critical service
    - P4: Internal tooling, dev environments, cosmetic issues
    """

    P1_INDICATORS = {
        "error_rate_threshold": 0.05,      # >5% errors
        "latency_multiplier": 5.0,          # >5x baseline latency
        "outage_minutes": 10,               # >10min complete outage
    }

    P2_INDICATORS = {
        "error_rate_threshold": 0.01,       # >1% errors
        "latency_multiplier": 3.0,          # >3x baseline latency
        "outage_minutes": 30,
    }

    def classify(
        self,
        error_rate: Optional[float] = None,
        latency_multiplier: Optional[float] = None,
        is_complete_outage: bool = False,
        affected_enterprise_customers: int = 0,
        is_security_incident: bool = False,
    ) -> IncidentSeverity:
        """Classify incident severity from impact signals."""

        if is_security_incident:
            return IncidentSeverity.P1

        if is_complete_outage and affected_enterprise_customers > 0:
            return IncidentSeverity.P1

        if error_rate is not None:
            if error_rate >= self.P1_INDICATORS["error_rate_threshold"]:
                return IncidentSeverity.P1
            if error_rate >= self.P2_INDICATORS["error_rate_threshold"]:
                return IncidentSeverity.P2

        if latency_multiplier is not None:
            if latency_multiplier >= self.P1_INDICATORS["latency_multiplier"]:
                return IncidentSeverity.P1
            if latency_multiplier >= self.P2_INDICATORS["latency_multiplier"]:
                return IncidentSeverity.P2

        if affected_enterprise_customers > 0:
            return IncidentSeverity.P3

        return IncidentSeverity.P4

    def get_response_target_minutes(self, severity: IncidentSeverity) -> int:
        """SLO for time to acknowledge by severity."""
        return {
            IncidentSeverity.P1: 5,
            IncidentSeverity.P2: 30,
            IncidentSeverity.P3: 240,
            IncidentSeverity.P4: 1440,
        }[severity]


class RCAGenerator:
    """
    Generates Root Cause Analysis templates for incidents.

    Follows the blameless post-mortem framework: focus on systemic failures,
    not individual blame. Produces structured RCAs for customer communication
    and internal learning.
    """

    def generate_rca_template(self, incident: Incident) -> str:
        """Generate a structured RCA document from an incident."""
        action_items_text = ""
        for i, item in enumerate(incident.action_items, 1):
            action_items_text += (
                f"\n{i}. [{item.priority.upper()}] {item.title}\n"
                f"   Owner: {item.owner} | Due: {item.due_date}\n"
                f"   Status: {item.status}\n"
            )
            if item.description:
                action_items_text += f"   Details: {item.description}\n"

        timeline_text = ""
        for event in incident.timeline.events:
            timeline_text += f"\n- **{event['timestamp']}** — {event['actor']}: {event['action']}"
            if event.get("details"):
                timeline_text += f"\n  _{event['details']}_"

        contributing_factors_text = "\n".join(
            f"- {factor}" for factor in incident.contributing_factors
        ) or "- Under investigation"

        return f"""# Post-Incident Review — {incident.id}

## Summary

| Field | Value |
|-------|-------|
| Incident ID | {incident.id} |
| Title | {incident.title} |
| Severity | {incident.severity.value} |
| Type | {incident.incident_type.value} |
| Affected Services | {', '.join(incident.affected_services)} |
| Detected | {incident.detected_at} |
| Resolved | {incident.resolved_at or 'TBD'} |
| Incident Commander | {incident.incident_commander or 'TBD'} |

## Customer Impact

{incident.customer_impact or 'No customer impact identified.'}

Error Budget Impact: {f"{incident.error_budget_impact_percent:.2f}%" if incident.error_budget_impact_percent else "TBD"}

## Timeline
{timeline_text or "- Timeline not recorded"}

## Root Cause

{incident.root_cause or "Root cause analysis in progress."}

## Contributing Factors

{contributing_factors_text}

## What Went Well

- [To be completed in post-mortem meeting]

## What Could Be Improved

- [To be completed in post-mortem meeting]

## Action Items
{action_items_text or "- No action items defined yet"}

## Lessons Learned

- [To be documented after post-mortem meeting]

---
*This is a blameless post-mortem. Focus on systems and processes, not individuals.*
*Generated by Sixfold SRE Incident Manager*
"""

    def generate_customer_communication(
        self,
        incident: Incident,
        communication_type: str = "update",
    ) -> str:
        """Generate customer-facing incident communication."""
        if communication_type == "initial":
            return (
                f"**Service Incident — {incident.detected_at}**\n\n"
                f"We are investigating an issue affecting {', '.join(incident.affected_services)}. "
                f"Our team has been notified and is actively working on resolution. "
                f"We will provide updates every 30 minutes.\n\n"
                f"Impact: {incident.customer_impact or 'Under assessment'}\n\n"
                f"Next update: 30 minutes from now."
            )
        elif communication_type == "resolution":
            return (
                f"**Incident Resolved — {incident.resolved_at}**\n\n"
                f"The issue affecting {', '.join(incident.affected_services)} has been resolved. "
                f"Services are operating normally. "
                f"A full post-incident review will be published within 5 business days."
            )
        else:
            return (
                f"**Incident Update — {datetime.now(timezone.utc).isoformat()}**\n\n"
                f"We continue to investigate the issue. "
                f"Current status: {incident.status.value}. "
                f"Next update in 30 minutes."
            )


class DisasterRecoveryRunner:
    """
    Executes disaster recovery runbooks and validates recovery.

    Supports: database failover, EKS cluster recovery, S3 restore,
    and multi-region traffic rerouting.
    """

    def __init__(self):
        self._runbooks: Dict[str, Dict] = {}
        self._execution_log: List[Dict] = []

    def register_runbook(self, name: str, steps: List[str], rto_minutes: int, rpo_minutes: int) -> None:
        """Register a DR runbook."""
        self._runbooks[name] = {
            "name": name,
            "steps": steps,
            "rto_minutes": rto_minutes,
            "rpo_minutes": rpo_minutes,
        }

    def get_runbook(self, name: str) -> Optional[Dict]:
        return self._runbooks.get(name)

    def list_runbooks(self) -> List[str]:
        return list(self._runbooks.keys())

    def execute_runbook(self, name: str, dry_run: bool = True) -> Dict:
        """
        Execute a DR runbook.

        In production, each step calls AWS APIs or kubectl commands.
        In dry_run mode, simulates execution for testing.
        """
        runbook = self._runbooks.get(name)
        if not runbook:
            return {"success": False, "error": f"Runbook '{name}' not found"}

        execution_result = {
            "runbook": name,
            "dry_run": dry_run,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "steps_completed": [],
            "steps_failed": [],
            "success": True,
        }

        for i, step in enumerate(runbook["steps"]):
            step_result = {
                "step": i + 1,
                "description": step,
                "status": "simulated" if dry_run else "executed",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            execution_result["steps_completed"].append(step_result)

        execution_result["completed_at"] = datetime.now(timezone.utc).isoformat()
        self._execution_log.append(execution_result)
        return execution_result

    def get_execution_history(self) -> List[Dict]:
        return self._execution_log


class IncidentManager:
    """
    Central incident manager — orchestrates the full incident lifecycle.
    """

    def __init__(self):
        self._incidents: Dict[str, Incident] = {}
        self._classifier = SeverityClassifier()
        self._rca_generator = RCAGenerator()
        self._dr_runner = DisasterRecoveryRunner()
        self._incident_counter = 0

        # Register standard DR runbooks
        self._register_standard_runbooks()

    def _generate_incident_id(self) -> str:
        self._incident_counter += 1
        year_month = datetime.now(timezone.utc).strftime("%Y%m")
        return f"INC-{year_month}-{self._incident_counter:04d}"

    def _register_standard_runbooks(self) -> None:
        """Register Sixfold standard DR runbooks."""
        self._dr_runner.register_runbook(
            "rds-failover",
            steps=[
                "Verify RDS primary is unhealthy via CloudWatch alarms",
                "Initiate manual failover via AWS RDS API: aws rds failover-db-cluster",
                "Verify new primary is accepting connections",
                "Update application database endpoint env vars if needed",
                "Verify application connectivity and error rate drops to baseline",
                "Notify Customer Success of any data lag",
            ],
            rto_minutes=15,
            rpo_minutes=5,
        )
        self._dr_runner.register_runbook(
            "eks-node-group-recovery",
            steps=[
                "Identify failed node group via kubectl get nodes",
                "Cordon all unhealthy nodes: kubectl cordon <node>",
                "Drain pods: kubectl drain <node> --ignore-daemonsets",
                "Terminate unhealthy EC2 instances via Auto Scaling Group",
                "ASG replaces instances automatically — monitor node Ready status",
                "Verify all deployments reach desired replica count",
                "Remove cordon from recovered nodes if needed",
            ],
            rto_minutes=20,
            rpo_minutes=0,
        )
        self._dr_runner.register_runbook(
            "llm-inference-recovery",
            steps=[
                "Check LLM inference pod logs: kubectl logs -l app=llm-inference",
                "Check GPU node status and utilization",
                "Scale inference deployment to 0 and back: kubectl rollout restart",
                "Verify new pods reach Running state",
                "Send test inference request and validate response",
                "Check SLO dashboard for error rate recovery",
                "If GPU OOM: increase memory limits or reduce max_tokens config",
            ],
            rto_minutes=10,
            rpo_minutes=0,
        )

    def create_incident(
        self,
        title: str,
        incident_type: IncidentType,
        affected_services: List[str],
        detected_by: str,
        error_rate: Optional[float] = None,
        latency_multiplier: Optional[float] = None,
        is_complete_outage: bool = False,
        affected_enterprise_customers: int = 0,
        customer_impact: Optional[str] = None,
    ) -> Incident:
        """Create and register a new incident."""
        severity = self._classifier.classify(
            error_rate=error_rate,
            latency_multiplier=latency_multiplier,
            is_complete_outage=is_complete_outage,
            affected_enterprise_customers=affected_enterprise_customers,
        )

        incident = Incident(
            id=self._generate_incident_id(),
            title=title,
            severity=severity,
            incident_type=incident_type,
            affected_services=affected_services,
            detected_at=datetime.now(timezone.utc).isoformat(),
            detected_by=detected_by,
            customer_impact=customer_impact,
        )
        incident.timeline.add_event(detected_by, "detected", f"Severity auto-classified as {severity.value}")
        self._incidents[incident.id] = incident
        return incident

    def get_incident(self, incident_id: str) -> Optional[Incident]:
        return self._incidents.get(incident_id)

    def list_open_incidents(self) -> List[Incident]:
        return [i for i in self._incidents.values() if not i.is_resolved]

    def list_all_incidents(self) -> List[Incident]:
        return list(self._incidents.values())

    def generate_rca(self, incident_id: str) -> Optional[str]:
        """Generate RCA document for an incident."""
        incident = self._incidents.get(incident_id)
        if not incident:
            return None
        return self._rca_generator.generate_rca_template(incident)

    def generate_customer_communication(
        self, incident_id: str, communication_type: str = "update"
    ) -> Optional[str]:
        incident = self._incidents.get(incident_id)
        if not incident:
            return None
        return self._rca_generator.generate_customer_communication(
            incident, communication_type
        )

    def execute_dr_runbook(self, runbook_name: str, dry_run: bool = True) -> Dict:
        return self._dr_runner.execute_runbook(runbook_name, dry_run)

    def get_mttr_minutes(self) -> Optional[float]:
        """Calculate mean time to resolve across all resolved incidents."""
        resolved = [i for i in self._incidents.values() if i.is_resolved and i.resolved_at]
        if not resolved:
            return None
        # Simplified — return count-based mock for POC testing
        return 45.0  # 45 min MTTR placeholder

    def get_incident_stats(self) -> Dict:
        """Get incident statistics for reporting."""
        all_incidents = list(self._incidents.values())
        by_severity: Dict[str, int] = {}
        by_type: Dict[str, int] = {}

        for inc in all_incidents:
            sev = inc.severity.value
            by_severity[sev] = by_severity.get(sev, 0) + 1
            inc_type = inc.incident_type.value
            by_type[inc_type] = by_type.get(inc_type, 0) + 1

        return {
            "total": len(all_incidents),
            "open": len([i for i in all_incidents if not i.is_resolved]),
            "resolved": len([i for i in all_incidents if i.is_resolved]),
            "by_severity": by_severity,
            "by_type": by_type,
            "dr_runbooks_available": len(self._dr_runner.list_runbooks()),
        }
