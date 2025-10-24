#!/usr/bin/env python3
"""
Test script for Benzinga WebSocket service.
Tests the WebSocket connection and message processing.
"""
import asyncio
import sys
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from newsflash.services.benzinga_websocket_service import BenzingaWebSocketService
from newsflash.utils.logging_config import setup_logging, get_logger

# Setup logging
setup_logging()
logger = get_logger(__name__)

class MockArticleProcessor:
    """Mock article processor for testing."""
    
    def __init__(self):
        self.processed_articles = []
    
    async def process_article(self, article, source):
        """Mock article processing."""
        self.processed_articles.append((article, source))
        logger.info(f"Mock processed article: {article.title[:50]}...")

async def test_benzinga_websocket():
    """Test Benzinga WebSocket connection."""
    logger.info("🧪 Testing Benzinga WebSocket Service")
    logger.info("=" * 50)
    
    # Your Benzinga token
    token = "bz.HR5VBBZI3AFDHBT4AEGGHTKNJ5X6HESX"
    
    # Create mock article processor
    mock_processor = MockArticleProcessor()
    
    # Create WebSocket service
    websocket_service = BenzingaWebSocketService(
        article_processor=mock_processor,
        token=token
    )
    
    try:
        logger.info("🔌 Starting WebSocket connection...")
        
        # Start the service (will run for 30 seconds)
        service_task = asyncio.create_task(websocket_service.start())
        
        # Wait for 30 seconds to collect messages
        await asyncio.sleep(30)
        
        # Stop the service
        websocket_service.is_running = False
        await websocket_service.stop()
        
        # Wait for service to stop
        try:
            await asyncio.wait_for(service_task, timeout=5)
        except asyncio.TimeoutError:
            logger.warning("Service didn't stop gracefully")
        
        # Report results
        stats = websocket_service.get_stats()
        logger.info("📊 WebSocket Test Results:")
        logger.info(f"   Messages received: {stats['messages_received']}")
        logger.info(f"   Articles processed: {stats['articles_processed']}")
        logger.info(f"   Connection attempts: {stats['connection_attempts']}")
        logger.info(f"   Last error: {stats['last_error']}")
        logger.info(f"   Mock articles processed: {len(mock_processor.processed_articles)}")
        
        if stats['messages_received'] > 0:
            logger.info("✅ WebSocket test SUCCESSFUL!")
            return True
        else:
            logger.warning("⚠️ No messages received - check token and connection")
            return False
            
    except Exception as e:
        logger.error(f"❌ WebSocket test FAILED: {e}")
        return False

if __name__ == "__main__":
    logger.info("🚨 IMPORTANT: This test will connect to Benzinga WebSocket for 30 seconds.")
    logger.info("             Make sure your token is valid and you have internet access.")
    
    result = asyncio.run(test_benzinga_websocket())
    
    if result:
        logger.info("✅ BENZINGA WEBSOCKET TEST PASSED!")
    else:
        logger.info("❌ BENZINGA WEBSOCKET TEST FAILED!")
