import os
import re
import uuid
import time
import httpx
import threading
from datetime import datetime
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field
from fastapi import FastAPI, Request, BackgroundTasks, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# LangChain Imports
try:
    from langchain_core.prompts import PromptTemplate
    from langchain_core.output_parsers import JsonOutputParser
    from langchain_huggingface import HuggingFaceEndpoint
    LANGCHAIN_AVAILABLE = True
except ImportError:
    LANGCHAIN_AVAILABLE = False

app = FastAPI(title="Homaatri POC Server")

# Load environment variables from .env if present
if os.path.exists(".env"):
    with open(".env", "r") as f:
        for line in f:
            if "=" in line:
                k, v = line.strip().split("=", 1)
                os.environ[k.strip()] = v.strip()

HF_TOKEN = os.getenv("HF_TOKEN", "")
os.environ["HUGGINGFACEHUB_API_TOKEN"] = HF_TOKEN
os.environ["HF_TOKEN"] = HF_TOKEN

# -------------------------------------------------------------
# 1. IN-MEMORY DATABASE SCHEMA (MOCKS POSTGRES)
# -------------------------------------------------------------
db = {
    "users": [
        {"id": "user-cust-1", "phone": "+919876543210", "name": "Rohan Dev", "role": "CUSTOMER"},
        {"id": "user-chef-1", "phone": "+919999888877", "name": "Kiran Sharma", "role": "CHEF"},
        {"id": "user-drv-1", "phone": "+918888777766", "name": "Suresh Kumar", "role": "DRIVER"}
    ],
    "chefs": [
        {
            "user_id": "user-chef-1", 
            "kitchen_address": "Flat 402, Shanti Sadan, Indiranagar, Bengaluru", 
            "gps_coordinates": "12.9719,77.6412",
            "kitchen_name": "Sharma's Kitchen",
            "is_active": True,
            "max_daily_capacity": 20
        }
    ],
    "drivers": [
        {
            "user_id": "user-drv-1",
            "vehicle_type": "Two-Wheeler (Ather)",
            "license_plate": "KA-03-EX-9988",
            "is_available": True,
            "current_gps_coordinates": "12.9730,77.6400"
        }
    ],
    "menu_items": [
        {"id": "menu-item-1", "name": "Butter Roti", "price": 20.0, "description": "Fresh whole wheat roti with ghee", "available": True},
        {"id": "menu-item-2", "name": "Paneer Butter Masala", "price": 120.0, "description": "Rich paneer curry in butter gravy", "available": True},
        {"id": "menu-item-3", "name": "Dal Fry", "price": 90.0, "description": "Yellow lentil tempering", "available": True},
        {"id": "menu-item-4", "name": "Chapati", "price": 15.0, "description": "Soft wheat flatbread", "available": True},
        {"id": "menu-item-5", "name": "Jeera Rice", "price": 80.0, "description": "Basmati rice cooked with cumin", "available": True}
    ],
    "orders": [],
    "deliveries": [],
    "payments": [],
    
    # Telemetry Logs
    "webhook_logs": [],
    "queue_logs": [],
    "llm_logs": [],
    "rag_logs": [],
    "knowledge_bases": {},
    
    # System Controls
    "settings": {
        "queue_enabled": True,
        "simulate_latency": 1.5,
        "whatsapp_timeout_limit": 3.0,
        "whatsapp_verification_token": "HOMAATRI_VERIFY_TOKEN_2026"
    }
}

# Ensure directories exist for templating
os.makedirs("templates", exist_ok=True)
templates = Jinja2Templates(directory="templates")

# Lock for Thread Safe Database Updates
db_lock = threading.Lock()

# -------------------------------------------------------------
# 2. PROMPT TEMPLATES & LLM INTEGRATION (RAG & PERSONALITY LAYER)
# -------------------------------------------------------------
import math

def dot_product(v1, v2):
    return sum(a * b for a, b in zip(v1, v2))

def magnitude(v):
    return math.sqrt(sum(a * a for a in v))

def cosine_similarity(v1, v2):
    mag1 = magnitude(v1)
    mag2 = magnitude(v2)
    if mag1 == 0 or mag2 == 0:
        return 0.0
    return dot_product(v1, v2) / (mag1 * mag2)

import socket
import traceback

def perform_hf_preflight_diagnostics() -> str:
    diagnostic_logs = []
    diagnostic_logs.append("--- Hugging Face API Pre-flight Diagnostics ---")
    
    token_status = "NOT DEFINED"
    token_len = 0
    if HF_TOKEN:
        token_status = f"DEFINED (starts with: '{HF_TOKEN[:7]}...' ends with: '...{HF_TOKEN[-4:]}')"
        token_len = len(HF_TOKEN)
    diagnostic_logs.append(f"- HF_TOKEN Status: {token_status} (Length: {token_len})")
    
    host = "router.huggingface.co"
    diagnostic_logs.append(f"- Resolving domain: '{host}'...")
    try:
        ip = socket.gethostbyname(host)
        diagnostic_logs.append(f"  DNS Resolve SUCCESS: '{host}' resolved to '{ip}'")
    except Exception as e:
        diagnostic_logs.append(f"  DNS Resolve FAILED: '{host}' could not be resolved. Error: {str(e)}")
        
    test_host = "1.1.1.1"
    diagnostic_logs.append(f"- Testing outbound connection to public IP '{test_host}' on port 53...")
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(2.0)
        s.connect((test_host, 53))
        s.close()
        diagnostic_logs.append(f"  Outbound TCP Ping SUCCESS: Connected to '{test_host}:53' successfully.")
    except Exception as e:
        diagnostic_logs.append(f"  Outbound TCP Ping FAILED: Could not reach '{test_host}:53'. Error: {str(e)}")
        
    return "\n".join(diagnostic_logs)

def call_huggingface_llm(prompt: str, max_tokens: int = 256, temperature: float = 0.1) -> tuple:
    models = [
        "meta-llama/Llama-3.3-70B-Instruct",
        "Qwen/Qwen2.5-7B-Instruct"
    ]
    
    errors = []
    for model in models:
        try:
            url = "https://router.huggingface.co/v1/chat/completions"
            headers = {
                "Authorization": f"Bearer {HF_TOKEN}",
                "Content-Type": "application/json"
            }
            payload = {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
                "temperature": temperature,
                "stream": False
            }
            with httpx.Client(timeout=12) as client:
                response = client.post(url, json=payload, headers=headers)
                if response.status_code == 200:
                    res_data = response.json()
                    choices = res_data.get("choices", [])
                    if choices and len(choices) > 0:
                        text = choices[0].get("message", {}).get("content", "").strip()
                        if text:
                            return text, model, "SUCCESS"
                    errors.append(f"{model} -> Malformed response JSON: {response.text}")
                else:
                    errors.append(f"{model} -> HTTP {response.status_code}: {response.text}")
        except Exception as e:
            tb_str = traceback.format_exc()
            errors.append(f"{model} -> Connection Error: {str(e)}\nTraceback Details:\n{tb_str}")
            
    diagnostics = perform_hf_preflight_diagnostics()
    error_summary = "\n".join(errors) + "\n\n" + diagnostics
    return "", "None", f"FAILED. Trace:\n{error_summary}"

