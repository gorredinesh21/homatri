# 📚 Homaatri Platform Research & System Design Notes

This document consolidates the engineering research, platform analyses, and architectural decisions compiled during the design and development of the **Homaatri** hyper-local food service platform (including both the conversational chatbot POC and the documentation website).

---

## 🌐 1. Hugging Face Serverless API & Domain Migration
*   **The Discovery:** The original domain `api-inference.huggingface.co` has been officially decommissioned by Hugging Face for direct chat/completions endpoints, leading to `[Errno -2] Name or service not known` resolution failures in local environments.
*   **The Resolution:** We migrated the LLM integration layer to the modern Hugging Face Inference Router:
    *   **Router Base URL:** `https://router.huggingface.co/v1/chat/completions`
    *   **API Protocol:** Fully OpenAI-compatible chat-completions JSON payload.
    *   **Availability:** Automatically routes tasks to active providers (such as Groq, Together, or Hugging Face's own backend cluster).
*   **DNS & Outbound Ping Diagnostics:**
    *   Outbound IP connections are verified by TCP pinging Cloudflare DNS (`1.1.1.1` on port 53).
    *   Local DNS servers fail-over parameters can be bypassed when offline by fetching record answers directly over HTTP via Cloudflare's DNS-over-HTTPS (DoH) API.

---

## 💬 2. WhatsApp Business API Integration Analysis
To deploy Homaatri in production, we analyzed the optimal integration path with the Meta WhatsApp Developer Network.

### A. Connectivity Options: Meta Cloud API vs. Twilio

| Parameter | Meta Cloud API (Direct) (Recommended) | Twilio API for WhatsApp |
| :--- | :--- | :--- |
| **Pricing Model** | Direct Meta conversation charges only. No setup fees. | Meta conversation charges + $0.005 per message surcharge. |
| **Feature Parity** | Instant access to WhatsApp Catalogs, Lists, and native WhatsApp Flows. | Delayed support for interactive menus and form components. |
| **Operational Control** | Full control over system users and webhook security signing. | Intermediated. Relies on Twilio console sandboxes. |
| **Webhook Latency** | Low (~30ms direct post). | Moderate (gateway routing overhead). |

### B. Interactive WhatsApp Formats for Zero Friction
To maximize conversions, the conversational flow is mapped into four distinct interactive features:
1.  **Lists (`interactive/list`):** Standard selection lists of today's active menu choices (limits of 10 choices, price previews).
2.  **Quick Replies:** Confirming checkout totals or pickup slots instantly using simple tap triggers.
3.  **WhatsApp Flows:** Dynamic forms rendering inside the WhatsApp interface (e.g. entering delivery address coordinates and times), eliminating external webpage loading.
4.  **WhatsApp Catalogs:** Providing search cards with images and cart items for standard meal plans.

---

## 🧠 3. Serverless LLM Comparison for Order Ingestion

We evaluated the primary AI models for parsing unstructured customer messages into database-ready JSON:

| Model | Input Cost / 1M | Output Cost / 1M | Avg Latency | Native Schema Enforcement |
| :--- | :--- | :--- | :--- | :--- |
| **Gemini 1.5 Flash (Recommended)** | $0.075 | $0.30 | ~0.8s | **Excellent** (Native Pydantic JSON Mode) |
| **Llama-3.3-70B-Instruct** | $0.50 | $0.80 | ~0.6s | **Excellent** (Via Hugging Face Router providers) |
| **Qwen/Qwen2.5-7B-Instruct** | $0.07 | $0.10 | ~0.4s | **Good** (Requires structured prompt controls) |
| **GPT-4o-mini** | $0.15 | $0.60 | ~1.1s | **Excellent** (Structured Outputs API) |

---

## 🗄️ 4. Relational Database & TSP Routing Design

### A. Schema Normalization
For clean transactional integrity, the Postgres layout isolates users by role (`USERS -> CUSTOMERS / CHEFS / DRIVERS`) and tracks order lifecycles through a historic state table:
*   **Capacity Control:** The `chefs` table tracks `max_daily_capacity` to prevent kitchen overhead.
*   **Consolidation Indexes:** Composite database indexes are added to `(chef_id, status)` to aggregate pending meal checklists rapidly at cutoff times.

### B. Logistics Routing Logic
At operating scales of 100–500 orders per day, dedicated vehicle-routing solvers represent excessive infrastructure cost. Homaatri implements a hybrid routing model:
1.  **Greedy TSP (Traveling Salesperson Problem):** Customer drop-off coordinates are sorted geographically inside a simple greedy path algorithm on order window cutoffs.
2.  **Google Maps URL Dispatch:** The optimized path is parsed into a Google Maps multi-destination routing link (e.g. `https://www.google.com/maps/dir/Kitchen_Coords/Cust1_Coords/Cust2_Coords`) and sent to the Rider's WhatsApp, leveraging native navigation.

---

## 📋 5. Presentation Demo Walkthrough Cases (Offline Mode)
For stable demonstrations, the system is equipped with deterministic fallbacks that match exact presentation scenario messages:
*   **Order creation test string:** `hey , my name is dinesh , i need 3 butter rotis , 1 jeer rice and i will also have 2 paaaneer batter musala`
*   **Time change modification request:** `hey , can i change the time of delivery , i want it at 8 30`
*   **Chef food update request:** `hey of the order is not picked up yet , can you ask the chef to add 2 more butter rotis`
