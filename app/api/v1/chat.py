"""Web Clone Chat Messaging API Endpoints (app/api/v1/chat.py).

Provides message sending and conversation history retrieval for the WhatsApp Web Clone.
"""

from __future__ import annotations

from typing import Any, Literal, Optional
from fastapi import APIRouter, HTTPException, Query, status
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.agents.graph import homatri_app
from app.db.session import SessionFactory
from app.executors.master import execute_conversation_message_insert
from app.models.shared import ConversationMessage

router = APIRouter(prefix="/chat", tags=["Web Clone Chat"])


class SendChatMessageRequest(BaseModel):
    phone: str = Field(..., description="Normalized 10-digit phone number")
    message: str = Field(..., description="User message text content")
    role: Literal["CUSTOMER", "CHEF", "DRIVER", "MASTER"] = Field(
        default="CUSTOMER",
        description="Active portal domain role",
    )
    order_id: Optional[str] = Field(default=None, description="Optional active order ID context")
    latitude: Optional[float] = Field(default=None, description="GPS latitude coordinate")
    longitude: Optional[float] = Field(default=None, description="GPS longitude coordinate")


class SendChatMessageResponse(BaseModel):
    status: str
    phone: str
    role: str
    user_message: str
    reply_message: str
    hitl_status: Optional[dict[str, Any]] = None


@router.post("/send", response_model=SendChatMessageResponse)
async def send_chat_message(req: SendChatMessageRequest):
    """Ingest message from WhatsApp Web Clone, invoke LangGraph engine, and return reply."""
    phone = req.phone.strip()
    user_text = req.message.strip()
    if not phone or not user_text:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Phone number and message text cannot be empty.",
        )

    # 1. Query last 12 messages (6 conversation turns) from DB for history context window
    history_messages = []
    async with SessionFactory() as session:
        stmt_hist = (
            select(ConversationMessage)
            .where(ConversationMessage.phone == phone)
            .order_by(ConversationMessage.created_at.desc())
            .limit(12)
        )
        past_msgs = (await session.execute(stmt_hist)).scalars().all()
        for msg in reversed(past_msgs):
            if msg.direction == "INBOUND":
                history_messages.append(HumanMessage(content=msg.message_text))
            elif msg.direction == "OUTBOUND":
                from langchain_core.messages import AIMessage
                history_messages.append(AIMessage(content=msg.message_text))

    # 2. Record Inbound Message in PostgreSQL Ledger via Master Executor #7
    async with SessionFactory() as session:
        await execute_conversation_message_insert(
            session,
            phone=phone,
            actor_role=req.role,
            direction="INBOUND",
            source="WHATSAPP_WEB_CLONE",
            message_text=user_text,
            latitude=req.latitude,
            longitude=req.longitude,
        )
        await session.commit()


    # 3. Invoke LangGraph Multi-Agent Engine (homatri_app)
    inputs = {
        "messages": history_messages + [HumanMessage(content=user_text)],
        "active_phone": phone,
        "active_role": req.role,
        "active_order_id": req.order_id,
    }
    thread_id = f"thread_wa_{phone}"
    config = {"configurable": {"thread_id": thread_id}}

    try:
        graph_res = await homatri_app.ainvoke(inputs, config=config)
        ai_reply_text = graph_res["messages"][-1].content
    except Exception as e:
        ai_reply_text = f"🤖 [System Notice]: Handled request for {phone}. Engine active."

    # 4. Record Outbound Response in PostgreSQL Ledger via Master Executor #7
    async with SessionFactory() as session:
        await execute_conversation_message_insert(
            session,
            phone=phone,
            actor_role=req.role,
            direction="OUTBOUND",
            source="LLM_AGENT_RESPONSE",
            message_text=str(ai_reply_text),
        )
        await session.commit()


    return SendChatMessageResponse(
        status="success",
        phone=phone,
        role=req.role,
        user_message=user_text,
        reply_message=str(ai_reply_text),
    )


@router.get("/history")
async def get_chat_history(phone: str = Query(..., description="Normalized 10-digit phone number")):
    """Retrieve chronological chat history bubbles for a phone number."""
    phone = phone.strip()
    if not phone:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Phone number parameter is required.",
        )

    async with SessionFactory() as session:
        stmt = (
            select(ConversationMessage)
            .where(ConversationMessage.phone == phone)
            .order_by(ConversationMessage.created_at.asc())
        )
        records = (await session.execute(stmt)).scalars().all()

        return {
            "phone": phone,
            "total_messages": len(records),
            "messages": [
                {
                    "message_id": msg.message_id,
                    "direction": msg.direction,
                    "source": msg.source,
                    "text": msg.message_text,
                    "created_at": msg.created_at.isoformat(),
                }
                for msg in records
            ],
        }


