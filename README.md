# Sixfold SRE Observability POC

**A production-grade Site Reliability & Observability platform for an AI-powered insurance underwriting system.**

This POC demonstrates the full SRE/observability stack required for a platform like Sixfold: Prometheus metrics, Grafana dashboards, OpenTelemetry distributed tracing, SLO/error budget management, incident automation, Kubernetes reliability patterns, and chaos engineering — all tailored to AI/LLM service observability.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                    AI Insurance Platform                         │
│                                                                 │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────────┐  │
│  │  Submission  │───▶│  Risk Score  │───▶│  Underwriting    │  │
│  │  Ingestion   │    │  LLM Service │    │  Decision API    │  │
│  └──────┬───────┘    └──────┬───────┘    └────────┬─────────┘  │
│         │                  │                     │             │
│         └──────────────────┴─────────────────────┘             │
│                            │                                    │
│                   OTEL Collector                                 │
│                  (traces/metrics)                                │
│                            │                                    │
│         ┌──────────────────┼─────────────────────┐             │
│         ▼                  ▼                     ▼             │
│    Prometheus          Grafana Tempo          Loki Logs         │
│    (metrics)          (traces)               (logs)            │
│         │                  │                     │             │
│         └──────────────────┴─────────────────────┘             │
│                            │                                    │
│                   Grafana Dashboards                             │
│               (SLO / Error Budget / Tracing)                     │
│                            │                                    │
│                   Alertmanager + PagerDuty                       │
└─────────────────────────────────────────────────────────────────┘
```

---

## Repository Structure

```
sixfold-sre-observability-poc/
├── README.md
├── observability/
│   ├── slo_manager.py          # SLO/error budget calculator & tracker
│   ├── alert_rules.py          # Prometheus alert rule generator
│   └── otel_instrumentation.py # OpenTelemetry instrumentation for AI services
├── incident/
│   └── incident_manager.py     # Incident management & RCA automation
├── k8s/
│   ├── prometheus-values.yaml  # Prometheus Helm values
│   ├── grafana-values.yaml     # Grafana Helm values
│   └── otel-collector.yaml     # OpenTelemetry Collector config
├── terraform/
│   └── main.tf                 # AWS infra: EKS, CloudWatch, PagerDuty
└── tests/
    ├── test_slo_manager.py     # 30+ tests for SLO/error budget logic
    ├── test_alert_rules.py     # 25+ tests for alert rule generation
    ├── test_otel_instrumentation.py  # 20+ tests for OTEL instrumentation
    └── test_incident_manager.py      # 15+ tests for incident management
```

---

## Components

### 1. SLO Manager (`observability/slo_manager.py`)
- Define SLOs for latency (P99), error rate, and availability
- Calculate error budgets in real-time from Prometheus data
- Multi-window/multi-burn-rate alerting (Google SRE Workbook method)
- Error budget reports with burn rate projections

### 2. Alert Rule Generator (`observability/alert_rules.py`)
- Generate Prometheus alerting rules from SLO definitions
- Multi-burn-rate rules (1h/6h/24h/72h windows)
- Severity-tiered routing (P1/P2/P3) for PagerDuty
- AI/LLM-specific alert patterns (token latency, model timeout, inference errors)

### 3. OpenTelemetry Instrumentation (`observability/otel_instrumentation.py`)
- OTEL SDK setup for Python AI/LLM services
- Automatic span creation for LLM inference calls
- Custom attributes: model name, token count, prompt hash, insurance line
- Async pipeline tracing with context propagation

### 4. Incident Manager (`incident/incident_manager.py`)
- Incident lifecycle: detection → triage → resolution → RCA
- Severity classification (P1/P2/P3/P4) with auto-escalation
- RCA template generator with timeline reconstruction
- Post-mortem report builder with action item tracking
- DR runbook executor with automated health checks

---

## SLO Targets

| Service | SLO Type | Target | Window |
|---------|----------|--------|--------|
| Risk Score API | P99 Latency | < 3000ms | 30d |
| Risk Score API | Error Rate | < 0.1% | 30d |
| Submission Ingestion | Availability | 99.9% | 30d |
| Underwriting API | P99 Latency | < 1000ms | 30d |
| LLM Inference | P95 Latency | < 5000ms | 30d |

---

## Kubernetes Reliability Patterns

```yaml
# Pod Disruption Budget — ensures high availability
apiVersion: policy/v1
kind: PodDisruptionBudget
spec:
  minAvailable: 2   # Always keep 2 pods running during disruptions

# Horizontal Pod Autoscaler — scale on CPU + custom metrics
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
spec:
  metrics:
  - type: Resource
    resource: { name: cpu, target: { averageUtilization: 70 } }
  - type: External  # Scale on Prometheus queue depth
    external:
      metric: { name: submission_queue_depth }
      target: { averageValue: "100" }
```

---

## Terraform Infrastructure

The `terraform/main.tf` provisions:
- AWS EKS cluster with managed node groups
- CloudWatch log groups and metric alarms
- PagerDuty service integration via AWS SNS
- S3 bucket for Thanos long-term metrics storage

---

## Running the Tests

```bash
pip install pytest prometheus-client opentelemetry-sdk opentelemetry-api
pytest tests/ -v --tb=short
```

Expected: 90+ tests passing across all modules.

---

## Key SRE Practices Demonstrated

1. **SLO-driven alerting**: Alert on burn rate, not raw metrics
2. **Error budget management**: Teams own their error budget spend
3. **Distributed tracing**: End-to-end trace visibility for AI pipelines
4. **Incident automation**: Structured response reduces cognitive load
5. **Chaos engineering**: Regular DR exercises validate recovery procedures
6. **AI/LLM observability**: Non-deterministic systems need special handling

---

## Author

Ram Gopal Reddy Basireddy
GitHub: [ramagopalb](https://github.com/ramagopalb)
LinkedIn: [ram-ba-29b110261](https://www.linkedin.com/in/ram-ba-29b110261/)
