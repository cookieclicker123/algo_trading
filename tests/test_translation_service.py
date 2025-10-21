#!/usr/bin/env python3
"""
Test script for the translation service.
"""
import asyncio
import json
from pathlib import Path
import sys

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from newsflash.services.translation_service import TranslationService
from newsflash.config.settings import get_classification_config
from newsflash.utils.logging_config import setup_logging, get_logger

# Setup logging
setup_logging()
logger = get_logger(__name__)


async def test_translation_service():
    """Test the translation service with sample financial news."""
    
    # Get configuration
    config = get_classification_config()
    
    # Initialize translation service
    translator = TranslationService(
        api_key=config["api_key"],
        enabled=True
    )
    
    if not translator.enabled:
        logger.error("Translation service not enabled")
        return
    
    # Sample message data (similar to what would be sent to Telegram)
    sample_messages = [
        {
            "emoji": "🚀",
            "classification": "IMMINENT",
            "confidence": "HIGH",
            "tickers": "Company Symbol: 'EPAM'",
            "headline": "EPAM Authorizes $1 Billion Share Repurchase Program, Highlighting Cash Flow And AI-Native Strategy",
            "url": "https://www.benzinga.com/news/earnings/25/10/48298616/epam-authorizes-1-billion-share-repurchase-program-highlighting-cash-flow-and-ai-native-strategy",
            "source": "Benzinga",
            "published_gmt": "2025-10-21T18:30:00+00:00"
        },
        {
            "emoji": "💰",
            "classification": "IMMINENT", 
            "confidence": "HIGH",
            "tickers": "Company Symbol: 'AAPL'",
            "headline": "Apple Announces Major Partnership with Microsoft Worth $2 Billion for AI Integration",
            "url": "https://example.com/apple-microsoft-partnership",
            "source": "Benzinga",
            "published_gmt": "2025-10-21T19:15:00+00:00"
        },
        {
            "emoji": "🏛️",
            "classification": "IMMINENT",
            "confidence": "MEDIUM", 
            "tickers": "Company Symbol: 'BA'",
            "headline": "Boeing Secures $500 Million Defense Contract from Pentagon for New Aircraft Systems",
            "url": "https://example.com/boeing-contract",
            "source": "Finlight",
            "published_gmt": "2025-10-21T20:45:00+00:00"
        }
    ]
    
    logger.info(f"Testing translation service with {len(sample_messages)} sample messages")
    
    for i, message in enumerate(sample_messages, 1):
        logger.info(f"\n--- Testing Message {i} ---")
        logger.info("Original English:", **message)
        
        try:
            # Translate the message
            translated_message = await translator.translate_to_chinese(message)
            
            logger.info("Translated Chinese:", **translated_message)
            
            # Verify the structure is maintained
            assert translated_message.keys() == message.keys(), "Translation changed JSON structure"
            logger.info("✅ Translation structure verified")
            
            # Save to test file
            test_results = {
                "original": message,
                "translated": translated_message
            }
            
            with open(f"tmp/translation_test_{i}.json", "w", encoding="utf-8") as f:
                json.dump(test_results, f, ensure_ascii=False, indent=2)
            
            logger.info(f"✅ Translation test {i} completed successfully")
            
        except Exception as e:
            logger.error(f"❌ Translation test {i} failed", error=str(e))
    
    logger.info("\n🎉 Translation service testing completed!")


if __name__ == "__main__":
    asyncio.run(test_translation_service())
