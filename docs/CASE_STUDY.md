# Case-study template

## Problem

Solar portfolio analysis needs to distinguish poor generation from absent or inconsistent source measurements. Missing records must not silently become zero production.

## Implementation

Python collects and classifies evidence. A bounded local LLM has fixed read-only tools and returns observations separately from hypotheses and missing evidence. The dashboard exposes the source ledger and trace.

## Demonstration scenarios

The included fictional cases cover missing telemetry, missing tags, a missing source record, usable energy, and missing corrected energy despite other telemetry. All demo values and identifiers are synthetic.

## Evaluation and limitations

Use deterministic unit tests and compare model-assisted replay with the Python baseline. Track schema/reference validation, typed numeric accuracy, tool errors, latency and tokens. Review semantic support separately with a human expert. Do not claim actual saved energy, reduced downtime or production root-cause accuracy from these examples.

## Evidence to add for a job application

Record the source-to-output architecture, a short demo, your reasoning about failure modes, and results from reviewed cases you are authorized to share. Explain what the agent improves over the baseline and where it does not.
