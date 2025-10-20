"""
AI-powered news classification service using Groq's Llama 3.
"""
import json
import asyncio
from typing import Union, Optional
from pathlib import Path
import structlog
from groq import AsyncGroq

from ..models.base_models import StandardizedArticle
from ..models.benzinga_models import BenzingaArticle
from ..models.classification_models import ClassificationResult, NewsClassification

logger = structlog.get_logger(__name__)


class NewsClassifier:
    """
    Classifies news articles using Groq's Llama 3 model.
    
    Determines if articles are IMMINENT (immediate trading opportunity),
    NOTEWORTHY (worth monitoring), or IGNORE (filter out).
    """
    
    def __init__(
        self,
        api_key: str,
        model: str = "llama-3.3-70b-versatile",
        enabled: bool = True,
    ):
        """
        Initialize the news classifier.
        
        Args:
            api_key: Groq API key
            model: Model name to use
            enabled: Whether classification is enabled
        """
        self.enabled = enabled
        self.model = model
        self.client: Optional[AsyncGroq] = None
        
        if enabled and api_key:
            self.client = AsyncGroq(api_key=api_key)
            logger.info("NewsClassifier initialized", model=model)
        else:
            logger.info("NewsClassifier disabled or no API key provided")
        
        # Load system prompt
        self.system_prompt = self._load_prompt()
    
    def _load_prompt(self) -> str:
        """Load the classification prompt from file."""
        prompt_path = Path(__file__).parent.parent.parent.parent / "prompts" / "classification_prompt.txt"
        
        try:
            with open(prompt_path, "r") as f:
                prompt = f.read()
            logger.info("Classification prompt loaded", path=str(prompt_path))
            return prompt
        except Exception as e:
            logger.error("Failed to load classification prompt", error=str(e))
            # Fallback to minimal prompt
            return "Classify the news headline as IMMINENT, NOTEWORTHY, or IGNORE. Return JSON only."
    
    def _format_article_for_classification(
        self,
        article: Union[BenzingaArticle, StandardizedArticle]
    ) -> str:
        """
        Format article data for LLM classification.
        
        Args:
            article: Article to format
            
        Returns:
            Formatted string with article details
        """
        if isinstance(article, BenzingaArticle):
            title = article.title
            tickers = ", ".join(article.tickers) if article.tickers else "No tickers"
            summary = article.teaser or "No summary"
        else:  # StandardizedArticle
            title = article.title
            tickers = ", ".join(article.tickers) if article.tickers else "No tickers"
            summary = article.summary or article.content or "No summary"
        
        # Truncate summary to avoid token limits
        if len(summary) > 500:
            summary = summary[:500] + "..."
        
        return f"""Headline: {title}
Tickers: {tickers}
Summary: {summary}"""
    
    async def classify_article(
        self,
        article: Union[BenzingaArticle, StandardizedArticle]
    ) -> Optional[ClassificationResult]:
        """
        Classify a single article using Groq's Llama 3.
        
        Args:
            article: Article to classify
            
        Returns:
            ClassificationResult or None if classification fails/disabled
        """
        if not self.enabled or not self.client:
            logger.debug("Classification disabled, skipping")
            return None
        
        try:
            # Format article for classification
            article_text = self._format_article_for_classification(article)
            
            # Get article ID for logging
            article_id = self._get_article_id(article)
            
            logger.debug(
                "Classifying article",
                article_id=article_id,
                title=article.title[:100]
            )
            
            # Call Groq API
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": self.system_prompt},
                    {"role": "user", "content": article_text}
                ],
                temperature=0.1,  # Low temperature for consistent classification
                max_tokens=200,   # Short response expected
                response_format={"type": "json_object"},  # Force JSON output
            )
            
            # Parse response
            result_text = response.choices[0].message.content
            result_json = json.loads(result_text)
            
            # Normalize classification to lowercase (LLM might return uppercase)
            if "classification" in result_json:
                result_json["classification"] = result_json["classification"].lower()
            
            # Validate and create ClassificationResult
            classification_result = ClassificationResult(**result_json)
            
            logger.info(
                "Article classified",
                article_id=article_id,
                classification=classification_result.classification.value,
                confidence=classification_result.confidence,
                reasoning=classification_result.reasoning
            )
            
            return classification_result
            
        except json.JSONDecodeError as e:
            logger.error(
                "Failed to parse LLM response as JSON",
                article_id=self._get_article_id(article),
                error=str(e),
                response=result_text if 'result_text' in locals() else "No response"
            )
            return None
            
        except Exception as e:
            logger.error(
                "Classification failed",
                article_id=self._get_article_id(article),
                error=str(e)
            )
            return None
    
    async def classify_batch(
        self,
        articles: list[Union[BenzingaArticle, StandardizedArticle]]
    ) -> list[tuple[Union[BenzingaArticle, StandardizedArticle], Optional[ClassificationResult]]]:
        """
        Classify multiple articles in parallel.
        
        Args:
            articles: List of articles to classify
            
        Returns:
            List of (article, classification_result) tuples
        """
        if not articles:
            return []
        
        # Classify all articles concurrently
        classification_tasks = [
            self.classify_article(article)
            for article in articles
        ]
        
        classifications = await asyncio.gather(*classification_tasks, return_exceptions=True)
        
        # Pair articles with their classifications
        results = []
        for article, classification in zip(articles, classifications):
            if isinstance(classification, Exception):
                logger.error(
                    "Batch classification failed for article",
                    article_id=self._get_article_id(article),
                    error=str(classification)
                )
                results.append((article, None))
            else:
                results.append((article, classification))
        
        return results
    
    def _get_article_id(self, article: Union[BenzingaArticle, StandardizedArticle]) -> str:
        """Get article ID for logging."""
        if isinstance(article, BenzingaArticle):
            return str(article.benzinga_id)
        return article.source_id
    
    def should_notify(self, classification: Optional[ClassificationResult]) -> bool:
        """
        Determine if article should trigger Telegram notification.
        
        STRICT FILTERING - Only truly actionable news:
        - IMMINENT + HIGH confidence → Notify (trade immediately)
        - IMMINENT + MEDIUM confidence → Notify (probably trade)
        - Everything else → Don't notify
        
        This ensures maximum signal-to-noise ratio.
        
        Args:
            classification: Classification result
            
        Returns:
            True if article should be sent to Telegram
        """
        if not classification:
            # If classification failed, don't notify (conservative)
            return False
        
        # IGNORE classification = no notification
        if classification.classification == NewsClassification.IGNORE:
            return False
        
        # LOW confidence = don't notify (too uncertain, even if IMMINENT)
        if classification.confidence == "LOW":
            return False
        
        # Only IMMINENT with HIGH or MEDIUM confidence gets through
        # This is the strictest filter for maximum signal-to-noise
        if classification.classification == NewsClassification.IMMINENT:
            return classification.confidence in ["HIGH", "MEDIUM"]
        
        # Anything else = no notification
        return False
    
    def get_stats(self) -> dict:
        """Get classifier statistics."""
        return {
            "enabled": self.enabled,
            "model": self.model,
            "has_api_key": bool(self.client),
        }

