# End-to-end scenarios

The deterministic suite runs both controlled symptom types from clean temporary
repositories and databases:

```bash
uv run pytest tests/test_e2e_scenarios.py
```

Each scenario exercises alert intake, durable workflow, frozen evidence,
fake-agent hypothesis and review, deterministic validation, isolated
remediation, validation approval, and a safe local merge.

Opt-in live stack smoke paths:

```bash
uv run scenarios/live_smoke.py error
uv run scenarios/live_smoke.py latency
```

The error path invokes live Copilot triage. The latency path verifies the real
alert and durable incident lifecycle; adaptive latency evidence gathering is
explicitly deferred by ADR-0001 rather than simulated as if implemented.
