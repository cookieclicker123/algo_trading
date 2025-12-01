"""
Unit tests for storage domain factories.
"""
import pytest
from datetime import datetime
from newsflash.domain.storage.factories import StoredArticleFactory, AuditEntryFactory
from newsflash.domain.websocket.models import Article, ArticleSource
from newsflash.domain.classification.models import ClassificationResult, ClassificationCategory, ClassificationConfidence


class TestStoredArticleFactory:
    """Tests for StoredArticleFactory."""
    
    def test_create_from_domain_article(self):
        """Test creating StoredArticle from domain Article."""
        # Create domain Article
        domain_article = Article(
            id="benzinga:12345",
            source=ArticleSource.BENZINGA,
            source_id="12345",
            title="Test Article",
            content="Test content",
            summary="Test summary",
            author="Test Author",
            published_at=datetime.now(),
            updated_at=None,
            url="https://example.com/article",
            tickers=frozenset(["AAPL", "MSFT"]),
            tags=frozenset(["tech", "news"]),
            categories=frozenset(["breaking"])
        )
        
        # Create StoredArticle using factory
        stored_article = StoredArticleFactory.create_from_domain_article(domain_article)
        
        # Assertions
        assert stored_article is not None
        assert stored_article.article_id == "benzinga:12345"
        assert stored_article.source == "benzinga"
        assert stored_article.source_id == "12345"
        assert stored_article.title == "Test Article"
        assert stored_article.content == "Test content"
        assert stored_article.summary == "Test summary"
        assert stored_article.author == "Test Author"
        assert stored_article.url == "https://example.com/article"
        assert stored_article.tickers == frozenset(["AAPL", "MSFT"])
        assert stored_article.tags == frozenset(["tech", "news"])
        assert stored_article.categories == frozenset(["breaking"])
        assert stored_article.stored_at is not None
    
    def test_create_from_dict(self):
        """Test creating StoredArticle from dictionary."""
        article_data = {
            "article_id": "benzinga:12345",
            "source": "benzinga",
            "source_id": "12345",
            "title": "Test Article",
            "content": "Test content",
            "summary": "Test summary",
            "author": "Test Author",
            "published_at": "2024-01-01T12:00:00+00:00",
            "updated_at": None,
            "url": "https://example.com/article",
            "tickers": ["AAPL", "MSFT"],
            "tags": ["tech", "news"],
            "categories": ["breaking"],
            "stored_at": "2024-01-01T12:05:00+00:00"
        }
        
        # Create StoredArticle using factory
        stored_article = StoredArticleFactory.create_from_dict(article_data)
        
        # Assertions
        assert stored_article is not None
        assert stored_article.article_id == "benzinga:12345"
        assert stored_article.source == "benzinga"
        assert stored_article.title == "Test Article"
        assert stored_article.tickers == frozenset(["AAPL", "MSFT"])


class TestAuditEntryFactory:
    """Tests for AuditEntryFactory."""
    
    def test_create_from_classification(self):
        """Test creating AuditEntry from classification result."""
        # Create domain Article
        domain_article = Article(
            id="benzinga:12345",
            source=ArticleSource.BENZINGA,
            source_id="12345",
            title="Test Article",
            content="Test content",
            summary="Test summary",
            author="Test Author",
            published_at=datetime.now(),
            updated_at=None,
            url="https://example.com/article",
            tickers=frozenset(["AAPL"]),
            tags=frozenset(),
            categories=frozenset()
        )
        
        # Create ClassificationResult
        classification_result = ClassificationResult(
            article_id="benzinga:12345",
            classification=ClassificationCategory.IMMINENT,
            confidence=ClassificationConfidence.HIGH,
            reasoning="Test reasoning",
            classified_at=datetime.now(),
            latency_ms=150.0
        )
        
        news_received_at = datetime.now()
        
        # Create AuditEntry using factory
        audit_entry = AuditEntryFactory.create_from_classification(
            article=domain_article,
            classification_result=classification_result,
            news_received_at=news_received_at,
            metadata={"test": "value"}
        )
        
        # Assertions
        assert audit_entry is not None
        assert audit_entry.article_id == "benzinga:12345"
        assert audit_entry.article_title == "Test Article"
        assert audit_entry.article_tickers == frozenset(["AAPL"])
        assert audit_entry.classification == "imminent"
        assert audit_entry.confidence == "HIGH"
        assert audit_entry.reasoning == "Test reasoning"
        assert audit_entry.source == "benzinga"
        assert audit_entry.metadata == {"test": "value"}
        assert audit_entry.is_imminent() is True
    
    def test_create_from_dict(self):
        """Test creating AuditEntry from dictionary."""
        audit_data = {
            "article_id": "benzinga:12345",
            "article_title": "Test Article",
            "article_tickers": ["AAPL"],
            "article_published": "2024-01-01T12:00:00+00:00",
            "classification": "imminent",
            "confidence": "HIGH",
            "reasoning": "Test reasoning",
            "source": "benzinga",
            "news_received_at": "2024-01-01T12:00:00+00:00",
            "classified_at": "2024-01-01T12:00:05+00:00",
            "logged_at": "2024-01-01T12:00:10+00:00",
            "metadata": {"test": "value"},
            "trade_details": {},
            "timing_stats": {},
            "price_history": {}
        }
        
        # Create AuditEntry using factory
        audit_entry = AuditEntryFactory.create_from_dict(audit_data)
        
        # Assertions
        assert audit_entry is not None
        assert audit_entry.article_id == "benzinga:12345"
        assert audit_entry.article_title == "Test Article"
        assert audit_entry.classification == "imminent"
        assert audit_entry.confidence == "HIGH"
        assert audit_entry.is_imminent() is True

