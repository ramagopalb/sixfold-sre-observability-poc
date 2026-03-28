"""
SLO Manager — SLO/Error Budget Calculator for AI Insurance Platform.

Implements the Google SRE Workbook multi-window/multi-burn-rate alerting method.
Tracks error budgets for latency, error rate, and availability SLOs across
Sixfold's AI underwriting services.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple


class SLOType(Enum):
    LATENCY = "latency"
    ERROR_RATE = "error_rate"
    AVAILABILITY = "availability"


class AlertSeverity(Enum):
    PAGE = "page"        # P1 — immediate action required
    TICKET = "ticket"    # P2 — action required within hours
    WARNING = "warning"  # P3 — monitor closely


@dataclass
class SLODefinition:
    """Defines an SLO for a service."""
    service: str
    slo_type: SLOType
    target: float          # e.g. 0.999 for 99.9% availability, 0.001 for 0.1% error rate
    window_days: int = 30
    latency_threshold_ms: Optional[float] = None  # for LATENCY SLOs
    percentile: Optional[float] = None             # e.g. 0.99 for P99

    @property
    def error_budget_fraction(self) -> float:
        """Fraction of requests allowed to fail/be slow."""
        if self.slo_type == SLOType.AVAILABILITY:
            return 1.0 - self.target
        elif self.slo_type == SLOType.ERROR_RATE:
            return self.target  # target IS the allowed error rate
        elif self.slo_type == SLOType.LATENCY:
            return 1.0 - self.target  # fraction allowed to exceed threshold
        return 0.0

    @property
    def window_seconds(self) -> int:
        return self.window_days * 24 * 3600


@dataclass
class SLOWindow:
    """A time window for burn rate calculation."""
    name: str
    duration_seconds: int
    severity: AlertSeverity
    burn_rate_threshold: float


# Standard multi-window burn rate configs (Google SRE Workbook)
STANDARD_ALERT_WINDOWS: List[SLOWindow] = [
    SLOWindow("1h",  3600,      AlertSeverity.PAGE,    14.4),
    SLOWindow("6h",  21600,     AlertSeverity.PAGE,    6.0),
    SLOWindow("24h", 86400,     AlertSeverity.TICKET,  3.0),
    SLOWindow("72h", 259200,    AlertSeverity.TICKET,  1.0),
]


@dataclass
class ErrorBudgetStatus:
    """Current error budget state for an SLO."""
    slo: SLODefinition
    total_budget_fraction: float
    consumed_fraction: float
    remaining_fraction: float
    burn_rate: float        # current burn rate relative to budget
    projected_exhaustion_hours: Optional[float]
    alert_windows: List[Dict] = field(default_factory=list)

    @property
    def budget_percent_remaining(self) -> float:
        if self.total_budget_fraction == 0:
            return 100.0
        return max(0.0, (self.remaining_fraction / self.total_budget_fraction) * 100.0)

    @property
    def is_budget_exhausted(self) -> bool:
        return self.remaining_fraction <= 0

    @property
    def status_label(self) -> str:
        pct = self.budget_percent_remaining
        if pct >= 75:
            return "HEALTHY"
        elif pct >= 25:
            return "WARNING"
        elif pct > 0:
            return "CRITICAL"
        else:
            return "EXHAUSTED"


class SLOManager:
    """
    Manages SLO definitions and calculates error budgets.

    In production, this integrates with Prometheus via the HTTP API.
    For the POC, it accepts pre-computed metrics for testing.
    """

    def __init__(self):
        self._slos: Dict[str, SLODefinition] = {}
        self._metrics_cache: Dict[str, Dict] = {}

    def register_slo(self, slo: SLODefinition) -> None:
        """Register an SLO definition."""
        key = f"{slo.service}:{slo.slo_type.value}"
        self._slos[key] = slo

    def get_slo(self, service: str, slo_type: SLOType) -> Optional[SLODefinition]:
        key = f"{service}:{slo_type.value}"
        return self._slos.get(key)

    def list_slos(self) -> List[SLODefinition]:
        return list(self._slos.values())

    def ingest_metrics(self, service: str, metrics: Dict) -> None:
        """
        Ingest pre-computed metrics for an SLO calculation.

        metrics dict keys:
          - total_requests: int
          - failed_requests: int
          - slow_requests: int (for latency SLO)
          - window_seconds: int
        """
        self._metrics_cache[service] = metrics

    def calculate_error_budget(
        self,
        service: str,
        slo_type: SLOType,
        total_requests: int,
        bad_requests: int,
        window_seconds: int,
    ) -> ErrorBudgetStatus:
        """
        Calculate current error budget status.

        Args:
            service: Service name
            slo_type: Type of SLO
            total_requests: Total request count in window
            bad_requests: Count of failed/slow requests in window
            window_seconds: Window duration in seconds
        """
        slo = self.get_slo(service, slo_type)
        if slo is None:
            raise ValueError(f"No SLO registered for {service}:{slo_type.value}")

        total_budget_fraction = slo.error_budget_fraction

        # Actual error fraction
        if total_requests == 0:
            actual_error_fraction = 0.0
        else:
            actual_error_fraction = bad_requests / total_requests

        consumed_fraction = actual_error_fraction
        remaining_fraction = total_budget_fraction - consumed_fraction

        # Burn rate: how fast budget is being consumed relative to allowance
        # Burn rate of 1.0 means exactly consuming budget at the SLO window rate
        if total_budget_fraction > 0:
            burn_rate = actual_error_fraction / total_budget_fraction
        else:
            burn_rate = float('inf') if actual_error_fraction > 0 else 0.0

        # Project exhaustion time
        projected_exhaustion_hours = None
        if burn_rate > 1.0 and total_budget_fraction > 0:
            # At current burn rate, hours until budget exhausted from now
            remaining_budget_in_window = remaining_fraction / total_budget_fraction
            if remaining_budget_in_window > 0:
                slo_window_hours = slo.window_seconds / 3600
                projected_exhaustion_hours = slo_window_hours / burn_rate * remaining_budget_in_window

        # Evaluate alert windows
        alert_windows = []
        for window in STANDARD_ALERT_WINDOWS:
            # Scale burn rate for this alert window
            window_fraction = window.duration_seconds / slo.window_seconds
            window_bad = bad_requests * window_fraction
            window_total = total_requests * window_fraction
            if window_total > 0:
                window_error_rate = window_bad / window_total
            else:
                window_error_rate = 0.0

            window_burn_rate = (window_error_rate / total_budget_fraction
                                if total_budget_fraction > 0 else 0.0)

            firing = window_burn_rate >= window.burn_rate_threshold
            alert_windows.append({
                "window": window.name,
                "burn_rate": round(window_burn_rate, 4),
                "threshold": window.burn_rate_threshold,
                "severity": window.severity.value,
                "firing": firing,
            })

        return ErrorBudgetStatus(
            slo=slo,
            total_budget_fraction=total_budget_fraction,
            consumed_fraction=consumed_fraction,
            remaining_fraction=remaining_fraction,
            burn_rate=round(burn_rate, 4),
            projected_exhaustion_hours=projected_exhaustion_hours,
            alert_windows=alert_windows,
        )

    def generate_prometheus_recording_rules(self, service: str, slo_type: SLOType) -> str:
        """Generate Prometheus recording rules YAML for an SLO."""
        slo = self.get_slo(service, slo_type)
        if not slo:
            raise ValueError(f"SLO not found: {service}:{slo_type.value}")

        metric_base = f"{service.replace('-', '_')}_{slo_type.value}"
        rules = [
            f"# Recording rules for {service} {slo_type.value} SLO",
            f"# Target: {slo.target} | Error budget: {slo.error_budget_fraction:.4f}",
            "",
            "groups:",
            f"  - name: slo_{metric_base}",
            "    rules:",
        ]

        for window in ["5m", "30m", "1h", "6h", "1d", "3d"]:
            rules.append(f"      - record: slo:{metric_base}:rate{window}")
            rules.append(f"        expr: rate({metric_base}_total[{window}])")

        return "\n".join(rules)

    def get_all_budget_statuses(self) -> Dict[str, ErrorBudgetStatus]:
        """Get error budget status for all registered SLOs using cached metrics."""
        statuses = {}
        for key, slo in self._slos.items():
            metrics = self._metrics_cache.get(slo.service, {})
            if metrics:
                status = self.calculate_error_budget(
                    service=slo.service,
                    slo_type=slo.slo_type,
                    total_requests=metrics.get("total_requests", 0),
                    bad_requests=metrics.get("bad_requests", 0),
                    window_seconds=metrics.get("window_seconds", slo.window_seconds),
                )
                statuses[key] = status
        return statuses


def create_sixfold_slos() -> SLOManager:
    """Create the standard Sixfold SLO definitions."""
    manager = SLOManager()

    # Risk Score API — core LLM inference service
    manager.register_slo(SLODefinition(
        service="risk-score-api",
        slo_type=SLOType.ERROR_RATE,
        target=0.001,  # 0.1% error rate allowed
        window_days=30,
    ))
    manager.register_slo(SLODefinition(
        service="risk-score-api",
        slo_type=SLOType.LATENCY,
        target=0.99,   # 99% of requests under threshold
        window_days=30,
        latency_threshold_ms=3000.0,
        percentile=0.99,
    ))

    # Submission Ingestion — intake pipeline
    manager.register_slo(SLODefinition(
        service="submission-ingestion",
        slo_type=SLOType.AVAILABILITY,
        target=0.999,  # 99.9% availability
        window_days=30,
    ))

    # Underwriting Decision API
    manager.register_slo(SLODefinition(
        service="underwriting-api",
        slo_type=SLOType.LATENCY,
        target=0.999,  # 99.9% of requests under threshold
        window_days=30,
        latency_threshold_ms=1000.0,
        percentile=0.99,
    ))
    manager.register_slo(SLODefinition(
        service="underwriting-api",
        slo_type=SLOType.ERROR_RATE,
        target=0.0005,  # 0.05% error rate allowed
        window_days=30,
    ))

    # LLM Inference Service
    manager.register_slo(SLODefinition(
        service="llm-inference",
        slo_type=SLOType.LATENCY,
        target=0.95,   # 95% of requests under threshold (looser — LLM is slow)
        window_days=30,
        latency_threshold_ms=5000.0,
        percentile=0.95,
    ))

    return manager
