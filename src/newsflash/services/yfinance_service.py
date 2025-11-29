"""
YFinance service for fetching fundamental and real-time market data.
Only called for IMMINENT news to respect rate limits.
"""
import yfinance as yf
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta

from ..utils.logging_config import get_logger

logger = get_logger(__name__)


class YFinanceService:
    """
    Service for fetching fundamental and market data using yfinance.
    Only called for IMMINENT classified news to minimize API usage.
    """
    
    def __init__(self):
        """Initialize the yfinance service."""
        self.cache = {}  # Simple cache to avoid repeated calls for same ticker
        logger.info("YFinance service initialized")
    
    def _clean_ticker(self, ticker: str) -> str:
        """
        Clean and validate ticker symbol for yfinance.
        
        Args:
            ticker: Raw ticker symbol from news
            
        Returns:
            Cleaned ticker symbol
        """
        # Remove common suffixes and clean up
        ticker = ticker.upper().strip()
        ticker = ticker.replace('.', '-')  # Convert dots to dashes for yfinance
        
        # Remove common suffixes that might cause issues
        suffixes_to_remove = ['_', ':', '(', ')', '[', ']']
        for suffix in suffixes_to_remove:
            ticker = ticker.replace(suffix, '')
        
        return ticker
    
    def _calculate_growth_rate(self, values: List[float]) -> Optional[float]:
        """
        Calculate growth rate between most recent and previous period.
        
        Args:
            values: List of values (most recent first)
            
        Returns:
            Growth rate as percentage, or None if insufficient data
        """
        if len(values) < 2:
            return None
        
        try:
            current = values[0]
            previous = values[1]
            
            if previous == 0:
                return None
            
            growth_rate = ((current - previous) / abs(previous)) * 100
            return round(growth_rate, 2)
        except (TypeError, ZeroDivisionError):
            return None
    
    async def get_fundamental_data(self, ticker: str) -> Dict[str, Any]:
        """
        Fetch comprehensive fundamental and market data for a ticker.
        
        Args:
            ticker: Stock ticker symbol
            
        Returns:
            Dictionary containing all fundamental and market data
        """
        try:
            # Clean the ticker
            clean_ticker = self._clean_ticker(ticker)
            
            # Check cache first
            if clean_ticker in self.cache:
                cache_time_str = self.cache[clean_ticker].get('_cache_time')
                if cache_time_str:
                    try:
                        cache_time = datetime.fromisoformat(cache_time_str)
                        if datetime.now() - cache_time < timedelta(minutes=5):
                            logger.info("Using cached data for ticker", ticker=clean_ticker)
                            return self.cache[clean_ticker]
                    except ValueError:
                        pass  # Invalid cache time, fetch fresh data
            
            logger.info("Fetching fundamental data", ticker=clean_ticker)
            
            # Initialize yfinance ticker
            yf_ticker = yf.Ticker(clean_ticker)
            
            # Get company info
            info = yf_ticker.info
            
            # Get quarterly financials (newer API)
            quarterly_financials = yf_ticker.quarterly_financials
            quarterly_income = yf_ticker.quarterly_income_stmt
            
            # Get recent price/volume data (last day with 1-minute intervals)
            recent_data = yf_ticker.history(period="1d", interval="1m")
            
            # Process earnings data
            earnings_data = self._process_earnings_data(quarterly_income)
            
            # Process revenue data
            revenue_data = self._process_revenue_data(quarterly_financials)
            
            # Process margin data
            margin_data = self._process_margin_data(quarterly_financials, info)
            
            # Process price/volume data
            price_volume_data = self._process_price_volume_data(recent_data)
            
            def _safe_int(value):
                try:
                    if value is None or value == "N/A":
                        return None
                    return int(float(value))
                except (TypeError, ValueError):
                    return None

            def _safe_str(value):
                if value is None:
                    return None
                try:
                    return str(value)
                except Exception:
                    return None

            market_cap_value = _safe_int(info.get("marketCap"))
            average_volume_30d = (
                _safe_int(info.get("averageVolume"))
                or _safe_int(info.get("averageVolume90Day"))
                or _safe_int(info.get("averageDailyVolume3Month"))
            )
            average_volume_10d = (
                _safe_int(info.get("averageDailyVolume10Day"))
                or _safe_int(info.get("averageVolume10days"))
            )
            regular_market_volume = _safe_int(info.get("regularMarketVolume"))
            regular_market_price = info.get("regularMarketPrice")

            # Combine all data
            fundamental_data = {
                'ticker': clean_ticker,
                'company_name': info.get('longName', clean_ticker),
                'market_cap': market_cap_value,
                'sector': info.get('sector', 'N/A'),
                'industry': info.get('industry', 'N/A'),
                'primary_exchange': info.get('exchange', 'N/A'),
                'market': info.get('market', 'N/A'),
                'quote_type': info.get('quoteType'),
                'sic_code': _safe_str(info.get('sicCode')),
                'industry_key': info.get('industryKey'),
                'long_business_summary': info.get('longBusinessSummary'),
                'average_volume_30d': average_volume_30d,
                'average_volume_10d': average_volume_10d,
                'regular_market_volume': regular_market_volume,
                'regular_market_price': regular_market_price,
                'earnings': earnings_data,
                'revenue': revenue_data,
                'margins': margin_data,
                'price_volume': price_volume_data,
                '_cache_time': datetime.now().isoformat()
            }
            
            # Cache the data
            self.cache[clean_ticker] = fundamental_data
            
            logger.info("Successfully fetched fundamental data", ticker=clean_ticker)
            return fundamental_data
            
        except Exception as e:
            logger.error("Failed to fetch fundamental data", ticker=ticker, error=str(e))
            return self._get_empty_fundamental_data(ticker)
    
    def _process_earnings_data(self, quarterly_income) -> Dict[str, Any]:
        """Process quarterly earnings data."""
        try:
            if quarterly_income is None or quarterly_income.empty:
                return {'current_earnings': 'N/A', 'earnings_growth': 'N/A'}
            
            # Look for Net Income in the rows (not columns)
            net_income_row = None
            for idx, row_name in enumerate(quarterly_income.index):
                if 'Net Income' in str(row_name) and 'Continuing' in str(row_name):
                    net_income_row = quarterly_income.iloc[idx]
                    break
            
            if net_income_row is None:
                return {'current_earnings': 'N/A', 'earnings_growth': 'N/A'}
            
            # Get the most recent quarter (first column)
            current_earnings = net_income_row.iloc[0]
            earnings_values = net_income_row.tolist()
            earnings_growth = self._calculate_growth_rate(earnings_values)
            
            return {
                'current_earnings': f"${current_earnings:,.0f}M" if current_earnings else 'N/A',
                'earnings_growth': f"{earnings_growth:+.1f}%" if earnings_growth else 'N/A'
            }
        except Exception as e:
            logger.error("Failed to process earnings data", error=str(e))
            return {'current_earnings': 'N/A', 'earnings_growth': 'N/A'}
    
    def _process_revenue_data(self, quarterly_financials) -> Dict[str, Any]:
        """Process quarterly revenue data."""
        try:
            if quarterly_financials is None or quarterly_financials.empty:
                return {'current_revenue': 'N/A', 'revenue_growth': 'N/A'}
            
            # Look for Total Revenue in the rows (not columns)
            revenue_row = None
            for idx, row_name in enumerate(quarterly_financials.index):
                if 'Total Revenue' in str(row_name):
                    revenue_row = quarterly_financials.iloc[idx]
                    break
            
            if revenue_row is None:
                return {'current_revenue': 'N/A', 'revenue_growth': 'N/A'}
            
            # Get the most recent quarter (first column)
            current_revenue = revenue_row.iloc[0]
            revenue_values = revenue_row.tolist()
            revenue_growth = self._calculate_growth_rate(revenue_values)
            
            return {
                'current_revenue': f"${current_revenue:,.0f}M" if current_revenue else 'N/A',
                'revenue_growth': f"{revenue_growth:+.1f}%" if revenue_growth else 'N/A'
            }
        except Exception as e:
            logger.error("Failed to process revenue data", error=str(e))
            return {'current_revenue': 'N/A', 'revenue_growth': 'N/A'}
    
    def _process_margin_data(self, quarterly_financials, info) -> Dict[str, Any]:
        """Process margin data."""
        try:
            # Get gross margin from company info (more reliable)
            gross_margin = info.get('grossMargins', None)
            
            # Get net margin from company info
            net_margin = info.get('profitMargins', None)
            
            return {
                'gross_margin': f"{gross_margin * 100:.1f}%" if gross_margin else 'N/A',
                'net_margin': f"{net_margin * 100:.1f}%" if net_margin else 'N/A'
            }
        except Exception as e:
            logger.error("Failed to process margin data", error=str(e))
            return {'gross_margin': 'N/A', 'net_margin': 'N/A'}
    
    def _process_price_volume_data(self, recent_data) -> Dict[str, Any]:
        """Process recent price and volume data."""
        try:
            if recent_data is None or recent_data.empty:
                return {
                    'current_price': 'N/A',
                    'price_change_10min': 'N/A',
                    'current_volume': 'N/A',
                    'volume_change_10min': 'N/A'
                }
            
            # Current data (most recent)
            current_price = recent_data['Close'].iloc[-1]
            current_volume = recent_data['Volume'].iloc[-1]
            
            # Data from 10 minutes ago (if available)
            if len(recent_data) >= 10:
                price_10min_ago = recent_data['Close'].iloc[-10]
                volume_10min_ago = recent_data['Volume'].iloc[-10]
                
                price_change_10min = ((current_price - price_10min_ago) / price_10min_ago) * 100
                volume_change_10min = ((current_volume - volume_10min_ago) / volume_10min_ago) * 100 if volume_10min_ago > 0 else 0
            else:
                price_change_10min = 0
                volume_change_10min = 0
            
            return {
                'current_price': f"${current_price:.2f}",
                'price_change_10min': f"{price_change_10min:+.2f}%",
                'current_volume': f"{current_volume:,.0f}",
                'volume_change_10min': f"{volume_change_10min:+.1f}%"
            }
        except Exception as e:
            logger.error("Failed to process price/volume data", error=str(e))
            return {
                'current_price': 'N/A',
                'price_change_10min': 'N/A',
                'current_volume': 'N/A',
                'volume_change_10min': 'N/A'
            }
    
    def _get_empty_fundamental_data(self, ticker: str) -> Dict[str, Any]:
        """Return empty fundamental data structure when fetching fails."""
        return {
            'ticker': self._clean_ticker(ticker),
            'company_name': ticker,
            'market_cap': None,
            'sector': 'N/A',
            'industry': 'N/A',
            'primary_exchange': 'N/A',
            'market': 'N/A',
            'average_volume_30d': None,
            'average_volume_10d': None,
            'regular_market_volume': None,
            'regular_market_price': None,
            'earnings': {'current_earnings': 'N/A', 'earnings_growth': 'N/A'},
            'revenue': {'current_revenue': 'N/A', 'revenue_growth': 'N/A'},
            'margins': {'gross_margin': 'N/A', 'net_margin': 'N/A'},
            'price_volume': {
                'current_price': 'N/A',
                'price_change_10min': 'N/A',
                'current_volume': 'N/A',
                'volume_change_10min': 'N/A'
            }
        }


