# Mandate Retry Sequencer

`SPEC.md` is the contract. Read it before implementing. Changing a decision means editing SPEC first.

## Commands

- api: `.venv/Scripts/python -m uvicorn backend.app.main:app --reload --port 8000`
- ui: `cd frontend && npm run dev` (:5173)
- test: `.venv/Scripts/python -m pytest` — run single tests while iterating, not the suite
- data (both are needed; the batch is never fitted on — SPEC §2.1):
  - `.venv/Scripts/python -m backend.scripts.generate_data --seed 42 --n 500 --name batch --edtech-off-cycle 0.85`
  - `.venv/Scripts/python -m backend.scripts.generate_data --seed 1042 --n 8000 --name corpus --split --edtech-off-cycle 0.45`
- train: `.venv/Scripts/python -m backend.scripts.train_scorer`

Python is 3.12 in `.venv`, managed by `uv`. Never use the system 3.14 — LightGBM and SHAP wheels break.

## Rules

- Money is **integer paise**. A float never touches a currency value.
- Never report a metric on training data. `batch_holdout.csv` is not read before the model is fit.
- All data is synthetic and seeded. Same seed must reproduce byte-identical output.
- Policy constants (attempt caps, cooling periods, retry windows, score bands) live **only** in
  `backend/app/policy.py`. Never hardcode them elsewhere.
- Guardrail rules 1–4 are absolute. No score and no agent proposal may override them.
- The `/batch/run` response shape (SPEC §7.2) is frozen. The frontend is built against it.
- Never commit `.env`. Never write to `data/` by hand — it is generated.
- `cache/llm/` and `models/` **are** committed on purpose. Do not add them to `.gitignore`.
- Nothing offense-capable belongs in this repo. Defense-only, always.

## Verification

Every change ships with something that returns pass/fail. Show test output, not assurances.
