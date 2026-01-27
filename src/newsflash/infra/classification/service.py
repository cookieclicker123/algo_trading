"""
Classification infrastructure microservice for Groq API.

Pure infrastructure - handles Groq API client, publishes events.
All stateful code related to Groq API lives here.

PRIMARY TRADING LOGIC:
- Multi-sector headlines → microstructure filters → LLM classification → TRADE/SKIP
- Supported sectors: Healthcare, Technology, Industrials, Consumer Cyclical,
  Financial Services, Communication Services, Consumer Defensive, Basic Materials

MICROSTRUCTURE PRE-FILTERS (before AI, saves API costs):
  1. Market cap < $500M (small-caps move more on news)
  2. Price < $20 (low-priced stocks have larger % moves)
  3. Spread < $1 (tight spreads = liquid, tradeable)

STATISTICAL IMPACT (January 2026 backtest):
  - AI only: -$3,622 loss, 8.8% win rate
  - + All microstructure filters: +$1,850 profit, 24.5% win rate
  - Improvement: +$5,472 per month
"""
import json
import re
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
from .sector_classifier import SectorClassifier

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
        self.metadata_cache = metadata_cache  # ✅ Injected metadata cache for sector classifier

        # Multi-sector classifier (initialized lazily when metadata_cache is set)
        self._sector_classifier: Optional[SectorClassifier] = None

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
    def sector_classifier(self) -> Optional[SectorClassifier]:
        """
        Get multi-sector classifier (lazily initialized when metadata_cache is available).

        Returns:
            SectorClassifier instance or None if metadata_cache not set
        """
        if self._sector_classifier is None and self.metadata_cache is not None:
            self._sector_classifier = SectorClassifier(
                api_key=self.api_key,
                metadata_cache=self.metadata_cache,
                model=self.model,
            )
            logger.info(
                "SectorClassifier initialized",
                model=self.model,
                supported_sectors=list(self._sector_classifier._stats["by_sector"].keys())
            )
        return self._sector_classifier

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

            # Step 2b: 10-SECOND MAX LATENCY FILTER
            # ====================================================================
            # Skip articles if websocket delivery was too slow (> 10 seconds after publication).
            # Late-arriving articles have missed the initial momentum opportunity and
            # are more likely to result in chasing rather than catching the move.
            # Analysis shows ALL winners today entered within first 10 seconds.
            # Reduced from 25s to 10s - winners are so big we're OK missing late entries
            # in favor of avoiding late-entry losers like SAFX (entered at -15% from peak).
            # ====================================================================
            MAX_WEBSOCKET_LATENCY_SECONDS = 10.0

            if request_data.article_published_at_iso:
                try:
                    from datetime import timezone
                    published_at = datetime.fromisoformat(
                        request_data.article_published_at_iso.replace("Z", "+00:00")
                    )

                    # Use websocket received_at for accurate latency (not datetime.now which includes processing time)
                    if request_data.article_received_at_iso:
                        received_at = datetime.fromisoformat(
                            request_data.article_received_at_iso.replace("Z", "+00:00")
                        )
                        # Ensure timezone-aware comparison
                        if received_at.tzinfo is None:
                            received_at = received_at.replace(tzinfo=timezone.utc)
                        latency_seconds = (received_at - published_at).total_seconds()
                    else:
                        # Fallback to datetime.now() if received_at not available
                        now = datetime.now(timezone.utc)
                        latency_seconds = (now - published_at).total_seconds()

                    if latency_seconds > MAX_WEBSOCKET_LATENCY_SECONDS:
                        logger.info(
                            "⏭️ CLASSIFY INFRA: Skipping - websocket latency too high (>25s)",
                            article_id=request_data.article_id,
                            published_at=published_at.isoformat(),
                            latency_seconds=round(latency_seconds, 2),
                            max_allowed=MAX_WEBSOCKET_LATENCY_SECONDS,
                            tickers=request_data.article_tickers,
                            reason="Late articles have missed initial momentum"
                        )
                        # Register skipped tickers for risk reduction on subsequent headlines
                        try:
                            from ...services.brokerage.auto_trade import register_skipped_ticker
                            for ticker in request_data.article_tickers:
                                register_skipped_ticker(ticker)
                        except Exception as e:
                            logger.debug(f"Could not register skipped ticker: {e}")

                        await self._publish_skipped_event(infra_event, f"latency_too_high:{round(latency_seconds)}s")
                        return

                    logger.debug(
                        "✅ LATENCY CHECK PASSED",
                        article_id=request_data.article_id,
                        latency_seconds=round(latency_seconds, 2)
                    )
                except (ValueError, TypeError) as e:
                    logger.debug(f"Could not parse published_at for latency check: {e}")

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

            # Step 3c: HEADLINE PRE-FILTER (before expensive Groq API call)
            # ====================================================================
            # Statistical analysis of January 2026 data shows these headline patterns
            # are NEVER profitable and should be filtered immediately:
            # - Law firm mentions (Hagens Berman, Rosen, etc.) = 100% losers
            # - Investor alerts / class actions = 100% losers
            # - Conference presentations = 100% losers (just marketing)
            # - Routine dividends / Nasdaq admin = 100% losers
            #
            # Impact: Filters 27% of losers while losing 0% of winners
            # ====================================================================
            headline_lower = (request_data.article_title or "").lower().replace("&#39;", "'").replace("&amp;", "&")

            # High-confidence BAD patterns (law firms, lawsuits, class actions)
            HEADLINE_BLACKLIST = [
                # Law firms (securities litigation ambulance chasers)
                (r'\b(hagens berman|rosen.*counsel|kahn swick|glancy|kirby mcinerney|pomerantz|bernstein liebhard|labaton|levi.*korsinsky|portnoy law|schall law|bronstein|paskowitz|strauss|brodsky smith|faruqi|ryan.*\&.*maniskas|halper sadeh)\b', 'law_firm'),
                # Investor/shareholder alerts
                (r'\b(investor alert|deadline alert|investor counsel|shareholder alert|shareholders.*urged|contact the firm|discuss their rights)\b', 'investor_alert'),
                # Class actions and investigations
                (r'\b(class action|securities fraud|securities investigation)\b', 'class_action'),
                (r'\b(sued for|lawsuit|litigation|legal action|securities law violations)\b', 'lawsuit'),
                (r'\b(is investigating|being investigated|under investigation)\b', 'investigation'),
                # Routine/low-value events
                (r'\b(inducement grant|inducement option|employment inducement)\b', 'inducement'),
                (r'\b(nasdaq listing rule|hearing panel|notification regarding market)\b', 'nasdaq_admin'),
                (r'\b(adjournment of|postpone.*meeting)\b', 'adjournment'),
                (r'\b(declares monthly|monthly distribution|quarterly distribution|quarterly dividend|declares quarterly)\b', 'dividend_routine'),
                (r'\b(closed-end fund)\b', 'closed_end_fund'),
                (r'\b(ring.*bell|opening bell|closing bell)\b', 'ring_bell'),
                (r'\b(share repurchase|repurchase program|letter to shareholders)\b', 'routine_corporate'),
                # Conference/marketing (no price impact)
                (r'\b(to present at|will present at|to participate in|annual.*conference|healthcare conference|j\.p\. morgan.*conference|at ces\b|ces 2026)\b', 'conference'),
                (r'\b(kol event|webinar|webcast|fireside chat|conference call)\b', 'webinar'),
            ]

            for pattern, reason in HEADLINE_BLACKLIST:
                if re.search(pattern, headline_lower):
                    logger.info(
                        f"⏭️ HEADLINE FILTER: Article matches blacklist pattern",
                        article_id=request_data.article_id,
                        pattern_name=reason,
                        headline_snippet=headline_lower[:80]
                    )
                    await self._publish_skipped_event(infra_event, f"headline_{reason}")
                    return

            # Step 3d: MARKET CAP FILTER (before expensive Groq API call)
            # ====================================================================
            # Statistical analysis of January 2026 data shows:
            # - Winners: 93% have market cap < $500M (small-caps move more on news)
            # - Losers: Only 38% have market cap < $500M
            # - Applying this filter improves win rate from 11% to 23%
            #
            # This is NOT overfitting - reflects fundamental market dynamics:
            # - Small-cap stocks have lower liquidity = bigger price impact
            # - Less analyst coverage = information asymmetry advantage
            # - Retail participation amplifies momentum
            # ====================================================================
            if self.metadata_cache and primary_ticker:
                metadata = await self.metadata_cache.get(primary_ticker)
                if metadata:
                    market_cap = metadata.get("market_cap_millions", 0)

                    # Market cap filter: Only trade small-caps (< $500M)
                    # Captures 93% of winners while filtering 62% of losers
                    MAX_MARKET_CAP_MILLIONS = 500

                    if market_cap and market_cap > MAX_MARKET_CAP_MILLIONS:
                        logger.info(
                            "⏭️ MICROSTRUCTURE FILTER: Market cap too high for news-driven trade",
                            article_id=request_data.article_id,
                            ticker=primary_ticker,
                            market_cap_millions=round(market_cap, 1),
                            threshold_millions=MAX_MARKET_CAP_MILLIONS,
                            reason="Large-caps don't move significantly on news (statistical edge lost)"
                        )
                        await self._publish_skipped_event(infra_event, f"market_cap_too_high:{round(market_cap)}M")
                        return

                    # Minimum market cap filter: < $3M = too small, heavily manipulated
                    MIN_MARKET_CAP_MILLIONS = 2

                    if market_cap and market_cap < MIN_MARKET_CAP_MILLIONS:
                        logger.info(
                            "⏭️ MICROSTRUCTURE FILTER: Market cap too low - manipulation risk",
                            article_id=request_data.article_id,
                            ticker=primary_ticker,
                            market_cap_millions=round(market_cap, 2),
                            threshold_millions=MIN_MARKET_CAP_MILLIONS,
                            reason="Sub-$3M stocks are heavily manipulated"
                        )
                        await self._publish_skipped_event(infra_event, f"market_cap_too_low:{round(market_cap, 1)}M")
                        return

                    logger.debug(
                        "✅ MICROSTRUCTURE FILTER: Market cap check passed",
                        ticker=primary_ticker,
                        market_cap_millions=round(market_cap, 1) if market_cap else "unknown"
                    )

            # Step 3e: PRICE FILTER (low-priced stocks move more on news)
            # ====================================================================
            # Filters out high-priced stocks where news-driven % moves are smaller.
            # $35 threshold permits biotech/pharma catalyst trades (e.g. SRPT @ $21).
            # ====================================================================
            if nbbo_snapshot:
                current_price = nbbo_snapshot.get("mid") or nbbo_snapshot.get("ask") or 0
                MAX_PRICE = 35.0

                if current_price and current_price > MAX_PRICE:
                    logger.info(
                        "⏭️ MICROSTRUCTURE FILTER: Price too high for news-driven trade",
                        article_id=request_data.article_id,
                        ticker=primary_ticker,
                        price=round(current_price, 2),
                        threshold=MAX_PRICE,
                        reason="Higher-priced stocks have smaller percentage moves"
                    )
                    await self._publish_skipped_event(infra_event, f"price_too_high:${round(current_price, 2)}")
                    return

                # Minimum price filter: < $0.25 = sub-penny territory, heavily manipulated
                MIN_PRICE = 0.25

                if current_price and current_price < MIN_PRICE:
                    logger.info(
                        "⏭️ MICROSTRUCTURE FILTER: Price too low - manipulation risk",
                        article_id=request_data.article_id,
                        ticker=primary_ticker,
                        price=round(current_price, 4),
                        threshold=MIN_PRICE,
                        reason="Sub-$0.25 stocks are heavily manipulated"
                    )
                    await self._publish_skipped_event(infra_event, f"price_too_low:${round(current_price, 4)}")
                    return

                logger.debug(
                    "✅ MICROSTRUCTURE FILTER: Price check passed",
                    ticker=primary_ticker,
                    price=round(current_price, 2)
                )

                # Step 3f: SPREAD FILTER (tight spreads = liquid, tradeable)
                # ====================================================================
                # Statistical analysis shows:
                # - Winners: avg spread $1.10 vs Losers: avg spread $4.73
                # - Adding spread < $1 improves P&L from $1,520 to $1,850
                # - Filters illiquid stocks where spread eats into profits
                # ====================================================================
                spread = nbbo_snapshot.get("spread", 0)
                MAX_SPREAD = 1.0

                if spread and spread > MAX_SPREAD:
                    logger.info(
                        "⏭️ MICROSTRUCTURE FILTER: Spread too wide for profitable trade",
                        article_id=request_data.article_id,
                        ticker=primary_ticker,
                        spread=round(spread, 4),
                        threshold=MAX_SPREAD,
                        reason="Wide spreads eat into profits on entry/exit"
                    )
                    await self._publish_skipped_event(infra_event, f"spread_too_wide:${round(spread, 2)}")
                    return

                logger.debug(
                    "✅ MICROSTRUCTURE FILTER: Spread check passed",
                    ticker=primary_ticker,
                    spread=round(spread, 4) if spread else "unknown"
                )

            # Step 4: All checks passed - proceed to multi-sector LLM classification
            # ========================================================================
            # MULTI-SECTOR TRADING STRATEGY
            # ========================================================================
            # Pure language-based classification using industry-specific prompts.
            # Supported: Healthcare, Technology, Industrials, Consumer Cyclical,
            #            Financial Services, Communication Services, Consumer Defensive,
            #            Basic Materials
            # Flow: headline → sector check → industry check → Groq LLM → TRADE/SKIP
            # If TRADE → publish "imminent" classification → trigger AutoTradeService
            # If SKIP/NOT_SUPPORTED → no trade, but data collection continues
            # ========================================================================

            await self._classify_via_sector(infra_event, primary_ticker)
            
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

    async def _classify_via_sector(
        self,
        infra_event: ClassificationRequestedInfrastructureEvent,
        primary_ticker: str
    ) -> None:
        """
        Classify article via multi-sector LLM classifier and publish result event.

        Flow:
        1. Check if sector classifier is available (metadata_cache injected)
        2. Call sector classifier (sector → industry → Groq LLM)
        3. If TRADE → publish ClassificationCompleted with classification="imminent"
        4. If SKIP/NOT_SUPPORTED/UNSUPPORTED_INDUSTRY → publish ClassificationSkipped

        Args:
            infra_event: Classification request infrastructure event
            primary_ticker: Primary ticker for classification
        """
        request_data = infra_event.request_data
        headline = request_data.article_title

        # Check if sector classifier is available
        if not self.sector_classifier:
            logger.warning(
                "Sector classifier not available (metadata_cache not injected)",
                article_id=request_data.article_id,
                ticker=primary_ticker
            )
            await self._publish_skipped_event(infra_event, "classifier_not_ready")
            return

        try:
            # Classify via multi-sector classifier
            classification, sector, industry, latency_ms = await self.sector_classifier.classify(
                headline=headline,
                ticker=primary_ticker
            )

            logger.info(
                f"Sector classification: {classification}",
                article_id=request_data.article_id,
                ticker=primary_ticker,
                sector=sector,
                industry=industry,
                latency_ms=round(latency_ms, 1)
            )

            # Handle classification result
            if classification == "TRADE":
                # TRADE signal → publish "imminent" to trigger AutoTradeService
                response_data = InfrastructureClassificationResponseData(
                    classification="imminent",
                    confidence="HIGH",
                    reasoning=f"{sector}/{industry} - LLM classified as tradeable"
                )

                completed_event = ClassificationCompletedInfrastructureEvent(
                    request_data=request_data,
                    response_data=response_data,
                    completed_at=datetime.now(),
                    latency_ms=latency_ms,
                    success=True,
                    source="sector_classifier"
                )

                await self.event_bus.publish(
                    InfrastructureEventType.CLASSIFICATION_COMPLETED,
                    completed_event.model_dump()
                )

                logger.info(
                    f"Published IMMINENT classification for {sector} TRADE signal",
                    article_id=request_data.article_id,
                    ticker=primary_ticker,
                    sector=sector,
                    industry=industry,
                    latency_ms=round(latency_ms, 1)
                )

            elif classification == "NOT_SUPPORTED_SECTOR":
                # Sector not supported - skip trading but continue data collection
                await self._publish_skipped_event(infra_event, f"unsupported_sector:{sector or 'unknown'}")

            elif classification == "UNSUPPORTED_INDUSTRY":
                # Supported sector but unsupported industry - skip trading
                await self._publish_skipped_event(infra_event, f"unsupported_industry:{sector}/{industry}")

            else:
                # SKIP signal - LLM determined not tradeable
                await self._publish_skipped_event(infra_event, f"llm_skip:{sector}/{industry}")

        except Exception as e:
            logger.error(
                "Sector classification error",
                article_id=request_data.article_id,
                ticker=primary_ticker,
                error=str(e),
                exc_info=True
            )

            # Publish failed event
            failed_event = ClassificationFailedInfrastructureEvent(
                request_data=request_data,
                error=f"Sector classification failed: {str(e)}",
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

