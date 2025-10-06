#!/usr/bin/env python3
"""
Test script for standardized models and multi-source processing.
"""
import pytest
from datetime import datetime, timezone
from pathlib import Path
import sys

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from newsflash.models.base_models import StandardizedArticle, NewsSource, MultiSourceStats, ArticleProcessor
from newsflash.models.benzinga_models import BenzingaArticleProcessor
from newsflash.models.finlight_models import FinlightArticleProcessor
from newsflash.utils.logging_config import setup_logging, get_logger

# Setup logging
setup_logging()
logger = get_logger(__name__)


def test_news_source_enum():
    """Test NewsSource enum values."""
    assert NewsSource.BENZINGA == "benzinga"
    assert NewsSource.FINLIGHT == "finlight"
    
    # Test enum iteration
    sources = list(NewsSource)
    assert NewsSource.BENZINGA in sources
    assert NewsSource.FINLIGHT in sources


def test_standardized_article_creation():
    """Test creating a standardized article."""
    article = StandardizedArticle(
        source=NewsSource.BENZINGA,
        source_id="12345",
        title="Test Article",
        content="Test content",
        summary="Test summary",
        author="Test Author",
        published=datetime.now(timezone.utc),
        updated=datetime.now(timezone.utc),
        url="https://example.com",
        tickers=["AAPL", "MSFT"],
        tags=["earnings", "tech"],
        categories=["financial"],
        images=["https://example.com/image.jpg"],
        raw_data={"test": "data"}
    )
    
    assert article.source == NewsSource.BENZINGA
    assert article.source_id == "12345"
    assert article.title == "Test Article"
    assert article.tickers == ["AAPL", "MSFT"]  # Should be uppercase
    assert len(article.images) == 1
    assert article.is_recent(hours=1) is True


def test_standardized_article_ticker_validation():
    """Test that tickers are properly validated and formatted."""
    article = StandardizedArticle(
        source=NewsSource.BENZINGA,
        source_id="12345",
        title="Test Article",
        published=datetime.now(timezone.utc),
        tickers=["aapl", " msft ", "  ", "invalid ticker", "GOOGL"],
        raw_data={}
    )
    
    # Should be uppercase, trimmed, but not filtered (invalid tickers are still included)
    assert article.tickers == ["AAPL", "MSFT", "INVALID TICKER", "GOOGL"]


def test_standardized_article_datetime_parsing():
    """Test datetime parsing from various formats."""
    # Test ISO format with Z
    article1 = StandardizedArticle(
        source=NewsSource.BENZINGA,
        source_id="12345",
        title="Test Article",
        published="2025-01-01T12:00:00Z",
        raw_data={}
    )
    assert isinstance(article1.published, datetime)
    
    # Test ISO format with timezone
    article2 = StandardizedArticle(
        source=NewsSource.BENZINGA,
        source_id="12345",
        title="Test Article",
        published="2025-01-01T12:00:00+00:00",
        raw_data={}
    )
    assert isinstance(article2.published, datetime)


def test_standardized_article_trading_relevance():
    """Test trading relevance score calculation."""
    # High relevance article
    high_relevance = StandardizedArticle(
        source=NewsSource.BENZINGA,
        source_id="12345",
        title="Apple Reports Record Earnings Beat Expectations",
        content="Apple reported earnings that beat analyst expectations...",
        summary="Earnings beat",
        published=datetime.now(timezone.utc),
        tickers=["AAPL", "MSFT", "GOOGL", "TSLA"],
        categories=["exclusives", "earnings"],
        raw_data={}
    )
    
    # Low relevance article
    low_relevance = StandardizedArticle(
        source=NewsSource.BENZINGA,
        source_id="12346",
        title="General Market Commentary",
        content="The market had a quiet day...",
        summary="Market commentary",
        published=datetime.now(timezone.utc),
        tickers=["SPY"],
        categories=["general"],
        raw_data={}
    )
    
    assert high_relevance.trading_relevance_score > low_relevance.trading_relevance_score
    assert high_relevance.trading_relevance_score >= 5  # Should be high relevance
    assert low_relevance.trading_relevance_score < 5   # Should be low relevance


def test_benzinga_article_processor():
    """Test Benzinga article processor."""
    processor = BenzingaArticleProcessor()
    
    # Mock Benzinga article data
    benzinga_data = {
        "benzinga_id": 12345,
        "author": "Test Author",
        "published": "2025-01-01T12:00:00Z",
        "last_updated": "2025-01-01T12:01:00Z",
        "title": "Test Benzinga Article",
        "teaser": "Test teaser",
        "body": "Test body content",
        "url": "https://benzinga.com/test",
        "images": ["https://benzinga.com/image.jpg"],
        "channels": ["exclusives"],
        "tickers": ["AAPL"],
        "tags": ["earnings"]
    }
    
    standardized = processor.process_raw_article(benzinga_data)
    
    assert isinstance(standardized, StandardizedArticle)
    assert standardized.source == NewsSource.BENZINGA
    assert standardized.source_id == "12345"
    assert standardized.title == "Test Benzinga Article"
    assert standardized.content == "Test body content"
    assert standardized.summary == "Test teaser"
    assert standardized.categories == ["exclusives"]


def test_finlight_article_processor():
    """Test Finlight article processor."""
    processor = FinlightArticleProcessor()
    
    # Mock Finlight article data (using string dates as expected by the model)
    finlight_data = {
        "id": "67890",
        "title": "Test Finlight Article",
        "content": "Test content",
        "summary": "Test summary",
        "author": "Test Author",
        "published_at": "2025-01-01T12:00:00Z",
        "updated_at": "2025-01-01T12:01:00Z",
        "url": "https://finlight.com/test",
        "tickers": ["MSFT"],
        "tags": ["tech"],
        "category": "financial",
        "source": "test_source"
    }
    
    # The processor should handle string dates correctly
    standardized = processor.process_raw_article(finlight_data)
    
    assert isinstance(standardized, StandardizedArticle)
    assert standardized.source == NewsSource.FINLIGHT
    assert standardized.source_id == "67890"
    assert standardized.title == "Test Finlight Article"
    assert standardized.content == "Test content"
    assert standardized.summary == "Test summary"
    assert standardized.categories == ["financial"]


def test_finlight_processor_error_handling():
    """Test Finlight processor handles invalid data gracefully."""
    processor = FinlightArticleProcessor()
    
    # Invalid data (missing required fields)
    invalid_data = {
        "title": "Test Article",  # Only title provided
        "invalid_field": "should be ignored"
    }
    
    standardized = processor.process_raw_article(invalid_data)
    
    assert isinstance(standardized, StandardizedArticle)
    assert standardized.source == NewsSource.FINLIGHT
    assert standardized.title == "Test Article"
    assert standardized.source_id is not None  # Should generate an ID


def test_multi_source_stats():
    """Test multi-source statistics tracking."""
    stats = MultiSourceStats()
    
    # Add stats for different sources
    stats.add_source_stats(NewsSource.BENZINGA, {
        "articles_processed": 100,
        "last_article_time": datetime.now(timezone.utc)
    })
    
    stats.add_source_stats(NewsSource.FINLIGHT, {
        "articles_processed": 50,
        "last_article_time": datetime.now(timezone.utc)
    })
    
    assert len(stats.sources) == 2
    assert NewsSource.BENZINGA in stats.sources
    assert NewsSource.FINLIGHT in stats.sources
    assert stats.get_total_articles() == 150


if __name__ == "__main__":
    # Run a quick test when executed directly
    test_standardized_article_creation()
    test_news_source_enum()
    print("All basic tests passed!")
