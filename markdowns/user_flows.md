# Homaatri — User Flows (Source of Truth for the Rebuild)

**Branch:** `homatri_1.0` · **Status:** living design doc · **Supersedes:** the old per-agent tool specs and the 40-tool file (never built).

This document captures the **execution flows** exactly as the founder walked through them. Tools are **derived from these flows** (see §12), not the other way around. The frozen bedrock (models/tables + 21 executors) stays; everything above it is rebuilt from here.

---

## 1. Tech stack (locked)
- **LLM:** Google **Vertex AI** via `langchain_google_vertexai.ChatVertexAI`.
- **Auth:** ADC / service-account (**no API key**).
- **Model:** **Gemini 3.6** (exact Vertex model ID pinned at wiring time).
- **Deploy target:** **GCP**.
- **Never** AWS / Bedrock.
- **Framework:** LangGraph (chosen specifically because the cross-domain waits need durable pause/resume — see §11).

---

## 2. Core architecture principles (locked)
1. **Orchestration, not choreography.** One central conductor — the **Master agent** — mediates every cross-domain interaction. No agent talks to another agent directly.
2. **Master is the operator, not a dumb pipe.** Master = COO / General Manager / Security Officer of Homaatri. It owns the cutoff clock, the payment gateway, route optimization, policy enforcement, delegation, and audit. Mediation is only *one* of its jobs.
3. **Agent-in-the-loop.** Cross-domain requests are human-in-the-loop where **Master plays the human/approver**, and can escalate to a **real human (Admin)** when needed. Admin is a separate system/persona (the founder), distinct from the Master AI.
4. **Least-privilege write delegation (locked).** Each agent may write **only its own tables**. Any cross-domain write is **delegated through Master** to the owning domain's executor (DW1/DW2/etc.) — never written directly. Bedrock already enforces single-owner writes.
5. **Legend used below:** 🛡️ guardrail · 🧠 LLM decision · 🔧 tool · 🔗 cross-domain relay (via Master) · ⏸️ pause & resume (interrupt + checkpoint) · ⏰ time-pool

---

## 3. Global invariants (locked)
- **⭐ Pending-State Rollback.** Any paused/pending operation (awaiting location, awaiting chef, awaiting payment) must **cleanly roll back** if it (a) **times out** or (b) is **superseded by a new inbound message**. Nothing half-done is ever left behind. → every paused turn sits inside a transaction + a discardable checkpoint.
- **No-input timeout.** If the user goes silent mid-operation → roll everything back + send *"Timed out — please restart, just say hi."*
- **New message wins (cleanly).** A new inbound while paused first rolls back the pending op, then serves the new request.
- **Cutoff clock (fixed):** Lunch **11:30 AM**, Dinner **6:30 PM**. Orders pulled/batched at cutoff.

---

## 4. Flow 1 — Customer onboarding
1. User: **"hi"** → 🛡️ Customer agent checks if the profile exists.
2. **Not registered** → 🔗 relay to **Master** → 🧠 Master sends a welcome / "register with us — share your details" message.
3. Customer sends **name + address** → 🔧 register function:
   - validates the basics arrived, then calls a **reusable `send_and_await_reply` primitive** → sends *"tap the clip and share your location pin"* → ⏸️ **pauses** until the pin arrives.
   - **No LLM in this step** — it's deterministic code sending a fixed message.
4. Location pin arrives → validate it's a proper lat/long → 🔧 save the full profile (name, address, lat/long).
5. **Auto-chain (no waiting):** the moment save completes, the function **automatically calls the next step itself** (find nearby kitchens) and proactively pushes the kitchen list — releasing its held turn, not waiting for the customer to type.
6. Kitchen list ideally includes **cuisine + chef rating**.

---

## 5. Flow 2 — Time-pool (⏰) at greeting / post-registration
Compute current time → decide what's orderable → message accordingly:

| Current time | Orderable window | Message framing |
|---|---|---|
| 00:00 – 11:30 | **Today's LUNCH** | show lunch kitchens/menu |
| 11:30 – 18:30 | **Today's DINNER** | *"Lunch is closed — order for tonight's dinner."* |
| 18:30 – 24:00 | **Tomorrow's LUNCH** | *"Today's orders are closed — order for tomorrow's lunch."* |

---

## 6. Flow 3 — Browse & order
1. Customer picks a chef → 🔧 view menu (dishes, price, availability).
2. Customer lists items → 🧠 compute the bill (subtotal + delivery fee) → 🔧 create order header + items (⏰ cutoff re-check) in the DB.

---

