# Suppress LangChain and SDK deprecation warnings cleanly
import warnings
try:
    from langchain_core._api.deprecation import LangChainDeprecationWarning
    warnings.filterwarnings("ignore", category=LangChainDeprecationWarning)
except ImportError:
    pass
warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore")

# ==============================================================================
# GEMINI LLM CALL WITH LANGCHAIN (GCP VERTEX AI — GEMINI 3.6 FLASH)
# ==============================================================================

from langchain_google_vertexai import ChatVertexAI

# Step 1: Initialize Gemini using the latest model (gemini-3.6-flash) via GCP Vertex AI
llm = ChatVertexAI(
    model="gemini-3.6-flash",
    project="homatri-503308",
    location="global",
)

# Step 2: Send 'hi' to the LLM
print("Making LangChain LLM call to GCP Gemini 3.6 Flash...")
response = llm.invoke("Hi! Respond with 'Hello from Gemini 3.6 Flash via LangChain!'.")

# Helper to extract clean text if response is a list of blocks
def extract_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [c.get("text", "") if isinstance(c, dict) else str(c) for c in content]
        return " ".join(p for p in parts if p).strip()
    return str(content)

# Step 3: Print result
print("\nResult from LLM:")
print(extract_text(response.content))
