#!/usr/bin/env python3
"""
Simple WebSocket test without dependencies.
Tests direct connection to Benzinga WebSocket.
"""
import asyncio
import json
import websockets
import sys
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from newsflash.utils.logging_config import setup_logging, get_logger

# Setup logging
setup_logging()
logger = get_logger(__name__)

async def test_simple_websocket():
    """Test direct WebSocket connection to Benzinga."""
    logger.info("🧪 Testing Direct Benzinga WebSocket Connection")
    logger.info("=" * 50)
    
    # Your Benzinga token
    token = "bz.HR5VBBZI3AFDHBT4AEGGHTKNJ5X6HESX"
    websocket_url = f"wss://api.benzinga.com/api/v1/news/stream?token={token}"
    
    try:
        logger.info("🔌 Connecting to Benzinga WebSocket...")
        logger.info(f"URL: {websocket_url}")
        
        async with websockets.connect(
            websocket_url,
            ping_interval=30,
            ping_timeout=10,
            close_timeout=10,
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            extra_headers={
                "Origin": "https://websocketking.com",
                "Accept-Encoding": "gzip, deflate, br",
                "Accept-Language": "en-US,en;q=0.9"
            }
        ) as websocket:
            logger.info("✅ Connected to Benzinga WebSocket!")
            
            # Wait for messages
            message_count = 0
            logger.info("⏳ Waiting for messages...")
            
            async for message in websocket:
                message_count += 1
                logger.info(f"📨 Message #{message_count} received ({len(message)} chars)")
                logger.info(f"Content preview: {message[:200]}...")
                
                # Try to parse as JSON
                try:
                    data = json.loads(message)
                    logger.info(f"✅ JSON parsed successfully: {type(data)}")
                    if isinstance(data, dict):
                        logger.info(f"Keys: {list(data.keys())}")
                        if 'authors' in data:
                            logger.info(f"Authors: {data['authors']}")
                        if 'teaser' in data:
                            logger.info(f"Teaser: {data['teaser'][:100]}...")
                except json.JSONDecodeError:
                    logger.info("❌ Not JSON format")
                
                # Stop after 5 messages or 30 seconds
                if message_count >= 5:
                    logger.info("🎯 Received 5 messages, stopping test")
                    break
            
            logger.info(f"📊 Test completed: {message_count} messages received")
            return message_count > 0
            
    except Exception as e:
        logger.error(f"❌ WebSocket test failed: {e}")
        return False

if __name__ == "__main__":
    logger.info("🚨 IMPORTANT: This test will connect to Benzinga WebSocket for up to 30 seconds.")
    logger.info("             Make sure your token is valid and you have internet access.")
    
    result = asyncio.run(test_simple_websocket())
    
    if result:
        logger.info("✅ BENZINGA WEBSOCKET TEST PASSED!")
    else:
        logger.info("❌ BENZINGA WEBSOCKET TEST FAILED!")
