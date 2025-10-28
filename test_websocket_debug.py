#!/usr/bin/env python3
"""
Test WebSocket with JSON logging for detailed debugging.
Tests with 3-second rate limiting and logs everything to JSON.
"""
import websocket
import json
import time
import os
from datetime import datetime
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
test_data = {
    "test_started_at": datetime.now().isoformat(),
    "messages": [],
    "connection_events": [],
    "errors": [],
    "stats": {
        "total_messages": 0,
        "messages_with_data": 0,
        "connection_time": None,
        "duration_seconds": None
    }
}

def log_event(event_type, data):
    """Log an event to the test data."""
    entry = {
        "timestamp": datetime.now().isoformat(),
        "type": event_type,
        "data": data
    }
    test_data["connection_events"].append(entry)
    print(f"📋 [{event_type}] {json.dumps(data)[:100]}")

def on_message(ws, message):
    global message_count
    message_count += 1
    
    start_time = time.time()
    receive_time = datetime.now().isoformat()
    
    print(f"📨 Message {message_count}: {message[:200]}...")
    
    # Try to parse as JSON
    try:
        data = json.loads(message)
        parse_time = time.time() - start_time
        
        # Log detailed message info
        message_info = {
            "message_number": message_count,
            "received_at": receive_time,
            "parse_time_ms": round(parse_time * 1000, 3),
            "has_content": "content" in data or "data" in data,
            "message_type": data.get("kind", "unknown"),
            "message_length": len(message),
            "data": data
        }
        test_data["messages"].append(message_info)
        test_data["stats"]["total_messages"] += 1
        
        if "content" in data or "data" in data:
            test_data["stats"]["messages_with_data"] += 1
        
        print(f"✅ Parsed JSON in {parse_time*1000:.2f}ms")
        print(f"   Type: {data.get('kind', 'unknown')}")
        if "content" in data:
            print(f"   Content keys: {list(data['content'].keys())}")
    except json.JSONDecodeError:
        parse_time = time.time() - start_time
        print(f"❌ Not JSON, raw message: {message[:100]}")
        
        message_info = {
            "message_number": message_count,
            "received_at": receive_time,
            "parse_time_ms": round(parse_time * 1000, 3),
            "is_json": False,
            "raw_message": message[:500]
        }
        test_data["messages"].append(message_info)
        test_data["stats"]["total_messages"] += 1

def on_error(ws, error):
    error_info = {
        "timestamp": datetime.now().isoformat(),
        "error": str(error)
    }
    test_data["errors"].append(error_info)
    print(f"❌ WebSocket error: {error}")

def on_close(ws, close_status_code, close_msg):
    global connected
    connected = False
    
    log_event("connection_closed", {
        "close_status_code": close_status_code,
        "close_message": close_msg
    })
    
    print(f"🔌 WebSocket closed: {close_status_code} - {close_msg}")

def on_open(ws):
    global connected
    connected = True
    
    log_event("connection_opened", {
        "connected_at": datetime.now().isoformat()
    })
    
    print("✅ WebSocket connection opened successfully!")
    print("⏳ Waiting for messages...")

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

# Create WebSocket app
ws = websocket.WebSocketApp(url,
                          header=headers,
                          on_message=on_message,
                          on_error=on_error,
                          on_close=on_close,
                          on_open=on_open)

log_event("test_start", {
    "url": url,
    "api_key_prefix": api_key[:10]
})

try:
    # Run for 30 seconds to collect data
    print("🚀 Starting test (30 seconds)...")
    
    # Run in a separate thread with a timeout
    import threading
    
    def run_ws():
        ws.run_forever()
    
    ws_thread = threading.Thread(target=run_ws, daemon=True)
    ws_thread.start()
    
    # Wait for 30 seconds
    time.sleep(30)
    
    # Close connection
    ws.close()
    
except KeyboardInterrupt:
    print("\n⌨️ Keyboard interrupt received. Stopping...")
except Exception as e:
    log_event("unexpected_error", {"error": str(e)})
    print(f"❌ Unexpected error: {e}")
finally:
    # Finalize test data
    test_data["test_ended_at"] = datetime.now().isoformat()
    start_time = datetime.fromisoformat(test_data["test_started_at"])
    end_time = datetime.fromisoformat(test_data["test_ended_at"])
    duration = (end_time - start_time).total_seconds()
    test_data["stats"]["duration_seconds"] = round(duration, 2)
    
    # Save to JSON file
    output_file = "tmp/websocket_test_results.json"
    with open(output_file, 'w') as f:
        json.dump(test_data, f, indent=2)
    
    print(f"\n📊 Test Summary:")
    print(f"   Duration: {duration:.2f} seconds")
    print(f"   Total messages: {test_data['stats']['total_messages']}")
    print(f"   Messages with data: {test_data['stats']['messages_with_data']}")
    print(f"   Connection events: {len(test_data['connection_events'])}")
    print(f"   Errors: {len(test_data['errors'])}")
    print(f"\n💾 Detailed results saved to: {output_file}")
    print(f"🎯 You can now review the JSON file to analyze the news feed!")

    ws.close()
    print("🏁 Test completed. All connections cleaned up.")
