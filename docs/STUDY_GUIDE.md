# Start here

This is a sanitized study copy. All included demo and evaluation data are synthetic. Your original Mac project and its historical data remain separate.

Read these in order:

1. `copilot/daily_energy.py`: timestamps, units and daily energy counters.
2. `copilot/diagnostics.py`: pure classification versus API/database I/O; missing tags versus missing readings versus source-copy gaps.
3. `tests/test_copilot_diagnostics.py`: zero is not missing, and API failures are not empty successful responses.
4. `copilot/agent.py`: fixed SAP/day scope, allowed tools, call budget, evidence IDs, typed numeric checks and Python fallback.
5. `tests/test_copilot_agent.py`: fabricated metrics, unknown evidence, out-of-scope devices and invalid model output.
6. `copilot/evaluation.py` and `evals/cases/`: Python/model comparison, synthetic case provenance, latency, tokens and unmeasured fields.
7. `copilot/app.py` and `web/`: HTTP API, replay UI, filters and evidence viewer.

## Try it

Use the setup commands in the root README. Then:

```sh
python -m pytest -q -p no:cacheprovider tests
python -m copilot.evaluation --output evals/results.json
python -m uvicorn copilot.app:app --host 127.0.0.1 --port 8765
```

The Python baseline works without Ollama or production credentials. Model mode requires your own Ollama server. Real source adapters require a private environment configuration and compatible schemas; synthetic SAP IDs are not live plant assignments.

## Interview exercises

- Explain why Python owns numerical calculations.
- Trace an absent tag through classification, evidence IDs and the UI.
- Demonstrate rejection of another plant's inverter request.
- Explain why a valid citation can still support an incorrect claim.
- Compare the agent with the baseline, including model failures and cost limitations.
- Explain how you would replace the model adapter without changing data collection.

The fixture labels are demonstrations, not expert-confirmed production diagnoses. Complete operator review and semantic evaluation before claiming root-cause accuracy or business savings.
