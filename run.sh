#!/usr/bin/env bash
# Launch FastAPI backend + React frontend concurrently

echo "🚀 Starting Repo Context Copilot..."

# Ensure Redis is running
redis-server --daemonize yes 2>/dev/null || true

# Start FastAPI Backend
cd backend
uvicorn main:app --host 0.0.0.0 --port 8000 &
BACKEND_PID=$!
cd ..

# Wait for backend to be ready
echo "Waiting for FastAPI backend on http://localhost:8000..."
until curl -s http://localhost:8000/api/repos > /dev/null 2>&1; do
    sleep 1
done
echo "✅ Backend is ready!"

# Update the React app's CORS origin to match (optional)
# The FastAPI backend already allows all origins by default.

# Also add /auth/login CORS origin patch for the popup
# The backend serves /auth/login directly from port 8000,
# so no extra config needed.

# Start React frontend
cd frontend-react
npm run dev

# Cleanup on exit
trap "kill $BACKEND_PID" EXIT
