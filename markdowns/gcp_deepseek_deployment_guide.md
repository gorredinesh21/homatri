# 📘 STEP-BY-STEP GUIDE: DEPLOYING DEEPSEEK (V3 / R1) ON GCP VERTEX AI MODEL GARDEN

This guide walks you through deploying **DeepSeek V3** or **DeepSeek R1** as a managed endpoint on Google Cloud Platform (GCP) Vertex AI.

---

## 📋 Prerequisites & GCP Project Details

- **GCP Project ID**: `homatri-503308`
- **Region**: `us-central1` (or `europe-west4`)
- **Active Service Account / ADC**: `errog2107@gmail.com`

---

## 🛠️ Step 1: Check GPU Quota in GCP Console

DeepSeek models run on dedicated GPU acceleration (NVIDIA L4 or A100 GPUs) on GCP.

1. Open **GCP Quotas Console**:
   👉 [GCP IAM & Admin Quotas](https://console.cloud.google.com/iam-admin/quotas?project=homatri-503308)
2. Search for: `Nvidia L4 GPUs` or `Nvidia A100 GPUs`
3. Ensure you have at least **1 GPU allocated** in region `us-central1`.
   *(If limit is 0, click "Edit Quota" ➔ Request 1 GPU, approval takes ~5 minutes).*

---

## 🚀 Step 2: Open Vertex AI Model Garden

1. Go to **Vertex AI Model Garden**:
   👉 [GCP Vertex AI Model Garden](https://console.cloud.google.com/vertex-ai/model-garden?project=homatri-503308)
2. In the search bar at top, search for: **`DeepSeek-R1`** or **`DeepSeek-V3`**.
3. Click on the **DeepSeek-R1 / DeepSeek-V3** model card.

---

## ⚡ Step 3: Deploy managed Endpoint

1. On the model card page, click the **Deploy** button.
2. Configure deployment options:
   - **Endpoint Name**: `homatri-deepseek-r1`
   - **Region**: `us-central1`
   - **Machine Specs**:
     - **Container**: vLLM High-Performance Serving Container (default selected)
     - **Accelerator Type**: `NVIDIA_L4` (or `NVIDIA_TESLA_A100`)
     - **Accelerator Count**: `1` (or `4` for full unquantized weights)
3. Click **Deploy**.
4. GCP will spend ~5 to 10 minutes spinning up the GPU container and assigning an Endpoint ID.

---

## 🔑 Step 4: Retrieve Endpoint ID

Once deployment status shows **Active (Green)**:
1. Go to [Vertex AI Endpoints Console](https://console.cloud.google.com/vertex-ai/online-prediction/endpoints?project=homatri-503308).
2. Copy the **Endpoint ID** (e.g. `749201849201730`).

---

## 🐍 Step 5: Call the GCP Vertex AI DeepSeek Endpoint in Python

Now add your Endpoint ID into `.env`:
```env
GCP_DEEPSEEK_ENDPOINT_ID=749201849201730
```

Use the following Python snippet to call your GCP DeepSeek endpoint:

```python
import os
from google.cloud import aiplatform

# Initialize Vertex AI
aiplatform.init(
    project=os.getenv("GCP_PROJECT", "homatri-503308"),
    location=os.getenv("GCP_LOCATION", "us-central1")
)

# Connect to deployed DeepSeek Endpoint
endpoint_id = os.getenv("GCP_DEEPSEEK_ENDPOINT_ID", "YOUR_ENDPOINT_ID")
endpoint = aiplatform.Endpoint(endpoint_id)

# Call DeepSeek Model
response = endpoint.predict(
    instances=[
        {
            "prompt": "Hello DeepSeek! Act as Homaatri AI assistant and greet the customer.",
            "max_tokens": 250,
            "temperature": 0.2
        }
    ]
)

print("🟢 GCP DeepSeek Response:")
print(response.predictions)
```

---

## 💡 Cost Optimization Tip
If you are doing local development or testing without wanting to pay hourly GCP GPU server uptime (~$0.70/hr):
- **Option 1**: Use **AWS Bedrock (`us.deepseek.r1-v1:0`)** which is pay-per-token with **$0 fixed hourly uptime cost**.
- **Option 2**: Use **Direct DeepSeek API (`api.deepseek.com`)** by adding `DEEPSEEK_API_KEY=sk-...` in `.env`.
