"""
Simple validation script to verify Phase 1 implementation.
Doesn't require pytest - just runs basic checks.
"""
import sys
from pathlib import Path

def test_imports():
    """Test that all new modules can be imported."""
    print("Testing imports...")
    
    try:
        from newsflash.models.classification_models import NewsClassification, ClassificationResult
        print("  ✅ Classification models imported")
    except ImportError as e:
        print(f"  ❌ Failed to import classification models: {e}")
        return False
    
    try:
        from newsflash.services.telegram_service import TelegramNotifier
        print("  ✅ Telegram service imported")
    except ImportError as e:
        print(f"  ❌ Failed to import Telegram service: {e}")
        return False
    
    try:
        from newsflash.services.article_processor import ArticleProcessor
        print("  ✅ Article processor imported")
    except ImportError as e:
        print(f"  ❌ Failed to import article processor: {e}")
        return False
    
    try:
        from newsflash.services.feed_manager import FeedManager
        print("  ✅ Feed manager imported")
    except ImportError as e:
        print(f"  ❌ Failed to import feed manager: {e}")
        return False
    
    return True


def test_classification_model():
    """Test classification model creation."""
    print("\nTesting classification models...")
    
    from newsflash.models.classification_models import NewsClassification, ClassificationResult
    
    try:
        result = ClassificationResult(
            classification=NewsClassification.IMMINENT,
            confidence="HIGH",
            reasoning="Test reasoning"
        )
        print(f"  ✅ Created classification result: {result.classification}")
        return True
    except Exception as e:
        print(f"  ❌ Failed to create classification result: {e}")
        return False


def test_telegram_service():
    """Test Telegram service initialization."""
    print("\nTesting Telegram service...")
    
    from newsflash.services.telegram_service import TelegramNotifier
    
    try:
        # Test mode - no credentials needed
        notifier = TelegramNotifier(test_mode=True, enabled=True)
        print("  ✅ Created TelegramNotifier in test mode")
        
        # Test message formatting
        from newsflash.models.base_models import StandardizedArticle, NewsSource
        from datetime import datetime
        
        article = StandardizedArticle(
            source=NewsSource.BENZINGA,
            source_id="test_123",
            title="Test Article",
            published=datetime.now(),
            tickers=["AAPL"],
            raw_data={}
        )
        
        message = notifier.format_message(article, None)
        if "Test Article" in message and "AAPL" in message:
            print("  ✅ Message formatting works")
            return True
        else:
            print("  ❌ Message formatting failed")
            return False
            
    except Exception as e:
        print(f"  ❌ Failed to test Telegram service: {e}")
        return False


def test_article_processor():
    """Test ArticleProcessor with Telegram integration."""
    print("\nTesting ArticleProcessor...")
    
    from newsflash.services.article_processor import ArticleProcessor
    
    try:
        processor = ArticleProcessor()
        print(f"  ✅ Created ArticleProcessor")
        print(f"  ℹ️  Telegram enabled: {processor.telegram.enabled}")
        print(f"  ℹ️  Telegram test mode: {processor.telegram.test_mode}")
        return True
    except Exception as e:
        print(f"  ❌ Failed to create ArticleProcessor: {e}")
        return False


def test_feed_manager():
    """Test FeedManager initialization."""
    print("\nTesting FeedManager...")
    
    from newsflash.services.feed_manager import FeedManager
    
    try:
        manager = FeedManager()
        print(f"  ✅ Created FeedManager")
        print(f"  ℹ️  Available sources: {manager.get_available_sources()}")
        print(f"  ℹ️  Telegram enabled: {manager.article_processor.telegram.enabled}")
        return True
    except Exception as e:
        print(f"  ❌ Failed to create FeedManager: {e}")
        return False


def test_config():
    """Test configuration loading."""
    print("\nTesting configuration...")
    
    from newsflash.config.settings import get_telegram_config
    
    try:
        config = get_telegram_config()
        print(f"  ✅ Telegram config loaded")
        print(f"  ℹ️  Enabled: {config['enabled']}")
        print(f"  ℹ️  Has bot token: {'***' if config['bot_token'] else 'No'}")
        print(f"  ℹ️  Has chat ID: {'***' if config['chat_id'] else 'No'}")
        return True
    except Exception as e:
        print(f"  ❌ Failed to load config: {e}")
        return False


def main():
    """Run all validation tests."""
    print("=" * 80)
    print("PHASE 1 VALIDATION")
    print("=" * 80)
    
    tests = [
        test_imports,
        test_classification_model,
        test_telegram_service,
        test_article_processor,
        test_feed_manager,
        test_config,
    ]
    
    results = []
    for test in tests:
        try:
            results.append(test())
        except Exception as e:
            print(f"\n❌ Test {test.__name__} crashed: {e}")
            results.append(False)
    
    print("\n" + "=" * 80)
    print(f"RESULTS: {sum(results)}/{len(results)} tests passed")
    print("=" * 80)
    
    if all(results):
        print("\n✅ ALL TESTS PASSED - Phase 1 implementation is valid!")
        print("\nNext steps:")
        print("1. Create Telegram bot via @BotFather")
        print("2. Get chat ID from @userinfobot")
        print("3. Update .env with credentials")
        print("4. Run: python -m tests.test_telegram_bot_connection")
        return 0
    else:
        print("\n❌ SOME TESTS FAILED - Review errors above")
        return 1


if __name__ == "__main__":
    sys.exit(main())