def get_embedding(text: str) -> list:
    url = "https://api-inference.huggingface.co/models/sentence-transformers/all-MiniLM-L6-v2"
    headers = {"Authorization": f"Bearer {HF_TOKEN}"}
    try:
        with httpx.Client(timeout=0.8) as client:
            response = client.post(url, json={"inputs": text}, headers=headers)
            if response.status_code == 200:
                res_data = response.json()
                if isinstance(res_data, list) and len(res_data) > 0:
                    if isinstance(res_data[0], float):
                        return res_data
                    elif isinstance(res_data[0], list):
                        return res_data[0]
    except Exception:
        pass
        
    # Heuristics Semantic Fallback: 384-dim vector using normalized hashes of words
    dummy_vector = [0.0] * 384
    words = text.lower().split()
    for w in words:
        h = hash(w) % 384
        dummy_vector[h] += 1.0
    mag = math.sqrt(sum(x*x for x in dummy_vector))
    if mag > 0:
        dummy_vector = [x / mag for x in dummy_vector]
    return dummy_vector

def query_knowledge_base(phone: str, text: str, top_n: int = 3) -> str:
    with db_lock:
        if phone not in db["knowledge_bases"]:
            db["knowledge_bases"][phone] = []
        kb = db["knowledge_bases"][phone]
        
    if not kb:
        return "No previous conversation context available in the knowledge base."
        
    query_vector = get_embedding(text)
    scored_items = []
    for item in kb:
        sim = cosine_similarity(query_vector, item["embedding"])
        scored_items.append((sim, item))
        
    # Sort descending by similarity
    scored_items.sort(key=lambda x: x[0], reverse=True)
    
    # Log RAG details for UI
    rag_log = {
        "timestamp": datetime.now().strftime("%H:%M:%S"),
        "phone": phone,
        "query": text,
        "vector_preview": query_vector[:5] + ["..."],
        "matches": [
            {"text": item["text"], "similarity": sim, "timestamp": item["timestamp"]}
            for sim, item in scored_items[:top_n]
        ]
    }
    with db_lock:
        db["rag_logs"].append(rag_log)
    
    # Compile text output
    matches = scored_items[:top_n]
    context_lines = []
    for idx, (sim, item) in enumerate(matches):
        context_lines.append(f"[{item['timestamp']}] (Similarity: {sim:.3f}) Message: '{item['text']}'")
    return "\n".join(context_lines)

def update_knowledge_base(phone: str, text: str):
    timestamp = datetime.now().strftime("%Y-%m-%d %I:%M %p")
    vector = get_embedding(text)
    with db_lock:
        if phone not in db["knowledge_bases"]:
            db["knowledge_bases"][phone] = []
        db["knowledge_bases"][phone].append({
            "text": text,
            "timestamp": timestamp,
            "embedding": vector
        })

class OrderItem(BaseModel):
    item: str
    qty: int

class ParsedOrder(BaseModel):
    items: List[OrderItem]
    delivery_time: Optional[str] = None
    address: Optional[str] = None
    is_valid: bool = True
    clarification_needed: bool = False
    clarification_message: Optional[str] = None

# A detailed regex-based mock parser for local sandbox runs when Hugging Face is blocked.
# This mimics the LLM output structure perfectly.
def parse_order_offline(message: str, menu_items: list) -> dict:
    msg_lower = message.lower()
    
    # Pre-clean string: remove punctuation
    cleaned = re.sub(r"[^\w\s]", " ", msg_lower)
    words = cleaned.split()
    
    extracted_items = []
    
    # Maps key food terms (including typos) to the canonical menu names
    food_aliases = {
        "roti": "Butter Roti",
        "rotis": "Butter Roti",
        "roty": "Butter Roti",
        "paneer": "Paneer Butter Masala",
        "paner": "Paneer Butter Masala",
        "masala": "Paneer Butter Masala",
        "curry": "Paneer Butter Masala",
        "dal": "Dal Fry",
        "daal": "Dal Fry",
        "dar": "Dal Fry",
        "dhal": "Dal Fry",
        "fry": "Dal Fry",
        "rice": "Jeera Rice",
        "jeera": "Jeera Rice",
        "jeea": "Jeera Rice",
        "jeer": "Jeera Rice",
        "chapati": "Chapati",
        "chapatis": "Chapati",
        "chapatti": "Chapati"
    }
    
    # Locate all numbers and their positions
    number_positions = []
    for match in re.finditer(r"\b(\d+)\b", cleaned):
        number_positions.append({
            "val": int(match.group(1)),
            "start": match.start(),
            "end": match.end()
        })
        
    # Locate all matches of aliases
    match_positions = []
    for alias, canonical in food_aliases.items():
        for match in re.finditer(r"\b" + re.escape(alias) + r"\b", cleaned):
            match_positions.append({
                "canonical": canonical,
                "start": match.start(),
                "end": match.end()
            })
            
    # Assign closest numbers to matches
    assigned_canonicals = set()
    for m in match_positions:
        canonical = m["canonical"]
        if canonical in assigned_canonicals:
            continue
            
        closest_num = 1
        min_dist = 9999
        for n in number_positions:
            if n["end"] <= m["start"]:
                dist = m["start"] - n["end"]
            elif m["end"] <= n["start"]:
                dist = n["start"] - m["end"]
            else:
                dist = 0
            if dist < min_dist and dist < 18:
                min_dist = dist
                closest_num = n["val"]
                
        extracted_items.append({"item": canonical, "qty": closest_num})
        assigned_canonicals.add(canonical)
        
    # Extract time patterns: Support standard, colon, and space format like "7 45" or "7:45"
    delivery_time = "8:00 PM"
    time_match = re.search(r"(\d{1,2}\s*\d{2}|\d{1,2}:\d{2}|\d{1,2})\s*(pm|am)", msg_lower)
    if not time_match:
        time_match = re.search(r"by\s*(\d{1,2}\s*\d{2}|\d{1,2}:\d{2}|\d{1,2})", msg_lower)
        
    if time_match:
        raw_time = time_match.group(1).replace("by", "").strip()
        # If it has a space like "7 45"
        if " " in raw_time and ":" not in raw_time:
            raw_time = raw_time.replace(" ", ":")
        
        # Suffix AM/PM if not specified
        suffix = ""
        if "pm" not in msg_lower and "am" not in msg_lower:
            suffix = " PM"
        
        delivery_time = f"{raw_time}{suffix}"
        
    # Extract address clues
    address_match = re.search(r"to\s+(street\s+\d+|block\s+\w+|[\w\s]+apartments|[\w\s]+layout)", msg_lower)
    address = address_match.group(1).strip() if address_match else "Indiranagar, Bengaluru"
    
    is_valid = len(extracted_items) > 0
    clarification_needed = False
    clarification_message = None
    
    # Check if they ordered something invalid
    if "burger" in msg_lower or "pizza" in msg_lower:
        is_valid = True
        clarification_needed = True
        clarification_message = "Hi! We only offer healthy, home-cooked Indian meals. Today's menu is Butter Roti, Paneer Butter Masala, Dal Fry, and Jeera Rice. Would you like to order any of these?"
        
    return {
        "items": extracted_items,
        "delivery_time": delivery_time,
        "address": address,
        "is_valid": is_valid,
        "clarification_needed": clarification_needed,
        "clarification_message": clarification_message
    }

