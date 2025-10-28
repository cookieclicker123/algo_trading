#!/usr/bin/env python3
"""
Test WebSocket with proper rate limiting (3 seconds) and connection cleanup.
"""
import websocket
import json
import time
import threading
import os
import signal
import sys
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Get API key from environment
api_key = os.getenv('BENZINGA_API_KEY')
if not api_key:
    print("Error: BENZINGA_API_KEY not found in environment variables")
    exit(1)

# WebSocket URL
url = f'wss://api.benzinga.com/api/v1/news/stream'

# Track connection state
connected = False
message_count = 0
ws = None
stop_requested = False
last_request_time = 0
min_request_interval = 3.0  # 3 seconds between requests

def cleanup_connections():
    """Force close any existing WebSocket connections"""
    global ws, connected, stop_requested
    
    print("🧹 Cleaning up any existing WebSocket connections...")
    stop_requested = True
    
    if ws:
        try:
            ws.close()
            print("✅ WebSocket connection closed")
        except Exception as e:
            print(f"❌ Error closing WebSocket: {e}")
    
    connected = False
    time.sleep(2)  # Give more time for cleanup

def on_message(ws, message):
    global message_count
    message_count += 1
    print(f"📨 Message {message_count}: {message}")
    
    # Try to parse as JSON
    try:
        data = json.loads(message)
        print(f"✅ Parsed JSON: {json.dumps(data, indent=2)}")
    except json.JSONDecodeError:
        print("❌ Not JSON, raw message:", message)

def on_error(ws, error):
    print(f"❌ WebSocket error: {error}")

def on_close(ws, close_status_code, close_msg):
    global connected
    connected = False
    print(f"🔌 WebSocket closed: {close_status_code} - {close_msg}")

def on_open(ws):
    global connected
    connected = True
    print("✅ WebSocket connection opened successfully!")
    print("⏳ Waiting for messages...")

def signal_handler(sig, frame):
    print("\n🛑 Received interrupt signal. Cleaning up...")
    cleanup_connections()
    sys.exit(0)

# Set up signal handler for graceful shutdown
signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

# Clean up any existing connections first
cleanup_connections()

# Rate limiting: ensure we don't exceed 1 request per 3 seconds
current_time = time.time()
time_since_last_request = current_time - last_request_time
if time_since_last_request < min_request_interval:
    sleep_time = min_request_interval - time_since_last_request
    print(f"⏰ Rate limiting: sleeping for {sleep_time:.2f} seconds")
    time.sleep(sleep_time)

last_request_time = time.time()

# Create WebSocket connection with headers
headers = {
    'Authorization': api_key,
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
    'Origin': 'https://api.benzinga.com',
    'Accept-Encoding': 'gzip, deflate, br',
    'Accept-Language': 'en-US,en;q=0.9'
}

print(f"🔌 Connecting to: {url}")
print(f"🔑 Using API key: {api_key[:10]}...")

# Enable debug trace
websocket.enableTrace(True)

# Create WebSocket app
ws = websocket.WebSocketApp(url,
                          header=headers,
                          on_message=on_message,
                          on_error=on_error,
                          on_close=on_close,
                          on_open=on_open)

try:
    # Run the WebSocket connection
    ws.run_forever()
except KeyboardInterrupt:
    print("\n⌨️ Keyboard interrupt received. Cleaning up...")
    cleanup_connections()
except Exception as e:
    print(f"❌ Unexpected error: {e}")
    cleanup_connections()
finally:
    print("🏁 Test completed. All connections cleaned up.")