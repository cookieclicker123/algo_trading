#!/usr/bin/env python3
"""
Completely standalone WebSocket test.
No dependencies, just pure websockets.
"""
import asyncio
import json
import websockets

async def test_websocket():
    """Test direct WebSocket connection to Benzinga."""
    print("🧪 Testing Direct Benzinga WebSocket Connection")
    print("=" * 50)
    
    # Your Benzinga token
    token = "bz.HR5VBBZI3AFDHBT4AEGGHTKNJ5X6HESX"
    websocket_url = f"wss://api.benzinga.com/api/v1/news/stream?token={token}"
    
    try:
        print("🔌 Connecting to Benzinga WebSocket...")
        print(f"URL: {websocket_url}")
        
        async with websockets.connect(
            websocket_url,
            ping_interval=30,
            ping_timeout=10,
            close_timeout=10
        ) as websocket:
            print("✅ Connected to Benzinga WebSocket!")
            
            # Wait for messages
            message_count = 0
            print("⏳ Waiting for messages...")
            
            async for message in websocket:
                message_count += 1
                print(f"📨 Message #{message_count} received ({len(message)} chars)")
                print(f"Content preview: {message[:200]}...")
                
                # Try to parse as JSON
                try:
                    data = json.loads(message)
                    print(f"✅ JSON parsed successfully: {type(data)}")
                    if isinstance(data, dict):
                        print(f"Keys: {list(data.keys())}")
                        if 'authors' in data:
                            print(f"Authors: {data['authors']}")
                        if 'teaser' in data:
                            print(f"Teaser: {data['teaser'][:100]}...")
                except json.JSONDecodeError:
                    print("❌ Not JSON format")
                
                # Stop after 3 messages
                if message_count >= 3:
                    print("🎯 Received 3 messages, stopping test")
                    break
            
            print(f"📊 Test completed: {message_count} messages received")
            return message_count > 0
            
    except Exception as e:
        print(f"❌ WebSocket test failed: {e}")
        return False

if __name__ == "__main__":
    print("🚨 IMPORTANT: This test will connect to Benzinga WebSocket.")
    print("             Make sure your token is valid and you have internet access.")
    
    result = asyncio.run(test_websocket())
    
    if result:
        print("✅ BENZINGA WEBSOCKET TEST PASSED!")
    else:
        print("❌ BENZINGA WEBSOCKET TEST FAILED!")