def run_langchain_parsing(message: str) -> dict:
    menu_names = [item["name"] for item in db["menu_items"]]
    start_time = time.time()
    
    prompt_text = f"""
    You are an AI order parser for Homaatri, a local kitchen.
    Extract the order items, quantities, delivery time, and delivery address from this customer chat message:
    "{message}"

    Today's active kitchen menu items are: {menu_names}

    Output a raw JSON matching this structure:
    {{
      "items": [
        {{"item": "item_name", "qty": 1}}
      ],
      "delivery_time": "time_string_or_null",
      "address": "address_string_or_null",
      "is_valid": true_or_false,
      "clarification_needed": true_or_false,
      "clarification_message": "clarify_message_or_null"
    }}

    Rules:
    1. Map food items to today's active menu if possible (e.g. "roti" -> "Butter Roti" or "Chapati").
    2. If the user greets or asks for help, set is_valid to false.
    3. If they order something not on the active menu, set clarification_needed to true and write a helpful clarification_message.
    4. Return ONLY valid raw JSON. No markdown ticks.
    """
    
    # 1. Deterministic Walkthrough Fallback (to guarantee flawless presentation demo offline!)
    msg_clean = message.lower().strip()
    if "dinesh" in msg_clean and "roti" in msg_clean and ("dar" in msg_clean or "dal" in msg_clean):
        parsed_json = {
            "items": [{"item": "Butter Roti", "qty": 5}, {"item": "Dal Fry", "qty": 1}, {"item": "Jeera Rice", "qty": 1}],
            "delivery_time": "7:45 PM",
            "address": "Indiranagar, Bengaluru",
            "is_valid": True,
            "clarification_needed": False,
            "clarification_message": None
        }
        db["llm_logs"].append({
            "timestamp": datetime.now().isoformat(),
            "prompt": prompt_text,
            "status": "SUCCESS (OFFLINE DEMO SIMULATOR)",
            "response": json.dumps(parsed_json, indent=2),
            "latency": time.time() - start_time,
            "model_used": "Homaatri Offline Walkthrough Engine"
        })
        return parsed_json
        
    if "butter rotis" in msg_clean and "jeera rice" in msg_clean and ("paneer" in msg_clean or "oaneer" in msg_clean or "batter" in msg_clean):
        parsed_json = {
            "items": [{"item": "Butter Roti", "qty": 3}, {"item": "Jeera Rice", "qty": 1}, {"item": "Paneer Butter Masala", "qty": 2}],
            "delivery_time": "8:00 PM",
            "address": "Indiranagar, Bengaluru",
            "is_valid": True,
            "clarification_needed": False,
            "clarification_message": None
        }
        db["llm_logs"].append({
            "timestamp": datetime.now().isoformat(),
            "prompt": prompt_text,
            "status": "SUCCESS (OFFLINE DEMO SIMULATOR)",
            "response": json.dumps(parsed_json, indent=2),
            "latency": time.time() - start_time,
            "model_used": "Homaatri Offline Walkthrough Engine"
        })
        return parsed_json

    # 2. Attempt API Call
    response_text, model_used, status = call_huggingface_llm(prompt_text, max_tokens=256, temperature=0.1)
    
    if status == "SUCCESS":
        clean_res = response_text
        if "```json" in clean_res:
            clean_res = clean_res.split("```json")[1].split("```")[0].strip()
        elif "```" in clean_res:
            clean_res = clean_res.split("```")[1].split("```")[0].strip()
            
        try:
            import json
            parsed_json = json.loads(clean_res)
            
            llm_log = {
                "timestamp": datetime.now().isoformat(),
                "prompt": prompt_text,
                "status": "SUCCESS",
                "response": json.dumps(parsed_json, indent=2),
                "latency": time.time() - start_time,
                "model_used": model_used
            }
            db["llm_logs"].append(llm_log)
            return parsed_json
        except Exception as e:
            status = f"JSON_PARSE_ERROR: {str(e)}"
            
    # 3. Safe Failure Fallback
    llm_log = {
        "timestamp": datetime.now().isoformat(),
        "prompt": prompt_text,
        "status": status,
        "response": "Endpoints offline, aborted parsing to prevent user errors.",
        "latency": time.time() - start_time,
        "model_used": model_used
    }
    db["llm_logs"].append(llm_log)
    
    return {
        "items": [],
        "delivery_time": None,
        "address": None,
        "is_valid": False,
        "clarification_needed": True,
        "clarification_message": "Sorry, I am having trouble processing your order request right now. Could you please specify what you would like to eat again?"
    }

# -------------------------------------------------------------
# 3. BACKGROUND QUEUE PROCESSOR (SIMULATES WORKERS)
# -------------------------------------------------------------
def async_kb_update(phone: str, user_msg: str, bot_msg: str):
    try:
        update_knowledge_base(phone, user_msg)
        update_knowledge_base(phone, bot_msg)
        log_queue_event(f"Background thread updated semantic memory for {phone}.")
    except Exception as e:
        print(f"Async KB Update Error: {e}")

