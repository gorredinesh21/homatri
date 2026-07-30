# 👨‍🍳 Master Specification: Chef Entity Tables & Column Schemas

This document contains the 100% finalized production SQL column schemas, data types, constraints, default values, foreign keys, indexes, and usage rationale for the **Chef Domain** (Entity 1).

---

## 🧭 Base Architectural Rules Applied
1. **Phone is Natural Key**: `chef_phone` (`VARCHAR(15)` - Normalized 10-digit) is the primary key for `chef_profiles`.
2. **Write Invariant**: Chef tools write **ONLY** to `chef_*` tables. Cross-domain status updates are handoffs delegated to Master Agent.
3. **Single Source of Truth**: Chef daily cooking checklists are queried via global read from `customer_orders` & `customer_order_items`.

---

## 🗄️ Chef Domain Tables (4 Tables)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           CHEF DOMAIN TABLES                                │
├───────────────────┬───────────────────┬───────────────────┬─────────────────┤
│ 1. chef_profiles  │ 2. chef_menu_items│3. chef_daily_inventory│4. chef_readiness│
│ Kitchen metadata, │ Dish catalog,     │ Daily batch caps &│ Order packing   │
│ FSSAI, lat/lng    │ prices, allergens │ slots for dates   │ timestamps      │
└───────────────────┴───────────────────┴───────────────────┴─────────────────┘
```

---

### Table 1: `chef_profiles` (Master Kitchen Registry)
* **Primary Key**: `chef_phone` (`VARCHAR(15)` - Normalized 10-digit phone)
* **Purpose**: Master registry for onboarded home chefs. Stores identity, physical kitchen location (GPS coordinates for GCP Route Solver), business compliance, banking info, and operational settings.

| # | Column Name | Data Type | Nullable? | Default Value | Foreign Key / Index | Description & Usage Rationale |
|---|---|---|---|---|---|---|
| 1 | **`chef_phone`** | `VARCHAR(15)` | `NOT NULL` | *None* | `PRIMARY KEY` | Normalized 10-digit phone (e.g. `9876543210`). Serves as natural key across all tools. |
| 2 | **`kitchen_name`** | `VARCHAR(100)` | `NOT NULL` | *None* | B-Tree Index | Public display name of kitchen (e.g. "Ramesh Home Kitchen"). |
| 3 | **`chef_name`** | `VARCHAR(100)` | `NOT NULL` | *None* | *None* | Full legal human name of the home cook. |
| 4 | **`address`** | `TEXT` | `NOT NULL` | *None* | *None* | Full physical street address of the home kitchen. |
| 5 | **`apartment_or_locality`** | `VARCHAR(100)` | `YES` | `NULL` | B-Tree Index | Complex/locality name (useful for hyper-local filtering). |
| 6 | **`city`** | `VARCHAR(50)` | `NOT NULL` | `'Hyderabad'` | *None* | Operating city (defaults to Hyderabad). |
| 7 | **`pincode`** | `VARCHAR(10)` | `YES` | `NULL` | *None* | Postal code for zonal grouping & analytics. |
| 8 | **`latitude`** | `DECIMAL(10, 8)` | `NOT NULL` | *None* | *None* | Exact GPS Latitude (passed to GCP Route Solver API). |
| 9 | **`longitude`** | `DECIMAL(11, 8)` | `NOT NULL` | *None* | *None* | Exact GPS Longitude (passed to GCP Route Solver API). |
| 10 | **`fssai_license_number`** | `VARCHAR(50)` | `YES` | `NULL` | *None* | FSSAI food safety registration/license number. |
| 11 | **`dietary_type`** | `VARCHAR(20)` | `YES` | `NULL` | *None* | Kitchen classification: `PURE_VEG`, `NON_VEG`, `HYBRID`. |
| 12 | **`kitchen_bio`** | `TEXT` | `YES` | `NULL` | *None* | Short intro/story displayed when customers browse menus. |
| 13 | **`profile_image_url`** | `TEXT` | `YES` | `NULL` | *None* | URL to kitchen logo or chef profile photo. |
| 14 | **`alternate_phone`** | `VARCHAR(15)` | `YES` | `NULL` | *None* | Secondary emergency contact phone number. |
| 15 | **`bank_account_details`** | `JSONB` | `YES` | `'{}'` | *None* | UPI ID, account number, IFSC code for weekly payouts. |
| 16 | **`operating_days`** | `JSONB` | `YES` | `'["MON","TUE","WED","THU","FRI","SAT","SUN"]'` | *None* | Days of the week the kitchen operates. |
| 17 | **`is_verified`** | `BOOLEAN` | `NOT NULL` | `false` | *None* | Set to `true` when Admin approves onboarding. |
| 18 | **`active_status`** | `BOOLEAN` | `NOT NULL` | `true` | B-Tree Index | Master toggle (`true` = accepting orders, `false` = temporarily closed). |
| 19 | **`created_at`** | `TIMESTAMPTZ` | `NOT NULL` | `CURRENT_TIMESTAMP` | *None* | Timestamp when profile was created. |
| 20 | **`updated_at`** | `TIMESTAMPTZ` | `NOT NULL` | `CURRENT_TIMESTAMP` | *None* | Timestamp of last profile modification. |

* **Indexes**: `PRIMARY KEY (chef_phone)`, `INDEX idx_chef_profiles_active (active_status)`, `INDEX idx_chef_profiles_locality (apartment_or_locality)`.

---

### Table 2: `chef_menu_items` (Dish Catalog)
* **Primary Key**: `menu_item_id` (`VARCHAR(36)` - UUID)
* **Foreign Key**: `chef_phone` $\rightarrow$ `chef_profiles(chef_phone)` (`ON DELETE RESTRICT`)
* **Purpose**: Master catalog of all dishes offered by home chefs. Stores dish names, pricing, descriptions, dietary tags, spice levels, allergens, packaging info, and mid-day stock availability toggles.

| # | Column Name | Data Type | Nullable? | Default Value | Foreign Key / Index | Description & Usage Rationale |
|---|---|---|---|---|---|---|
| 1 | **`menu_item_id`** | `VARCHAR(36)` | `NOT NULL` | *UUID* | `PRIMARY KEY` | Unique surrogate ID for the dish item. |
| 2 | **`chef_phone`** | `VARCHAR(15)` | `NOT NULL` | *None* | `FK(chef_profiles)`, B-Tree Index | Chef who cooks and owns this dish. |
| 3 | **`dish_name`** | `VARCHAR(100)` | `NOT NULL` | *None* | B-Tree Index | Name of dish (e.g. "Special Paneer Thali"). |
| 4 | **`description`** | `TEXT` | `YES` | `NULL` | *None* | Detailed description, ingredients, or cooking style. |
| 5 | **`unit_price`** | `DECIMAL(10, 2)` | `NOT NULL` | *None* | *None* | Price per unit in INR (e.g. `180.00`). |
| 6 | **`meal_type`** | `VARCHAR(20)` | `NOT NULL` | `'LUNCH'` | B-Tree Index | Meal window availability: `'LUNCH'`, `'DINNER'`, `'BOTH'`. |
| 7 | **`dietary_tag`** | `VARCHAR(20)` | `YES` | `'VEG'` | *None* | Dish classification: `'VEG'`, `'NON_VEG'`, `'EGG'`, `'JAIN'`. |
| 8 | **`spice_level`** | `VARCHAR(20)` | `YES` | `'MEDIUM'` | *None* | Spice intensity: `'MILD'`, `'MEDIUM'`, `'SPICY'`, `'EXTRA_SPICY'`. |
| 9 | **`allergens`** | `JSONB` | `YES` | `'[]'` | *None* | List of common allergens (e.g. `["Nuts", "Dairy", "Gluten"]`). |
| 10 | **`preparation_time_mins`** | `INTEGER` | `YES` | `NULL` | *None* | Estimated cooking prep time in minutes. |
| 11 | **`packaging_type`** | `VARCHAR(50)` | `YES` | `'3_COMPARTMENT_BOX'` | *None* | Container / packaging info for chef packing instructions. |
| 12 | **`image_url`** | `TEXT` | `YES` | `NULL` | *None* | Dish photo URL for customer browsing. |
| 13 | **`max_availability`** | `INTEGER` | `YES` | `10` | *None* | Default maximum prep capacity/plates per meal window. |
| 14 | **`is_available`** | `BOOLEAN` | `NOT NULL` | `true` | B-Tree Index | Mid-day stock toggle (`true` = available, `false` = sold out). |
| 15 | **`created_at`** | `TIMESTAMPTZ` | `NOT NULL` | `CURRENT_TIMESTAMP` | *None* | Timestamp when dish was added to catalog. |
| 16 | **`updated_at`** | `TIMESTAMPTZ` | `NOT NULL` | `CURRENT_TIMESTAMP` | *None* | Timestamp of last dish modification. |

* **Indexes**: `PRIMARY KEY (menu_item_id)`, `FK (chef_phone) REFERENCES chef_profiles(chef_phone)`, `INDEX idx_menu_items_chef_meal (chef_phone, meal_type, is_available)`.

---

### Table 3: `chef_daily_inventory` (Batch Capacity Limits)
* **Primary Key**: `inventory_id` (`VARCHAR(36)` - UUID)
* **Foreign Key**: `chef_phone` $\rightarrow$ `chef_profiles(chef_phone)`, `menu_item_id` $\rightarrow$ `chef_menu_items(menu_item_id)`
* **Unique Constraint**: `UNIQUE(chef_phone, menu_item_id, service_date, meal_window)`
* **Purpose**: Tracks daily meal prep capacity limits for specific dates & meal windows. Allows chefs to set date-specific capacity overrides.

| # | Column Name | Data Type | Nullable? | Default Value | Foreign Key / Index | Description & Usage Rationale |
|---|---|---|---|---|---|---|
| 1 | **`inventory_id`** | `VARCHAR(36)` | `NOT NULL` | *UUID* | `PRIMARY KEY` | Unique surrogate ID for this daily inventory record. |
| 2 | **`chef_phone`** | `VARCHAR(15)` | `NOT NULL` | *None* | `FK(chef_profiles)`, B-Tree Index | Chef who owns this inventory record. |
| 3 | **`menu_item_id`** | `VARCHAR(36)` | `NOT NULL` | *None* | `FK(chef_menu_items)`, B-Tree Index | Dish item being capped for today's batch. |
| 4 | **`service_date`** | `DATE` | `NOT NULL` | *None* | B-Tree Index | Date of service (e.g. `2026-07-31`). |
| 5 | **`meal_window`** | `VARCHAR(20)` | `NOT NULL` | `'LUNCH'` | B-Tree Index | Meal window: `'LUNCH'` or `'DINNER'`. |
| 6 | **`max_capacity`** | `INTEGER` | `NOT NULL` | `10` | *None* | Maximum quantity chef can cook for this dish on this date/window. |
| 7 | **`is_unlimited`** | `BOOLEAN` | `NOT NULL` | `false` | *None* | True if chef places no cap on this dish for today's batch. |
| 8 | **`notes`** | `TEXT` | `YES` | `NULL` | *None* | Optional chef notes for today's batch. |
| 9 | **`created_at`** | `TIMESTAMPTZ` | `NOT NULL` | `CURRENT_TIMESTAMP` | *None* | Record creation timestamp. |
| 10 | **`updated_at`** | `TIMESTAMPTZ` | `NOT NULL` | `CURRENT_TIMESTAMP` | *None* | Timestamp of last inventory update. |

* **Indexes**: `PRIMARY KEY (inventory_id)`, `UNIQUE CONSTRAINT unique_chef_item_window (chef_phone, menu_item_id, service_date, meal_window)`, `INDEX idx_inventory_lookup (chef_phone, service_date, meal_window)`.

---

### Table 4: `chef_order_readiness` (Packing Handshake Signals)
* **Primary Key**: `readiness_id` (`VARCHAR(36)` - UUID)
* **Foreign Key**: `order_id` $\rightarrow$ `customer_orders(order_id)`, `chef_phone` $\rightarrow$ `chef_profiles(chef_phone)`
* **Purpose**: Records food packing completion timestamps when a chef finishes cooking and packages an order (`PACKED_READY`).

| # | Column Name | Data Type | Nullable? | Default Value | Foreign Key / Index | Description & Usage Rationale |
|---|---|---|---|---|---|---|
| 1 | **`readiness_id`** | `VARCHAR(36)` | `NOT NULL` | *UUID* | `PRIMARY KEY` | Unique surrogate ID for this readiness signal. |
| 2 | **`order_id`** | `VARCHAR(36)` | `NOT NULL` | *None* | `FK(customer_orders)`, B-Tree Index | Order ID marked packed and ready. |
| 3 | **`chef_phone`** | `VARCHAR(15)` | `NOT NULL` | *None* | `FK(chef_profiles)`, B-Tree Index | Chef who cooked and packed this order. |
| 4 | **`status`** | `VARCHAR(20)` | `NOT NULL` | `'PACKED_READY'` | B-Tree Index | Readiness status: `'PREPARING'`, `'PACKED_READY'`. |
| 5 | **`packed_at`** | `TIMESTAMPTZ` | `NOT NULL` | `CURRENT_TIMESTAMP` | *None* | Exact timestamp when food box was sealed. |
| 6 | **`box_count`** | `INTEGER` | `YES` | `1` | *None* | Number of food containers/boxes packed for this order. |
| 7 | **`special_packing_notes`**| `TEXT` | `YES` | `NULL` | *None* | Optional chef packing note (e.g. "Sauce packed separately"). |
| 8 | **`driver_notified`** | `BOOLEAN` | `NOT NULL` | `true` | *None* | Audit flag confirming Master Agent relayed alert to driver. |
| 9 | **`created_at`** | `TIMESTAMPTZ` | `NOT NULL` | `CURRENT_TIMESTAMP` | *None* | Record creation timestamp. |

* **Indexes**: `PRIMARY KEY (readiness_id)`, `FK (order_id) REFERENCES customer_orders(order_id)`, `FK (chef_phone) REFERENCES chef_profiles(chef_phone)`, `INDEX idx_readiness_order (order_id)`.
