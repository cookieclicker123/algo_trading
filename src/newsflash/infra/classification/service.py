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
import html
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
        groq_api_key: str,
        anthropic_api_key: str,
        anthropic_model: str = "claude-sonnet-4-6-20250514",
        metrics_service=None,  # Required - injected via DI
        ticker_validator=None,  # Will be injected after brokerage is initialized
        market_data_validator=None,  # Will be injected after brokerage is initialized
        quote_fetcher=None,  # Will be injected after brokerage is initialized
        metadata_cache=None,  # Will be injected after cache is initialized
        enabled: bool = True,
    ):
        """
        Initialize classification infrastructure service.

        Args:
            event_bus: Event bus instance for publishing/subscribing to events
            groq_api_key: Groq API key for triage (Llama 70B headline type detection)
            anthropic_api_key: Anthropic API key for sector classification (Claude Sonnet)
            anthropic_model: Anthropic model name for sector classification
            enabled: Whether classification is enabled
            metrics_service: Optional metrics service for statistics (injected via DI)
            ticker_validator: TickerValidator instance for exchange validation (injected via DI)
            market_data_validator: MarketDataValidator instance for market cap/price validation (injected via DI)
            quote_fetcher: AlpacaQuoteFetcher instance for NBBO availability check (injected via DI)
            metadata_cache: MetadataCache instance for sector/industry lookup (injected via DI)
        """
        self.enabled = enabled
        self.groq_api_key = groq_api_key
        self.anthropic_api_key = anthropic_api_key
        self.anthropic_model = anthropic_model
        # Legacy: keep api_key reference for triage and any code that reads self.api_key
        self.api_key = groq_api_key
        self.model = anthropic_model
        self.metrics_service = metrics_service  # ✅ Injected metrics service
        self.ticker_validator = ticker_validator  # ✅ Injected ticker validator
        self.market_data_validator = market_data_validator  # ✅ Injected market data validator
        self.quote_fetcher = quote_fetcher  # ✅ Injected quote fetcher for NBBO check
        self.metadata_cache = metadata_cache  # ✅ Injected metadata cache for sector classifier

        # Multi-sector classifier (initialized lazily when metadata_cache is set)
        self._sector_classifier: Optional[SectorClassifier] = None

        # Triage cache: article_id → headline_type from prefilter LLM triage
        # Reused in _classify_via_sector to avoid duplicate LLM call.
        # Auto-evicts oldest entries when size > 50 (prevents memory leak from skipped articles).
        self._triage_cache: dict = {}

        # Legacy Groq client (kept for _classify_via_groq fallback path)
        self.client: Optional[AsyncGroq] = None
        if enabled and groq_api_key:
            self.client = AsyncGroq(api_key=groq_api_key)
            logger.info(
                "ClassificationInfrastructureService initialized",
                triage_model="claude-haiku-4-5 (Anthropic)",
                sector_model=f"{anthropic_model} (Anthropic)",
            )
        else:
            logger.info("ClassificationInfrastructureService: Disabled or no API keys provided")
        
        # Stateful: System prompt (cached, loaded once)
        self.system_prompt = self._load_prompt()
        
        # Event bus for publishing events
        self.event_bus = event_bus
        
        # ✅ No stats dictionary - MetricsService aggregates from events!
        
        logger.info(
            "ClassificationInfrastructureService initialized",
            sector_model=anthropic_model,
            enabled=enabled,
            has_groq_key=bool(groq_api_key),
            has_anthropic_key=bool(anthropic_api_key),
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
                api_key=self.anthropic_api_key,
                metadata_cache=self.metadata_cache,
                model=self.anthropic_model,
                groq_api_key=self.groq_api_key,
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
                    reason = await self.ticker_validator.get_validation_reason(request_data.article_tickers[0])
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
            # Raised to 30s — the real safeguard is the postfilter pub-to-recv price
            # movement check (max 3% ask change), NOT the time elapsed. VNRX arrived
            # 21s late but the stock hadn't moved yet → missed +28.66%.
            # Postfilter catches actual late-to-move entries via price checks:
            #   - pub_to_recv: max 3% ask change (8% mega)
            #   - momentum_exhaustion: max 5% runup
            #   - pump_and_dump: max 5.5% ask vs VWAP
            # ====================================================================
            MAX_WEBSOCKET_LATENCY_SECONDS = 15.0  # Was 30s — too stale. 15s gives late entry monitoring room to work (was originally 10s)

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
                            "⏭️ CLASSIFY INFRA: Skipping - websocket latency too high (>15s)",
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

            # Early triage: classify headline type for ALL articles with valid tickers.
            # Runs before any remaining prefilters so recall records always have headline_type.
            # Cost: ~200-300ms (Groq 8B). Also used for HC spread bypass downstream.
            triage_headline_type = None
            if request_data.article_title:
                try:
                    from ...shared.statistics.headline_classifier import get_headline_classifier
                    triage_classifier = get_headline_classifier()
                    triage_headline_type = await triage_classifier.triage(
                        headline=request_data.article_title,
                        timeout=3.0,
                    )
                    if triage_headline_type:
                        logger.debug(
                            f"Early headline triage: {triage_headline_type}",
                            article_id=request_data.article_id,
                        )
                except Exception as e:
                    logger.debug(f"Early headline triage failed (non-blocking): {e}")

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
                    await self._publish_skipped_event(infra_event, "nbbo_unavailable", headline_type=triage_headline_type)
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
                (r'\b(letter to shareholders)\b', 'routine_corporate'),
                # Conference/marketing (no price impact)
                (r'\b(to present at|will present at|to participate in|annual.*conference|healthcare conference|j\.p\. morgan.*conference|at ces\b|ces 2026)\b', 'conference'),
                (r'\b(kol event|webinar|webcast|fireside chat|conference call)\b', 'webinar'),

                # ============================================================
                # VAGUE/NON-COMMITTAL LANGUAGE (Feb 2026 backtest: 100% losers)
                # ============================================================
                # Non-binding = not a real deal, frequently falls through
                (r'\bnon-binding\b', 'non_binding'),
                (r'\bnon binding\b', 'non_binding'),
                # NOTE: LOI (Letter of Intent) removed from prefilter - sent to AI for nuanced classification
                # Acquisition LOIs with named targets are winning patterns, vague LOIs are losers
                # NOTE: "enters_into_vague" removed - too aggressive, blocks legitimate
                # distribution agreements with named counterparties (JFBR false negative).
                # LLM prompts already handle vague vs specific "enters into" language.

                # ============================================================
                # DEFENSIVE/DISTRESSED LANGUAGE (Feb 2026 backtest: 100% losers)
                # ============================================================
                # Financial restructuring = often distressed company
                (r'\b(financing restructuring|restructures? (debt|loan|credit)|debt restructuring)\b', 'restructuring_distress'),
                # Defensive corporate language
                (r'\bstrengthens? (its )?financial position\b', 'defensive_language'),
                (r'\bimproves? (its )?financial (position|flexibility)\b', 'defensive_language'),

                # ============================================================
                # PROVISIONAL/LIMITED (Feb 2026 backtest: high loser rate)
                # ============================================================
                # Patent allowance = not final grant
                (r'\bpatent allowance\b', 'patent_not_final'),
                # Canadian-only patents (limited market)
                (r'\bcanadian patent\b(?!.*us|.*fda|.*united states)', 'limited_geography'),

                # ============================================================
                # LINE EXTENSIONS / LABEL EXPANSIONS (not breakthrough - BFRI pattern)
                # ============================================================
                # Body part expansions for existing drugs
                (r'\b(extremities|trunk|neck|torso|limbs)\b.*\b(study|trial|results)\b', 'line_extension_body'),
                (r'\b(study|trial|results)\b.*\b(extremities|trunk|neck|torso|limbs)\b', 'line_extension_body'),
                # Supplemental NDA / label expansion language
                (r'\b(supplemental nda|snda|label expansion|additional indication)\b', 'line_extension'),
                # "New indication" for existing drug
                (r'\bnew indication\b.*\bexisting\b', 'line_extension'),
            ]

            for pattern, reason in HEADLINE_BLACKLIST:
                if re.search(pattern, headline_lower):
                    logger.info(
                        f"⏭️ HEADLINE FILTER: Article matches blacklist pattern",
                        article_id=request_data.article_id,
                        pattern_name=reason,
                        headline_snippet=headline_lower[:80]
                    )
                    await self._publish_skipped_event(infra_event, f"headline_{reason}", headline_type=triage_headline_type)
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
                    # EXCEPTION: Healthcare Biotechnology and Medical Devices are exempt
                    # — larger biotechs are more established with real drugs/revenue
                    # EXCEPTION: HC / Clinical Breakthrough headlines get $1B cap
                    # — high-signal headlines (gov contracts, clinical breakthroughs) move
                    #   stocks reliably even at larger market caps (CGNT $535M +8.7%)
                    MAX_MARKET_CAP_MILLIONS = 500
                    MAX_MARKET_CAP_HIGH_SIGNAL_MILLIONS = 1000

                    sector = metadata.get("sector", "")
                    industry = metadata.get("industry", "")
                    healthcare_exempt = (
                        sector == "Healthcare"
                        and industry in ("Biotechnology", "Medical Devices")
                    )

                    # HC and clinical breakthrough headline types use relaxed $1B cap
                    HIGH_SIGNAL_PREFILTER_TYPES = frozenset({
                        "government_contract", "military_contract", "defense_order",
                        "major_contract", "clinical_breakthrough", "cancer_catalyst",
                        "ai_rebranding",
                    })
                    is_high_signal_headline = triage_headline_type in HIGH_SIGNAL_PREFILTER_TYPES

                    effective_market_cap_limit = (
                        MAX_MARKET_CAP_HIGH_SIGNAL_MILLIONS if is_high_signal_headline
                        else MAX_MARKET_CAP_MILLIONS
                    )

                    if market_cap and market_cap > effective_market_cap_limit and not healthcare_exempt:
                        logger.info(
                            "⏭️ MICROSTRUCTURE FILTER: Market cap too high for news-driven trade",
                            article_id=request_data.article_id,
                            ticker=primary_ticker,
                            market_cap_millions=round(market_cap, 1),
                            threshold_millions=effective_market_cap_limit,
                            reason="Large-caps don't move significantly on news (statistical edge lost)"
                        )
                        await self._publish_skipped_event(infra_event, f"market_cap_too_high:{round(market_cap)}M", headline_type=triage_headline_type)
                        return

                    # Minimum market cap filter: < $1M = too small, heavily manipulated
                    # Lowered from $1.5M to $1M - borderline stocks like LRHC ($1.08M) with
                    # legitimate AI headlines were being unfairly blocked (+30.9% missed).
                    # EXCEPTION: Transformational headlines (large $ relative to company) bypass this
                    MIN_MARKET_CAP_MILLIONS = 1.0

                    if market_cap and market_cap < MIN_MARKET_CAP_MILLIONS:
                        # Check for transformational headline exception
                        # If headline contains dollar amount > 5x market cap, let AI decide
                        headline = request_data.article_title
                        dollar_amount = self._extract_dollar_amount_millions(headline)

                        if dollar_amount and market_cap > 0:
                            magnitude_ratio = dollar_amount / market_cap
                            if magnitude_ratio >= 5.0:
                                # Transformational headline - bypass market cap filter
                                logger.info(
                                    "✅ MARKET CAP EXCEPTION: Transformational headline bypasses low market cap filter",
                                    article_id=request_data.article_id,
                                    ticker=primary_ticker,
                                    market_cap_millions=round(market_cap, 2),
                                    dollar_amount_millions=round(dollar_amount, 1),
                                    magnitude_ratio=f"{magnitude_ratio:.1f}x",
                                    headline=headline[:80],
                                    reason="Dollar amount is transformational relative to company size - letting AI decide"
                                )
                                # Don't return - continue to classification
                            else:
                                # Dollar amount not large enough to be transformational
                                logger.info(
                                    "⏭️ MICROSTRUCTURE FILTER: Market cap too low - manipulation risk",
                                    article_id=request_data.article_id,
                                    ticker=primary_ticker,
                                    market_cap_millions=round(market_cap, 2),
                                    threshold_millions=MIN_MARKET_CAP_MILLIONS,
                                    dollar_amount_millions=round(dollar_amount, 1) if dollar_amount else None,
                                    magnitude_ratio=f"{magnitude_ratio:.1f}x" if dollar_amount else None,
                                    reason="Sub-$1M stocks are heavily manipulated (dollar amount not transformational)"
                                )
                                await self._publish_skipped_event(infra_event, f"prefilter_market_cap_too_low:{round(market_cap, 1)}M", headline_type=triage_headline_type)
                                return
                        else:
                            # No dollar amount in headline - apply normal filter
                            logger.info(
                                "⏭️ MICROSTRUCTURE FILTER: Market cap too low - manipulation risk",
                                article_id=request_data.article_id,
                                ticker=primary_ticker,
                                market_cap_millions=round(market_cap, 2),
                                threshold_millions=MIN_MARKET_CAP_MILLIONS,
                                reason="Sub-$1M stocks are heavily manipulated"
                            )
                            await self._publish_skipped_event(infra_event, f"prefilter_market_cap_too_low:{round(market_cap, 1)}M", headline_type=triage_headline_type)
                            return

                    logger.debug(
                        "✅ MICROSTRUCTURE FILTER: Market cap check passed",
                        ticker=primary_ticker,
                        market_cap_millions=round(market_cap, 1) if market_cap else "unknown"
                    )

            # Step 3e: PRICE FILTER (minimum only - max price filter removed)
            # ====================================================================
            # MAX PRICE FILTER REMOVED: AI classification now handles headline quality.
            # Biotech analysis showed best winners are $1-10 stocks with strong catalysts.
            # The AI prompt filters weak headlines (IND clearances, service partnerships).
            # ====================================================================
            if nbbo_snapshot:
                current_price = nbbo_snapshot.get("mid") or nbbo_snapshot.get("ask") or 0

                # Minimum price filter: < $0.05 = sub-penny territory
                # Safety filters (pump-and-dump, momentum exhaustion, spread, etc.) handle risk
                MIN_PRICE = 0.05

                if current_price and current_price < MIN_PRICE:
                    logger.info(
                        "⏭️ MICROSTRUCTURE FILTER: Price too low",
                        article_id=request_data.article_id,
                        ticker=primary_ticker,
                        price=round(current_price, 4),
                        threshold=MIN_PRICE,
                        reason="Sub-$0.05 stocks"
                    )
                    await self._publish_skipped_event(infra_event, f"price_too_low:${round(current_price, 4)}", headline_type=triage_headline_type)
                    return

                logger.debug(
                    "✅ MICROSTRUCTURE FILTER: Price check passed",
                    ticker=primary_ticker,
                    price=round(current_price, 2)
                )

                # Step 3f: SPREAD FILTER (tight spreads = liquid, tradeable)
                # ====================================================================
                # Percentage-based: spread relative to price matters, not absolute $
                # A $1.70 spread on a $62 stock (2.7%) is fine, but $0.50 on a $2 stock (25%) is not
                # ====================================================================
                spread = nbbo_snapshot.get("spread", 0)
                spread_pct = nbbo_snapshot.get("spread_pct", 0)
                MAX_SPREAD_PCT_PREFILTER = 5.0

                # HIGH-CONVICTION SPREAD BYPASS: If headline is high-conviction (e.g. military_contract),
                # relax spread from 5% → 10%. Uses early triage result (already populated above).
                HIGH_CONVICTION_PREFILTER_TYPES = frozenset({
                    "government_contract", "military_contract", "defense_order",
                    "major_contract",  # Commercial contracts — 46.2% IMMINENT win rate, avg MFE +50%
                    "ai_rebranding",   # Corporate rebrand to AI identity — sustained moves
                })
                MAX_SPREAD_PCT_HIGH_CONVICTION = 10.0  # Defense sweet spot is 3-10%, zero winners above 10%

                is_high_conviction_headline = triage_headline_type in HIGH_CONVICTION_PREFILTER_TYPES

                # AI BREAKTHROUGH SPREAD LENIENCY: Price-tiered thresholds for cheap stocks
                # Cheap stocks with genuine AI breakthroughs have structurally wide spreads
                # that thin rapidly when the news is real (ISPC: 9.73% → 1.73% in 10 min).
                AI_BREAKTHROUGH_PREFILTER_TYPES = frozenset({"ai_breakthrough"})
                is_ai_breakthrough_headline = triage_headline_type in AI_BREAKTHROUGH_PREFILTER_TYPES

                CLINICAL_BREAKTHROUGH_PREFILTER_TYPES = frozenset({"clinical_breakthrough"})
                is_clinical_breakthrough_headline = triage_headline_type in CLINICAL_BREAKTHROUGH_PREFILTER_TYPES

                # MERGER AGREEMENT: Definitive merger between two companies (not acquisition).
                # Wide spreads are normal for small-cap biotechs announcing mergers.
                MERGER_PREFILTER_TYPES = frozenset({"merger_agreement"})
                is_merger_headline = triage_headline_type in MERGER_PREFILTER_TYPES
                MAX_SPREAD_PCT_MERGER = 7.5

                # ACQUISITION WITH DOLLAR AMOUNTS: Acquisitions that name specific dollar
                # figures (revenue, deal size) convert from cash-outflow to growth catalyst.
                # Allow up to 10% spread — these are high-signal events (LRHC +91%, DGNX +111%).
                MAX_SPREAD_PCT_ACQUISITION_WITH_DOLLARS = 10.0
                is_acquisition_with_dollars = (
                    triage_headline_type == "acquisition_announced"
                    and "$" in request_data.article_title
                )

                if is_high_conviction_headline:
                    effective_spread_threshold = MAX_SPREAD_PCT_HIGH_CONVICTION
                elif is_clinical_breakthrough_headline:
                    effective_spread_threshold = 10.0
                elif is_acquisition_with_dollars:
                    effective_spread_threshold = MAX_SPREAD_PCT_ACQUISITION_WITH_DOLLARS
                elif is_merger_headline:
                    effective_spread_threshold = MAX_SPREAD_PCT_MERGER
                elif is_ai_breakthrough_headline and current_price:
                    if current_price < 0.30:
                        effective_spread_threshold = 10.0
                    else:
                        effective_spread_threshold = 7.5
                else:
                    effective_spread_threshold = MAX_SPREAD_PCT_PREFILTER

                if is_merger_headline and spread_pct and spread_pct > MAX_SPREAD_PCT_PREFILTER:
                    logger.info(
                        f"🤝 MERGER AGREEMENT: Relaxing spread prefilter (5% → {MAX_SPREAD_PCT_MERGER}%)",
                        article_id=request_data.article_id,
                        ticker=primary_ticker,
                        spread_pct=round(spread_pct, 2),
                        headline_type=triage_headline_type,
                        effective_threshold=MAX_SPREAD_PCT_MERGER,
                    )

                if is_acquisition_with_dollars and spread_pct and spread_pct > MAX_SPREAD_PCT_PREFILTER:
                    logger.info(
                        f"💰 ACQUISITION WITH $: Relaxing spread prefilter (5% → {MAX_SPREAD_PCT_ACQUISITION_WITH_DOLLARS}%)",
                        article_id=request_data.article_id,
                        ticker=primary_ticker,
                        spread_pct=round(spread_pct, 2),
                        headline_type=triage_headline_type,
                        effective_threshold=MAX_SPREAD_PCT_ACQUISITION_WITH_DOLLARS,
                    )

                if is_high_conviction_headline and spread_pct and spread_pct > MAX_SPREAD_PCT_PREFILTER:
                    logger.info(
                        "✅ HIGH-CONVICTION HEADLINE: Relaxing spread prefilter (5% → 10%)",
                        article_id=request_data.article_id,
                        ticker=primary_ticker,
                        spread_pct=round(spread_pct, 2),
                        headline_type=triage_headline_type,
                        effective_threshold=MAX_SPREAD_PCT_HIGH_CONVICTION,
                    )

                if is_ai_breakthrough_headline and spread_pct and spread_pct > MAX_SPREAD_PCT_PREFILTER:
                    logger.info(
                        "🤖 AI BREAKTHROUGH HEADLINE: Price-tiered spread prefilter",
                        article_id=request_data.article_id,
                        ticker=primary_ticker,
                        price=round(current_price, 2) if current_price else None,
                        spread_pct=round(spread_pct, 2),
                        headline_type=triage_headline_type,
                        effective_threshold=effective_spread_threshold,
                    )

                if spread_pct and spread_pct > effective_spread_threshold:
                    logger.info(
                        "⏭️ MICROSTRUCTURE FILTER: Spread too wide for profitable trade",
                        article_id=request_data.article_id,
                        ticker=primary_ticker,
                        spread=round(spread, 4),
                        spread_pct=round(spread_pct, 2),
                        threshold_pct=effective_spread_threshold,
                        headline_type=triage_headline_type,
                        reason="Wide spreads eat into profits on entry/exit"
                    )
                    await self._publish_skipped_event(infra_event, f"prefilter_spread_too_wide:{round(spread_pct, 2)}%", headline_type=triage_headline_type)
                    return

                logger.debug(
                    "✅ MICROSTRUCTURE FILTER: Spread check passed",
                    ticker=primary_ticker,
                    spread=round(spread, 4) if spread else "unknown",
                    spread_pct=round(spread_pct, 2) if spread_pct else "unknown"
                )

                # Cache triage result for reuse in _classify_via_sector (avoids duplicate LLM call)
                if triage_headline_type:
                    self._triage_cache[request_data.article_id] = triage_headline_type
                    # Evict oldest entries to prevent memory leak
                    if len(self._triage_cache) > 50:
                        oldest_key = next(iter(self._triage_cache))
                        del self._triage_cache[oldest_key]

            # Step 4: UNIVERSAL CATALYST CHECK (before industry-specific LLM)
            # ====================================================================
            # Some catalysts are universally bullish regardless of industry:
            # - Debt elimination/restructuring (improves balance sheet)
            # - Definitive agreements (M&A or partnership finalized)
            # - Being acquired (acquisition target)
            # These bypass industry LLM classification to avoid false negatives.
            # ====================================================================
            UNIVERSAL_TRADE_PATTERNS = [
                # Debt elimination/restructuring
                (r'\b(eliminates?|removes?|restructures?|retires?).*\b(debt|obligation|liability|liabilities)\b', 'debt_elimination'),
                (r'\b(debt|obligation).*\b(eliminat|remov|restructur|retir)\b', 'debt_elimination'),
                (r'\bimproving cash flow\b', 'cash_flow_improvement'),
                # Definitive agreements (M&A finalized)
                (r'\b(completes?|signs?|enters?|executes?).*definitive.*agreement\b', 'definitive_agreement'),
                (r'\bdefinitive.*agreement.*\b(complet|sign|enter|execut)\b', 'definitive_agreement'),
                # Acquisition target (company being bought = stock goes UP)
                (r'\bto be acquired\b', 'acquisition_target'),
                (r'\bagrees to be acquired\b', 'acquisition_target'),
                (r'\bwill be acquired\b', 'acquisition_target'),
                (r'\breceives? (buyout|acquisition) (offer|proposal)\b', 'acquisition_target'),
                (r'\btender offer for\b', 'acquisition_target'),
                # Strategic investment received (not making, receiving)
                (r'\b(receives?|secures?|closes?).*strategic investment\b', 'strategic_investment_received'),
            ]

            for pattern, catalyst_type in UNIVERSAL_TRADE_PATTERNS:
                if re.search(pattern, headline_lower):
                    logger.info(
                        f"🎯 UNIVERSAL CATALYST: Matched '{catalyst_type}' - bypassing industry LLM",
                        article_id=request_data.article_id,
                        ticker=primary_ticker,
                        pattern=catalyst_type,
                        headline_snippet=headline_lower[:80]
                    )

                    # Get sector/industry for logging (optional, don't fail if unavailable)
                    sector, industry = None, None
                    if self.metadata_cache:
                        metadata = await self.metadata_cache.get(primary_ticker)
                        if metadata:
                            sector = metadata.get("sector")
                            industry = metadata.get("industry")

                    # Get headline_type from triage cache (always populated after prefilters)
                    cached_headline_type = self._triage_cache.pop(request_data.article_id, None)

                    # Publish IMMINENT classification directly (bypass LLM)
                    response_data = InfrastructureClassificationResponseData(
                        classification="imminent",
                        confidence="HIGH",
                        reasoning=f"Universal catalyst: {catalyst_type}",
                        headline_type=cached_headline_type,
                    )

                    completed_event = ClassificationCompletedInfrastructureEvent(
                        request_data=request_data,
                        response_data=response_data,
                        completed_at=datetime.now(),
                        latency_ms=0.0,  # No LLM call
                        success=True,
                        source="universal_catalyst"
                    )

                    await self.event_bus.publish(
                        InfrastructureEventType.CLASSIFICATION_COMPLETED,
                        completed_event.model_dump()
                    )

                    logger.info(
                        f"✅ UNIVERSAL CATALYST: Published IMMINENT for {catalyst_type}",
                        article_id=request_data.article_id,
                        ticker=primary_ticker,
                        sector=sector,
                        industry=industry
                    )
                    return  # Don't proceed to industry LLM

            # Step 5: Proceed to multi-sector LLM classification
            # ========================================================================
            # MULTI-SECTOR TRADING STRATEGY
            # ========================================================================
            # Pure language-based classification using industry-specific prompts.
            # Supported: Healthcare, Technology, Industrials, Consumer Cyclical,
            #            Financial Services, Communication Services, Consumer Defensive,
            #            Basic Materials, Energy
            # Flow: headline → sector check → industry check → Groq LLM → TRADE/SKIP
            # If TRADE → publish "imminent" classification → trigger AutoTradeService
            # If SKIP/NOT_SUPPORTED → no trade, but data collection continues
            # ========================================================================

            await self._classify_via_sector(infra_event, primary_ticker, triage_headline_type)
            
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
        primary_ticker: str,
        triage_headline_type: Optional[str] = None,
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
        # Sanitize headline: decode HTML entities (&#39; → ', &amp; → &, etc.)
        # This prevents Groq API "invalid syntax (400)" errors from malformed input
        headline = html.unescape(request_data.article_title or "")

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
            # SECTOR LLM BYPASS: Headline types that are universally tradeable
            # regardless of sector/industry. The triage already validated the type —
            # the sector LLM adds false-SKIP risk without adding signal.
            #
            # HC types (gov/military/major contracts): sector prompt can't evaluate
            # cross-industry deals (e.g. biotech-classified company winning defense
            # contract). Activity confirmation + postfilters still protect against duds.
            # Data: CETX +104%, ELAB +24% missed by sector LLM; WRAP/GNSS caught by
            # activity filter (no surge = no trade). Zero losers from bypass.
            #
            # Buybacks: structurally bullish in every sector (company is the buyer).
            # AI breakthrough: the sector prompt can't judge cross-industry AI/robotics
            # (ONCO: biotech-classified company launching AI humanoid robot → +80%).
            HC_BYPASS_TYPES = frozenset({
                "government_contract", "military_contract", "defense_order", "major_contract",
                "stock_buyback", "ai_breakthrough", "ai_rebranding",
            })

            if triage_headline_type in HC_BYPASS_TYPES:
                logger.info(
                    f"🎖️ HC BYPASS: Skipping sector LLM — {triage_headline_type} trades on headline signal + activity confirmation",
                    article_id=request_data.article_id,
                    ticker=primary_ticker,
                    headline_type=triage_headline_type,
                    headline=headline[:60],
                )

                # Buybacks get LARGE; HC contract types get MODERATE (confluence multiplier scales up)
                bypass_size = "LARGE" if triage_headline_type == "stock_buyback" else "MODERATE"

                response_data = InfrastructureClassificationResponseData(
                    classification="imminent",
                    confidence="HIGH",
                    reasoning=f"{triage_headline_type} - sector LLM bypassed (HC headline type)",
                    position_size=bypass_size,
                    headline_type=triage_headline_type,
                )

                completed_event = ClassificationCompletedInfrastructureEvent(
                    request_data=request_data,
                    response_data=response_data,
                    completed_at=datetime.now(),
                    latency_ms=0.0,
                    success=True,
                    source="hc_bypass"
                )

                await self.event_bus.publish(
                    InfrastructureEventType.CLASSIFICATION_COMPLETED,
                    completed_event.model_dump()
                )
                return

            # Classify via multi-sector classifier
            classification, sector, industry, latency_ms, position_size = await self.sector_classifier.classify(
                headline=headline,
                ticker=primary_ticker
            )

            logger.info(
                f"Sector classification: {classification}" + (f" {position_size}" if position_size else ""),
                article_id=request_data.article_id,
                ticker=primary_ticker,
                sector=sector,
                industry=industry,
                position_size=position_size,
                latency_ms=round(latency_ms, 1)
            )

            # Handle classification result
            if classification == "TRADE":
                # Get headline_type: reuse prefilter triage if available, else classify now
                headline_type = triage_headline_type or self._triage_cache.pop(request_data.article_id, None)
                if not headline_type:
                    # Early triage returned None (headline didn't match known types).
                    # Retry as fallback — classifier may have updated since early call.
                    try:
                        from ...shared.statistics.headline_classifier import get_headline_classifier
                        headline_classifier = get_headline_classifier()
                        headline_type = await headline_classifier.triage(
                            headline=headline,
                            timeout=3.0,
                        )
                    except Exception as e:
                        logger.debug(f"Headline type classification failed (non-blocking): {e}")

                if headline_type:
                    logger.info(
                        f"Headline type: {headline_type}",
                        article_id=request_data.article_id,
                        ticker=primary_ticker,
                        headline_type=headline_type,
                        source="triage_cache" if request_data.article_id not in self._triage_cache else "fresh_call",
                    )

                # TRADE signal → publish "imminent" to trigger AutoTradeService
                response_data = InfrastructureClassificationResponseData(
                    classification="imminent",
                    confidence="HIGH",
                    reasoning=f"{sector}/{industry} - LLM classified as tradeable",
                    position_size=position_size,
                    headline_type=headline_type,
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
                    f"Published IMMINENT classification for {sector} TRADE {position_size or 'MODERATE'} signal",
                    article_id=request_data.article_id,
                    ticker=primary_ticker,
                    sector=sector,
                    industry=industry,
                    position_size=position_size,
                    latency_ms=round(latency_ms, 1)
                )

            elif classification == "NOT_SUPPORTED_SECTOR":
                # Sector not supported - skip trading but continue data collection
                await self._publish_skipped_event(infra_event, f"unsupported_sector:{sector or 'unknown'}", headline_type=triage_headline_type)

            elif classification == "UNSUPPORTED_INDUSTRY":
                # Supported sector but unsupported industry - skip trading
                await self._publish_skipped_event(infra_event, f"unsupported_industry:{sector}/{industry}", headline_type=triage_headline_type)

            else:
                # SKIP signal - LLM determined not tradeable
                await self._publish_skipped_event(infra_event, f"llm_skip:{sector}/{industry}", headline_type=triage_headline_type)

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

    def _extract_dollar_amount_millions(self, headline: str) -> Optional[float]:
        """
        Extract the largest dollar amount from a headline and convert to millions.

        Handles formats:
        - "$40 Million" / "$40M" / "$40 million"
        - "US$40 Million" / "US$40M"
        - "$4.75 Million"
        - "$500,000" (converts to 0.5M)
        - "$1 Billion" / "$1B" (converts to 1000M)

        Returns:
            Dollar amount in millions, or None if no amount found
        """
        if not headline:
            return None

        headline_lower = headline.lower()

        # Pattern for millions: $X Million, $XM, US$X Million
        millions_patterns = [
            r'(?:us)?\$(\d+(?:\.\d+)?)\s*(?:million|m\b)',  # $40 Million, $40M, US$40M
            r'(?:us)?\$(\d+(?:\.\d+)?)\s*(?:billion|b\b)',  # $1 Billion, $1B (multiply by 1000)
        ]

        amounts = []

        # Check millions patterns
        for pattern in millions_patterns[:1]:  # First pattern is millions
            matches = re.findall(pattern, headline_lower)
            for match in matches:
                try:
                    amounts.append(float(match))
                except ValueError:
                    pass

        # Check billions patterns (convert to millions)
        for pattern in millions_patterns[1:]:  # Second pattern is billions
            matches = re.findall(pattern, headline_lower)
            for match in matches:
                try:
                    amounts.append(float(match) * 1000)  # Convert billions to millions
                except ValueError:
                    pass

        # Also check for raw dollar amounts without Million/M suffix
        # e.g., "$500,000" → 0.5M
        raw_pattern = r'(?:us)?\$(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)'
        raw_matches = re.findall(raw_pattern, headline_lower)
        for match in raw_matches:
            try:
                # Remove commas and convert
                value = float(match.replace(',', ''))
                # Only include if it looks like a significant amount (> $100k)
                if value >= 100000:
                    amounts.append(value / 1_000_000)  # Convert to millions
            except ValueError:
                pass

        if amounts:
            return max(amounts)  # Return the largest amount found
        return None

    async def _publish_skipped_event(
        self,
        infra_event: ClassificationRequestedInfrastructureEvent,
        reason: str,
        headline_type: Optional[str] = None,
    ) -> None:
        """
        Publish ClassificationSkipped infrastructure event.

        Args:
            infra_event: Original classification request event
            reason: Skip reason ('no_tickers', 'invalid_exchange', 'broker_not_tradeable', 'nbbo_unavailable', or 'no_volume_since_publication')
            headline_type: Headline type from universal triage (only for post-prefilter skips)
        """
        skipped_event = ClassificationSkippedInfrastructureEvent(
            request_data=infra_event.request_data,
            skipped_at=datetime.now(),
            reason=reason,
            source="classification_infrastructure",
            headline_type=headline_type,
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

