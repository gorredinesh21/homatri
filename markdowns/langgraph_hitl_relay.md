# 🤝 LangGraph Human-In-The-Loop (HITL) & Agent Relay Architecture

This document details the **Agent-in-the-Loop & Human-in-the-Loop (HITL) Relay Pattern** for Homaatri, eliminating token bloat and context loss by replacing raw chat history passing with structured intent relay chains.

---

## ❌ 1. The Antipattern: Raw Chat History Broadcasting

Passing raw chat transcripts (e.g., last 10 messages) between agents suffers from three major flaws:

1. **Token Bloat**: Burning thousands of unnecessary LLM tokens on every turn by passing irrelevant conversation context.
2. **Context Fragmentation**: Critical order details fall out of the sliding window if unrelated messages (e.g., payment inquiries) occur in between.
3. **Noisy Context**: Specialists (e.g., Chefs or Drivers) are overwhelmed with irrelevant customer greeting/payment dialogue.

---

## ✅ 2. The Solution: Structured Intent Relay & LangGraph HITL

Instead of broadcasting raw text, agents extract **structured intent payloads** (JSON) attached to specific `order_id`s, while LangGraph manages state checkpoints and human interrupts (`interrupt()`).

```
┌──────────┐     "No garlic on #104"      ┌────────────────┐
│ CUSTOMER ├─────────────────────────────►│ CUSTOMER AGENT │
└──────────┘                              └───────┬────────┘
                                                  │ 1. Extract Intent: {"order_id": 104, "note": "No garlic"}
                                                  ▼
                                          ┌────────────────┐
                                          │  MASTER AGENT  │
                                          └───────┬────────┘
                                                  │ 2. Relay Payload & Wait
                                                  ▼
                                          ┌────────────────┐
                                          │   CHEF AGENT   │
                                          └───────┬────────┘
                                                  │ 3. Trigger interrupt() (Pause Execution)
                                                  ▼
┌──────────┐     WhatsApp Notification    ┌────────────────┐
│REAL CHEF ◄──────────────────────────────┤  STATE PAUSED  │
└────┬─────┘                              └────────────────┘
     │ 4. Chef Replies: "Sure, no garlic!"
     ▼
┌──────────────────┐
│   CHEF AGENT     │ (Resume Execution)
└────────┬─────────┘
         │ 5. Pass Confirmation
         ▼
┌──────────────────┐
│  MASTER AGENT    │
└────────┬─────────┘
         │ 6. Pass Confirmation
         ▼
┌──────────────────┐
│ CUSTOMER AGENT   │
└────────┬─────────┘
         │ 7. Notify Customer on WhatsApp
         ▼
┌──────────┐
│ CUSTOMER │ "Chef confirmed! No garlic will be added."
└──────────┘
```

---

## ⚙️ 3. Execution Lifecycle & LangGraph Mechanics

### Step 1: Intent Extraction (Customer Agent)
- Customer sends: *"Could you tell the chef not to add garlic to Order #104?"*
- Customer Agent parses the request into a clean JSON payload:
  ```json
  {
    "order_id": "ord_104",
    "target_role": "chef",
    "action": "custom_dietary_request",
    "details": "No garlic"
  }
  ```

### Step 2: Synchronous Master Relay
- Customer Agent sends payload to Master Agent and enters a waiting state.
- Master Agent verifies permissions and relays the payload directly to the Chef Agent.

### Step 3: LangGraph `interrupt()` (Human-in-the-Loop)
- Chef Agent receives the payload and calls LangGraph's `interrupt()` function.
- LangGraph freezes the execution state and saves a checkpoint to the database.
- An outbound WhatsApp message is sent to the real Chef:
  > *"Customer requested: 'No garlic' for Order #104. Reply YES to accept or NO to decline."*

### Step 4: Resume & Chain Reverse
- Real Chef replies on WhatsApp: *"YES"*.
- LangGraph catches the incoming WhatsApp webhook, loads the checkpoint, updates the state with `chef_approval = True`, and resumes execution.
- Chef Agent $\rightarrow$ Master Agent $\rightarrow$ Customer Agent $\rightarrow$ Customer WhatsApp message.

---

## 🎯 4. Key Homaatri Use Cases for HITL

1. **Mid-Cooking Custom Instructions**: Special dietary requests or allergen alerts sent to home-cook chefs.
2. **Order Cancellations / Refunds**: Customer requests refund after cutoff; requires Admin/Chef approval before money is returned.
3. **Delivery Address Clarifications**: Driver reports address unlocatable; system pauses trip and requests customer location update on WhatsApp.