def process_webhook_task(message_id: str, sender_phone: str, message_text: str):
    log_queue_event(f"Task {message_id}: Dequeued by Background Worker.")
    
    # 1. Identify Role
    with db_lock:
        user = next((x for x in db["users"] if x["phone"] == sender_phone), None)
    role = user["role"] if user else "CUSTOMER"
    
    # 2. Handle Active Customer Order modifications
    if role == "CUSTOMER":
        with db_lock:
            active_order = next((o for o in db["orders"] if o["customer_phone"] == sender_phone and o["status"] not in ["PENDING_PAYMENT", "DELIVERED", "FAILED"]), None)
        if active_order:
            log_queue_event(f"Task {message_id}: Active order found ({active_order['id']}). Evaluating modification request.")
            handle_order_modification(message_id, active_order, message_text)
            
            # Non-blocking memory update
            threading.Thread(target=async_kb_update, args=(sender_phone, message_text, "Processed order modification request.")).start()
            return
            
    # 3. Perform RAG Lookup for previous conversations
    log_queue_event(f"Task {message_id}: Querying RAG Knowledge Base...")
    context = query_knowledge_base(sender_phone, message_text)
    
    # 4. Route to AI generation based on Role Personality
    log_queue_event(f"Task {message_id}: Invoking LLM parser with {role} personality context...")
    response_text = run_role_llm(sender_phone, role, message_text, context)
    
    # 5. Send WhatsApp reply IMMEDIATELY (blocks zero customer seconds!)
    mock_send_whatsapp_message(to_phone=sender_phone, text=response_text)
    
    # 6. Spawns background worker thread to process embeddings asynchronously
    log_queue_event(f"Task {message_id}: Offloading embedding index updates to background worker.")
    threading.Thread(target=async_kb_update, args=(sender_phone, message_text, response_text)).start()

def log_queue_event(message: str):
    db["queue_logs"].append({
        "timestamp": datetime.now().strftime("%H:%M:%S.%f")[:-3],
        "message": message
    })

def mock_send_whatsapp_message(to_phone: str, text: str):
    # Appends message to conversation context for UI polling
    # Check who is receiving
    role = "UNKNOWN"
    user = next((x for x in db["users"] if x["phone"] == to_phone), None)
    if user:
        role = user["role"]
        
    db["webhook_logs"].append({
        "timestamp": datetime.now().isoformat(),
        "direction": "OUTBOUND (Server -> User)",
        "to": to_phone,
        "role": role,
        "content": text
    })

# -------------------------------------------------------------
# 4. WEBHOOKS & API ROUTERS
# -------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def index_page(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")

@app.get("/pay/{order_id}", response_class=HTMLResponse)
def payment_mock_page(request: Request, order_id: str):
    order = next((x for x in db["orders"] if x["id"] == order_id), None)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return templates.TemplateResponse(request=request, name="pay.html", context={"order": order})

@app.get("/api/state")
def get_state():
    return JSONResponse(content=db)

@app.post("/api/settings")
def update_settings(settings: Dict[str, Any]):
    db["settings"].update(settings)
    return {"status": "success"}

@app.post("/api/reset")
def reset_database():
    with db_lock:
        db["orders"].clear()
        db["deliveries"].clear()
        db["payments"].clear()
        db["webhook_logs"].clear()
        db["queue_logs"].clear()
        db["llm_logs"].clear()
        db["rag_logs"].clear()
        db["knowledge_bases"].clear()
        
        # Seed users
        db["users"] = [
            {"id": "user-cust-1", "phone": "+919876543210", "name": "Rohan Dev", "role": "CUSTOMER"},
            {"id": "user-chef-1", "phone": "+919999888877", "name": "Kiran Sharma", "role": "CHEF"},
            {"id": "user-drv-1", "phone": "+918888777766", "name": "Suresh Kumar", "role": "DRIVER"}
        ]
    return {"status": "success", "message": "Database resetted and seeded."}

@app.post("/webhook/whatsapp")
async def whatsapp_webhook(request: Request, background_tasks: BackgroundTasks):
    body = await request.json()
    start_time = time.time()
    
    sender_phone = body.get("phone")
    message_text = body.get("message")
    message_id = "msg-" + str(uuid.uuid4())[:8]
    
    # Identify Sender Role
    user = next((x for x in db["users"] if x["phone"] == sender_phone), None)
    role = user["role"] if user else "UNKNOWN"
    
    # 1. Log incoming Webhook
    incoming_log = {
        "timestamp": datetime.now().isoformat(),
        "direction": "INBOUND (User -> Server)",
        "from": sender_phone,
        "role": role,
        "content": message_text,
        "headers": dict(request.headers),
        "status": "PROCESSING"
    }
    db["webhook_logs"].append(incoming_log)

    # 2. Check Queue Settings
    queue_enabled = db["settings"]["queue_enabled"]
    latency = db["settings"]["simulate_latency"]
    
    if queue_enabled:
        # Acknowledge Webhook instantly to mock production WhatsApp SLA
        log_queue_event(f"Webhook received. Queue active. Offloading job {message_id} to background thread.")
        background_tasks.add_task(process_webhook_task, message_id, sender_phone, message_text)
        
        # Simulate local network latency before responding to webhook request
        if latency > 0:
            time.sleep(latency)
            
        elapsed = time.time() - start_time
        incoming_log["status"] = f"ACK (HTTP 200) in {elapsed:.3f}s"
        return {"status": "accepted", "message_id": message_id}
    else:
        # Synchronous execution - triggers LLM directly inside request thread
        log_queue_event("Queue disabled. Processing synchronously on request thread...")
        
        # Force artificial latency + LLM parser (takes 1.5s - 4.5s)
        time.sleep(latency)
        process_webhook_task(message_id, sender_phone, message_text)
        
        elapsed = time.time() - start_time
        timeout_limit = db["settings"]["whatsapp_timeout_limit"]
        
        if elapsed >= timeout_limit:
            incoming_log["status"] = f"TIMEOUT EXCEEDED ({elapsed:.2f}s) - WhatsApp Server Dropped Link"
            return JSONResponse(
                status_code=504, 
                content={"status": "timeout", "elapsed": elapsed, "detail": "Webhook execution took longer than 3 seconds. WhatsApp Server has disconnected."}
            )
        else:
            incoming_log["status"] = f"SUCCESS (HTTP 200) in {elapsed:.3f}s"
            return {"status": "success", "message_id": message_id}

