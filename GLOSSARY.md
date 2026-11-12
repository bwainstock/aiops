# Local AIOps

The local AIOps context turns operational symptoms into evidence-backed incident decisions and, when justified, validated remediation candidates.

## Language

**Seed Evidence**:
The trusted facts collected deterministically when an investigation begins, such as the firing alert, service identity, time window, and recent deployment.
_Avoid_: Default evidence, initial context

**Investigation Plan**:
A typed, bounded request for additional operational evidence, including the tool, query, purpose, and expected relationship to the active hypothesis.
_Avoid_: Prompt, tool call list

**Investigation Journal**:
The mutable record of investigation plans, tool calls, results, denials, and follow-up questions accumulated before evidence is frozen.
_Avoid_: Evidence bundle, conversation history

**Evidence Item**:
One normalized, timestamped, source-identified, and digested observation that may be cited by a conclusion.
_Avoid_: Tool output, context blob

**Evidence Bundle**:
The immutable set of evidence items against which a hypothesis and independent review are validated.
_Avoid_: Investigation journal, prompt context

**Triage Hypothesis**:
A structured explanation of an incident containing ranked suspected causes, evidence citations, contradictions, missing evidence, falsification checks, and a remediation decision.
_Avoid_: Diagnosis, answer
