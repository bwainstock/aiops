---
status: proposed
---

# Use bounded adaptive investigation before freezing evidence

The current MVP collector uses fixed Prometheus, Loki, Git, Docker, alert, and deployment queries for the controlled `/api/items` error scenario. We propose replacing that scenario-specific collector with a hybrid flow: deterministic code first records **Seed Evidence**, then Copilot produces typed **Investigation Plans** that are executed only through bounded read-only adapters; every result is normalized into a digested **Evidence Item** in the mutable **Investigation Journal**, and the resulting set is frozen as an **Evidence Bundle** before hypothesis generation and independent review. This lets new endpoints and alert types be investigated without adding one code path per symptom while preserving deterministic control over available tools, query windows, result sizes, redaction, budgets, evidence integrity, and stop conditions.

## Considered options

- **Keep evidence collection fully predefined**: simplest and most reproducible, but every new symptom requires code or configuration changes and unknown failure modes receive poor coverage.
- **Let the LLM investigate without an orchestrated evidence model**: flexible, but tool output, conclusions, and conversation state become difficult to validate, reproduce, audit, or safely bound.
- **Use the proposed hybrid**: the LLM chooses what evidence to request, while deterministic modules execute, normalize, digest, audit, budget, and freeze that evidence.

## Consequences

- The existing `EvidenceCollector` remains the MVP implementation until this ADR is accepted and implemented.
- Investigation must become iterative and budgeted, with explicit maximum rounds, query windows, result counts, and AI-credit limits.
- Tool results remain untrusted input and must pass existing redaction and size controls before becoming evidence.
- Conclusions and independent reviews may cite only IDs from the final frozen bundle, never mutable conversation history or uncaptured tool output.
- Alert rules should eventually provide machine-readable symptom metadata and optional seed queries, but they must not dictate the entire investigation.
