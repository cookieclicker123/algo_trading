"""
Test the STRICT 2-category classification (IMMINENT vs IGNORE only).
"""
import asyncio
import json
from datetime import datetime
from pathlib import Path
import os
from dotenv import load_dotenv

from newsflash.models.base_models import StandardizedArticle, NewsSource
from newsflash.services.news_classifier import NewsClassifier

load_dotenv()


def create_strict_test_articles():
    """Create test articles for IMMINENT vs IGNORE classification."""
    
    # IMMINENT examples (should be VERY rare)
    imminent_articles = [
        StandardizedArticle(
            source=NewsSource.BENZINGA,
            source_id="test_imminent_1",
            title="Apple Announces $150 Billion Acquisition of Tesla - Deal Closes Today",
            summary="Apple Inc. announced today a definitive agreement to acquire Tesla Inc. for $150 billion cash, closing immediately.",
            published=datetime.now(),
            tickers=["AAPL", "TSLA"],
            raw_data={}
        ),
        StandardizedArticle(
            source=NewsSource.FINLIGHT,
            source_id="test_imminent_2",
            title="FDA Grants Full Approval for Moderna COVID Treatment - $8B Revenue Expected",
            summary="FDA granted immediate full approval with projected $8B annual revenue starting this quarter.",
            published=datetime.now(),
            tickers=["MRNA"],
            raw_data={}
        ),
    ]
    
    # IGNORE examples (should be 95%+ of news)
    ignore_articles = [
        StandardizedArticle(
            source=NewsSource.BENZINGA,
            source_id="test_ignore_1",
            title="Goldman Sachs Upgrades Amazon to Buy, Raises Price Target to $200",
            summary="Analyst upgrade based on AWS growth potential.",
            published=datetime.now(),
            tickers=["AMZN"],
            raw_data={}
        ),
        StandardizedArticle(
            source=NewsSource.FINLIGHT,
            source_id="test_ignore_2",
            title="Microsoft Plans to Open AI Research Lab in 2026",
            summary="Company considering expansion of AI research facilities in coming years.",
            published=datetime.now(),
            tickers=["MSFT"],
            raw_data={}
        ),
        StandardizedArticle(
            source=NewsSource.BENZINGA,
            source_id="test_ignore_3",
            title="Tesla CEO Speaks at Industry Conference Next Week",
            summary="Elon Musk to present at automotive summit discussing industry trends.",
            published=datetime.now(),
            tickers=["TSLA"],
            raw_data={}
        ),
        StandardizedArticle(
            source=NewsSource.FINLIGHT,
            source_id="test_ignore_4",
            title="Nvidia in Talks for Potential Partnership with Cloud Provider",
            summary="Discussions underway but no details or timeframe disclosed.",
            published=datetime.now(),
            tickers=["NVDA"],
            raw_data={}
        ),
    ]
    
    return {
        "IMMINENT": imminent_articles,
        "IGNORE": ignore_articles,
    }


async def test_strict_classification():
    """Test the strict 2-category classifier."""
    print("\n" + "=" * 80)
    print("TESTING STRICT CLASSIFICATION (IMMINENT vs IGNORE ONLY)")
    print("=" * 80 + "\n")
    
    api_key = os.getenv("GROQ_API_KEY", "")
    
    if not api_key:
        print("❌ GROQ_API_KEY not configured")
        return
    
    print(f"✅ API key configured")
    print(f"📊 Model: llama-3.3-70b-versatile")
    print(f"🎯 Target: 95-98% IGNORE, 2-5% IMMINENT\n")
    
    classifier = NewsClassifier(
        api_key=api_key,
        model="llama-3.3-70b-versatile",
        enabled=True
    )
    
    test_articles = create_strict_test_articles()
    
    results = {
        "correct": 0,
        "incorrect": 0,
        "should_notify": 0,
        "total": 0,
    }
    
    for expected_category, articles in test_articles.items():
        print(f"\n{'─' * 80}")
        print(f"Expected: {expected_category}")
        print(f"{'─' * 80}\n")
        
        for article in articles:
            print(f"📰 {article.title}")
            print(f"   Tickers: {', '.join(article.tickers) if article.tickers else 'None'}")
            
            classification = await classifier.classify_article(article)
            
            if classification:
                actual = classification.classification.value.upper()
                is_correct = actual == expected_category
                should_notify = classifier.should_notify(classification)
                
                symbol = "✅" if is_correct else "❌"
                
                print(f"\n   {symbol} Classification: {classification.classification.value}")
                print(f"   Confidence: {classification.confidence}")
                print(f"   Reasoning: {classification.reasoning}")
                print(f"   📱 Send to Telegram: {'YES' if should_notify else 'NO'}")
                
                if is_correct:
                    results["correct"] += 1
                else:
                    results["incorrect"] += 1
                    print(f"   ⚠️  Expected: {expected_category}")
                
                if should_notify:
                    results["should_notify"] += 1
                
                results["total"] += 1
            else:
                print("   ❌ Classification failed")
            
            print()
    
    print("\n" + "=" * 80)
    print("RESULTS")
    print("=" * 80)
    
    accuracy = (results["correct"] / results["total"] * 100) if results["total"] > 0 else 0
    notify_rate = (results["should_notify"] / results["total"] * 100) if results["total"] > 0 else 0
    
    print(f"\n✅ Accuracy: {results['correct']}/{results['total']} ({accuracy:.1f}%)")
    print(f"📱 Notification Rate: {results['should_notify']}/{results['total']} ({notify_rate:.1f}%)")
    print(f"🎯 Target: 2-5% notification rate for high signal\n")
    
    if notify_rate <= 40:
        print("🎉 Good signal-to-noise ratio!")
    else:
        print("⚠️  Too many notifications - prompt may need tuning")


if __name__ == "__main__":
    asyncio.run(test_strict_classification())

