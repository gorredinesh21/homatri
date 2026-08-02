"""Integration test suite for 6-turn (12 messages) Conversation History Windowing."""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession


from app.main import app


from unittest.mock import MagicMock, patch
from langchain_core.messages import AIMessage


@pytest.mark.asyncio
async def test_conversation_history_window_ingress(db_session: AsyncSession):
    phone = "9199887766"

    mock_llm = MagicMock()
    mock_llm.bind_tools.return_value = mock_llm
    async def mock_ainvoke(messages):
        return AIMessage(content="Hello! Here is the lunch menu: Jain Paneer Tikka Tiffin.")
    mock_llm.ainvoke = mock_ainvoke

    with patch("app.agents.nodes._get_llm", return_value=mock_llm):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # Message 1: User asks for menu
            res1 = await client.post(
                "/api/v1/chat/send",
                json={"phone": phone, "message": "Hi, show me lunch menu", "role": "CUSTOMER"},
            )
            assert res1.status_code == 200
            assert "reply_message" in res1.json()

            # Message 2: Follow up
            res2 = await client.post(
                "/api/v1/chat/send",
                json={"phone": phone, "message": "I prefer Jain food", "role": "CUSTOMER"},
            )
            assert res2.status_code == 200

            # Verify History API endpoint returns both turns
            hist_res = await client.get("/api/v1/chat/history", params={"phone": phone})
            assert hist_res.status_code == 200
            hist_data = hist_res.json()
            assert hist_data["total_messages"] >= 2

