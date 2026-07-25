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
# ULTRA-SIMPLE GEMINI LLM CALL WITH LANGCHAIN (GCP VERTEX AI)
# ==============================================================================
# FAQ: HOW DOES AUTHENTICATION WORK? IS AN API KEY NEEDED?
#
# -> GCP VERTEX AI METHOD (`ChatVertexAI`):
#    Uses your active `gcloud` CLI profile (`errog2107@gmail.com`) automatically
#    via GCP Application Default Credentials. NO API Key is required!
#    You only specify your GCP `project` ID ("homatri-503308").
#
# -> GOOGLE AI STUDIO METHOD (`ChatGoogleGenerativeAI`):
#    Used for free-tier / AI Studio developer API keys (`GEMINI_API_KEY="AIza..."`).
# ==============================================================================

from langchain_google_vertexai import ChatVertexAI

# Step 1: Initialize Gemini via GCP Vertex AI (Uses active gcloud CLI credentials)
llm = ChatVertexAI(
    model="gemini-2.5-flash",
    project="homatri-503308",
    location="global",
)

# Step 2: Make a simple call
print("Making LLM call to GCP Gemini...")
response = llm.invoke("Say 'Hello from GCP Gemini!' in 5 words.")

# Step 3: Print result
print("\nResult from LLM:")
print(response.content)
