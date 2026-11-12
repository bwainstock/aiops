# Experiment 0001: `/api/items` pagination regression

## Status

**Ready for a controlled triage run.** Do not run until you intentionally choose
to begin the experiment.

The current system can detect, persist, investigate, validate, and review this
incident. The first run should be treated as a triage experiment. A real
incident-to-remediation handoff is not yet automatically wired; remediation,
validation, and approval currently require an explicit operator command and a
constructed remediation request.

## Goal

Introduce a small code regression that:

- passes the current unit and contract tests;
- causes 40% of normal `/api/items` load-generator requests to return 500;
- fires the existing `HighErrorRate` alert;
- has an obvious introducing Git commit and deployment event;
- is reversible with one commit;
- gives Copilot enough metric, log, Git, deployment, and Docker evidence to
  investigate without using the explicit `X-Demo-Fault` mechanism.

The experiment succeeds when the AIOps system identifies the symptom, cites
frozen evidence, correlates the recent deployment and code change, and either
correctly identifies the pagination branch or explicitly requests the missing
evidence needed to prove it.

## The intentional bug

Create a branch:

```bash
git switch -c experiment/items-pagination-regression
```

In `apps/demo_api/src/aiops_demo_api/main.py`, add this behavior near the start
of `get_items`, after the controlled `fault` handling:

```python
if fault is None and query.offset in {2, 3}:
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="item page lookup failed",
    )
```

Why this bug:

- the existing API tests exercise offsets 0, 1, and 4, so the current suite
  should remain green;
- the deterministic load generator cycles through offsets 0–4;
- offsets 2 and 3 therefore fail 40% of item requests, exceeding the 25% alert
  threshold;
- the bug is visible in the introducing Git diff and produces bounded 500 logs.

Do not modify alert rules, load-generator fault headers, tests, or AIOps code
during the experiment.

## Safety setup

Before editing:

```bash
git status --short
git switch main
git pull --ff-only
make check
docker compose up -d --build
```

Record the known-good commit:

```bash
git rev-parse HEAD | tee /tmp/aiops-experiment-0001-good-commit
```

Confirm the normal baseline is quiet:

```bash
curl -s http://127.0.0.1:9090/api/v1/alerts |
  jq '.data.alerts | length'
```

Expected: `0`.

## Commit and deploy the regression

After adding the bug:

```bash
make check
git add apps/demo_api/src/aiops_demo_api/main.py
git commit -m "experiment: introduce item pagination regression"
```

`make check` is expected to pass. If it fails, stop: the experiment is no
longer testing an uncovered operational regression.

Record a deployment event before rebuilding:

```bash
commit=$(git rev-parse HEAD)
deployed_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)

jq -nc \
  --arg commit "$commit" \
  --arg deployed_at "$deployed_at" \
  '{
    service: "demo-api",
    environment: "local",
    commit_sha: $commit,
    version: "experiment-0001",
    deployed_at: $deployed_at,
    metadata: {
      source: "experiment-0001",
      description: "Intentional item pagination regression"
    }
  }' |
curl --fail --silent --show-error \
  -H 'Content-Type: application/json' \
  --data-binary @- \
  http://127.0.0.1:8081/api/v1/events/deployments |
jq .
```

Deploy only the workload:

```bash
docker compose up -d --build demo-api
```

## Trigger the symptom

Use ordinary load traffic, not `X-Demo-Fault`:

```bash
docker compose --profile load run --rm loadgen \
  aiops-loadgen \
  --base-url http://demo-api:8000 \
  --requests 400 \
  --interval 0.1 \
  --allow-failures
```

Expected summary:

- approximately 240 successes;
- approximately 160 failures;
- status counts containing 200 and 500.

## Observe the alert and incident

Prometheus:

```bash
curl -s http://127.0.0.1:9090/api/v1/alerts |
  jq '.data.alerts[] | select(.labels.alertname=="HighErrorRate")'
```

AIOps:

```bash
curl -s http://127.0.0.1:8081/api/v1/incidents |
  jq '.[] | select(.alertname=="HighErrorRate")'
```

Copy the newest open incident ID:

```bash
incident_id=$(
  curl -s http://127.0.0.1:8081/api/v1/incidents |
  jq -r '
    [.[] | select(.alertname=="HighErrorRate" and .status=="open")]
    | sort_by(.updated_at)
    | reverse
    | .[0].id
  '
)

echo "$incident_id"
```

## Run live triage

Run while the sustained error traffic is still active, or immediately after it
finishes:

```bash
make triage INCIDENT_ID="$incident_id"
```

Inspect the persisted result:

```bash
curl -s \
  "http://127.0.0.1:8081/api/v1/incidents/$incident_id/triage" |
jq '{
  status,
  validation_errors,
  symptom: .hypothesis.symptom_summary,
  causes: .hypothesis.suspected_causes,
  missing: .hypothesis.missing_evidence,
  review: .review
}'
```

## Expected analysis

A strong result should:

- cite the firing alert and reproduced Prometheus error ratio;
- cite Loki 500 logs for `/api/items`;
- cite the experiment deployment;
- inspect recent Git history or the introducing diff;
- identify the offset-dependent code branch as the likely cause, or explicitly
  request the code diff needed to prove it;
- avoid claiming the healthy Docker container disproves an application error;
- set `remediation_justified` only when the code-change evidence is sufficient;
- pass deterministic citation, digest, reproduction, and ordering checks.

A `more_evidence` result is acceptable if it precisely names the missing
evidence. Unsupported confidence or invented citations are failures.

## Current remediation boundary

The live workflow does not yet automatically convert a `triaged` result into a
real remediation request. Before attempting full automatic repair, add or invoke
an operator-controlled handoff that:

1. reads the persisted triage record;
2. requires status `triaged` and an approved hypothesis digest;
3. creates a `RemediationRequest` for the real incident;
4. allowlists only the demo API and focused test paths;
5. runs the existing isolated remediation and validation modules;
6. publishes the candidate and validation report to the dashboard;
7. waits for exact-digest human approval.

Do not substitute `make remediation-smoke`; that command intentionally creates
a synthetic exercise candidate.

## Stop conditions

Stop and preserve evidence if:

- baseline alerts were already firing;
- the test suite fails before deployment;
- another unrelated container becomes unhealthy;
- the alert does not fire after two minutes of sustained traffic;
- AIOps creates duplicate incidents for the same fingerprint;
- triage emits unknown evidence citations;
- any process modifies `main` outside the experiment commit.

## Rollback

Stop traffic and restore `main`:

```bash
git switch main
docker compose up -d --build demo-api
```

The experimental branch and commit remain available for postmortem inspection.
Delete them only after the experiment is complete:

```bash
git branch -D experiment/items-pagination-regression
```

Verify recovery:

```bash
docker compose --profile load run --rm loadgen
sleep 45

curl -s http://127.0.0.1:9090/api/v1/alerts |
  jq '.data.alerts | length'
```

Expected: normal load succeeds and active alert count returns to `0`.

## Artifacts to save

- experiment commit SHA;
- deployment event ID;
- incident ID and fingerprint;
- frozen evidence bundle digest;
- triage and review session IDs;
- triage status and validation errors;
- screenshots or exported JSON from the dashboard;
- rollback verification output.

## Resume prompt

Use this in a future Copilot session:

> Read `docs/experiments/0001-items-pagination-500-regression.md`, verify the
> stack and Git state, and walk me through Experiment 0001 one checkpoint at a
> time. Do not introduce the bug until I explicitly approve the safety setup.
