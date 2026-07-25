# ==============================================================================
# ULTRA-SIMPLE GEMINI LLM CALL WITH LANGCHAIN
# ==============================================================================
# FAQ: HOW DOES AUTHENTICATION WORK? IS AN API KEY NEEDED?
#
# -> GCP VERTEX AI METHOD (NO API KEY NEEDED):
#    When using `ChatVertexAI`, Google Cloud automatically picks up your logged-in
#    GCP account (`errog2107@gmail.com`) from your `gcloud` CLI.
#    You only specify your `project` ID ("homatri-503308").
#
# -> GOOGLE AI STUDIO METHOD (API KEY NEEDED):
#    If you want to use an API Key instead, you use `ChatGoogleGenerativeAI`
#    and pass `google_api_key="AIzaSy..."`.
# ==============================================================================

from langchain_google_vertexai import ChatVertexAI

# Step 1: Initialize the Gemini LLM (Uses active gcloud account, NO API key required!)
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