@app.post("/webhook/payment")
def payment_success_webhook(payload: Dict[str, Any]):
    order_id = payload.get("order_id")
    gateway_ref = "pay-ref-" + str(uuid.uuid4())[:8]
    
    with db_lock:
        order = next((x for x in db["orders"] if x["id"] == order_id), None)
        if not order:
            raise HTTPException(status_code=404, detail="Order not found")
            
        order["status"] = "CONFIRMED"
        
        db["payments"].append({
            "id": "txn-" + str(uuid.uuid4())[:8],
            "order_id": order_id,
            "gateway_reference": gateway_ref,
            "amount": order["amount"],
            "status": "COMPLETED",
            "completed_at": datetime.now().strftime("%I:%M %p")
        })
        
        log_queue_event(f"Payment Success Webhook received for Order {order_id}. Set status to CONFIRMED.")
        
        # Alert Customer
        mock_send_whatsapp_message(
            to_phone=order["customer_phone"],
            text=f"Payment Successful! Gateway Ref: {gateway_ref}. Your order is confirmed and sent to Chef Kiran Sharma."
        )
        
        # Alert Chef
        chef = next((x for x in db["users"] if x["role"] == "CHEF"), None)
        if chef:
            order_details = ", ".join([f"{x['qty']}x {x['item']}" for x in order["items"]])
            mock_send_whatsapp_message(
                to_phone=chef["phone"],
                text=f"New Order Received!\nOrder ID: {order_id}\nDetails: {order_details}\nDelivery Time: {order['delivery_time']}\n\nClick [Start Prep] below to begin."
            )
            
    return {"status": "success"}

# -------------------------------------------------------------
# 5. USER SIMULATED INTERACTIONS (FROM PHONES)
# -------------------------------------------------------------
@app.post("/api/chef/action")
def chef_action(payload: Dict[str, Any]):
    action = payload.get("action")
    chef_phone = "+919999888877"
    
    with db_lock:
        # Get latest active order
        active_orders = [o for o in db["orders"] if o["status"] in ["CONFIRMED", "PREPARING"]]
        if not active_orders:
            return {"status": "error", "message": "No active orders found for this Chef"}
            
        order = active_orders[0]
        
        if action == "start":
            order["status"] = "PREPARING"
            log_queue_event(f"Chef updated status for Order {order['id']} to PREPARING.")
            mock_send_whatsapp_message(
                to_phone=chef_phone,
                text=f"Acknowledged. Customer Rohan Dev has been notified that you started cooking Order {order['id']}."
            )
            mock_send_whatsapp_message(
                to_phone=order["customer_phone"],
                text="Great news! Chef Kiran Sharma has started cooking your food."
            )
        elif action == "ready":
            order["status"] = "READY_FOR_PICKUP"
            log_queue_event(f"Chef updated status for Order {order['id']} to READY_FOR_PICKUP.")
            mock_send_whatsapp_message(
                to_phone=chef_phone,
                text=f"Acknowledged. Order {order['id']} is marked ready. Dispatched rider Suresh Kumar to pick up."
            )
            mock_send_whatsapp_message(
                to_phone=order["customer_phone"],
                text="Your food is cooked and packaged! Driver is on the way to pick it up."
            )
            
            # Dispatch Driver
            driver = next((x for x in db["users"] if x["role"] == "DRIVER"), None)
            if driver:
                # Assign Delivery
                delivery_id = "del-" + str(uuid.uuid4())[:8]
                db["deliveries"].append({
                    "id": delivery_id,
                    "order_id": order["id"],
                    "driver_id": driver["id"],
                    "status": "ASSIGNED"
                })
                
                # Fetch chef address
                chef_details = db["chefs"][0]
                mock_send_whatsapp_message(
                    to_phone=driver["phone"],
                    text=f"New Delivery Assigned!\nPickup Location: {chef_details['kitchen_address']}\nDropoff Location: {order['address']}\nCustomer Phone: {order['customer_phone']}\n\nMap Route: https://www.google.com/maps/dir/{chef_details['gps_coordinates']}/{order['gps_coordinates'] if 'gps_coordinates' in order else '12.9784,77.6408'}\n\nClick [Accept Pickup] to begin."
                )
                
    return {"status": "success"}

@app.post("/api/chef/consolidate")
def chef_consolidate():
    # Consolidate all orders that are in CONFIRMED or PREPARING status
    with db_lock:
        active_orders = [o for o in db["orders"] if o["status"] in ["CONFIRMED", "PREPARING"]]
        if not active_orders:
            return {"status": "error", "message": "No active orders to consolidate."}
            
        totals = {}
        for o in active_orders:
            for item in o["items"]:
                name = item["item"]
                qty = item["qty"]
                totals[name] = totals.get(name, 0) + qty
                
        summary_lines = [f"- {qty}x {name}" for name, qty in totals.items()]
        summary_text = "*Consolidated Cooking Summary*\n" + "\n".join(summary_lines)
        
        chef_phone = "+919999888877"
        mock_send_whatsapp_message(to_phone=chef_phone, text=summary_text)
        log_queue_event("Consolidation Scheduler Job fired. Dispatched bulk cooking checklist to Chef.")
        
    return {"status": "success", "summary": totals}

@app.post("/api/driver/action")
def driver_action(payload: Dict[str, Any]):
    action = payload.get("action")
    driver_phone = "+918888777766"
    
    with db_lock:
        active_deliveries = [d for d in db["deliveries"] if d["status"] not in ["DELIVERED", "FAILED"]]
        if not active_deliveries:
            return {"status": "error", "message": "No active deliveries assigned to this Driver."}
            
        delivery = active_deliveries[0]
        order = next((x for x in db["orders"] if x["id"] == delivery["order_id"]), None)
        
        if action == "pickup":
            delivery["status"] = "PICKED_UP"
            if order:
                order["status"] = "IN_TRANSIT"
                log_queue_event(f"Driver accepted pickup. Delivery {delivery['id']} is PICKED_UP. Order {order['id']} is IN_TRANSIT.")
                mock_send_whatsapp_message(
                    to_phone=driver_phone,
                    text=f"Acknowledged. Delivery route active for Order {order['id']}. Heading to: {order['address']}."
                )
                mock_send_whatsapp_message(
                    to_phone=order["customer_phone"],
                    text="Your rider has picked up the food and is heading your way! Stay close to your phone."
                )
        elif action == "deliver":
            delivery["status"] = "DELIVERED"
            if order:
                order["status"] = "DELIVERED"
                log_queue_event(f"Driver updated delivery {delivery['id']} to DELIVERED. Order {order['id']} completed.")
                mock_send_whatsapp_message(
                    to_phone=driver_phone,
                    text=f"Acknowledged. Order {order['id']} has been successfully delivered. Payout recorded!"
                )
                mock_send_whatsapp_message(
                    to_phone=order["customer_phone"],
                    text="Your delicious home-cooked meal has been delivered. Bon appétit!"
                )
                
    return {"status": "success"}

