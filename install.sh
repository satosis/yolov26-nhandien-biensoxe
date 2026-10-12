#!/bin/bash

PROJECT_ROOT=$(pwd)
FRONTEND_DIR="$PROJECT_ROOT/frontend"
BACKEND_DIR="$PROJECT_ROOT/backend"

echo "--------------------------------------------------------"
echo "🛠️  Automated Project Setup & Run 🛠️"
echo "--------------------------------------------------------"

if ! command -v python3 &> /dev/null; then
    echo "❌ Python3 NOT found. Please install Python 3.10+ and try again."
    exit 1
fi

echo "📦 Installing/Updating dependencies..."
python3 -m pip install --upgrade pip > /dev/null 2>&1
python3 -m pip install -r backend/requirements.txt > /dev/null 2>&1
python3 -m pip install watchdog > /dev/null 2>&1
python3 -m pip install onnxruntime-silicon 2>/dev/null || python3 -m pip install onnxruntime > /dev/null 2>&1

echo "🧹 Cleaning up old processes..."
pkill -f "uvicorn" || true
pkill -f "python3 backend/main.py" || true
sleep 1

export PYTHONPATH=$PYTHONPATH:$PROJECT_ROOT
echo "🚀 Starting Java-style Hot Reload..."

function cleanup_all() {
    echo ""
    echo "🛑 Stopping Project..."
    pkill -P $$
    exit
}
trap cleanup_all SIGINT SIGTERM

(
    cd "$FRONTEND_DIR"
    uvicorn app:app --host 0.0.0.0 --port 8000 --reload --log-level warning
) &

sleep 2
watchmedo auto-restart \
    --directory="$BACKEND_DIR" \
    --pattern="*.py" \
    --recursive \
    -- python3 "$BACKEND_DIR/main.py" &

echo "--------------------------------------------------------"
echo "✅ Everything is running!"
echo "👉 Web UI: http://localhost:8000"
echo "--------------------------------------------------------"

wait
