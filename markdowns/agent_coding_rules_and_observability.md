# 🛡️ Defensive AI Agent Coding Rules & Observability Standard

This document establishes the **4 Golden Defensive Coding Rules** to prevent infinite loops, zombie states, and context corruption in Homaatri, along with the **LangSmith Observability Standard** for real-time tracing.

---

## 🔒 The 4 Golden Defensive AI Coding Rules

Whenever writing Python or LangGraph AI code for Homaatri, the following 4 rules MUST be strictly enforced:

### Rule 1: Enforce `return_direct=True` or Route Directly to `END`
- **Principle**: Once an action completes its business task (e.g. `generate_payment_link()` or `mark_gate_delivery_completed()`), execution MUST NOT be passed back to the LLM.
- **Implementation**: Set `return_direct=True` on terminal tool definitions or configure LangGraph conditional routing edges to transition directly to `END`.
- **Reason**: Eliminates infinite tool-retry loops and prevents unnecessary LLM follow-up turns.

### Rule 2: Implement Hard Pre-Condition Assertions in Python Tools
- **Principle**: Every Python backend tool function must validate database state pre-conditions *before* performing work.
- **Implementation**:
  ```python
  def mark_order_packed_ready(order_id: str, chef_id: str):
      order = get_order(order_id)
      if order.status != "COOKING":
          return f"Error: Order #{order_id} cannot be marked packed because status is '{order.status}', not 'COOKING'."
  ```
- **Reason**: If state is invalid, returning an explicit error string prevents the LLM from trying 5 different tool parameter variations.

### Rule 3: Use Strict Pydantic Tool Input Schemas
- **Principle**: Never allow an LLM to pass optional or unvalidated parameters into backend tools.
- **Implementation**: Define strict Pydantic schemas for every tool with explicit field descriptions and required parameters.
- **Reason**: Catches malformed LLM tool arguments at the schema boundary before database execution.

### Rule 4: Clean State Schema Hygiene (`TypedDict` / Pydantic)
- **Principle**: LangGraph state schemas must be explicitly typed and immutable per domain key.
- **Implementation**: Define explicit keys: `messages`, `sender_phone`, `current_role`, `active_order_id`, `is_interrupted`.
- **Reason**: Prevents accidental variable overwrites across graph node transitions.

---

## ⏳ Timeout Engine Safeguards

1. **`recursion_limit = 10`**: All LangGraph graph invocations must specify `recursion_limit: 10` to hard-kill any loop that exceeds 10 steps in a single turn.
2. **Checkpoint TTL (15-Minute Expiration)**: Every Human-in-the-Loop (`interrupt()`) state saved in PostgreSQL must specify an `expires_at` timestamp.
3. **Background Expiry Worker**: A background worker polls for expired checkpoints and resumes graph execution with a safe default fallback (`chef_approved = False`).

---

## 👁️ Observability & Tracing Standard (LangSmith)

All Homaatri environments MUST use **LangSmith** for 100% execution visibility.

### Environment Configuration:
```bash
LANGCHAIN_TRACING_V2=true
LANGCHAIN_API_KEY="ls__your_api_key_here"
LANGCHAIN_PROJECT="homatri-production"
```

### What LangSmith Traces:
- Visual graph execution trees (`CustomerAgent` $\rightarrow$ `MasterAgent` $\rightarrow$ `ChefAgent`).
- Prompt inputs, raw LLM completions, and token costs per turn.
- Tool input/output JSON payloads and execution spans.
- LangGraph `interrupt()` pause points and human resume payloads.
