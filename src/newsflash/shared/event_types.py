"""
Centralized event type constants.

All event types used in the system are defined here as constants.
This provides:
- Type safety (no magic strings)
- Single source of truth
- Easy refactoring
- IDE autocomplete
"""


class DomainEventType:
    """Domain event types - business events."""
    
    # WebSocket domain events
    ARTICLE_RECEIVED = "Domain.ArticleReceived"
    ARTICLE_VALIDATION_FAILED = "Domain.ArticleValidationFailed"
    
    # Classification domain events
    CLASSIFICATION_REQUESTED = "Domain.ClassificationRequested"
    ARTICLE_CLASSIFIED = "Domain.ArticleClassified"
    CLASSIFICATION_FAILED = "Domain.ClassificationFailed"
    
    # Storage domain events
    ARTICLE_STORAGE_REQUESTED = "Domain.ArticleStorageRequested"
    ARTICLE_STORED = "Domain.ArticleStored"
    ARTICLE_STORAGE_FAILED = "Domain.ArticleStorageFailed"
    ARTICLE_FETCH_REQUESTED = "Domain.ArticleFetchRequested"
    ARTICLE_FETCHED = "Domain.ArticleFetched"
    AUDIT_LOG_STORAGE_REQUESTED = "Domain.AuditLogStorageRequested"
    AUDIT_LOG_STORED = "Domain.AuditLogStored"
    AUDIT_LOG_STORAGE_FAILED = "Domain.AuditLogStorageFailed"
    
    # Notification domain events
    NOTIFICATION_REQUESTED = "Domain.NotificationRequested"
    NOTIFICATION_SENT = "Domain.NotificationSent"
    NOTIFICATION_FAILED = "Domain.NotificationFailed"
    
    # Brokerage domain events
    TRADE_REQUESTED = "Domain.TradeRequested"
    TRADE_EXECUTED = "Domain.TradeExecuted"
    TRADE_FAILED = "Domain.TradeFailed"
    TRADE_QUEUED = "Domain.TradeQueued"
    QUOTE_RECEIVED = "Domain.QuoteReceived"
    BROKERAGE_CONNECTION_STATUS = "Domain.BrokerageConnectionStatus"
    BROKERAGE_HEALTH_STATUS = "Domain.BrokerageHealthStatus"
    
    # WebSocket health domain events
    WEBSOCKET_HEALTH_STATUS = "Domain.WebSocketHealthStatus"
    WEBSOCKET_ERROR = "Domain.WebSocketError"
    WEBSOCKET_RATE_LIMIT = "Domain.WebSocketRateLimit"
    WEBSOCKET_DISCONNECTED = "Domain.WebSocketDisconnected"
    WEBSOCKET_CONNECTED = "Domain.WebSocketConnected"
    
    # Process article domain events
    ARTICLE_PROCESSED = "Domain.ArticleProcessed"


class InfrastructureEventType:
    """Infrastructure event types - technical events."""
    
    # WebSocket infrastructure events
    ARTICLE_RECEIVED = "ArticleReceived"
    
    # Classification infrastructure events
    CLASSIFICATION_REQUESTED = "ClassificationRequested"
    CLASSIFICATION_COMPLETED = "ClassificationCompleted"
    CLASSIFICATION_FAILED = "ClassificationFailed"
    
    # Storage infrastructure events
    ARTICLE_STORAGE_REQUESTED = "ArticleStorageRequested"
    ARTICLE_STORED = "ArticleStored"
    ARTICLE_STORAGE_FAILED = "ArticleStorageFailed"
    ARTICLE_FETCH_REQUESTED = "ArticleFetchRequested"
    ARTICLE_FETCHED = "ArticleFetched"
    AUDIT_LOG_STORAGE_REQUESTED = "AuditLogStorageRequested"
    AUDIT_LOG_STORED = "AuditLogStored"
    AUDIT_LOG_STORAGE_FAILED = "AuditLogStorageFailed"
    
    # Notification infrastructure events
    NOTIFICATION_SEND_REQUESTED = "NotificationSendRequested"
    NOTIFICATION_SENT = "NotificationSent"
    NOTIFICATION_FAILED = "NotificationFailed"
    
    # Brokerage infrastructure events
    TRADE_EXECUTION_REQUESTED = "TradeExecutionRequested"
    TRADE_EXECUTED = "TradeExecuted"
    TRADE_FAILED = "TradeFailed"
    TRADE_REQUEST_QUEUED = "TradeRequestQueued"
    QUOTE_RECEIVED = "QuoteReceived"
    CONNECTION_STATUS_CHANGED = "ConnectionStatusChanged"
    BROKERAGE_HEALTH_STATUS = "BrokerageHealthStatus"
    
    # WebSocket infrastructure events
    WEBSOCKET_HEALTH_STATUS = "WebSocketHealthStatus"
    WEBSOCKET_CONNECTED = "WebSocketConnected"
    WEBSOCKET_DISCONNECTED = "WebSocketDisconnected"
    WEBSOCKET_ERROR = "WebSocketError"
    WEBSOCKET_RATE_LIMIT = "WebSocketRateLimit"

