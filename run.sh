#!/usr/bin/env bash
# Script to launch FastAPI backend and Streamlit frontend concurrently

echo "🚀 Starting Repo Context Copilot..."

# Ensure Redis server daemon is running
redis-server --daemonize yes 2>/dev/null || true

# Start FastAPI Backend in background
cd backend
uvicorn main:app --host 0.0.0.0 --port 8000 &
BACKEND_PID=$!
cd ..

# Wait for FastAPI backend to be fully initialized and ready
echo "Waiting for FastAPI backend to start on http://localhost:8000..."
until curl -s http://localhost:8000/api/repos > /dev/null 2>&1; do
    sleep 1
done
echo "✅ Backend is ready!"

# Start Streamlit Frontend
streamlit run frontend/app.py --server.port 8501

# Trap cleanup to terminate backend when script exits
trap "kill $BACKEND_PID" EXIT
