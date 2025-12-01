"""
Classification domain layer.

Pure business logic - listens to infrastructure events, validates, transforms, publishes domain events.
"""
from .models import (
    ClassificationCategory,
    ClassificationConfidence,
    ClassificationRequest,
    ClassificationResult
)
from .events import (
    ClassificationRequestedDomainEvent,
    ArticleClassifiedDomainEvent,
    ClassificationFailedDomainEvent
)

__all__ = [
    "ClassificationCategory",
    "ClassificationConfidence",
    "ClassificationRequest",
    "ClassificationResult",
    "ClassificationRequestedDomainEvent",
    "ArticleClassifiedDomainEvent",
    "ClassificationFailedDomainEvent",
]

