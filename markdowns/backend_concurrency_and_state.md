# ⚡ Homaatri Backend Concurrency, Execution & State Isolation

This document outlines the production execution architecture, concurrency model, and state isolation mechanics for **Homaatri**, explaining how a single server container processes thousands of concurrent WhatsApp messages without state cross-contamination.

---

## 🖥️ 1. What a "Server" & "Agent" Mean in Code Form

### A. The Server (`FastAPI`)
The backend server is a single, 24/7 Python application process running **FastAPI**. It exposes an HTTP endpoint (e.g., `/webhook`) that receives HTTP POST JSON payloads sent by Meta (WhatsApp Business API) whenever a user sends a message.

```python
from fastapi import FastAPI, Request

app = FastAPI()

@app.post("/webhook")
async def handle_whatsapp_webhook(request: Request):
    payload = await request.json()
    # Extract sender phone and text message
    phone_number = payload["entry"][0]["changes"][0]["value"]["messages"][0]["from"]
    message_text = payload["entry"][0]["changes"][0]["value"]["messages"][0]["text"]["body"]
    
    # Delegate to Agent Graph asynchronously
    await process_user_turn(phone_number, message_text)
    return {"status": "ok"}
```

### B. The Agent (LLM + System Prompt + Tools)
In code form, an **Agent** is not a separate physical process. It is a stateful Python execution node that:
1. Binds a System Prompt (e.g. *"You are the Homaatri Customer Agent..."*).
2. Loads conversation memory and database state for the user.
3. Calls Google Gemini 3.6 Flash via Vertex AI (`google-genai` SDK).
4. Executes Python database tools if requested by the LLM.
5. Returns a text string to be dispatched back to WhatsApp.

---

## 🔑 2. Identity & Onboarding Flow (Phone Number Auth)

In WhatsApp-native applications, the user's phone number (E.164 format, e.g. `+919876543210`) acts as their **immutable unique identifier**.

```
  Incoming Webhook (+919876543210)
                 │
                 ▼
  SELECT * FROM customer_profiles WHERE phone_number = '+919876543210'
                 │
        ┌────────┴────────┐
        │                 │
    [ Found ]       [ NOT Found ]
        │                 │
        ▼                 ▼
  Existing User      New User Onboarding
  Load active        Greet & request name,
  order context      delivery address, & GPS
```

---

## 📦 3. Containerization: Single Shared Container

- **Architecture**: A single server container (Docker) handles all incoming webhooks for all users.
- **Stateless Execution**: The server container itself holds no session state in RAM.
- **Database Persistence**: State is stored externally in PostgreSQL. After a user turn finishes (approx 300–800 ms), state is saved to PostgreSQL, freeing the server container to immediately process requests from other users.

---

## 🔀 4. Concurrency Execution & LangGraph State Isolation

When Customer A (short message) and Customer B (long message) send messages at the exact same millisecond:

```
          INCOMING WHATSAPP MESSAGES (20:00:00.000)
          Customer A (+919876...)  &  Customer B (+919999...)
                                │
                                ▼
                   FASTAPI SERVER /WEBHOOK
                                │
         ┌──────────────────────┴──────────────────────┐
         │ (Python asyncio creates 2 parallel Tasks)   │
         ▼                                             ▼
  [ TASK A ]                                    [ TASK B ]
  thread_id = "+919876..."                      thread_id = "+919999..."
  │                                             │
  ├─► Loads DB state for A                      ├─► Loads DB state for B
  ├─► Calls Gemini (small prompt)               ├─► Calls Gemini (large prompt)
  ├─► Receives Gemini response (0.4s)           │   (Gemini is still processing...)
  ├─► Sends WA reply to A                       │
  └─► FINISHED at 20:00:00.400                  ├─► Receives Gemini response (1.2s)
                                                ├─► Sends WA reply to B
                                                └─► FINISHED at 20:00:01.200
```

### LangGraph `thread_id` Mechanics:
LangGraph isolates memory and execution state per user by passing a `thread_id` config object on every graph invocation:

```python
# Customer A Task (Isolated)
config_a = {"configurable": {"thread_id": "+919876543210"}}
await langgraph_agent.ainvoke({"messages": [user_a_message]}, config=config_a)

# Customer B Task (Isolated)
config_b = {"configurable": {"thread_id": "+919999999999"}}
await langgraph_agent.ainvoke({"messages": [user_b_message]}, config=config_b)
```

Because `asyncio` runs each request in a separate memory stack, **Customer A and Customer B never mix up variables or conversation history**.
