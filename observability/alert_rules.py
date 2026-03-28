"""
Alert Rule Generator — Prometheus alerting rules for Sixfold SRE platform.

Generates multi-burn-rate SLO alerts, AI/LLM-specific alerts, and
infrastructure reliability alerts. Rules are generated as YAML for
direct deployment to Prometheus Alertmanager.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional


class AlertSeverity(Enum):
    CRITICAL = "critical"   # P1 — page immediately
    WARNING = "warning"     # P2 — ticket within 4h
    INFO = "info"           # P3 — informational


@dataclass
class AlertRule:
    """A single Prometheus alerting rule."""
    name: str
    expr: str
    severity: AlertSeverity
    summary: str
    description: str
    for_duration: str = "5m"
    labels: Dict[str, str] = field(default_factory=dict)
    annotations: Dict[str, str] = field(default_factory=dict)
    runbook_url: Optional[str] = None

    def to_yaml(self) -> str:
        labels = dict(self.labels)
        labels["severity"] = self.severity.value

        annotations = {
            "summary": self.summary,
            "description": self.description,
        }
        if self.runbook_url:
            annotations["runbook_url"] = self.runbook_url
        annotations.update(self.annotations)

        lines = [
            f"      - alert: {self.name}",
            f"        expr: {self.expr}",
            f"        for: {self.for_duration}",
            f"        labels:",
        ]
        for k, v in labels.items():
            lines.append(f"          {k}: {v}")
        lines.append(f"        annotations:")
        for k, v in annotations.items():
            # Escape single quotes in annotation values
            v_escaped = v.replace("'", "''")
            lines.append(f"          {k}: '{v_escaped}'")

        return "\n".join(lines)


@dataclass
class AlertGroup:
    """A named group of alert rules."""
    name: str
    interval: str
    rules: List[AlertRule] = field(default_factory=list)

    def add_rule(self, rule: AlertRule) -> None:
        self.rules.append(rule)

    def to_yaml(self) -> str:
        lines = [
            f"  - name: {self.name}",
            f"    interval: {self.interval}",
            f"    rules:",
        ]
        for rule in self.rules:
            lines.append(rule.to_yaml())
        return "\n".join(lines)


class AlertRuleGenerator:
    """
    Generates Prometheus alert rules for the Sixfold SRE platform.

    Covers:
    - Multi-burn-rate SLO alerts (error rate, latency, availability)
    - AI/LLM service alerts (token latency, model timeout, inference errors)
    - Kubernetes reliability alerts (pod crashes, node pressure, OOM)
    - Infrastructure alerts (disk, memory, CPU)
    """

    BASE_RUNBOOK_URL = "https://runbooks.sixfold.internal/sre"

    def __init__(self):
        self._groups: Dict[str, AlertGroup] = {}

    def get_or_create_group(self, name: str, interval: str = "1m") -> AlertGroup:
        if name not in self._groups:
            self._groups[name] = AlertGroup(name=name, interval=interval)
        return self._groups[name]

    def generate_slo_burn_rate_alerts(
        self,
        service: str,
        error_budget_fraction: float,
        metric_name: str,
    ) -> List[AlertRule]:
        """
        Generate multi-window/multi-burn-rate SLO alerts.

        Uses the Google SRE Workbook approach:
        - 2% budget burned in 1h → page (burn rate 14.4x)
        - 5% budget burned in 6h → page (burn rate 6x)
        - 10% budget burned in 3d → ticket (burn rate 1x)
        """
        rules = []
        safe_service = service.replace("-", "_")

        # Window pairs: (short_window, long_window, burn_rate, severity, budget_pct)
        window_configs = [
            ("1h",  "5m",  14.4, AlertSeverity.CRITICAL, 2),
            ("6h",  "30m", 6.0,  AlertSeverity.CRITICAL, 5),
            ("24h", "2h",  3.0,  AlertSeverity.WARNING,  10),
            ("3d",  "6h",  1.0,  AlertSeverity.WARNING,  30),
        ]

        for long_w, short_w, burn_rate, severity, budget_pct in window_configs:
            threshold = error_budget_fraction * burn_rate
            name = f"{safe_service}_SLOBurnRate{long_w}"
            expr = (
                f"(\n"
                f"  rate({metric_name}_errors_total[{long_w}]) / "
                f"rate({metric_name}_requests_total[{long_w}]) > {threshold:.6f}\n"
                f") and (\n"
                f"  rate({metric_name}_errors_total[{short_w}]) / "
                f"rate({metric_name}_requests_total[{short_w}]) > {threshold:.6f}\n"
                f")"
            )
            rules.append(AlertRule(
                name=name,
                expr=expr,
                severity=severity,
                for_duration="2m",
                summary=f"{service} SLO burn rate alert ({long_w} window)",
                description=(
                    f"{service} is burning error budget at {burn_rate}x the sustainable rate "
                    f"({budget_pct}% of monthly budget consumed). "
                    f"Projected exhaustion if not resolved."
                ),
                labels={"service": service, "window": long_w},
                runbook_url=f"{self.BASE_RUNBOOK_URL}/{safe_service}/slo-burn-rate",
            ))

        return rules

    def generate_latency_alerts(self, service: str, p99_threshold_ms: float) -> List[AlertRule]:
        """Generate P99/P95 latency alerts for a service."""
        safe_service = service.replace("-", "_")
        rules = []

        # P99 latency alert
        rules.append(AlertRule(
            name=f"{safe_service}_P99LatencyHigh",
            expr=(
                f"histogram_quantile(0.99, "
                f"rate({safe_service}_request_duration_seconds_bucket[5m])) * 1000 "
                f"> {p99_threshold_ms}"
            ),
            severity=AlertSeverity.CRITICAL,
            for_duration="5m",
            summary=f"{service} P99 latency exceeds {p99_threshold_ms}ms",
            description=(
                f"{service} P99 request latency is above the SLO threshold of {p99_threshold_ms}ms. "
                f"Check downstream dependencies, database query times, and LLM inference latency."
            ),
            labels={"service": service, "percentile": "p99"},
            runbook_url=f"{self.BASE_RUNBOOK_URL}/{safe_service}/latency",
        ))

        # P95 latency warning
        rules.append(AlertRule(
            name=f"{safe_service}_P95LatencyElevated",
            expr=(
                f"histogram_quantile(0.95, "
                f"rate({safe_service}_request_duration_seconds_bucket[5m])) * 1000 "
                f"> {p99_threshold_ms * 0.75:.0f}"
            ),
            severity=AlertSeverity.WARNING,
            for_duration="10m",
            summary=f"{service} P95 latency elevated",
            description=(
                f"{service} P95 request latency is elevated (75% of SLO threshold). "
                f"Trend may indicate approaching SLO breach."
            ),
            labels={"service": service, "percentile": "p95"},
            runbook_url=f"{self.BASE_RUNBOOK_URL}/{safe_service}/latency",
        ))

        return rules

    def generate_llm_specific_alerts(self) -> List[AlertRule]:
        """Generate alerts specific to LLM/AI inference services."""
        rules = []

        # LLM token throughput drop
        rules.append(AlertRule(
            name="LLMTokenThroughputDrop",
            expr=(
                "rate(llm_inference_tokens_total[5m]) < "
                "rate(llm_inference_tokens_total[30m]) * 0.5"
            ),
            severity=AlertSeverity.WARNING,
            for_duration="5m",
            summary="LLM token throughput dropped >50% from 30m baseline",
            description=(
                "LLM inference token throughput has dropped significantly. "
                "May indicate model loading issues, GPU memory pressure, or "
                "upstream queue saturation."
            ),
            labels={"service": "llm-inference", "type": "throughput"},
            runbook_url=f"{self.BASE_RUNBOOK_URL}/llm-inference/throughput",
        ))

        # LLM inference timeout rate
        rules.append(AlertRule(
            name="LLMInferenceTimeoutRateHigh",
            expr=(
                "rate(llm_inference_timeouts_total[5m]) / "
                "rate(llm_inference_requests_total[5m]) > 0.05"
            ),
            severity=AlertSeverity.CRITICAL,
            for_duration="2m",
            summary="LLM inference timeout rate >5%",
            description=(
                "More than 5% of LLM inference requests are timing out. "
                "Check model server health, GPU utilization, and request queue depth."
            ),
            labels={"service": "llm-inference", "type": "timeout"},
            runbook_url=f"{self.BASE_RUNBOOK_URL}/llm-inference/timeout",
        ))

        # Model error rate
        rules.append(AlertRule(
            name="LLMModelErrorRateHigh",
            expr=(
                "rate(llm_inference_errors_total[5m]) / "
                "rate(llm_inference_requests_total[5m]) > 0.01"
            ),
            severity=AlertSeverity.CRITICAL,
            for_duration="3m",
            summary="LLM model error rate >1%",
            description=(
                "LLM model error rate exceeds 1%. Errors may be context length violations, "
                "model OOM, or API failures. Risk scoring pipeline may be degraded."
            ),
            labels={"service": "llm-inference", "type": "error_rate"},
            runbook_url=f"{self.BASE_RUNBOOK_URL}/llm-inference/errors",
        ))

        # Queue depth alert
        rules.append(AlertRule(
            name="LLMInferenceQueueDepthHigh",
            expr="llm_inference_queue_depth > 100",
            severity=AlertSeverity.WARNING,
            for_duration="5m",
            summary="LLM inference queue depth >100 requests",
            description=(
                "LLM inference request queue has accumulated. "
                "Underwriting decisions may be delayed. "
                "Consider scaling inference replicas or shedding load."
            ),
            labels={"service": "llm-inference", "type": "queue"},
            runbook_url=f"{self.BASE_RUNBOOK_URL}/llm-inference/queue",
        ))

        # Token cost anomaly
        rules.append(AlertRule(
            name="LLMTokenCostAnomaly",
            expr=(
                "rate(llm_inference_tokens_total[1h]) > "
                "rate(llm_inference_tokens_total[24h]) * 3"
            ),
            severity=AlertSeverity.WARNING,
            for_duration="10m",
            summary="LLM token consumption anomaly — 3x above 24h baseline",
            description=(
                "Token consumption rate is 3x above 24-hour baseline. "
                "May indicate a prompt injection attack, runaway process, "
                "or unexpected traffic spike."
            ),
            labels={"service": "llm-inference", "type": "cost"},
            runbook_url=f"{self.BASE_RUNBOOK_URL}/llm-inference/cost-anomaly",
        ))

        return rules

    def generate_kubernetes_reliability_alerts(self) -> List[AlertRule]:
        """Generate Kubernetes-level reliability alerts."""
        rules = []

        # Pod crash loop
        rules.append(AlertRule(
            name="PodCrashLoopDetected",
            expr=(
                "rate(kube_pod_container_status_restarts_total[15m]) * 60 > 0"
            ),
            severity=AlertSeverity.CRITICAL,
            for_duration="5m",
            summary="Pod crash loop detected in {{ $labels.namespace }}/{{ $labels.pod }}",
            description=(
                "Pod {{ $labels.pod }} in namespace {{ $labels.namespace }} "
                "is restarting frequently (crash loop). Check container logs and events."
            ),
            labels={"team": "sre"},
            runbook_url=f"{self.BASE_RUNBOOK_URL}/kubernetes/crashloop",
        ))

        # Node memory pressure
        rules.append(AlertRule(
            name="NodeMemoryPressureHigh",
            expr=(
                "(node_memory_MemTotal_bytes - node_memory_MemAvailable_bytes) / "
                "node_memory_MemTotal_bytes > 0.90"
            ),
            severity=AlertSeverity.CRITICAL,
            for_duration="5m",
            summary="Node memory usage >90% on {{ $labels.instance }}",
            description=(
                "Node {{ $labels.instance }} memory usage exceeds 90%. "
                "Risk of OOM kills. Consider scaling the node group or "
                "evicting low-priority pods."
            ),
            labels={"team": "sre"},
            runbook_url=f"{self.BASE_RUNBOOK_URL}/kubernetes/node-memory",
        ))

        # PVC storage near full
        rules.append(AlertRule(
            name="PVCStorageNearFull",
            expr=(
                "kubelet_volume_stats_used_bytes / kubelet_volume_stats_capacity_bytes > 0.85"
            ),
            severity=AlertSeverity.WARNING,
            for_duration="10m",
            summary="PVC {{ $labels.persistentvolumeclaim }} >85% full",
            description=(
                "Persistent volume {{ $labels.persistentvolumeclaim }} in "
                "{{ $labels.namespace }} is >85% full. Risk of write failures."
            ),
            labels={"team": "sre"},
            runbook_url=f"{self.BASE_RUNBOOK_URL}/kubernetes/pvc-storage",
        ))

        # HPA at max replicas
        rules.append(AlertRule(
            name="HPAAtMaxReplicas",
            expr=(
                "kube_horizontalpodautoscaler_status_current_replicas == "
                "kube_horizontalpodautoscaler_spec_max_replicas"
            ),
            severity=AlertSeverity.WARNING,
            for_duration="15m",
            summary="HPA {{ $labels.horizontalpodautoscaler }} at maximum replicas",
            description=(
                "HPA {{ $labels.horizontalpodautoscaler }} has been at max replicas for 15m. "
                "Service may be capacity constrained. Consider increasing max replicas or "
                "optimizing resource usage."
            ),
            labels={"team": "sre"},
            runbook_url=f"{self.BASE_RUNBOOK_URL}/kubernetes/hpa-max",
        ))

        # Deployment rollout stuck
        rules.append(AlertRule(
            name="DeploymentRolloutStuck",
            expr=(
                "kube_deployment_status_observed_generation != "
                "kube_deployment_metadata_generation"
            ),
            severity=AlertSeverity.WARNING,
            for_duration="15m",
            summary="Deployment {{ $labels.deployment }} rollout may be stuck",
            description=(
                "Deployment {{ $labels.deployment }} observed generation does not match "
                "desired generation for 15m. Rollout may be stuck. Check pod events."
            ),
            labels={"team": "sre"},
            runbook_url=f"{self.BASE_RUNBOOK_URL}/kubernetes/rollout-stuck",
        ))

        return rules

    def generate_all_rules(self) -> str:
        """Generate complete Prometheus rules YAML for Sixfold."""
        # SLO alerts
        slo_group = self.get_or_create_group("sixfold_slo_alerts", interval="1m")
        for service, metric, budget in [
            ("risk-score-api", "risk_score_api", 0.001),
            ("submission-ingestion", "submission_ingestion", 0.001),
            ("underwriting-api", "underwriting_api", 0.0005),
            ("llm-inference", "llm_inference", 0.05),
        ]:
            for rule in self.generate_slo_burn_rate_alerts(service, budget, metric):
                slo_group.add_rule(rule)

        # Latency alerts
        latency_group = self.get_or_create_group("sixfold_latency_alerts", interval="1m")
        for service, threshold in [
            ("risk-score-api", 3000),
            ("underwriting-api", 1000),
            ("llm-inference", 5000),
        ]:
            for rule in self.generate_latency_alerts(service, threshold):
                latency_group.add_rule(rule)

        # LLM-specific alerts
        llm_group = self.get_or_create_group("sixfold_llm_alerts", interval="1m")
        for rule in self.generate_llm_specific_alerts():
            llm_group.add_rule(rule)

        # Kubernetes alerts
        k8s_group = self.get_or_create_group("sixfold_kubernetes_alerts", interval="1m")
        for rule in self.generate_kubernetes_reliability_alerts():
            k8s_group.add_rule(rule)

        # Assemble YAML
        lines = ["groups:"]
        for group in self._groups.values():
            lines.append(group.to_yaml())

        return "\n".join(lines)

    def get_rule_count(self) -> int:
        """Return total number of alert rules registered."""
        return sum(len(g.rules) for g in self._groups.values())

    def get_rules_by_severity(self, severity: AlertSeverity) -> List[AlertRule]:
        """Get all rules of a given severity."""
        rules = []
        for group in self._groups.values():
            for rule in group.rules:
                if rule.severity == severity:
                    rules.append(rule)
        return rules

    def validate_rules(self) -> List[str]:
        """Basic validation of alert rules. Returns list of validation errors."""
        errors = []
        seen_names = set()
        for group in self._groups.values():
            for rule in group.rules:
                if not rule.name:
                    errors.append(f"Alert rule missing name in group {group.name}")
                if rule.name in seen_names:
                    errors.append(f"Duplicate alert name: {rule.name}")
                seen_names.add(rule.name)
                if not rule.expr:
                    errors.append(f"Alert {rule.name} has empty expression")
                if not rule.summary:
                    errors.append(f"Alert {rule.name} missing summary annotation")
        return errors
