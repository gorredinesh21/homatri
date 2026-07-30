# 🚴‍♂️ Master Specification: Driver Entity Tables & Column Schemas

This document contains the 100% finalized production SQL column schemas, data types, constraints, default values, foreign keys, indexes, and usage rationale for the **Driver / Rider Domain** (Entity 3).

---

## 🧭 Base Architectural Rules Applied
1. **Phone is Natural Key**: `driver_phone` (`VARCHAR(15)` - Normalized 10-digit) is the primary key for `driver_profiles`.
2. **`driver_locations` Dropped**: Continuous live GPS telemetry streaming was dropped as impractical for motorcycle drivers in traffic.
3. **Stop-Based Progress Tracking**: Driver progress is tracked via stop checkpoints in `driver_trip_status` (`current_stop_index`, `status`). Combined with `system_delivery_stops`, this serves as the live-tracking board read by Customer and Chef agents.
4. **Master Delegations & Direct Writes**:
   - Master Direct Write: `mark_driver_reached_stop_tool` updates `system_delivery_stops.status = ARRIVED`.
   - Master Delegated Write: `mark_orders_picked_up_tool` & `mark_gate_delivery_completed_tool` trigger Customer executor **DW1** (`customer_orders.status = PICKED_UP / DELIVERED`).

---

