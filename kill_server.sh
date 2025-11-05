#!/bin/bash
# Kill all newsflash server processes

echo "Killing newsflash server processes..."

# Kill processes by name
pkill -f "python.*server.py" 2>/dev/null
pkill -f "uvicorn.*server" 2>/dev/null
pkill -f "python.*src.server" 2>/dev/null
pkill -f "python.*main.py" 2>/dev/null
pkill -f "newsflash" 2>/dev/null

# Kill processes on port 8000
lsof -ti :8000 | xargs kill -9 2>/dev/null

# Wait a moment
sleep 1

# Verify everything is killed
echo "Checking for remaining processes..."
REMAINING=$(ps aux | grep -E "python.*server|uvicorn.*server|newsflash" | grep -v grep | wc -l)
if [ "$REMAINING" -gt 0 ]; then
    echo "Force killing remaining processes..."
    ps aux | grep -E "python.*server|uvicorn.*server|newsflash" | grep -v grep | awk '{print $2}' | xargs kill -9 2>/dev/null
fi

# Final check
sleep 1
REMAINING=$(ps aux | grep -E "python.*server|uvicorn.*server|newsflash" | grep -v grep | wc -l)
if [ "$REMAINING" -eq 0 ]; then
    echo "✓ All server processes killed"
else
    echo "⚠ Some processes may still be running:"
    ps aux | grep -E "python.*server|uvicorn.*server|newsflash" | grep -v grep
fi