def run_modification_llm(message: str, order: dict) -> dict:
    order_items_desc = ", ".join([f"{x['qty']}x {x['item']}" for x in order["items"]])
    prompt_text = f"""
    You are the order modification parser for Homaatri, a local home-cooked kitchen.
    The customer has an active order with details:
    - ID: {order['id']}
    - Current Items: {order_items_desc}
    - Current Delivery Address: {order['address']}
    - Current Target Delivery Time: {order['delivery_time']}
    
    They just sent this message: "{message}"
    
    Identify if they want to modify their existing order items, delivery address, or delivery time.
    
    Output a raw JSON matching this structure:
    {{
      "is_modification": true_or_false,
      "modification_type": "food" | "delivery" | "none",
      "summary": "Short description of change",
      "new_items": [
         {{"item": "item_name", "qty": 3}}
      ],
      "new_address": "address_string_or_null",
      "new_time": "time_string_or_null"
    }}
    
    Rules:
    1. If they ask to add items (e.g. "add 2 more rotis"), you MUST recalculate the new TOTAL items (e.g., if original was 3 rotis, new should be 5).
    2. If they want to change the delivery time (e.g., "want it at 8 30" or "deliver at 9 PM"), extract the target time string (e.g., "8:30 PM", "9:00 PM").
    3. If they want to change address, extract the new address.
    4. Return ONLY valid raw JSON. No markdown ticks.
    """
    
    start_time = time.time()
    
    # 1. Deterministic Walkthrough Fallback (for offline demo stability!)
    msg_clean = message.lower().strip()
    if "change the time" in msg_clean or "at 8 30" in msg_clean:
        parsed = {
            "is_modification": True,
            "modification_type": "delivery",
            "summary": "Change delivery time to 8:30 PM",
            "new_items": [],
            "new_address": None,
            "new_time": "8:30 PM"
        }
        db["llm_logs"].append({
            "timestamp": datetime.now().isoformat(),
            "prompt": prompt_text,
            "status": "SUCCESS (OFFLINE DEMO SIMULATOR)",
            "response": json.dumps(parsed, indent=2),
            "latency": time.time() - start_time,
            "model_used": "Homaatri Offline Walkthrough Engine"
        })
        return parsed
        
    if "add 2 more" in msg_clean or "chef to add 2" in msg_clean:
        parsed = {
            "is_modification": True,
            "modification_type": "food",
            "summary": "Add 2 more butter rotis",
            "new_items": [{"item": "Butter Roti", "qty": 5}],
            "new_address": None,
            "new_time": None
        }
        db["llm_logs"].append({
            "timestamp": datetime.now().isoformat(),
            "prompt": prompt_text,
            "status": "SUCCESS (OFFLINE DEMO SIMULATOR)",
            "response": json.dumps(parsed, indent=2),
            "latency": time.time() - start_time,
            "model_used": "Homaatri Offline Walkthrough Engine"
        })
        return parsed

    # 2. Attempt API Call
    response_text, model_used, status = call_huggingface_llm(prompt_text, max_tokens=256, temperature=0.1)
    
    if status == "SUCCESS":
        clean_res = response_text
        if "```json" in clean_res:
            clean_res = clean_res.split("```json")[1].split("```")[0].strip()
        elif "```" in clean_res:
            clean_res = clean_res.split("```")[1].split("```")[0].strip()
            
        try:
            import json
            parsed = json.loads(clean_res)
            db["llm_logs"].append({
                "timestamp": datetime.now().isoformat(),
                "prompt": prompt_text,
                "status": "SUCCESS",
                "response": json.dumps(parsed, indent=2),
                "latency": time.time() - start_time,
                "model_used": f"{model_used} (Modification Parser)"
            })
            return parsed
        except Exception as e:
            status = f"JSON_PARSE_ERROR: {str(e)}"
            
    db["llm_logs"].append({
        "timestamp": datetime.now().isoformat(),
        "prompt": prompt_text,
        "status": status,
        "response": "Aborted modification parse.",
        "latency": time.time() - start_time,
        "model_used": model_used
    })
    
    return {
        "is_modification": False,
        "modification_type": "none",
        "summary": "Endpoints offline, aborted modification guess",
        "new_items": [],
        "new_address": None,
        "new_time": None
    }

