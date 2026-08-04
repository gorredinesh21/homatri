"""Sample: call Moonshot Kimi K2 on AWS Bedrock (Converse API).

Auth: the local AWS profile `homatri-bedrock` in ~/.aws (no API key).
Model: `moonshot.kimi-k2-thinking` — a reasoning MoE model (~1T total params,
~32B active). Because it's a *thinking* model, each reply contains a hidden
`reasoningContent` block plus the final answer; we separate the two below and
budget enough tokens so the answer isn't cut off after the reasoning.

Run:  python sample_scripts/bedrock_kimi_k2.py
"""

from __future__ import annotations

import sys

import boto3
from botocore.exceptions import ClientError

# Kimi replies can contain emoji; make stdout UTF-8 so the Windows console doesn't crash.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

PROFILE = "homatri-bedrock"
REGION = "us-east-1"
# Try in order; some models need the 'us.' cross-region inference profile.
MODEL_CANDIDATES = [
    "moonshot.kimi-k2-thinking",
    "us.moonshot.kimi-k2-thinking",
    "moonshotai.kimi-k2.5",
    "us.moonshotai.kimi-k2.5",
]

_session = boto3.Session(profile_name=PROFILE)
_bedrock = _session.client("bedrock-runtime", region_name=REGION)


def pick_model() -> str:
    """Return the first Kimi model id that actually invokes on this account."""
    ping = [{"role": "user", "content": [{"text": "ping"}]}]
    for mid in MODEL_CANDIDATES:
        try:
            _bedrock.converse(modelId=mid, messages=ping, inferenceConfig={"maxTokens": 8})
            return mid
        except ClientError:
            continue
    raise RuntimeError("No Kimi K2 model is invokable on this account.")


def split_response(resp: dict) -> tuple[str, str]:
    """Return (reasoning_text, answer_text) from a Bedrock Converse response."""
    reasoning, answer = [], []
    for block in resp["output"]["message"]["content"]:
        if "text" in block:
            answer.append(block["text"])
        elif "reasoningContent" in block:
            rc = block["reasoningContent"].get("reasoningText", {})
            reasoning.append(rc.get("text", ""))
    return "".join(reasoning).strip(), "".join(answer).strip()


def ask(model_id: str, prompt: str, max_tokens: int = 1024, temperature: float = 0.3) -> tuple[str, str]:
    """Send one prompt; return (reasoning, answer)."""
    resp = _bedrock.converse(
        modelId=model_id,
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        inferenceConfig={"maxTokens": max_tokens, "temperature": temperature},
    )
    return split_response(resp)


if __name__ == "__main__":
    model_id = pick_model()
    print(f"Bedrock model: {model_id}  (profile={PROFILE}, region={REGION})\n")

    prompts = [
        "In one short sentence, introduce yourself and name your model.",
        "A home-cooked tiffin costs Rs 120 and delivery is Rs 30 per order. "
        "What is the total for 3 tiffins in one order? Answer in one line.",
        "Write one short, warm WhatsApp message telling a customer their "
        "home-cooked lunch has been picked up and is on the way.",
    ]

    for i, p in enumerate(prompts, 1):
        print(f"===== Sample {i} =====")
        print("Q:", p)
        reasoning, answer = ask(model_id, p)
        if reasoning:
            print(f"[reasoning: {len(reasoning)} chars, hidden]")
        print("A:", answer or "(no answer text — try a higher maxTokens)")
        print()
