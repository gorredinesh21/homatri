# 🛠️ Master Specification: Admin Entity Tables & Column Schemas

This document contains the 100% finalized production SQL column schemas, data types, constraints, default values, foreign keys, indexes, and access rules for the **Admin Entity** (Entity 5).

---

## 🧭 Access Model & Architecture Rules
1. **Not Phone-Based**: Admin users log into the web dashboard using email and password $\rightarrow$ Primary key is surrogate `admin_id` (`VARCHAR(36)` UUID) with `UNIQUE(email)`.
2. **4th Access Tier (Superuser)**: Admin is a trusted human operator with **Direct Global Read & Direct Global Write** across ALL tables (`chef_profiles`, `chef_menu_items`, `driver_profiles`, `system_settings`) to onboard chefs/riders and set dish prices.
3. **Auditability**: Every admin write is audited in `admin_activity_log` with before/after JSON diffs.
4. **Dashboard Mini AI Agent**: Strictly **READ-ONLY** (SELECT queries only).

---

## 🗄️ Admin Domain Tables (2 Tables + 1 Future)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                            ADMIN ENTITY TABLES                              │
├───────────────────────────────────┬─────────────────────────────────────────┤
│ 1. admin_users                    │ 2. admin_activity_log                   │
│ Dashboard email/pass logins,      │ Immutable audit log of all human admin  │
│ roles (SUPER_ADMIN, OPS, SUPPORT) │ dashboard write actions & diffs         │
└───────────────────────────────────┴─────────────────────────────────────────┘
```

---

### Table 1: `admin_users` (Dashboard User Logins)
* **Primary Key**: `admin_id` (`VARCHAR(36)` - UUID)
* **Unique Constraint**: `UNIQUE(email)`
* **Purpose**: Stores human back-office admin user credentials, role permissions, and active login state.

| # | Column Name | Data Type | Nullable? | Default Value | Foreign Key / Index | Description & Usage Rationale |
|---|---|---|---|---|---|---|
| 1 | **`admin_id`** | `VARCHAR(36)` | `NOT NULL` | *UUID* | `PRIMARY KEY` | Unique surrogate ID for this admin user. |
| 2 | **`email`** | `VARCHAR(100)` | `NOT NULL` | `UNIQUE` | Unique B-Tree Index | Admin email login address. |
| 3 | **`name`** | `VARCHAR(100)` | `NOT NULL` | *None* | *None* | Full human name of admin operator. |
| 4 | **`password_hash`** | `VARCHAR(255)`| `NOT NULL` | *None* | *None* | Argon2 / bcrypt hashed password string. |
| 5 | **`role`** | `VARCHAR(30)` | `NOT NULL` | `'OPS'` | B-Tree Index | Admin role: `'SUPER_ADMIN'`, `'OPS'`, `'SUPPORT'`. |
| 6 | **`is_active`** | `BOOLEAN` | `NOT NULL` | `true` | *None* | Toggle to disable admin access if employee leaves. |
| 7 | **`last_login_at`** | `TIMESTAMPTZ` | `YES` | `NULL` | *None* | Timestamp of last successful dashboard login. |
| 8 | **`created_at`** | `TIMESTAMPTZ` | `NOT NULL` | `CURRENT_TIMESTAMP` | *None* | Account creation timestamp. |
| 9 | **`updated_at`** | `TIMESTAMPTZ` | `NOT NULL` | `CURRENT_TIMESTAMP` | *None* | Profile last modified timestamp. |

* **Indexes**: `PRIMARY KEY (admin_id)`, `UNIQUE INDEX (email)`.

---

### Table 2: `admin_activity_log` (Human Audit Trail)
* **Primary Key**: `activity_id` (`VARCHAR(36)` - UUID)
* **Foreign Key**: `admin_id` $\rightarrow$ `admin_users(admin_id)`
* **Purpose**: Immutable audit log recording every write action performed by human admins on the dashboard (e.g. onboarding chef, changing dish prices, modifying cutoff times). Stores before/after JSON diffs.

| # | Column Name | Data Type | Nullable? | Default Value | Foreign Key / Index | Description & Usage Rationale |
|---|---|---|---|---|---|---|
| 1 | **`activity_id`** | `VARCHAR(36)` | `NOT NULL` | *UUID* | `PRIMARY KEY` | Unique surrogate ID for this audit record. |
| 2 | **`admin_id`** | `VARCHAR(36)` | `NOT NULL` | `FK(admin_users)`, B-Tree Index | Admin user who performed the action. |
| 3 | **`action`** | `VARCHAR(50)` | `NOT NULL` | *None* | B-Tree Index | Action type: `'ONBOARD_CHEF'`, `'ONBOARD_DRIVER'`, `'UPDATE_DISH_PRICE'`, `'CHANGE_SETTING'`. |
| 4 | **`target_table`** | `VARCHAR(50)` | `NOT NULL` | *None* | B-Tree Index | Target table modified e.g. `'chef_menu_items'`. |
| 5 | **`target_id`** | `VARCHAR(100)`| `NOT NULL` | *None* | *None* | Target row key modified e.g. `menu_item_id` or `chef_phone`. |
| 6 | **`changes_diff`** | `JSONB` | `NOT NULL` | `'{}'` | *None* | JSON object storing before & after state diff (`{"before": {...}, "after": {...}}`). |
| 7 | **`ip_address`** | `VARCHAR(45)` | `YES` | `NULL` | *None* | IP address of admin workstation. |
| 8 | **`created_at`** | `TIMESTAMPTZ` | `NOT NULL` | `CURRENT_TIMESTAMP` | B-Tree Index | Audit log creation timestamp. |

* **Indexes**: `PRIMARY KEY (activity_id)`, `INDEX idx_admin_activity (admin_id, created_at)`, `INDEX idx_target_activity (target_table, target_id)`.

---

### Table 3: `admin_ai_queries` *(Observability / Future)*
* **Primary Key**: `query_id` (`VARCHAR(36)` - UUID)
* **Foreign Key**: `admin_id` $\rightarrow$ `admin_users(admin_id)`
* **Purpose**: Observability log tracking Natural Language $\rightarrow$ SQL queries executed by the dashboard mini AI agent for analytics.

| # | Column Name | Data Type | Nullable? | Default Value | Foreign Key / Index | Description & Usage Rationale |
|---|---|---|---|---|---|---|
| 1 | **`query_id`** | `VARCHAR(36)` | `NOT NULL` | *UUID* | `PRIMARY KEY` | Unique query log ID. |
| 2 | **`admin_id`** | `VARCHAR(36)` | `NOT NULL` | `FK(admin_users)`, B-Tree Index | Admin who asked the question. |
| 3 | **`nl_question`** | `TEXT` | `NOT NULL` | *None* | *None* | Natural language question asked. |
| 4 | **`generated_sql`** | `TEXT` | `NOT NULL` | *None* | *None* | SQL query generated by AI agent. |
| 5 | **`result_row_count`**| `INTEGER` | `YES` | `0` | *None* | Number of rows returned by query. |
| 6 | **`latency_ms`** | `INTEGER` | `YES` | `0` | *None* | Execution time in milliseconds. |
| 7 | **`created_at`** | `TIMESTAMPTZ` | `NOT NULL` | `CURRENT_TIMESTAMP` | *None* | Query timestamp. |

* **Indexes**: `PRIMARY KEY (query_id)`, `INDEX idx_admin_ai_queries (admin_id, created_at)`.
