# Alerting

LANA does not send alerts itself — no email, no Slack webhook, no
third-party incident service, on purpose. It already exposes structured
JSON logs and Prometheus metrics at `GET /metrics`
(see `app/observability.py`); wiring those into real notifications is a
solved problem for standard tooling, and reimplementing it inside LANA would
be exactly the "buys convenience, not capability" dependency the rest of the
observability layer already avoids.

This page is example [Prometheus](https://prometheus.io/) +
[Alertmanager](https://prometheus.io/docs/alerting/latest/alertmanager/)
configuration for the conditions LANA's own metrics actually support.
Alertmanager is what turns a firing rule into an email, a Slack message or a
PagerDuty page — point it at whatever your team already uses.

## Scrape LANA

```yaml
# prometheus.yml
scrape_configs:
  - job_name: lana
    static_configs:
      - targets: ["localhost:8000"]   # or wherever backend is reachable
    metrics_path: /metrics
    # If LANA_METRICS_TOKEN is set:
    # authorization:
    #   credentials: <the token>
```

## Alert rules

```yaml
# lana-alerts.yml
groups:
  - name: lana
    rules:
      - alert: LANACrashing
        expr: increase(lana_unhandled_exceptions_total[5m]) > 0
        for: 0m
        labels:
          severity: critical
        annotations:
          summary: "LANA route handler crashed ({{ $labels.path }})"
          description: >
            lana_unhandled_exceptions_total increased — a route raised
            something it did not expect to, not a deliberate error response.
            Check the logs for the request id and full traceback.

      - alert: LANAElevated5xxRate
        expr: |
          sum(rate(lana_http_requests_total{status=~"5.."}[5m]))
          / sum(rate(lana_http_requests_total[5m])) > 0.05
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "More than 5% of LANA requests are failing"
          description: >
            Includes both crashes and deliberate 5xx responses (e.g. a
            downstream LLM being unreachable) — cross-reference with
            LANACrashing to tell which.

      - alert: LANARateLimitSpike
        expr: increase(lana_rate_limited_total[5m]) > 50
        for: 0m
        labels:
          severity: warning
        annotations:
          summary: "LANA is rejecting an unusual number of requests"
          description: >
            Could be a runaway client script, or could be someone probing
            the instance. lana_rate_limited_total is labelled by scope
            (http/llm/pwreset) — check which bucket is firing.

      - alert: LANASessionMemoryHigh
        # lana_sessions_bytes has no companion "budget" metric — the budget
        # is host-derived and reported at GET /health (session_budget_mb),
        # not exported to Prometheus. Replace 3600000000 below with 90% of
        # your own deployment's session_budget_mb (in bytes) from /health;
        # it is a fixed number per machine, not something PromQL can look up.
        expr: lana_sessions_bytes > 3600000000
        labels:
          severity: warning
        annotations:
          summary: "LANA is close to its session memory budget"
          description: >
            Uploads may start being refused. Check GET /health for the
            current budget and resident total.
```

## What this deliberately doesn't cover

- **LLM answer quality** — `lana_validation_answers_total` and
  `lana_validation_claims_total` are measurement, not a service-health
  signal; a spike in "unsupported" claims usually means an unusual question
  or a weak model, not an outage.
- **Notification delivery itself** — Alertmanager's own routing/receivers
  configuration (email, Slack, PagerDuty, etc.) is standard and documented
  on its own; nothing LANA-specific about it.
