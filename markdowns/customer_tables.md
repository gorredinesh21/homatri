# 🙋‍♂️ Master Specification: Customer Entity Tables & Column Schemas

This document contains the 100% finalized production SQL column schemas, data types, constraints, default values, foreign keys, indexes, and usage rationale for the **Customer Domain** (Entity 2).

---

## 🧭 Base Architectural Rules Applied
1. **Phone is Natural Key**: `customer_phone` (`VARCHAR(15)` - Normalized 10-digit) is the primary key for `customer_profiles`.
2. **Write Invariant & Delegated Executors**: Customer tools write **ONLY** to `customer_*` tables. Master Agent delegates cross-domain status updates to Customer executors:
   - **DW1**: `execute_order_status_transition` (`CONFIRMED`, `BATCHED`, `PACKED`, `PICKED_UP`, `DELIVERED`, `CANCELLED`).
   - **DW2**: `execute_payment_status_update` (`PAID`, `FAILED`, `REFUNDED`).
3. **Derived Aggregates**: `units_sold` is derived via `SUM(customer_order_items.quantity)`, and average ratings are derived via `AVG(customer_reviews)`.

---

## 🗄️ Customer Domain Tables (5 Tables)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         CUSTOMER DOMAIN TABLES                              │
├──────────────────┬──────────────────┬──────────────────┬──────────────────┬─┤
│1.customer_profile│2.customer_orders │3.order_items     │4.customer_payment│5│
│Customer metadata,│Order headers,    │Line items, dish  │UPI payment links,│.│
│2-step onboarding │subtotals, status │snapshots, qty    │ledger, status    │c│
└──────────────────┴──────────────────┴──────────────────┴──────────────────┴─┘
```

---

### Table 1: `customer_profiles` (Master Customer Registry)
* **Primary Key**: `customer_phone` (`VARCHAR(15)` - Normalized 10-digit phone)
* **Purpose**: Master registry for onboarded customers. Stores identity, delivery address, apartment details, GPS coordinates (set via WhatsApp location pin attachment during 2-step onboarding), and preference settings.

| # | Column Name | Data Type | Nullable? | Default Value | Foreign Key / Index | Description & Usage Rationale |
|---|---|---|---|---|---|---|
| 1 | **`customer_phone`** | `VARCHAR(15)` | `NOT NULL` | *None* | `PRIMARY KEY` | Normalized 10-digit phone (e.g. `9876543210`). Serves as natural key across all tools. |
| 2 | **`name`** | `VARCHAR(100)` | `NOT NULL` | *None* | B-Tree Index | Customer's full name. |
| 3 | **`delivery_address`** | `TEXT` | `NOT NULL` | *None* | *None* | Full text street / building address entered during Step 1 onboarding. |
| 4 | **`apartment_name`** | `VARCHAR(100)` | `YES` | `NULL` | B-Tree Index | Apartment complex / gated community name (e.g. "My Home Bhooja") for gate stop consolidation. |
| 5 | **`flat_number`** | `VARCHAR(50)` | `YES` | `NULL` | *None* | Flat / door / tower number (e.g. "Flat 402, Block A"). |
| 6 | **`landmark`** | `VARCHAR(100)` | `YES` | `NULL` | *None* | Nearby landmark for driver delivery guidance. |
| 7 | **`city`** | `VARCHAR(50)` | `YES` | `'Hyderabad'` | *None* | Delivery city (defaults to Hyderabad). |
| 8 | **`pincode`** | `VARCHAR(10)` | `YES` | `NULL` | *None* | Postal code. |
| 9 | **`latitude`** | `DECIMAL(10, 8)` | `YES` | `NULL` | *None* | GPS Latitude set in Step 2 onboarding when user shares location pin on WhatsApp. |
| 10 | **`longitude`** | `DECIMAL(11, 8)` | `YES` | `NULL` | *None* | GPS Longitude set in Step 2 onboarding when user shares location pin on WhatsApp. |
| 11 | **`alternate_phone`** | `VARCHAR(15)` | `YES` | `NULL` | *None* | Secondary phone number for delivery contact. |
| 12 | **`email`** | `VARCHAR(100)` | `YES` | `NULL` | *None* | Customer email for receipts or account recovery. |
| 13 | **`dietary_preference`** | `VARCHAR(20)` | `YES` | `'VEG'` | *None* | Preferred dietary tag: `'VEG'`, `'NON_VEG'`, `'ANY'`. |
| 14 | **`delivery_instructions`**| `TEXT` | `YES` | `NULL` | *None* | Default gate delivery instructions (e.g. "Leave package with Gate 2 Security Guard"). |
| 15 | **`is_registered`** | `BOOLEAN` | `NOT NULL` | `false` | B-Tree Index | Set to `true` when Step 2 location pin is saved (`PENDING_LOCATION_PIN` $\rightarrow$ `REGISTERED`). |
| 16 | **`created_at`** | `TIMESTAMPTZ` | `NOT NULL` | `CURRENT_TIMESTAMP` | *None* | Account creation timestamp. |
| 17 | **`updated_at`** | `TIMESTAMPTZ` | `NOT NULL` | `CURRENT_TIMESTAMP` | *None* | Profile last updated timestamp. |

* **Indexes**: `PRIMARY KEY (customer_phone)`, `INDEX idx_customer_apartment (apartment_name)`, `INDEX idx_customer_registered (is_registered)`.

---

### Table 2: `customer_orders` (Master Order Header)
* **Primary Key**: `order_id` (`VARCHAR(36)` - UUID)
* **Foreign Key**: `customer_phone` $\rightarrow$ `customer_profiles(customer_phone)`, `chef_phone` $\rightarrow$ `chef_profiles(chef_phone)`
* **Purpose**: Master order header repository. Stores order status lifecycle (`PENDING_PAYMENT` $\rightarrow$ `CONFIRMED` $\rightarrow$ `BATCHED` $\rightarrow$ `COOKING` $\rightarrow$ `PACKED` $\rightarrow$ `PICKED_UP` $\rightarrow$ `DELIVERED` $\rightarrow$ `CANCELLED`), order financial subtotals, delivery fees, total amounts, meal window, service date, and immutable kitchen name snapshot.

| # | Column Name | Data Type | Nullable? | Default Value | Foreign Key / Index | Description & Usage Rationale |
|---|---|---|---|---|---|---|
| 1 | **`order_id`** | `VARCHAR(36)` | `NOT NULL` | *UUID* | `PRIMARY KEY` | Unique surrogate ID for the order (e.g. `ord_104`). |
| 2 | **`customer_phone`** | `VARCHAR(15)` | `NOT NULL` | *None* | `FK(customer_profiles)`, B-Tree Index | Customer who placed this order. |
| 3 | **`chef_phone`** | `VARCHAR(15)` | `NOT NULL` | *None* | `FK(chef_profiles)`, B-Tree Index | Home chef cooking this order. |
| 4 | **`kitchen_name`** | `VARCHAR(100)` | `NOT NULL` | *None* | *None* | **Immutable snapshot** of kitchen name at order creation time. |
| 5 | **`meal_window`** | `VARCHAR(20)` | `NOT NULL` | `'LUNCH'` | B-Tree Index | Meal window: `'LUNCH'` or `'DINNER'`. |
| 6 | **`service_date`** | `DATE` | `NOT NULL` | *None* | B-Tree Index | Service date (e.g. `2026-07-31`). |
| 7 | **`status`** | `VARCHAR(20)` | `NOT NULL` | `'PENDING_PAYMENT'` | B-Tree Index | Lifecycle status: `PENDING_PAYMENT`, `CONFIRMED`, `BATCHED`, `COOKING`, `PACKED`, `PICKED_UP`, `DELIVERED`, `CANCELLED`. |
| 8 | **`cart_subtotal`** | `DECIMAL(10, 2)` | `NOT NULL` | `0.00` | *None* | Sum total of line items in INR. |
| 9 | **`delivery_fee`** | `DECIMAL(10, 2)` | `NOT NULL` | `30.00` | *None* | Applied delivery fee in INR. |
| 10 | **`total_amount`** | `DECIMAL(10, 2)` | `NOT NULL` | `0.00` | *None* | Total payable amount (`cart_subtotal + delivery_fee`). |
| 11 | **`special_instructions`**| `TEXT` | `YES` | `NULL` | *None* | Order-level special dietary instructions or delivery notes. |
| 12 | **`cancellation_reason`**| `TEXT` | `YES` | `NULL` | *None* | Reason recorded if order is cancelled before cutoff. |
| 13 | **`cancelled_at`** | `TIMESTAMPTZ` | `YES` | `NULL` | *None* | Timestamp of order cancellation. |
| 14 | **`created_at`** | `TIMESTAMPTZ` | `NOT NULL` | `CURRENT_TIMESTAMP` | *None* | Order creation timestamp. |
| 15 | **`updated_at`** | `TIMESTAMPTZ` | `NOT NULL` | `CURRENT_TIMESTAMP` | *None* | Order last modified timestamp. |

* **Indexes**: `PRIMARY KEY (order_id)`, `INDEX idx_customer_orders_phone_status (customer_phone, status)`, `INDEX idx_orders_chef_batch (chef_phone, meal_window, service_date, status)`, `INDEX idx_orders_cutoff (meal_window, service_date, status)`.

---

### Table 3: `customer_order_items` (Order Line Items)
* **Primary Key**: `item_id` (`VARCHAR(36)` - UUID)
* **Foreign Key**: `order_id` $\rightarrow$ `customer_orders(order_id)` (`ON DELETE CASCADE`), `menu_item_id` $\rightarrow$ `chef_menu_items(menu_item_id)`
* **Purpose**: Order line items repository. Stores specific dishes selected for an order, item quantities, special dietary instructions, and **immutable snapshots** of `dish_name`, `unit_price`, and `service_date` captured at order creation time.

| # | Column Name | Data Type | Nullable? | Default Value | Foreign Key / Index | Description & Usage Rationale |
|---|---|---|---|---|---|---|
| 1 | **`item_id`** | `VARCHAR(36)` | `NOT NULL` | *UUID* | `PRIMARY KEY` | Unique surrogate ID for this order line item. |
| 2 | **`order_id`** | `VARCHAR(36)` | `NOT NULL` | *None* | `FK(customer_orders)`, B-Tree Index | Order header this item belongs to. |
| 3 | **`menu_item_id`** | `VARCHAR(36)` | `NOT NULL` | *None* | `FK(chef_menu_items)`, B-Tree Index | Original dish catalog ID. |
| 4 | **`chef_phone`** | `VARCHAR(15)` | `NOT NULL` | *None* | B-Tree Index | Denormalized chef phone for ultra-fast chef inventory aggregation (`units_sold`). |
| 5 | **`dish_name`** | `VARCHAR(100)` | `NOT NULL` | *None* | *None* | **Immutable snapshot** of dish name at time of order. |
| 6 | **`unit_price`** | `DECIMAL(10, 2)` | `NOT NULL` | *None* | *None* | **Immutable snapshot** of dish price per unit in INR at time of order. |
| 7 | **`quantity`** | `INTEGER` | `NOT NULL` | `1` | *None* | Quantity ordered (e.g. `2`). |
| 8 | **`item_subtotal`** | `DECIMAL(10, 2)` | `NOT NULL` | *None* | *None* | Subtotal for this line item (`quantity * unit_price`). |
| 9 | **`service_date`** | `DATE` | `NOT NULL` | *None* | B-Tree Index | **Immutable snapshot** of service date (e.g. `2026-07-31`). |
| 10 | **`special_instructions`**| `TEXT` | `YES` | `NULL` | *None* | Item-level dietary notes (e.g. "Less spicy", "Extra chutney"). |
| 11 | **`created_at`** | `TIMESTAMPTZ` | `NOT NULL` | `CURRENT_TIMESTAMP` | *None* | Item creation timestamp. |

* **Indexes**: `PRIMARY KEY (item_id)`, `INDEX idx_order_items_order_id (order_id)`, `INDEX idx_units_sold_derived (chef_phone, menu_item_id, service_date)`.

---

### Table 4: `customer_payments` (Financial Ledger)
* **Primary Key**: `payment_id` (`VARCHAR(36)` - UUID)
* **Foreign Key**: `order_id` $\rightarrow$ `customer_orders(order_id)`
* **Purpose**: Financial ledger for all customer payments. Tracks initial order payments, top-up payments, UPI payment link URLs, payment gateway transaction IDs, and payment status (`PENDING`, `PAID`, `FAILED`, `REFUNDED`).

| # | Column Name | Data Type | Nullable? | Default Value | Foreign Key / Index | Description & Usage Rationale |
|---|---|---|---|---|---|---|
| 1 | **`payment_id`** | `VARCHAR(36)` | `NOT NULL` | *UUID* | `PRIMARY KEY` | Unique surrogate ID for this payment attempt. |
| 2 | **`order_id`** | `VARCHAR(36)` | `NOT NULL` | `FK(customer_orders)`, B-Tree Index | Order header this payment belongs to. |
| 3 | **`customer_phone`** | `VARCHAR(15)` | `NOT NULL` | `FK(customer_profiles)`, B-Tree Index | Customer who made the payment. |
| 4 | **`payment_type`** | `VARCHAR(20)` | `NOT NULL` | `'INITIAL'` | B-Tree Index | Payment classification: `'INITIAL'`, `'TOPUP'`, `'REFUND'`. |
| 5 | **`amount_due`** | `DECIMAL(10, 2)` | `NOT NULL` | *None* | *None* | Amount payable in INR (e.g. `610.00`). |
| 6 | **`amount_paid`** | `DECIMAL(10, 2)` | `YES` | `0.00` | *None* | Actual amount collected from payment gateway callback. |
| 7 | **`payment_link_url`** | `TEXT` | `YES` | `NULL` | *None* | Programmatically generated UPI payment link URL sent to WhatsApp. |
| 8 | **`gateway`** | `VARCHAR(50)` | `NOT NULL` | `'RAZORPAY'` | *None* | Gateway vendor: `'RAZORPAY'`, `'STRIPE'`, `'PHONEPE'`. |
| 9 | **`gateway_payment_id`**| `VARCHAR(100)` | `YES` | `NULL` | B-Tree Index | Provider's payment ID (e.g. `pay_LMN12345`). |
| 10 | **`gateway_order_id`** | `VARCHAR(100)` | `YES` | `NULL` | *None* | Provider's order reference ID (e.g. `order_KJH789`). |
| 11 | **`transaction_id`** | `VARCHAR(100)` | `YES` | `NULL` | *None* | Bank UTR / Transaction reference number. |
| 12 | **`status`** | `VARCHAR(20)` | `NOT NULL` | `'PENDING'` | B-Tree Index | Payment status: `'PENDING'`, `'PAID'`, `'FAILED'`, `'REFUNDED'`. |
| 13 | **`refund_reason`** | `TEXT` | `YES` | `NULL` | *None* | Reason recorded if payment is refunded. |
| 14 | **`created_at`** | `TIMESTAMPTZ` | `NOT NULL` | `CURRENT_TIMESTAMP` | *None* | Payment link creation timestamp. |
| 15 | **`paid_at`** | `TIMESTAMPTZ` | `YES` | `NULL` | *None* | Exact timestamp when payment was confirmed by webhook. |

* **Indexes**: `PRIMARY KEY (payment_id)`, `INDEX idx_payments_order_id (order_id)`, `INDEX idx_payments_gateway_id (gateway_payment_id)`.

---

### Table 5: `customer_reviews` (Post-Delivery Reviews & Ratings)
* **Primary Key**: `review_id` (`VARCHAR(36)` - UUID)
* **Foreign Key**: `order_id` $\rightarrow$ `customer_orders(order_id)`, `customer_phone` $\rightarrow$ `customer_profiles(customer_phone)`, `chef_phone` $\rightarrow$ `chef_profiles(chef_phone)`, `driver_phone` $\rightarrow$ `driver_profiles(driver_phone)`
* **Purpose**: Post-delivery review repository. Stores 1–5 star ratings and feedback submitted by customers after receiving their food (`submit_order_review_tool`). Holds separate ratings for home chef and delivery driver.

| # | Column Name | Data Type | Nullable? | Default Value | Foreign Key / Index | Description & Usage Rationale |
|---|---|---|---|---|---|---|
| 1 | **`review_id`** | `VARCHAR(36)` | `NOT NULL` | *UUID* | `PRIMARY KEY` | Unique surrogate ID for this review entry. |
| 2 | **`order_id`** | `VARCHAR(36)` | `NOT NULL` | `FK(customer_orders)`, B-Tree Index | Delivered order being reviewed. |
| 3 | **`customer_phone`** | `VARCHAR(15)` | `NOT NULL` | `FK(customer_profiles)`, B-Tree Index | Customer submitting the review. |
| 4 | **`chef_phone`** | `VARCHAR(15)` | `NOT NULL` | `FK(chef_profiles)`, B-Tree Index | Home chef being rated. |
| 5 | **`driver_phone`** | `VARCHAR(15)` | `YES` | `FK(driver_profiles)`, B-Tree Index | Delivery driver being rated. |
| 6 | **`chef_rating`** | `INTEGER` | `NOT NULL` | *None* | *None* | Star rating for food/chef (`1` to `5`). |
| 7 | **`driver_rating`** | `INTEGER` | `YES` | `NULL` | *None* | Star rating for delivery/driver (`1` to `5`). |
| 8 | **`review_text`** | `TEXT` | `YES` | `NULL` | *None* | Optional written feedback / comments. |
| 9 | **`is_public`** | `BOOLEAN` | `NOT NULL` | `true` | *None* | Visibility toggle for public chef reviews. |
| 10 | **`created_at`** | `TIMESTAMPTZ` | `NOT NULL` | `CURRENT_TIMESTAMP` | *None* | Review submission timestamp. |

* **Indexes**: `PRIMARY KEY (review_id)`, `INDEX idx_reviews_chef_rating (chef_phone, chef_rating)`, `INDEX idx_reviews_driver_rating (driver_phone, driver_rating)`.