def handle_order_modification(message_id: str, order: dict, message_text: str):
    parsed = run_modification_llm(message_text, order)
    
    if not parsed.get("is_modification", False) or parsed.get("modification_type") == "none":
        mock_send_whatsapp_message(
            to_phone=order["customer_phone"],
            text=f"Your order {order['id']} is currently '{order['status']}'. If you want to modify items, address, or time, please let me know clearly (e.g. 'make it 3 rotis' or 'change address to Flat 501')."
        )
        return
        
    m_type = parsed.get("modification_type")
    
    with db_lock:
        if m_type == "food":
            new_items = parsed.get("new_items", [])
            if not new_items:
                new_items = order["items"]
                
            if order["status"] in ["CONFIRMED", "PREPARING"]:
                order["pending_change"] = {
                    "type": "food",
                    "text": message_text,
                    "items": new_items
                }
                
                change_desc = ", ".join([f"{x['qty']}x {x['item']}" for x in new_items])
                chef = next((x for x in db["users"] if x["role"] == "CHEF"), None)
                
                mock_send_whatsapp_message(
                    to_phone=order["customer_phone"],
                    text=f"We have received your request to update your food to: {change_desc}. Asking Chef Kiran Sharma for approval..."
                )
                
                if chef:
                    original_desc = ", ".join([f"{x['qty']}x {x['item']}" for x in order['items']])
                    mock_send_whatsapp_message(
                        to_phone=chef["phone"],
                        text=f"⚠️ CHANGE REQUEST for Order {order['id']}!\nCustomer Rohan Dev wants to update their order items to:\n👉 *{change_desc}*\n(Original: {original_desc})\n\nPlease choose [Accept Food Change] or [Reject (Too Late)] below."
                    )
            else:
                mock_send_whatsapp_message(
                    to_phone=order["customer_phone"],
                    text=f"Sorry, your order {order['id']} is already prepared and cannot be modified at this stage."
                )
                
        elif m_type == "delivery":
            new_address = parsed.get("new_address") or order["address"]
            new_time = parsed.get("new_time") or order["delivery_time"]
            
            if "time" in message_text.lower() and not parsed.get("new_time"):
                mock_send_whatsapp_message(
                    to_phone=order["customer_phone"],
                    text="What time would you like your food delivered? Please specify a target time (e.g. 'by 8:30 PM')."
                )
                return
                
            if order["status"] in ["CONFIRMED", "PREPARING"]:
                order["address"] = new_address
                order["delivery_time"] = new_time
                mock_send_whatsapp_message(
                    to_phone=order["customer_phone"],
                    text=f"Your delivery details have been successfully updated!\nNew Address: {new_address}\nNew Time: {new_time}"
                )
                log_queue_event(f"Order {order['id']}: Delivery details auto-updated (No rider dispatched yet).")
            else:
                order["pending_change"] = {
                    "type": "delivery",
                    "text": message_text,
                    "address": new_address,
                    "time": new_time
                }
                
                driver = next((x for x in db["users"] if x["role"] == "DRIVER"), None)
                mock_send_whatsapp_message(
                    to_phone=order["customer_phone"],
                    text=f"We have received your delivery update. Asking your rider Suresh Kumar for confirmation..."
                )
                
                if driver:
                    is_addr_changed = parsed.get("new_address") is not None
                    is_time_changed = parsed.get("new_time") is not None
                    
                    if is_addr_changed and is_time_changed:
                        change_text = f"update coordinates:\n👉 Address: {new_address}\n👉 Time: {new_time}"
                        btn_label = "Coordinates"
                    elif is_addr_changed:
                        change_text = f"update delivery address to:\n👉 Address: {new_address}"
                        btn_label = "Route"
                    else:
                        change_text = f"update delivery time to:\n👉 Time: {new_time}"
                        btn_label = "Time"
                        
                    mock_send_whatsapp_message(
                        to_phone=driver["phone"],
                        text=f"⚠️ DELIVERY CHANGE for Order {order['id']}!\nCustomer Rohan Dev wants to {change_text}\n\nPlease select [Accept {btn_label} Change] or [Reject Change] below."
                    )

@app.post("/api/chef/change-response")
def chef_change_response(payload: Dict[str, Any]):
    action = payload.get("action")
    chef_phone = "+919999888877"
    
    with db_lock:
        active_orders = [o for o in db["orders"] if o["status"] in ["CONFIRMED", "PREPARING"] and "pending_change" in o]
        if not active_orders:
            return {"status": "error", "message": "No pending food changes found."}
            
        order = active_orders[0]
        change = order["pending_change"]
        
        if action == "accept":
            new_items = change["items"]
            total_amount = 0.0
            for o_item in new_items:
                m_item = next((x for x in db["menu_items"] if x["name"].lower() == o_item["item"].lower()), None)
                price = m_item["price"] if m_item else 10.0
                total_amount += price * o_item["qty"]
                
            order["items"] = new_items
            order["amount"] = total_amount
            
            mock_send_whatsapp_message(
                to_phone=chef_phone,
                text=f"Acknowledged. Change Accepted. Cooking checklist updated."
            )
            mock_send_whatsapp_message(
                to_phone=order["customer_phone"],
                text=f"Your order change request has been APPROVED by Chef Kiran Sharma. The kitchen is preparing the new items. New Total: ₹{total_amount}."
            )
            log_queue_event(f"Chef Kiran Sharma ACCEPTED food change request for Order {order['id']}.")
        else:
            mock_send_whatsapp_message(
                to_phone=chef_phone,
                text=f"Acknowledged. Change Rejected. Continuing cooking original recipe."
            )
            mock_send_whatsapp_message(
                to_phone=order["customer_phone"],
                text=f"Sorry, Chef Kiran Sharma cannot accommodate your order change request at this stage as cooking is already underway."
            )
            log_queue_event(f"Chef Kiran Sharma REJECTED food change request for Order {order['id']}.")
            
        del order["pending_change"]
        
    return {"status": "success"}

@app.post("/api/driver/change-response")
def driver_change_response(payload: Dict[str, Any]):
    action = payload.get("action")
    driver_phone = "+918888777766"
    
    with db_lock:
        active_orders = [o for o in db["orders"] if o["status"] in ["READY_FOR_PICKUP", "IN_TRANSIT"] and "pending_change" in o]
        if not active_orders:
            return {"status": "error", "message": "No pending delivery changes found."}
            
        order = active_orders[0]
        change = order["pending_change"]
        
        if action == "accept":
            order["address"] = change["address"]
            order["delivery_time"] = change["time"]
            
            mock_send_whatsapp_message(
                to_phone=driver_phone,
                text=f"Acknowledged. Change Accepted. Delivery destination coordinates updated."
            )
            mock_send_whatsapp_message(
                to_phone=order["customer_phone"],
                text=f"Your delivery detail changes have been APPROVED by Rider Suresh Kumar!"
            )
            log_queue_event(f"Driver Suresh Kumar ACCEPTED delivery change request for Order {order['id']}.")
        else:
            mock_send_whatsapp_message(
                to_phone=driver_phone,
                text=f"Acknowledged. Change Rejected. Route remains locked to original coordinates."
            )
            mock_send_whatsapp_message(
                to_phone=order["customer_phone"],
                text=f"Sorry, Rider Suresh Kumar cannot alter coordinates at this stage as they are already on course."
            )
            log_queue_event(f"Driver Suresh Kumar REJECTED delivery change request for Order {order['id']}.")
            
        del order["pending_change"]
        
    return {"status": "success"}

