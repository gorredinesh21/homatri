# 🖱️ CLICK-BY-CLICK VISUAL WALKTHROUGH: DEPLOYING DEEPSEEK ON GCP VERTEX AI

This guide provides an **exact, screen-by-screen, button-by-button** tutorial to locate, configure, and deploy **DeepSeek V3 / R1** inside Google Cloud Platform (GCP).

---

## 🎯 Account & Project Verification First

Before starting, ensure your browser is logged into the correct GCP account:
- **Active GCP Account**: `errog2107@gmail.com`
- **Target GCP Project Name**: `homatri`
- **Target GCP Project ID**: `homatri-503308`

---

## 📌 PHASE 1: CHECK GPU QUOTA (Required for GPU Deployment)

Before GCP allows you to deploy DeepSeek, your project needs 1 GPU quota. Here is how to check and request it:

### Step 1.1: Open GCP Quotas Page
1. Open your browser and go to this direct link:
   👉 **[https://console.cloud.google.com/iam-admin/quotas?project=homatri-503308](https://console.cloud.google.com/iam-admin/quotas?project=homatri-503308)**
2. Alternatively, navigate manually:
   - Click the **Navigation Menu (☰ 3 horizontal lines)** at top left of GCP Console.
   - Hover over **IAM & Admin**.
   - Click on **Quotas**.

### Step 1.2: Search for L4 GPU Quota
1. At the top of the Quotas table, find the **Filter search box** (labelled `Filter`).
2. Type exactly: **`NVIDIA L4`** and press **Enter**.
3. Look for the row named:
   `NVIDIA L4 GPUs` | Service: `Vertex AI API` | Location: `us-central1`.

### Step 1.3: Request Quota Increase (If Limit is 0)
1. Check the column **Limit**:
   - If Limit is `1` or higher: **You are good to go! Skip to Phase 2.**
   - If Limit is `0`:
     - Click the checkbox next to `NVIDIA L4 GPUs`.
     - Click the blue **EDIT QUOTAS** button at top right.
     - In the slide-out panel on right, enter New Limit: **`1`**.
     - Enter Request description: `"Deploying DeepSeek model on Vertex AI"`.
     - Click **Submit Request**. *(Approval takes ~2-5 minutes).*

---

## 📌 PHASE 2: LOCATING DEEPSEEK IN VERTEX AI MODEL GARDEN

### Step 2.1: Open Model Garden
1. Go directly to Vertex AI Model Garden using this link:
   👉 **[https://console.cloud.google.com/vertex-ai/model-garden?project=homatri-503308](https://console.cloud.google.com/vertex-ai/model-garden?project=homatri-503308)**
2. Or navigate manually via GCP Console:
   - Click Navigation Menu (**☰** top left).
   - Scroll down to **Vertex AI**.
   - Click **Model Garden** (it has a flower/grid icon).

### Step 2.2: Search for DeepSeek
1. At the very top of the Model Garden page, you will see a prominent search bar with placeholder text:
   `Search models, task types, modalities...`
2. Type **`DeepSeek`** in the search bar and press **Enter**.

### Step 2.3: Select DeepSeek Model Card
You will see two cards appear in the search results:
- **`DeepSeek-R1`** (Reasoning model)
- **`DeepSeek-V3`** (General chat & instruction model)

Click on **`DeepSeek-R1`** or **`DeepSeek-V3`**.

---

## 📌 PHASE 3: DEPLOYING DEEPSEEK TO VERTEX AI ENDPOINT

### Step 3.1: Click Deploy Button
1. Once you click the model card, you will land on the **Model Overview** page.
2. In the top banner section (or top right corner), click the blue **DEPLOY** button.

### Step 3.2: Fill Out Deployment Settings
A deployment configuration drawer/form will open. Configure these exact settings:

| Form Field | What to Select / Type |
| :--- | :--- |
| **Endpoint Name** | Type: `homatri-deepseek-endpoint` |
| **Region** | Select dropdown: `us-central1 (Iowa)` |
| **Model Version** | Leave default selected |
| **Serving Framework** | Select `vLLM` (High performance LLM server) |
| **Machine Type** | Select dropdown: `g2-standard-8` (8 vCPUs, 32GB RAM) |
| **Accelerator Type** | Select dropdown: `NVIDIA_L4` |
| **Accelerator Count** | Select `1` |

### Step 3.3: Submit Deployment
1. Click the blue **DEPLOY** button at the bottom of the page.
2. A progress banner will appear showing:
   `Deploying model to endpoint... This usually takes 5-10 minutes.`

---

## 📌 PHASE 4: GETTING YOUR ENDPOINT ID FOR PYTHON

### Step 4.1: Open Endpoints Page
1. Go directly to Vertex AI Endpoints page:
   👉 **[https://console.cloud.google.com/vertex-ai/online-prediction/endpoints?project=homatri-503308](https://console.cloud.google.com/vertex-ai/online-prediction/endpoints?project=homatri-503308)**
2. Or navigate manually:
   - Left sidebar under Vertex AI ➔ Click **Online Prediction** ➔ **Endpoints**.

### Step 4.2: Copy Your Endpoint ID
1. When status indicator turns **🟢 Green (Active)**, look at the table row for `homatri-deepseek-endpoint`.
2. Locate the column labelled **Endpoint ID** (a numeric string like `8492019481029381`).
3. Click the copy icon next to the number.

---

## 📌 PHASE 5: CONNECTING YOUR GCP DEEPSEEK ENDPOINT IN PYTHON

Add your copied Endpoint ID into your `.env` file:
```env
GCP_PROJECT=homatri-503308
GCP_LOCATION=us-central1
GCP_DEEPSEEK_ENDPOINT_ID=8492019481029381
```

Now test calling your live GCP DeepSeek model using Python:

```python
import os
from google.cloud import aiplatform

# Initialize Vertex AI with your GCP project
aiplatform.init(
    project=os.getenv("GCP_PROJECT", "homatri-503308"),
    location=os.getenv("GCP_LOCATION", "us-central1")
)

# Connect to Deployed DeepSeek Endpoint
endpoint_id = os.getenv("GCP_DEEPSEEK_ENDPOINT_ID")
endpoint = aiplatform.Endpoint(endpoint_id)

# Execute Prompt on GCP DeepSeek
print("📡 Sending prompt to GCP DeepSeek Endpoint...")
response = endpoint.predict(
    instances=[
        {
            "prompt": "Hello DeepSeek! Act as Homaatri AI assistant and greet the customer.",
            "max_tokens": 200,
            "temperature": 0.2
        }
    ]
)

print("🟢 GCP DeepSeek Response:")
print(response.predictions)
```
