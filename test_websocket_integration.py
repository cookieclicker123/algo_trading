#!/usr/bin/env python3
"""
Test WebSocket article processing integration.
"""
import asyncio
import sys
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from newsflash.services.service_container import get_service_container, initialize_services
from newsflash.utils.logging_config import setup_logging

async def test_websocket_integration():
    """Test WebSocket article processing."""
    print("🧪 Testing WebSocket article processing integration")
    print("=" * 60)
    
    # Setup logging
    setup_logging()
    
    try:
        # Initialize services
        print("🔧 Initializing services...")
        container = initialize_services()
        
        # Start services
        print("🚀 Starting services...")
        await container.start_all_services()
        
        # Wait a bit for WebSocket to receive articles
        print("⏳ Waiting for WebSocket articles...")
        await asyncio.sleep(10)
        
        # Process WebSocket articles
        print("📝 Processing WebSocket articles...")
        await container.process_websocket_articles()
        
        # Check stats
        if 'benzinga_websocket' in container._services:
            websocket_service = container._services['benzinga_websocket']
            stats = websocket_service.get_stats()
            print(f"📊 WebSocket Stats: {stats}")
            
            queued_articles = websocket_service.get_queued_articles()
            print(f"📦 Queued articles: {len(queued_articles)}")
        
        print("✅ Test completed successfully!")
        
    except Exception as e:
        print(f"❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
    
    finally:
        # Stop services
        print("🛑 Stopping services...")
        await container.stop_all_services()

if __name__ == "__main__":
    asyncio.run(test_websocket_integration())
