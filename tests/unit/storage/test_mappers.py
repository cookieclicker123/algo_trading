"""
Unit tests for storage domain mappers.
"""
import pytest
from datetime import datetime
from newsflash.domain.storage.mappers import ArticleStorageMapper, AuditLogMapper
from newsflash.domain.websocket.models import Article, ArticleSource
from newsflash.domain.storage.models import StoredArticle, AuditEntry


class TestArticleStorageMapper:
    """Tests for ArticleStorageMapper."""
    
    def test_from_domain_article(self):
        """Test mapping domain Article to dict."""
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
            tags=frozenset(["tech"]),
            categories=frozenset(["breaking"])
        )
        
        # Map to dict
        article_data = ArticleStorageMapper.from_domain_article(domain_article)
        
        # Assertions
        assert article_data["article_id"] == "benzinga:12345"
        assert article_data["source"] == "benzinga"
        assert article_data["source_id"] == "12345"
        assert article_data["title"] == "Test Article"
        assert article_data["content"] == "Test content"
        assert article_data["tickers"] == ["AAPL", "MSFT"]
        assert article_data["tags"] == ["tech"]
        assert article_data["categories"] == ["breaking"]
    
    def test_to_infrastructure_request(self):
        """Test mapping article data to infrastructure request."""
        article_data = {
            "article_id": "benzinga:12345",
            "source": "benzinga",
            "source_id": "12345",
            "title": "Test Article",
            "published_at": datetime.now().isoformat()
        }
        
        # Map to infrastructure request
        infra_request = ArticleStorageMapper.to_infrastructure_request(
            article_data=article_data,
            article_id="benzinga:12345"
        )
        
        # Assertions
        assert infra_request.article_id == "benzinga:12345"
        assert infra_request.source == "benzinga"
        assert infra_request.article_data == article_data
        assert infra_request.stored_at is not None


class TestAuditLogMapper:
    """Tests for AuditLogMapper."""
    
    def test_from_domain_audit_entry(self):
        """Test mapping domain AuditEntry to dict."""
        # Create AuditEntry
        audit_entry = AuditEntry(
            article_id="benzinga:12345",
            article_title="Test Article",
            article_tickers=frozenset(["AAPL"]),
            article_published=datetime.now(),
            classification="imminent",
            confidence="HIGH",
            reasoning="Test reasoning",
            source="benzinga",
            news_received_at=datetime.now(),
            classified_at=datetime.now(),
            logged_at=datetime.now(),
            metadata={"test": "value"},
            trade_details={},
            timing_stats={},
            price_history={}
        )
        
        # Map to dict
        audit_data = AuditLogMapper.from_domain_audit_entry(audit_entry)
        
        # Assertions
        assert audit_data["article_id"] == "benzinga:12345"
        assert audit_data["article_title"] == "Test Article"
        assert audit_data["article_tickers"] == ["AAPL"]
        assert audit_data["classification"] == "imminent"
        assert audit_data["confidence"] == "HIGH"
        assert audit_data["reasoning"] == "Test reasoning"
        assert audit_data["metadata"] == {"test": "value"}
    
    def test_to_infrastructure_request(self):
        """Test mapping audit data to infrastructure request."""
        audit_data = {
            "article_id": "benzinga:12345",
            "article_title": "Test Article",
            "classification": "imminent",
            "confidence": "HIGH"
        }
        
        # Map to infrastructure request
        infra_request = AuditLogMapper.to_infrastructure_request(
            audit_data=audit_data,
            article_id="benzinga:12345"
        )
        
        # Assertions
        assert infra_request.article_id == "benzinga:12345"
        assert infra_request.audit_data == audit_data
        assert infra_request.logged_at is not None
        assert infra_request.entry_type == "classification"