## 🗄️ Driver Domain Tables (2 Tables)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           DRIVER DOMAIN TABLES                              │
├─────────────────────────────────────┬───────────────────────────────────────┤
│ 1. driver_profiles                  │ 2. driver_trip_status                 │
│ Driver metadata, vehicle, license,  │ Trip execution phase, stop index,     │
│ active route assignment             │ live progress board                   │
└─────────────────────────────────────┴───────────────────────────────────────┘
```

---

### Table 1: `driver_profiles` (Master Driver Registry)
* **Primary Key**: `driver_phone` (`VARCHAR(15)` - Normalized 10-digit phone)
* **Purpose**: Master registry for onboarded delivery drivers. Stores driver identity, vehicle details, license info, active route assignments, emergency contacts, and shift availability status. Onboarded by Admin.

| # | Column Name | Data Type | Nullable? | Default Value | Foreign Key / Index | Description & Usage Rationale |
|---|---|---|---|---|---|---|
| 1 | **`driver_phone`** | `VARCHAR(15)` | `NOT NULL` | *None* | `PRIMARY KEY` | Normalized 10-digit phone (e.g. `9988776655`). Serves as natural key across all tools. |
| 2 | **`driver_name`** | `VARCHAR(100)` | `NOT NULL` | *None* | B-Tree Index | Full legal name of the delivery driver. |
| 3 | **`vehicle_type`** | `VARCHAR(20)` | `NOT NULL` | `'BIKE'` | *None* | Vehicle category: `'BIKE'`, `'SCOOTER'`, `'EV'`, `'THREE_WHEELER'`. |
| 4 | **`vehicle_number`** | `VARCHAR(30)` | `NOT NULL` | *None* | *None* | License plate number (e.g. `TS 09 EQ 1234`). |
| 5 | **`vehicle_model`** | `VARCHAR(50)` | `YES` | `NULL` | *None* | Make and model (e.g. "Hero Splendor 125"). |
| 6 | **`driver_license_number`** | `VARCHAR(50)` | `YES` | `NULL` | *None* | Driving license number for compliance. |
| 7 | **`alternate_phone`** | `VARCHAR(15)` | `YES` | `NULL` | *None* | Emergency contact phone number. |
| 8 | **`bank_account_details`** | `JSONB` | `YES` | `'{}'` | *None* | UPI ID / account details for driver payouts. |
| 9 | **`current_assigned_route_id`**| `VARCHAR(36)` | `YES` | `NULL` | `FK(system_delivery_routes)`, B-Tree Index | Active GCP delivery route ID assigned to driver for current meal window. |
| 10 | **`is_on_shift`** | `BOOLEAN` | `NOT NULL` | `true` | *None* | True if driver is available for batch assignment. |
| 11 | **`active_status`** | `BOOLEAN` | `NOT NULL` | `true` | B-Tree Index | Master account toggle (`true` = active driver, `false` = suspended/inactive). |
| 12 | **`created_at`** | `TIMESTAMPTZ` | `NOT NULL` | `CURRENT_TIMESTAMP` | *None* | Profile creation timestamp. |
| 13 | **`updated_at`** | `TIMESTAMPTZ` | `NOT NULL` | `CURRENT_TIMESTAMP` | *None* | Last profile update timestamp. |

* **Indexes**: `PRIMARY KEY (driver_phone)`, `INDEX idx_driver_active_shift (active_status, is_on_shift)`, `INDEX idx_driver_current_route (current_assigned_route_id)`.

---

### Table 2: `driver_trip_status` (Live Trip Execution & Progress Board)
* **Primary Key**: `trip_id` (`VARCHAR(36)` - UUID)
* **Foreign Key**: `driver_phone` $\rightarrow$ `driver_profiles(driver_phone)`, `route_id` $\rightarrow$ `system_delivery_routes(route_id)`
* **Purpose**: Driver trip execution & progress tracking ledger. Tracks the driver's current operational phase (`ASSIGNED` $\rightarrow$ `EN_ROUTE_PICKUP` $\rightarrow$ `AT_KITCHEN` $\rightarrow$ `EN_ROUTE_DELIVERY` $\rightarrow$ `AT_GATE` $\rightarrow$ `COMPLETED`) and `current_stop_index`. Serves as the live-tracking progress board.

| # | Column Name | Data Type | Nullable? | Default Value | Foreign Key / Index | Description & Usage Rationale |
|---|---|---|---|---|---|---|
| 1 | **`trip_id`** | `VARCHAR(36)` | `NOT NULL` | *UUID* | `PRIMARY KEY` | Unique surrogate ID for this trip execution record. |
| 2 | **`driver_phone`** | `VARCHAR(15)` | `NOT NULL` | `FK(driver_profiles)`, B-Tree Index | Driver executing the trip. |
| 3 | **`route_id`** | `VARCHAR(36)` | `NOT NULL` | `FK(system_delivery_routes)`, B-Tree Index | GCP-optimized delivery route assigned to driver. |
| 4 | **`service_date`** | `DATE` | `NOT NULL` | *None* | B-Tree Index | Service date (e.g. `2026-07-31`). |
| 5 | **`meal_window`** | `VARCHAR(20)` | `NOT NULL` | `'LUNCH'` | B-Tree Index | Meal window: `'LUNCH'` or `'DINNER'`. |
| 6 | **`status`** | `VARCHAR(30)` | `NOT NULL` | `'ASSIGNED'` | B-Tree Index | Trip phase: `'ASSIGNED'`, `'EN_ROUTE_PICKUP'`, `'AT_KITCHEN'`, `'EN_ROUTE_DELIVERY'`, `'AT_GATE'`, `'COMPLETED'`. |
| 7 | **`current_stop_index`** | `INTEGER` | `NOT NULL` | `1` | *None* | Current stop sequence number driver is navigating to or at (`1`, `2`, `3`...). |
| 8 | **`total_stops`** | `INTEGER` | `NOT NULL` | *None* | *None* | Total stops on this assigned route. |
| 9 | **`completed_stops`** | `INTEGER` | `NOT NULL` | `0` | *None* | Number of stops completed so far. |
| 10 | **`trip_started_at`** | `TIMESTAMPTZ` | `YES` | `NULL` | *None* | Timestamp when driver started route (`Stop 1`). |
| 11 | **`trip_completed_at`**| `TIMESTAMPTZ` | `YES` | `NULL` | *None* | Timestamp when final stop was completed. |
| 12 | **`delay_notes`** | `TEXT` | `YES` | `NULL` | *None* | Notes if driver reported traffic or breakdown via Tool 8. |
| 13 | **`created_at`** | `TIMESTAMPTZ` | `NOT NULL` | `CURRENT_TIMESTAMP` | *None* | Trip record creation timestamp. |
| 14 | **`updated_at`** | `TIMESTAMPTZ` | `NOT NULL` | `CURRENT_TIMESTAMP` | *None* | Last trip status update timestamp. |

* **Indexes**: `PRIMARY KEY (trip_id)`, `INDEX idx_trip_driver_date (driver_phone, service_date, meal_window)`, `INDEX idx_trip_route_id (route_id)`.
