#!/bin/bash

# Visual Banner
echo "=========================================================="
echo "          HOMAATRI WORKING PROOTOTYPE (POC) SETUP         "
echo "=========================================================="
echo "This script will initialize a Python virtual environment,"
echo "install dependencies, and launch the mock server."
echo ""

# Navigate to project dir
cd "$(dirname "$0")"

# 1. Create virtual environment
if [ ! -d ".venv" ]; then
    echo "[1/3] Creating virtual environment (.venv)..."
    python3 -m venv .venv
else
    echo "[1/3] Virtual environment (.venv) already exists."
fi

# 2. Activate venv
source .venv/bin/activate

# 3. Install packages
echo "[2/3] Installing dependencies from requirements.txt..."
pip install --upgrade pip
pip install -r requirements.txt

# 4. Launch Server
echo ""
echo "[3/3] Launching Uvicorn Server..."
echo "----------------------------------------------------------"
echo "  👉 Access Prototype Interface: http://localhost:8000"
echo "  👉 Meta Access Token Configured Automatically"
echo "  👉 Hugging Face LangChain Models Pre-Loaded"
echo "----------------------------------------------------------"
echo "Press Ctrl+C to stop the server."
echo ""

python3 main.py
