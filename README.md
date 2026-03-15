# EE Model Library

Python module library for energy finance project models - European Energy A/S

Status: Early stage (v1) | Modules: 1 | Markets: DK, DE (planned: AU, UK, SE, PL)

---

## Architecture

This repo is the Backend in a three-layer system:

FRONTEND (Claude Project - EE Model Builder) reads/writes
INTELLIGENCE LAYER (Claude + system prompt) reads/writes
BACKEND (this repo - ee-model-library)

The Claude-based EE Model Builder uses this library to assemble production-ready Excel financial models for renewable energy projects on demand.

---

## Repository Structure

ee-model-library/
  modules/core/         WACC_001.py
  registry/             module_registry.json
  contributions/staged/ Draft modules flagged by Claude for review
  assumptions/          (planned) Market assumption DBs per market
  assembly/             (planned) Assembly engine
  tests/                (planned) Automated checks
  requirements.txt

---

## Module Registry

All modules are indexed in registry/module_registry.json. Each entry has: module_id, version, tier, markets, technologies, path.

Current modules:

| ID | Description | Tier | Markets |
|---|---|---|---|
| WACC_001 | Weighted Average Cost of Capital | both | DK, DE, * |

---

## Contribution Flow (Push)

When an operator submits a model with logic not yet in the registry, Claude flags it:

1. Claude detects new logic in the operator model
2. Claude proposes a module ID and description
3. Draft lands in contributions/staged/
4. Martin reviews and approves
5. Martin commits to modules/core/ and updates the registry

---

## Quality Standards

Every module must pass before merging: dynamic formulas only, assumptions sourced and documented, unit test in tests/, registry entry added.

---

## Planned Modules

| ID | Description | Priority |
|---|---|---|
| IRR_001 | Project IRR / Equity IRR | High |
| REV_PV_001 | Solar PV revenue (merchant + PPA) | High |
| REV_BESS_001 | BESS revenue (tolling + merchant) | High |
| DEBT_001 | Debt schedule + DSCR | High |
| TAX_DK_001 | Danish corporate tax | Medium |
| REV_WIND_001 | Wind onshore revenue | Medium |

---

Maintained by Martin Graa Jennum, European Energy A/S
