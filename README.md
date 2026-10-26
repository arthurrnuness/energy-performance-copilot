# Energy Performance Copilot — private study copy

A Python energy-monitoring project with source reconciliation, gap diagnostics, a bounded local LLM agent, evaluation tooling and a small dashboard.

**This copy contains synthetic demonstration data only.** Credentials, operational measurements, logs, local databases and production device assignments are excluded. The original Mac project is separate and unchanged.

## Study from anywhere

Open `docs/STUDY_GUIDE.md` first. Read `copilot/diagnostics.py`, then `copilot/agent.py`, then `copilot/evaluation.py`. Use GitHub file browsing even while your Mac is off.

## Run the demo on a computer

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-copilot.txt
python -m uvicorn copilot.app:app --host 127.0.0.1 --port 8765
```

Open http://127.0.0.1:8765. The Python baseline needs no live credentials or model. Agent mode needs a local Ollama server and the configured model. Live connectors need private configuration and the existing database schema; do not enable them for synthetic SAP IDs.

```sh
python -m pytest -q -p no:cacheprovider tests
python -m copilot.evaluation --output evals/results.json
```

Add `--agent` to evaluation only when Ollama is running. Source observation labels and valid citations are not expert-confirmed root causes. Dollar compute cost and semantic entailment accuracy are not measured.

## Reading map

- `copilot/`: diagnostics, scoped agent, evaluation and FastAPI app.
- `web/`: local dashboard.
- `tests/`: deterministic edge cases and agent safeguards.
- `evals/`: clearly labeled fictional examples and review form.
- `docs/`: architecture, study guide and case-study structure.
- Root Python files: generic source-sync and portfolio components retained for study.

GitHub hosts the source files, not a running instance of the dashboard. No daily scheduled job is installed by cloning this copy.
