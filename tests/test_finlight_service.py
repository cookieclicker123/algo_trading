#!/usr/bin/env python3
"""
Test script for the Finlight WebSocket service.
"""
import asyncio
import pytest
from pathlib import Path
import sys
from unittest.mock import AsyncMock, MagicMock

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from newsflash.services.finlight_service import FinlightWebSocketService
from newsflash.models.base_models import NewsSource, StandardizedArticle
from newsflash.utils.logging_config import setup_logging, get_logger

# Setup logging
setup_logging()
logger = get_logger(__name__)


class MockArticleCallback:
    """Mock callback to track received articles."""
    
    def __init__(self):
        self.received_articles = []
    
    def __call__(self, article: StandardizedArticle):
        """Handle article callback."""
        self.received_articles.append(article)
        logger.info(f"Mock callback received article: {article.title[:50]}...")


@pytest.mark.asyncio
async def test_finlight_service_initialization():
    """Test that the Finlight service initializes correctly."""
    mock_callback = MockArticleCallback()
    service = FinlightWebSocketService(mock_callback)
    
    # Check initial state
    assert service.is_connected is False
    assert service.is_running is False
    assert service.processor.source == NewsSource.FINLIGHT
    assert service.article_callback == mock_callback


def test_finlight_service_stats():
    """Test that the service returns correct stats."""
    mock_callback = MockArticleCallback()
    service = FinlightWebSocketService(mock_callback)
    
    stats = service.get_stats()
    
    # Check stats structure
    assert "is_connected" in stats
    assert "is_running" in stats
    assert "reconnect_attempts" in stats
    assert "max_reconnect_attempts" in stats
    assert "source" in stats
    assert stats["source"] == "finlight"
    assert isinstance(stats["is_connected"], bool)
    assert isinstance(stats["is_running"], bool)
    assert isinstance(stats["reconnect_attempts"], int)


@pytest.mark.asyncio
async def test_finlight_article_processing():
    """Test that articles are processed correctly."""
    mock_callback = MockArticleCallback()
    service = FinlightWebSocketService(mock_callback)
    
    # Mock article data (typical Finlight format)
    mock_article_data = {
        "id": "12345",
        "title": "Test Article Title",
        "content": "Test article content",
        "summary": "Test summary",
        "author": "Test Author",
        "published_at": "2025-01-01T12:00:00Z",
        "updated_at": "2025-01-01T12:01:00Z",
        "url": "https://example.com/article",
        "tickers": ["AAPL", "MSFT"],
        "tags": ["earnings", "tech"],
        "category": "financial",
        "source": "test_source"
    }
    
    # Test the article processing
    service._on_article(mock_article_data)
    
    # Check that callback was called
    assert len(mock_callback.received_articles) == 1
    
    article = mock_callback.received_articles[0]
    assert isinstance(article, StandardizedArticle)
    assert article.source == NewsSource.FINLIGHT
    assert article.source_id == "12345"
    assert article.title == "Test Article Title"
    assert article.tickers == ["AAPL", "MSFT"]


def test_finlight_service_initialization():
    """Test that the Finlight service initializes correctly."""
    mock_callback = MockArticleCallback()
    service = FinlightWebSocketService(mock_callback)
    
    # Test that the service is initialized correctly
    assert service.is_connected is False
    assert service.is_running is False
    assert service.processor.source == NewsSource.FINLIGHT
    assert service.article_callback == mock_callback
    assert service.reconnect_attempts == 0
    assert service.max_reconnect_attempts == 10


@pytest.mark.asyncio
async def test_finlight_disconnect():
    """Test that disconnection works correctly."""
    mock_callback = MockArticleCallback()
    service = FinlightWebSocketService(mock_callback)
    
    # Should handle disconnect gracefully even when not connected
    await service.disconnect()
    assert service.is_connected is False


if __name__ == "__main__":
    # Run a quick test when executed directly
    asyncio.run(test_finlight_service_initialization())
