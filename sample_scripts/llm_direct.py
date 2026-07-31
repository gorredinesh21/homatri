# ==============================================================================
# DIRECT GCP VERTEX AI LLM CALL (WITHOUT LANGCHAIN)
# ==============================================================================
# Uses Google's official `google-genai` SDK directly with Vertex AI authentication.
# No API key needed — uses active `gcloud` CLI profile credentials (`errog2107@gmail.com`).
# ==============================================================================

from google import genai

# Step 1: Initialize the official Google GenAI Client pointing to Vertex AI
client = genai.Client(
    vertexai=True,
    project="homatri-503308",
    location="global",
)

# Step 2: Make direct call to the latest model (gemini-3.6-flash)
print("Making Direct GCP Vertex AI call (Without LangChain) to Gemini 3.6 Flash...")
response = client.models.generate_content(
    model="gemini-3.6-flash",
    contents="Hi! Respond with 'Hello from GCP Gemini 3.6 Flash (Direct SDK, No LangChain)!'.",
)

# Step 3: Print result
print("\nResult from LLM:")
print(response.text)
