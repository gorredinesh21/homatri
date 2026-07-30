# 🛠️ Admin Entity — Table Design & Access Model

Milestone 1 table design for the **Admin** entity — the back-office human operator (dashboard), not a WhatsApp agent.

---

## 🧭 What makes Admin different
- **Not phone-based.** Admin logs into a **dashboard** with email/password → surrogate `admin_id` + unique `email` (no phone natural key).
- **New 4th access tier — true superuser.** Admin is the ONLY actor with direct global write; it bypasses the scoped-write/delegate model because the write-invariant exists to contain **LLM** mistakes, and Admin is a **trusted, authenticated human**. Accountability is via **auditing** (every write logged), not delegation.

### Access ladder (all actors)
| Actor | Read | Write |
|---|---|---|
| Subagent (Customer/Chef/Driver) | global | scoped (own domain); cross-domain via Master delegate |
| Master | global | own `system_*`; delegates the rest |
| **Admin (human)** | **global** | **GLOBAL — direct to all tables (audited)** |
| **Admin AI agent** (dashboard) | **global** | **NONE — read-only** |

---

## 📋 Admin — Operation/Query List

| Q | Operation | R/W | What it does | Table(s) | Notes |
|---|---|---|---|---|---|
| Q1 | login | R | authenticate admin | `admin_users` | own · UNIQUE(email) |
| Q2 | add/edit chef | W | onboard chef (profile) | `chef_profiles` | **global write** (Admin) |
| Q3 | add chef menu + prices | W | dishes & unit_price | `chef_menu_items` | global write |
| Q4 | add/edit rider | W | onboard driver | `driver_profiles` | global write |
| Q5 | update dish price / stock | W | edit menu | `chef_menu_items` | global write |
| Q6 | manage config | W | delivery_fee, cutoff times, radius | `system_settings` | global write |
| Q7 | view dashboards / lists | R | inspect any entity | **all tables** | global read |
| Q8 | mini AI agent query | R | NL → SQL, fetch any data | **all tables (read-only)** | global read, NO write |
| Q9 | *(every admin write)* | W | audit the action | `admin_activity_log` | own |

**Everything else Admin touches is an existing table** (`chef_*`, `driver_*`, `system_*`, `customer_*`) — Admin writes them directly (no new tables). Onboarding writes `chef_profiles`/`chef_menu_items`/`driver_profiles`, exactly as the agent specs assume ("Admin handles onboarding & menu pricing in DB").

---

## 🗄️ Admin-Owned Tables

| Table | Columns | Keys / Indexes |
|---|---|---|
| **`admin_users`** | admin_id (PK `VARCHAR(36)`), email (UNIQUE), name, password_hash, role `admin_role_enum`, is_active `bool`, last_login_at, created_at, updated_at | PK; UNIQUE(email) |
| **`admin_activity_log`** | activity_id (PK), admin_id (FK), action, target_table, target_id, changes `JSONB` (before/after diff), created_at | (admin_id, created_at); (target_table, target_id) — **INSERT-only audit** |
| **`admin_ai_queries`** *(observability, future)* | query_id (PK), admin_id (FK), nl_question `TEXT`, generated_sql `TEXT`, row_count, latency_ms, status, created_at | (admin_id, created_at) |

**New enum:** `admin_role_enum` (SUPER_ADMIN, OPS, SUPPORT).

---

## ✅ Resolved Decisions
1. **`admin_activity_log` is a separate table** (not folded into `system_agent_logs`) — admin/human accountability is a distinct audit domain with its own fields (admin_id, target_table, diff).
2. **Mini AI agent is strictly READ-ONLY** — SELECT-only, run under a **read-only DB role** (or read replica). A NL→SQL agent with write access is dangerous (one bad generation could mass-DELETE/UPDATE). All writes (add chef/rider, set price) go through **dashboard UI forms with validation**, never the free-form AI agent. Optionally log the agent's NL question + generated SQL to `admin_ai_queries`.

## 📝 Notes
- Admin is the **only** actor exempt from the write-invariant — justified because it's a trusted human, and every write is audited to `admin_activity_log`.
- Admin adds **2 owned tables now** (`admin_users`, `admin_activity_log`) + 1 future (`admin_ai_queries`), 1 enum, and a new **global-write access tier**. No changes to any existing table.
