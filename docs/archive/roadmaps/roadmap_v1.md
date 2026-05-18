# Sprint 1 Roadmap

## Step-by-step Plan

### Planning and Setup
- [X] **Clarify objectives** - align on sprint scope, success metrics, and required artifacts.
- [X] **Prepare environment** - ensure market data sources, configuration files, and `/runs/{run_id}/` storage structure exist.

### Data Foundations
- [X] **Stabilize data ingestion** - verify data collection and preprocessing produce consistent, clean datasets.
- [X] **Confirm feature engineering** - lock feature set and parameters feeding the forecasting models.

### Modeling and Allocation
- [X] **Harden forecasting pipeline** - run LSTM training with final settings and ensure outputs are reproducible.
- [X] **Finalize risk pipeline** - validate covariance and risk-parity stages consuming forecasts to produce candidate weights.
- [X] **Implement `run_monthly_update` contract** - orchestrate end-to-end execution returning `(weights, perf)` with clear inputs.
- [x] **Persist run artifacts** - generate `run_id` and write `weights.csv`, `perf.json`, `equity_curve.csv`, `log.txt` into `/runs/{run_id}/`.

### Reliability
- [X] **Instrument logging and failure surfacing** - capture runtime details and expose failure states for API consumption.

### Service Delivery
- [X] **Wire FastAPI service shell** - scaffold application, configure dependencies, and integrate pipeline entrypoint.
- [X] **Expose core API endpoints** - deliver `POST /run`, `GET /runs/{id}/weights`, `GET /runs/{id}/perf`, and `GET /weights/latest`.
- [X] **Implement export delivery** - support `GET /runs/{id}/export?fmt=csv|zip` for artifact downloads.

### User Experience
- [X] **Render mini result page** - build template displaying weights table and equity curve PNG.
- [X] **Add legal and intake surfaces** - show disclaimer, minimal Terms/Privacy text, and contact form for demo requests.

### Documentation
- [X] **Document operational flow** - outline daily run steps, artifact locations, and handoff notes.

