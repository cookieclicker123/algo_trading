"""
Classification infrastructure microservice for Groq API.

Pure infrastructure - handles Groq API client, publishes events.
All stateful code related to Groq API lives here.

PRIMARY TRADING LOGIC:
- Healthcare headlines → industry-specific LLM classification → TRADE/SKIP
- No microstructure filters - pure language-based instant decision
- Speed is critical for early entry
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
    ClassificationFailedInfrastructureEvent,
    ClassificationSkippedInfrastructureEvent
)
from .event_protocols import InfrastructureClassificationRequestEventSubscriber
from .healthcare_classifier import HealthcareClassifier

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
        metrics_service,  # Required - injected via DI
        ticker_validator=None,  # Will be injected after brokerage is initialized
        market_data_validator=None,  # Will be injected after brokerage is initialized
        quote_fetcher=None,  # Will be injected after brokerage is initialized
        metadata_cache=None,  # Will be injected after cache is initialized
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
            metrics_service: Optional metrics service for statistics (injected via DI)
            ticker_validator: TickerValidator instance for exchange validation (injected via DI)
            market_data_validator: MarketDataValidator instance for market cap/price validation (injected via DI)
            quote_fetcher: AlpacaQuoteFetcher instance for NBBO availability check (injected via DI)
            metadata_cache: MetadataCache instance for sector/industry lookup (injected via DI)
        """
        self.enabled = enabled
        self.model = model
        self.api_key = api_key
        self.metrics_service = metrics_service  # ✅ Injected metrics service
        self.ticker_validator = ticker_validator  # ✅ Injected ticker validator
        self.market_data_validator = market_data_validator  # ✅ Injected market data validator
        self.quote_fetcher = quote_fetcher  # ✅ Injected quote fetcher for NBBO check
        self.metadata_cache = metadata_cache  # ✅ Injected metadata cache for Healthcare classifier

        # Healthcare classifier (initialized lazily when metadata_cache is set)
        self._healthcare_classifier: Optional[HealthcareClassifier] = None

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
        
        # ✅ No stats dictionary - MetricsService aggregates from events!
        
        logger.info(
            "ClassificationInfrastructureService initialized",
            model=model,
            enabled=enabled,
            has_api_key=bool(api_key),
            has_ticker_validator=ticker_validator is not None,
            has_market_data_validator=market_data_validator is not None,
            has_quote_fetcher=quote_fetcher is not None,
            has_metadata_cache=metadata_cache is not None
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

    @property
    def healthcare_classifier(self) -> Optional[HealthcareClassifier]:
        """
        Get Healthcare classifier (lazily initialized when metadata_cache is available).

        Returns:
            HealthcareClassifier instance or None if metadata_cache not set
        """
        if self._healthcare_classifier is None and self.metadata_cache is not None:
            self._healthcare_classifier = HealthcareClassifier(
                api_key=self.api_key,
                metadata_cache=self.metadata_cache,
                model=self.model,
            )
            logger.info(
                "HealthcareClassifier initialized",
                model=self.model,
                supported_industries=list(self._healthcare_classifier._stats.keys())
            )
        return self._healthcare_classifier

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
        """
        Start the classification infrastructure service.
        
        Idempotent: Safe to call multiple times. Event bus prevents duplicate subscriptions.
        """
        logger.info("🚀 Starting Classification Infrastructure Service")
        
        # Subscribe to classification requests from domain layer
        # Start ticker validator (begins hourly refresh)
        # Note: TickerValidator may be None if not yet injected (set in composition_root)
        if self.ticker_validator:
            await self.ticker_validator.start()
            logger.info("TickerValidator started via ClassificationInfrastructureService")
        else:
            logger.warning("TickerValidator not set - exchange validation will be skipped")
        
        if self.market_data_validator:
            await self.market_data_validator.start()
            logger.info("MarketDataValidator started via ClassificationInfrastructureService")
        else:
            logger.warning("MarketDataValidator not available - market cap/price filtering disabled")
        
        # Domain listener will publish ClassificationRequestedInfrastructureEvent
        # Event bus automatically prevents duplicate subscriptions
        self.event_bus.subscribe(InfrastructureEventType.CLASSIFICATION_REQUESTED, self.handle_classification_requested)
        logger.info("ClassificationInfrastructureService: Subscribed to ClassificationRequested events")
        
        logger.info("✅ Classification Infrastructure Service started")
    
    async def stop(self) -> None:
        """
        Stop the classification infrastructure service.
        
        Idempotent: Safe to call multiple times. Unsubscribing when not subscribed is safe.
        """
        logger.info("Stopping Classification Infrastructure Service")
        
        # Stop ticker validator
        if self.ticker_validator:
            await self.ticker_validator.stop()
            logger.info("TickerValidator stopped via ClassificationInfrastructureService")
        
        if self.market_data_validator:
            await self.market_data_validator.stop()
            logger.info("MarketDataValidator stopped via ClassificationInfrastructureService")
        
        # Unsubscribe from events (safe even if not subscribed)
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
        
        Three-step pre-filtering process:
        1. Check if article has tickers (Python logic)
        2. Check if tickers are tradeable on NASDAQ/NYSE/AMEX (TickerValidator)
        3. Only if both pass → Call Groq API
        
        Args:
            event_type: Event type string
            event_data: Event data dictionary (will be validated to typed model)
        """
        try:
            # Reconstruct typed infrastructure event (Pydantic validates)
            infra_event = ClassificationRequestedInfrastructureEvent(**event_data)
            request_data = infra_event.request_data
            
            # ✅ No stats mutation - MetricsService subscribes to ClassificationRequested event
            
            logger.info(
                "🎯 CLASSIFY INFRA: Handling classification request",
                article_id=request_data.article_id,
                title=request_data.article_title or "",
                has_tickers=len(request_data.article_tickers) > 0
            )
            
            # Step 1: Check if article has tickers (Python logic - no API call)
            if not request_data.article_tickers:
                logger.info(
                    "⏭️ CLASSIFY INFRA: Skipping classification - article has no tickers",
                    article_id=request_data.article_id
                )
                await self._publish_skipped_event(infra_event, "no_tickers")
                return
            
            # Step 2: Check if tickers are tradeable on NASDAQ/NYSE/AMEX (TickerValidator - cached lookup)
            if not self.ticker_validator or not self.ticker_validator.are_tradeable(request_data.article_tickers):
                # Determine specific reason: invalid_exchange vs broker_not_tradeable
                filter_reason = "broker_not_tradeable"  # Default fallback
                if request_data.article_tickers:
                    # Check first ticker to determine reason (all tickers should have same reason)
                    reason = self.ticker_validator.get_validation_reason(request_data.article_tickers[0])
                    if reason:
                        filter_reason = reason
                
                logger.info(
                    f"⏭️ CLASSIFY INFRA: Skipping classification - {filter_reason}",
                    article_id=request_data.article_id,
                    tickers=request_data.article_tickers,
                    reason=filter_reason
                )
                await self._publish_skipped_event(infra_event, filter_reason)
                return
            
            # Step 3: Check NBBO availability (before expensive Groq API call)
            primary_ticker = request_data.article_tickers[0] if request_data.article_tickers else None
            if self.quote_fetcher and primary_ticker:
                logger.debug(
                    "CLASSIFY INFRA: Checking NBBO availability",
                    article_id=request_data.article_id,
                    ticker=primary_ticker
                )
                nbbo_snapshot = await self.quote_fetcher.get_nbbo_snapshot(primary_ticker)
                
                if not nbbo_snapshot:
                    logger.info(
                        "⏭️ CLASSIFY INFRA: Skipping classification - NBBO snapshot unavailable",
                        article_id=request_data.article_id,
                        ticker=primary_ticker,
                        reason="nbbo_unavailable",
                        diagnostic="Stock does not have active bid/ask in extended hours (check logs for detailed failure reason)"
                    )
                    await self._publish_skipped_event(infra_event, "nbbo_unavailable")
                    return
                
                # Filter 3b: Volume prefilter DISABLED
                # This filter was causing false negatives (e.g., JFBR +134% missed)
                # because the Alpaca trades API has latency - trades exist but aren't
                # visible yet when this check runs 1-2 seconds after publication.
                # The recall engine's volume analysis is more thorough and handles this.
                #
                # NOTE: If AI classification is re-enabled and API costs are a concern,
                # consider re-enabling this with a longer delay or async retry.
            
            # Step 4: All checks passed - proceed to Healthcare LLM classification
            # ========================================================================
            # HEALTHCARE-ONLY TRADING STRATEGY
            # ========================================================================
            # Pure language-based classification using industry-specific prompts.
            # Flow: headline → sector check → industry check → Groq LLM → TRADE/SKIP
            # If TRADE → publish "imminent" classification → trigger AutoTradeService
            # If SKIP/NOT_HEALTHCARE → no trade, but data collection continues
            # ========================================================================

            await self._classify_via_healthcare(infra_event, primary_ticker)
            
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
            
            logger.info(
                "🤖 CLASSIFY INFRA: Calling Groq API",
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
            
            # ✅ No stats mutation - MetricsService subscribes to ClassificationCompleted event
            
            logger.info(
                "✅ CLASSIFY INFRA: Classification completed",
                article_id=request_data.article_id,
                classification=response_data.classification,
                confidence=response_data.confidence,
                reasoning=response_data.reasoning,
                latency_ms=round(latency_ms, 2)
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
            
            # ✅ No stats mutation - MetricsService subscribes to ClassificationFailed event
            
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
            
            # ✅ No stats mutation - MetricsService subscribes to ClassificationFailed event
            
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

    async def _classify_via_healthcare(
        self,
        infra_event: ClassificationRequestedInfrastructureEvent,
        primary_ticker: str
    ) -> None:
        """
        Classify article via Healthcare LLM classifier and publish result event.

        Flow:
        1. Check if Healthcare classifier is available (metadata_cache injected)
        2. Call Healthcare classifier (sector → industry → Groq LLM)
        3. If TRADE → publish ClassificationCompleted with classification="imminent"
        4. If SKIP/NOT_HEALTHCARE/UNSUPPORTED → publish ClassificationSkipped

        Args:
            infra_event: Classification request infrastructure event
            primary_ticker: Primary ticker for classification
        """
        request_data = infra_event.request_data
        headline = request_data.article_title

        # Check if Healthcare classifier is available
        if not self.healthcare_classifier:
            logger.warning(
                "Healthcare classifier not available (metadata_cache not injected)",
                article_id=request_data.article_id,
                ticker=primary_ticker
            )
            await self._publish_skipped_event(infra_event, "classifier_not_ready")
            return

        try:
            # Classify via Healthcare classifier
            classification, industry, latency_ms = await self.healthcare_classifier.classify(
                headline=headline,
                ticker=primary_ticker
            )

            logger.info(
                f"Healthcare classification: {classification}",
                article_id=request_data.article_id,
                ticker=primary_ticker,
                industry=industry,
                latency_ms=round(latency_ms, 1)
            )

            # Handle classification result
            if classification == "TRADE":
                # TRADE signal → publish "imminent" to trigger AutoTradeService
                response_data = InfrastructureClassificationResponseData(
                    classification="imminent",
                    confidence="HIGH",
                    reasoning=f"Healthcare/{industry} - LLM classified as tradeable"
                )

                completed_event = ClassificationCompletedInfrastructureEvent(
                    request_data=request_data,
                    response_data=response_data,
                    completed_at=datetime.now(),
                    latency_ms=latency_ms,
                    success=True,
                    source="healthcare_classifier"
                )

                await self.event_bus.publish(
                    InfrastructureEventType.CLASSIFICATION_COMPLETED,
                    completed_event.model_dump()
                )

                logger.info(
                    "Published IMMINENT classification for Healthcare TRADE signal",
                    article_id=request_data.article_id,
                    ticker=primary_ticker,
                    industry=industry,
                    latency_ms=round(latency_ms, 1)
                )

            elif classification == "NOT_HEALTHCARE":
                # Not Healthcare sector - skip trading but continue data collection
                await self._publish_skipped_event(infra_event, "not_healthcare_sector")

            elif classification == "UNSUPPORTED_INDUSTRY":
                # Healthcare but unsupported industry - skip trading
                await self._publish_skipped_event(infra_event, f"unsupported_industry:{industry}")

            else:
                # SKIP signal - LLM determined not tradeable
                await self._publish_skipped_event(infra_event, "healthcare_skip")

        except Exception as e:
            logger.error(
                "Healthcare classification error",
                article_id=request_data.article_id,
                ticker=primary_ticker,
                error=str(e),
                exc_info=True
            )

            # Publish failed event
            failed_event = ClassificationFailedInfrastructureEvent(
                request_data=request_data,
                error=f"Healthcare classification failed: {str(e)}",
                failed_at=datetime.now()
            )
            await self.event_bus.publish(
                InfrastructureEventType.CLASSIFICATION_FAILED,
                failed_event.model_dump()
            )

    async def _publish_skipped_event(
        self,
        infra_event: ClassificationRequestedInfrastructureEvent,
        reason: str
    ) -> None:
        """
        Publish ClassificationSkipped infrastructure event.
        
        Args:
            infra_event: Original classification request event
            reason: Skip reason ('no_tickers', 'invalid_exchange', 'broker_not_tradeable', 'nbbo_unavailable', or 'no_volume_since_publication')
        """
        skipped_event = ClassificationSkippedInfrastructureEvent(
            request_data=infra_event.request_data,
            skipped_at=datetime.now(),
            reason=reason,
            source="classification_infrastructure"
        )
        
        await self.event_bus.publish(
            InfrastructureEventType.CLASSIFICATION_SKIPPED,
            skipped_event.model_dump()
        )
        
        logger.info(
            "ClassificationInfrastructureService: Published ClassificationSkipped event",
            article_id=infra_event.request_data.article_id,
            reason=reason
        )
    
    def get_stats(self) -> dict:
        """Get classification infrastructure service statistics."""
        # ✅ Delegate to MetricsService - statistics aggregated from events
        return self.metrics_service.get_classification_stats(
            model=self.model,
            enabled=self.enabled,
            has_api_key=bool(self.api_key)
        )