## 7. Flow 4 — Payment (Master owns the gateway, both legs)
1. Customer agent → 🔗 **Master**: *"initiate payment for ₹X."*
2. **Master** mints the **Razorpay** link → relays it back to the customer agent.
3. Customer agent → user: sends the link → ⏸️ **pauses**, waiting on Master's confirmation.
4. User pays → Razorpay webhook → **Master** (Master receives it) → Master relays confirmation → customer agent **resumes & confirms**.
5. Confirm triggers the write cascade (DW2 payment→PAID → DW1 order→CONFIRMED). **Order placed.**

> Principle: the customer **never** touches Razorpay. Master owns minting **and** the webhook.

---

## 8. Flow 5 — Cutoff & batch (Master core engine)
At the cutoff (11:30 / 6:30), Master:
1. **Locks** the meal window (`LOCKED_PROCESSING`).
2. **Allocates a driver** — **1 chef → 1 driver per window** (fixed for now; ⭐ keep flexible for multiple chefs per driver / reassignment later). No live driver-GPS tracking.
3. Calls **Google Maps route optimization once**, saves the full route (stop sequence, lat/long) to the DB. Navigation is computed from the **stored stop coordinates** — current stop → next stop (chef location + delivery addresses), not live GPS.
4. Transitions batched orders → `BATCHED`, dispatches checklists to chef and the route to the driver.

---

## 9. Flow 6 — Chef cooking + dietary negotiation
1. At cutoff the chef receives, **order-wise first** (priority): Order 1 → items + delivery address + who; Order 2 → …; with a 🧠 **consolidated cook-summary at the bottom** (e.g. *"2 paneer-butter-masala, 1 rice, 1 chapati"*) for easy cooking.
2. **Mid-cook custom request** ("no garlic") = **agent-in-the-loop**:
   - customer → 🔧 customer agent → 🔗 **Master ↔ Chef agent** → chef responds → Master relays back → ⏸️ waiting customer resumes.
   - Chef outcomes: **accept** / **hard reject** / **counter-offer** (relayed).
   - **⭐ Hard cap: max 2 turns.** (Model as a bounded negotiation / Contract-Net-style protocol.)
   - Default if unresolved after 2 turns: **keep the original order** (custom request dropped).

---

## 10. Flow 7 — Driver delivery
1. Route is pre-computed & stored (Flow 5). Driver gets the whole route at assignment, **but only the next leg is surfaced** to avoid overload.
2. Driver reaches chef → *"picked up."*
3. System reveals the **next leg**: Maps link current→next stop + the **per-order instructions** for that stop (full detail, never the aggregate) + which orders drop there.
4. On *"delivered here"*: all orders at that gate flip to `DELIVERED` together (**multi-order gate**); **but** a specific order can be marked **not delivered individually** (exception).
5. Mid-route the driver may need **chef** (e.g. before pickup) or **customer** (e.g. address not found) → always 🔗 **Master-mediated**, same wait-on-checkpoint protocol.

---

## 11. The cross-domain relay pattern (the mechanism)
Every 🔗 relay works the same way:
> A tool (may use the LLM to compose the message) hands off to Master → Master routes to the target agent → **the graph PAUSES via `interrupt()` + saves a checkpoint** (the origin thread is frozen) → when the reply arrives as a *new inbound WhatsApp event*, the graph **RESUMES** the frozen thread.

The "wait" is a **durable checkpoint**, never a blocked function call — because replies can arrive minutes/hours later. This is why LangGraph + a Postgres checkpointer are required (the piece the old build was missing entirely).

---

## 12. How we derive the tools (method — next step)
Tools are **not** guessed. For each flow above:
1. Draw a **sequence diagram** (who calls whom, in order, incl. every pause/resume) → each arrow reveals a **tool** + its **owning agent**.
2. Accumulate every tool into one **dependency graph** (edge = "triggers / needs output of").
3. Find **Strongly Connected Components** (cyclic clusters like the dietary loop) — those are **designed together**; condense → topological order = the **cycle-aware build order**.

Output of this step → per-agent tool inventories (Customer, Chef, Driver, Master) with dependencies, then build.

---

## 13. Resolved decisions (2026-08-03)
- **Cutoffs fixed:** Lunch **11:30 AM**, Dinner **6:30 PM** → §3, §5.
- **Driver allocation:** stays **1 chef : 1 driver per window**; navigation uses stored stop coordinates (current → next delivery address), **no live GPS** → §8.
- **Dietary negotiation default:** **keep the original order** if unresolved after 2 turns → §9.
- **Write model:** **least-privilege delegation** — each agent writes only its own tables; cross-domain writes are delegated through Master → §2.4.
- **Time-pool:** exact bracket → message table locked → §5.
