# 🔄 Homaatri Engine — SDLC Environment & Branching Policy

**Status:** ACTIVE SPECIFICATION  
**Authoritative Environments:** `DEV` (Development) · `STAGE` (Staging/QA) · `PROD` (Production)

---

## 🗺️ 1. Git Branching Strategy & Promotion Hierarchy

```
  ┌────────────────────────────────────────────────────────────────────────┐
  │                         FEATURE / FIX BRANCH                           │
  │                  feature/custom-request  /  fix/cutoff                 │
  └───────────────────────────────────┬────────────────────────────────────┘
                                      │
                                      │ (Pull Request & Unit Tests)
                                      ▼
  ┌────────────────────────────────────────────────────────────────────────┐
  │                      DEVELOPMENT BRANCH (`develop`)                    │
  │            Active feature integration & local dev testing              │
  └───────────────────────────────────┬────────────────────────────────────┘
                                      │
                                      │ (Integration Test Suite Pass)
                                      ▼
  ┌────────────────────────────────────────────────────────────────────────┐
  │                        STAGING BRANCH (`staging`)                      │
  │           Pre-production QA, end-to-end webhook verification           │
  └───────────────────────────────────┬────────────────────────────────────┘
                                      │
                                      │ (Production Deployment Sign-off)
                                      ▼
  ┌────────────────────────────────────────────────────────────────────────┐
  │                         PRODUCTION BRANCH (`main`)                     │
  │                 Stable, production-ready release state                 │
  └────────────────────────────────────────────────────────────────────────┘
```

---

## 🛠️ 2. Environment Matrix & Configuration

| Environment | Git Branch | Database Name | Env File | Razorpay Mode | Google Maps |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Development** (`DEV`) | `develop` | `homatri_dev` | `.env.dev` | `RAZORPAY_MOCK_MODE=True` | Mock / Dev Key |
| **Staging** (`STAGE`) | `staging` | `homatri_stage` | `.env.stage` | Test API Keys (`rzp_test_...`) | Live Routes Key |
| **Production** (`PROD`) | `main` | `homatri_prod` | `.env.prod` | Live API Keys (`rzp_live_...`) | Live Routes Key |

---

## 🔒 3. Environment Isolation Rules

1. **Database Isolation**: Each environment MUST connect to its dedicated database (`homatri_dev`, `homatri_stage`, `homatri_prod`) to prevent test data pollution.
2. **Main Branch Locking**: Direct commits to `main` and `staging` are discouraged. Changes MUST be merged via Pull Requests from `develop`.
3. **Pre-Promotion Check**: Before promoting `develop` ➔ `staging` ➔ `main`, all **70 integration test cases** (`pytest tests/test_*.py`) must pass 100% green.
