"""
Pydantic models for news classification.
"""
from enum import Enum
from typing import Literal
from pydantic import BaseModel, Field


class NewsClassification(str, Enum):
    """
    Classification categories for news articles.
    
    Two categories for maximum signal-to-noise ratio:
    - IMMINENT: Trade immediately (10%+ intraday moves expected)
    - IGNORE: Filter out (no actionable trading signal)
    """
    IMMINENT = "imminent"          # Immediate trading opportunity (10%+ intraday moves)
    IGNORE = "ignore"              # Filter out - no trading signal


class ClassificationResult(BaseModel):
    """
    Pydantic model for LLM classification output.
    
    This model enforces the structure of the classification response
    from the LLM to ensure deterministic parsing.
    """
    classification: NewsClassification = Field(
        ..., 
        description="The classification category for the news article"
    )
    confidence: Literal["HIGH", "MEDIUM", "LOW"] = Field(
        ..., 
        description="Confidence level in the classification"
    )
    reasoning: str = Field(
        ..., 
        max_length=200,
        description="Brief 1-2 sentence explanation for the classification"
    )

