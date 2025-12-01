"""
Classification infrastructure microservice for Groq API.

Pure infrastructure - handles Groq API client, publishes events.
All stateful code related to Groq API lives here.
"""
import json
from pathlib import Path
from typing import Optional
from datetime import datetime

from groq import AsyncGroq

from ...utils.logging_config import get_logger
from ...shared.event_bus import AsyncEventBus
from ...shared.event_types import InfrastructureEventType
from .infrastructure_models import (
    InfrastructureClassificationRequestData,
    InfrastructureClassificationResponseData,
    ClassificationRequestedInfrastructureEvent,
    ClassificationCompletedInfrastructureEvent,
    ClassificationFailedInfrastructureEvent
)
from .event_protocols import InfrastructureClassificationRequestEventSubscriber

logger = get_logger(__name__)


class ClassificationInfrastructureService(InfrastructureClassificationRequestEventSubscriber):
    """
    Classification infrastructure microservice for Groq API.
    
    Responsibilities:
    - Manage Groq API client (stateful)
    - Load and cache classification prompt (stateful)
    - Format articles for classification
    - Call Groq API asynchronously
    - Parse JSON responses
    - Publish infrastructure events
    
    Does NOT:
    - Know about business logic
    - Return results directly (publishes events instead)
    - Know about domain models
    """
    
    def __init__(
        self,
        event_bus: AsyncEventBus,  
        api_key: str,
        model: str = "llama-3.3-70b-versatile",
        enabled: bool = True,
    ):
        """
        Initialize classification infrastructure service.
        
        Args:
            event_bus: Event bus instance for publishing/subscribing to events
            api_key: Groq API key
            model: Groq model name to use
            enabled: Whether classification is enabled
        """
        self.enabled = enabled
        self.model = model
        self.api_key = api_key
            
        
        # Stateful: Groq client (initialized if enabled)
        self.client: Optional[AsyncGroq] = None
        if enabled and api_key:
            self.client = AsyncGroq(api_key=api_key)
            logger.info("ClassificationInfrastructureService: Groq client initialized", model=model)
        else:
            logger.info("ClassificationInfrastructureService: Disabled or no API key provided")
        
        # Stateful: System prompt (cached, loaded once)
        self.system_prompt = self._load_prompt()
        
        # Event bus for publishing events
        self.event_bus = event_bus
        
        # Statistics
        self.stats = {
            "classifications_requested": 0,
            "classifications_completed": 0,
            "classifications_failed": 0,
            "last_classification_time": None,
            "is_enabled": enabled,
            "has_api_key": bool(api_key),
        }
        
        # State
        self.is_running = False
        
        logger.info(
            "ClassificationInfrastructureService initialized",
            model=model,
            enabled=enabled,
            has_api_key=bool(api_key)
        )
    
    def _load_prompt(self) -> str:
        """
        Load classification prompt from file (stateful operation).
        
        Returns:
            Prompt text, or fallback prompt if file not found
        """
        # Prompt is relative to project root
        # From src/newsflash/infra/classification/service.py -> go up 5 levels to project root
        prompt_path = Path(__file__).parent.parent.parent.parent.parent / "prompts" / "classification_prompt.txt"
        
        try:
            with open(prompt_path, "r") as f:
                prompt = f.read()
            logger.info("ClassificationInfrastructureService: Prompt loaded", path=str(prompt_path))
            return prompt
        except Exception as e:
            logger.error("ClassificationInfrastructureService: Failed to load prompt", error=str(e), path=str(prompt_path))
            # Fallback to minimal prompt
            return "Classify the news headline as IMMINENT or IGNORE. Return JSON only."
    
    def _format_article_for_classification(
        self,
        request_data: InfrastructureClassificationRequestData
    ) -> str:
        """
        Format article data for LLM classification.
        
        Args:
            request_data: Infrastructure classification request data
            
        Returns:
            Formatted string with article details
        """
        title = request_data.article_title
        tickers = ", ".join(request_data.article_tickers) if request_data.article_tickers else "No tickers"
        summary = request_data.article_summary or "No summary"
        
        # Truncate summary to avoid token limits
        if len(summary) > 500:
            summary = summary[:500] + "..."
        
        return f"""Headline: {title}
Tickers: {tickers}
Summary: {summary}"""
    
    async def start(self) -> None:
        """Start the classification infrastructure service."""
        if self.is_running:
            logger.warning("ClassificationInfrastructureService: Already running")
            return
        
        logger.info("🚀 Starting Classification Infrastructure Service")
        self.is_running = True
        
        # Subscribe to classification requests from domain layer
        # Domain listener will publish ClassificationRequestedInfrastructureEvent
        self.event_bus.subscribe(InfrastructureEventType.CLASSIFICATION_REQUESTED, self.handle_classification_requested)
        logger.info("ClassificationInfrastructureService: Subscribed to ClassificationRequested events")
        
        logger.info("✅ Classification Infrastructure Service started")
    
    async def stop(self) -> None:
        """Stop the classification infrastructure service."""
        if not self.is_running:
            return
        
        logger.info("Stopping Classification Infrastructure Service")
        self.is_running = False
        
        # Unsubscribe from events
        self.event_bus.unsubscribe(InfrastructureEventType.CLASSIFICATION_REQUESTED, self.handle_classification_requested)
        
        logger.info("✅ Classification Infrastructure Service stopped")
    
    async def handle_classification_requested(
        self,
        event_type: str,
        event_data: dict
    ) -> None:
        """
        Handle ClassificationRequested infrastructure event.
        
        Implements InfrastructureClassificationRequestEventSubscriber protocol.
        Receives typed infrastructure event, calls Groq API, publishes result.
        
        Args:
            event_type: Event type string
            event_data: Event data dictionary (will be validated to typed model)
        """
        try:
            # Reconstruct typed infrastructure event (Pydantic validates)
            infra_event = ClassificationRequestedInfrastructureEvent(**event_data)
            
            # Update stats
            self.stats["classifications_requested"] += 1
            
            logger.debug(
                "ClassificationInfrastructureService: Handling classification request",
                article_id=infra_event.request_data.article_id,
                title=infra_event.request_data.article_title[:100] if infra_event.request_data.article_title else ""
            )
            
            # Classify via Groq API
            await self._classify_via_groq(infra_event)
            
        except Exception as e:
            logger.error(
                "ClassificationInfrastructureService: Error handling classification request",
                error=str(e),
                exc_info=True
            )
    
    async def _classify_via_groq(
        self,
        infra_event: ClassificationRequestedInfrastructureEvent
    ) -> None:
        """
        Classify article via Groq API and publish result event.
        
        Args:
            infra_event: Classification request infrastructure event
        """
        start_time = datetime.now()
        request_data = infra_event.request_data
        
        # Check if enabled
        if not self.enabled or not self.client:
            logger.debug("ClassificationInfrastructureService: Disabled, skipping")
            return
        
        try:
            # Format article for classification
            article_text = self._format_article_for_classification(request_data)
            
            logger.debug(
                "ClassificationInfrastructureService: Calling Groq API",
                article_id=request_data.article_id,
                model=self.model
            )
            
            # Call Groq API (stateful operation)
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
            
            # Calculate latency
            latency_ms = (datetime.now() - start_time).total_seconds() * 1000
            
            # Create typed infrastructure response model
            response_data = InfrastructureClassificationResponseData(**result_json)
            
            # Update stats
            self.stats["classifications_completed"] += 1
            self.stats["last_classification_time"] = datetime.now().isoformat()
            
            logger.info(
                "ClassificationInfrastructureService: Classification completed",
                article_id=request_data.article_id,
                classification=response_data.classification,
                confidence=response_data.confidence,
                latency_ms=latency_ms
            )
            
            # Publish typed infrastructure event (completed)
            completed_event = ClassificationCompletedInfrastructureEvent(
                request_data=request_data,
                response_data=response_data,
                completed_at=datetime.now(),
                latency_ms=latency_ms,
                success=True
            )
            
            await self.event_bus.publish(InfrastructureEventType.CLASSIFICATION_COMPLETED, completed_event.model_dump())
            
        except json.JSONDecodeError as e:
            # JSON parsing error
            latency_ms = (datetime.now() - start_time).total_seconds() * 1000
            error_msg = f"Failed to parse LLM response as JSON: {str(e)}"
            
            self.stats["classifications_failed"] += 1
            
            logger.error(
                "ClassificationInfrastructureService: JSON parse error",
                article_id=request_data.article_id,
                error=error_msg,
                response=result_text if 'result_text' in locals() else "No response"
            )
            
            # Publish typed infrastructure event (failed)
            failed_event = ClassificationFailedInfrastructureEvent(
                request_data=request_data,
                error=error_msg,
                failed_at=datetime.now()
            )
            
            await self.event_bus.publish(InfrastructureEventType.CLASSIFICATION_FAILED, failed_event.model_dump())
            
        except Exception as e:
            # General error
            latency_ms = (datetime.now() - start_time).total_seconds() * 1000
            error_msg = f"Classification failed: {str(e)}"
            
            self.stats["classifications_failed"] += 1
            
            logger.error(
                "ClassificationInfrastructureService: Classification error",
                article_id=request_data.article_id,
                error=error_msg,
                exc_info=True
            )
            
            # Publish typed infrastructure event (failed)
            failed_event = ClassificationFailedInfrastructureEvent(
                request_data=request_data,
                error=error_msg,
                failed_at=datetime.now()
            )
            
            await self.event_bus.publish(InfrastructureEventType.CLASSIFICATION_FAILED, failed_event.model_dump())
    
    def get_stats(self) -> dict:
        """Get classification infrastructure service statistics."""
        return {
            **self.stats,
            "model": self.model,
        }

