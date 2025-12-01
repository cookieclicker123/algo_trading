"""
Unit tests for storage domain models.
"""
import pytest
from datetime import datetime
from newsflash.domain.storage.models import StoredArticle, AuditEntry


class TestStoredArticle:
    """Tests for StoredArticle domain model."""
    
    def test_create_stored_article(self):
        """Test creating StoredArticle."""
        stored_article = StoredArticle(
            article_id="benzinga:12345",
            source="benzinga",
            source_id="12345",
            title="Test Article",
            content="Test content",
            published_at=datetime.now(),
            tickers=frozenset(["AAPL"]),
            tags=frozenset(),
            categories=frozenset()
        )
        
        assert stored_article.article_id == "benzinga:12345"
        assert stored_article.source == "benzinga"
        assert stored_article.title == "Test Article"
        assert stored_article.has_tickers() is True
        assert stored_article.get_primary_ticker() == "AAPL"
    
    def test_has_tickers(self):
        """Test has_tickers method."""
        article_with_tickers = StoredArticle(
            article_id="test:1",
            source="test",
            source_id="1",
            title="Test",
            published_at=datetime.now(),
            tickers=frozenset(["AAPL"])
        )
        
        article_without_tickers = StoredArticle(
            article_id="test:2",
            source="test",
            source_id="2",
            title="Test",
            published_at=datetime.now(),
            tickers=frozenset()
        )
        
        assert article_with_tickers.has_tickers() is True
        assert article_without_tickers.has_tickers() is False
    
    def test_get_primary_ticker(self):
        """Test get_primary_ticker method."""
        article = StoredArticle(
            article_id="test:1",
            source="test",
            source_id="1",
            title="Test",
            published_at=datetime.now(),
            tickers=frozenset(["AAPL", "MSFT"])
        )
        
        primary = article.get_primary_ticker()
        assert primary in ["AAPL", "MSFT"]  # frozenset order is not guaranteed


class TestAuditEntry:
    """Tests for AuditEntry domain model."""
    
    def test_create_audit_entry(self):
        """Test creating AuditEntry."""
        audit_entry = AuditEntry(
            article_id="benzinga:12345",
            article_title="Test Article",
            article_tickers=frozenset(["AAPL"]),
            classification="imminent",
            confidence="HIGH",
            reasoning="Test reasoning",
            source="benzinga",
            news_received_at=datetime.now(),
            classified_at=datetime.now()
        )
        
        assert audit_entry.article_id == "benzinga:12345"
        assert audit_entry.article_title == "Test Article"
        assert audit_entry.classification == "imminent"
        assert audit_entry.confidence == "HIGH"
        assert audit_entry.is_imminent() is True
    
    def test_is_imminent(self):
        """Test is_imminent method."""
        imminent_entry = AuditEntry(
            article_id="test:1",
            article_title="Test",
            article_tickers=frozenset(),
            classification="imminent",
            confidence="HIGH",
            reasoning="Test",
            source="test",
            news_received_at=datetime.now(),
            classified_at=datetime.now()
        )
        
        ignore_entry = AuditEntry(
            article_id="test:2",
            article_title="Test",
            article_tickers=frozenset(),
            classification="ignore",
            confidence="HIGH",
            reasoning="Test",
            source="test",
            news_received_at=datetime.now(),
            classified_at=datetime.now()
        )
        
        assert imminent_entry.is_imminent() is True
        assert ignore_entry.is_imminent() is False
    
    def test_has_trade_details(self):
        """Test has_trade_details method."""
        entry_with_trade = AuditEntry(
            article_id="test:1",
            article_title="Test",
            article_tickers=frozenset(),
            classification="imminent",
            confidence="HIGH",
            reasoning="Test",
            source="test",
            news_received_at=datetime.now(),
            classified_at=datetime.now(),
            trade_details={"entry_price": 100.0}
        )
        
        entry_without_trade = AuditEntry(
            article_id="test:2",
            article_title="Test",
            article_tickers=frozenset(),
            classification="imminent",
            confidence="HIGH",
            reasoning="Test",
            source="test",
            news_received_at=datetime.now(),
            classified_at=datetime.now(),
            trade_details={}
        )
        
        assert entry_with_trade.has_trade_details() is True
        assert entry_without_trade.has_trade_details() is False

