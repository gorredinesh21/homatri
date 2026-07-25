"""Sample LLM module using LangChain with GCP Vertex AI (Gemini).

Project: homatri-503308
Account: errog2107@gmail.com
"""
import os
import asyncio
from langchain_google_vertexai import ChatVertexAI
from langchain_core.messages import HumanMessage, SystemMessage

# Configuration
GCP_PROJECT_ID = os.getenv("GCP_PROJECT_ID", "homatri-503308")
GCP_LOCATION = os.getenv("GCP_LOCATION", "global")
DEFAULT_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")


def get_gemini_llm(model_name: str = DEFAULT_MODEL, temperature: float = 0.2) -> ChatVertexAI:
    """Initialize and return a LangChain ChatVertexAI LLM instance."""
    return ChatVertexAI(
        model=model_name,
        project=GCP_PROJECT_ID,
        location=GCP_LOCATION,
        temperature=temperature,
    )


def generate_response(prompt: str, system_prompt: str = "You are a helpful AI assistant.") -> str:
    """Synchronously invoke Gemini via LangChain ChatVertexAI."""
    llm = get_gemini_llm()
    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=prompt),
    ]
    response = llm.invoke(messages)
    return str(response.content)


async def generate_response_async(prompt: str, system_prompt: str = "You are a helpful AI assistant.") -> str:
    """Asynchronously invoke Gemini via LangChain ChatVertexAI."""
    llm = get_gemini_llm()
    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=prompt),
    ]
    response = await llm.ainvoke(messages)
    return str(response.content)


if __name__ == "__main__":
    print(f"Connecting to GCP Vertex AI (Project: {GCP_PROJECT_ID}, Model: {DEFAULT_MODEL})...")
    test_prompt = "Hello! Briefly explain what Homaatri is in 2 sentences."
    print(f"\nUser Prompt: '{test_prompt}'\n")
    
    reply = generate_response(test_prompt)
    print("Gemini Response:")
    print("-" * 50)
    print(reply)
    print("-" * 50)
