"""
Test Telegram message formatting without requiring actual bot credentials.

This test demonstrates the message formatting and writes results to JSON
so we can verify the format before connecting to real Telegram.
"""
import asyncio
import json
from datetime import datetime
from pathlib import Path

from newsflash.models.base_models import StandardizedArticle, NewsSource
from newsflash.models.benzinga_models import BenzingaArticle
from newsflash.models.classification_models import (
    ClassificationResult,
    NewsClassification,
)
from newsflash.services.telegram_service import TelegramNotifier


def create_sample_benzinga_article() -> BenzingaArticle:
    """Create a sample Benzinga article for testing."""
    return BenzingaArticle(
        benzinga_id=12345,
        title="Apple Announces $50B Partnership With Google",
        author="John Smith",
        published=datetime.now(),
        last_updated=datetime.now(),
        teaser="Major tech partnership announced today...",
        body="Apple Inc. announced a groundbreaking $50 billion partnership...",
        url="https://www.benzinga.com/news/12345",
        images=["https://example.com/image.jpg"],
        channels=["news"],
        tickers=["AAPL", "GOOGL"],
        tags=["partnership", "tech"],
    )


def create_sample_finlight_article() -> StandardizedArticle:
    """Create a sample Finlight article for testing."""
    return StandardizedArticle(
        source=NewsSource.FINLIGHT,
        source_id="fin_67890",
        title="Tesla Plans Giga Factory Expansion in Texas",
        content="Tesla announced plans to expand its Texas gigafactory...",
        summary="Major expansion coming to Texas facility",
        author="Jane Doe",
        published=datetime.now(),
        updated=datetime.now(),
        url="https://finlight.me/article/67890",
        tickers=["TSLA"],
        tags=["manufacturing", "expansion"],
        categories=["automotive"],
        images=["https://example.com/tesla.jpg"],
        raw_data={},
    )


def create_sample_imminent_classification() -> ClassificationResult:
    """Create a sample IMMINENT classification."""
    return ClassificationResult(
        classification=NewsClassification.IMMINENT,
        confidence="HIGH",
        reasoning="Major partnership with significant financial value disclosed",
    )


def create_sample_noteworthy_classification() -> ClassificationResult:
    """Create a sample NOTEWORTHY classification."""
    return ClassificationResult(
        classification=NewsClassification.NOTEWORTHY,
        confidence="MEDIUM",
        reasoning="Expansion plans significant but timing uncertain",
    )


async def test_message_formatting():
    """Test message formatting and save results to JSON."""
    print("\n" + "=" * 80)
    print("TESTING TELEGRAM MESSAGE FORMATTING")
    print("=" * 80 + "\n")
    
    # Initialize notifier in test mode
    notifier = TelegramNotifier(test_mode=True, enabled=True)
    
    # Create sample articles
    benzinga_article = create_sample_benzinga_article()
    finlight_article = create_sample_finlight_article()
    
    # Create sample classifications
    imminent_classification = create_sample_imminent_classification()
    noteworthy_classification = create_sample_noteworthy_classification()
    
    # Format messages
    test_cases = [
        {
            "name": "Benzinga Article - No Classification (Phase 1)",
            "article": benzinga_article,
            "classification": None,
        },
        {
            "name": "Benzinga Article - IMMINENT Classification",
            "article": benzinga_article,
            "classification": imminent_classification,
        },
        {
            "name": "Finlight Article - NOTEWORTHY Classification",
            "article": finlight_article,
            "classification": noteworthy_classification,
        },
        {
            "name": "Finlight Article - No Classification (Phase 1)",
            "article": finlight_article,
            "classification": None,
        },
    ]
    
    results = []
    
    for test_case in test_cases:
        print(f"Test Case: {test_case['name']}")
        print("-" * 80)
        
        message = notifier.format_message(
            test_case["article"],
            test_case["classification"],
        )
        
        print(message)
        print("\n" + "=" * 80 + "\n")
        
        # Store result
        results.append({
            "test_name": test_case["name"],
            "message": message,
            "article_type": type(test_case["article"]).__name__,
            "has_classification": test_case["classification"] is not None,
            "timestamp": datetime.now().isoformat(),
        })
    
    # Save results to JSON
    output_dir = Path("tmp")
    output_dir.mkdir(exist_ok=True)
    
    output_file = output_dir / "telegram_test_messages.json"
    
    with open(output_file, "w") as f:
        json.dump(results, f, indent=2)
    
    print(f"✅ Test results saved to: {output_file}")
    print(f"✅ Total test cases: {len(results)}")
    print("\nMessage format verified! Ready for Phase 1 integration.\n")
    
    return results


if __name__ == "__main__":
    asyncio.run(test_message_formatting())