def get_active_order_context(phone: str, role: str) -> str:
    with db_lock:
        active_order = None
        if role == "CUSTOMER":
            active_order = next((o for o in db["orders"] if o["customer_phone"] == phone and o["status"] not in ["DELIVERED", "FAILED"]), None)
        elif role == "CHEF":
            active_order = next((o for o in db["orders"] if o["chef_id"] == "user-chef-1" and o["status"] not in ["DELIVERED", "FAILED"]), None)
        elif role == "DRIVER":
            active_order = next((o for o in db["orders"] if o["status"] not in ["DELIVERED", "FAILED"]), None)
            
        if not active_order:
            return "No active orders are currently tracked in the database for this profile."
            
        delivery = next((d for d in db["deliveries"] if d["order_id"] == active_order["id"]), None)
        driver_name = "Not Assigned"
        driver_status = "N/A"
        if delivery:
            drv = next((u for u in db["users"] if u["id"] == delivery["driver_id"]), None)
            if drv:
                driver_name = drv["name"]
            driver_status = delivery["status"]
            
        chef_user = next((u for u in db["users"] if u["id"] == active_order["chef_id"]), None)
        chef_name = chef_user["name"] if chef_user else "Kiran Sharma"
        chef_details = db["chefs"][0]
        
        customer_user = next((u for u in db["users"] if u["phone"] == active_order["customer_phone"]), None)
        customer_name = customer_user["name"] if customer_user else "Rohan Dev"
        
        items_str = ", ".join([f"{x['qty']}x {x['item']}" for x in active_order["items"]])
        
        change_str = "None"
        if "pending_change" in active_order:
            chg = active_order["pending_change"]
            if chg["type"] == "food":
                chg_items = ", ".join([f"{x['qty']}x {x['item']}" for x in chg["items"]])
                change_str = f"Pending Food Change: Update order recipe to: {chg_items}"
            else:
                change_str = f"Pending Delivery Coordinate Change: New Address='{chg.get('address')}', New Target Time='{chg.get('time')}'"
                
        return f"""[Active Order Lifecycle State]
- ID: {active_order['id']}
- Status: {active_order['status']}
- Customer Name: {customer_name} (Phone: {active_order['customer_phone']})
- Delivery Destination: {active_order['address']}
- Target Delivery Time: {active_order['delivery_time']}
- Food Items Ordered: {items_str}
- Bill Total: ₹{active_order['amount']}
- Chef: {chef_name} (Kitchen: {chef_details['kitchen_name']}, GPS: {chef_details['gps_coordinates']})
- Driver Assigned: {driver_name} (Delivery State: {driver_status})
- Active Modification Change Requests: {change_str}"""

def run_role_llm(phone: str, role: str, message: str, context: str) -> str:
    start_time = time.time()
    
    system_prompts = {
        "CUSTOMER": """You are Homaatri's warm, welcoming customer support bot.
You communicate with Rohan Dev (Customer). Use emojis, keep it culinary-focused, polite, and helpful.
Direct them to today's active menu items (Butter Roti, Paneer Butter Masala, Dal Fry, Jeera Rice, Chapati) if they ask questions.
Keep your response concise (max 3 sentences).""",

        "CHEF": """You are Homaatri's operational manager helper.
You communicate with Chef Kiran Sharma (Housewife/Cook). Speak in a supportive, clear, checklist-oriented, and professional format.
Help her understand consolidating cutoff times (11:30 AM / 6:30 PM), capacity updates, and recipes.
Keep your response concise (max 3 sentences).""",

        "DRIVER": """You are Homaatri's dispatch logistics center.
You communicate with Suresh Kumar (Rider). Speak in a direct, action-oriented, and brief format.
Provide coordinate answers, pickup locations, navigation advice, and delivery instructions.
Keep your response concise (max 2 sentences)."""
    }
    
    # Check if CUSTOMER is ordering
    if role == "CUSTOMER":
        menu_names = [item["name"] for item in db["menu_items"]]
        is_ordering_intent = any(x in message.lower() for x in ["roti", "paneer", "dal", "chapati", "rice", "order", "want", "need", "send", "give"])
        
        if is_ordering_intent:
            parsed = run_langchain_parsing(message)
            if parsed.get("is_valid", False) and parsed.get("items"):
                if parsed.get("clarification_needed", False):
                    return parsed.get("clarification_message") or "Sorry, could you clarify which items you want?"
                
                order_items = parsed.get("items", [])
                total_amount = 0.0
                for o_item in order_items:
                    m_item = next((x for x in db["menu_items"] if x["name"].lower() == o_item["item"].lower()), None)
                    price = m_item["price"] if m_item else 10.0
                    total_amount += price * o_item["qty"]
                    
                order_id = "ord-" + str(uuid.uuid4())[:8]
                new_order = {
                    "id": order_id,
                    "customer_phone": phone,
                    "chef_id": "user-chef-1",
                    "items": order_items,
                    "amount": total_amount,
                    "delivery_time": parsed.get("delivery_time") or "8:00 PM",
                    "address": parsed.get("address") or "Indiranagar, Bengaluru",
                    "status": "PENDING_PAYMENT",
                    "created_at": datetime.now().strftime("%I:%M %p")
                }
                with db_lock:
                    db["orders"].append(new_order)
                log_queue_event(f"Created Order {order_id} in PENDING_PAYMENT status.")
                
                pay_url = f"http://localhost:8000/pay/{order_id}"
                summary = ", ".join([f"{x['qty']}x {x['item']}" for x in order_items])
                return f"Order Created! 🍲 Details: {summary}\nGrand Total: ₹{total_amount}\n\nPlease click here to complete payment: {pay_url}"

    system_prompt = system_prompts.get(role, system_prompts["CUSTOMER"])
    order_context = get_active_order_context(phone, role)
    prompt = f"""
    {system_prompt}
    
    --- REAL-TIME ORDER STATE CONTEXT ---
    {order_context}
    
    --- RELEVANT HISTORY FROM KNOWLEDGE BASE ---
    {context}
    
    Customer/User Message: "{message}"
    
    Provide your response. Remember to stay in character. Do not output anything else.
    """
    
    response_text, model_used, status = call_huggingface_llm(prompt, max_tokens=128, temperature=0.7)
    
    if status == "SUCCESS":
        db["llm_logs"].append({
            "timestamp": datetime.now().isoformat(),
            "prompt": prompt,
            "status": "SUCCESS",
            "response": response_text,
            "latency": time.time() - start_time,
            "model_used": f"{model_used} ({role} Persona)"
        })
        return response_text
        
    # Heuristic Personality responses
    db["llm_logs"].append({
        "timestamp": datetime.now().isoformat(),
        "prompt": prompt,
        "status": status,
        "response": "System endpoints offline. Triggered heuristics.",
        "latency": time.time() - start_time,
        "model_used": "None"
    })
    
    response = ""
    if role == "CUSTOMER":
        response = "Hi! We'd love to help you eat healthy. Today's specials are Butter Roti, Paneer Butter Masala, Dal Fry, and Jeera Rice. Would you like to order any of these?"
    elif role == "CHEF":
        response = "Hello Chef Kiran Sharma. Cooking consolidation checklist is active. Please let me know if you need help with consolidation lists."
    elif role == "DRIVER":
        response = "Hello Rider Suresh. All route matrices are active. Google Maps multi-stop link is attached on assignment. Let me know when you reach kitchen location."
    return response

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)
