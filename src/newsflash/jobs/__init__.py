"""
Background jobs for NewsFlash.

Jobs are scheduled tasks that run at specific times, separate from the event-driven system.

Daily jobs (run at 8pm ET / 1am UK after postmarket):
- DailyAnalyticsJob: Full stats with all features for ML/backtesting
- WinnersSummaryJob: Human-readable list of trades and missed winners
- TradeClassificationJob: Confusion matrix (TP/FP/FN/TN) for precision/recall

Weekly jobs (run Friday at 1am after postmarket):
- WeeklyAggregationJob: Aggregates week's data into training set for ML
"""
from .daily_analytics import DailyAnalyticsJob, run_daily_analytics
from .exit_strategy_stats import run_exit_strategy_stats
from .headline_exit_profiles import run_headline_exit_profiles, load_profiles, HeadlineExitProfile
from .winners_summary import WinnersSummaryJob, run_winners_summary
from .trade_classification import (
    TradeClassificationJob,
    WeeklyAggregationJob,
    run_daily_classification,
    run_weekly_aggregation,
)

__all__ = [
    "DailyAnalyticsJob",
    "run_daily_analytics",
    "run_exit_strategy_stats",
    "HeadlineExitProfile",
    "run_headline_exit_profiles",
    "load_profiles",
    "WinnersSummaryJob",
    "run_winners_summary",
    "TradeClassificationJob",
    "WeeklyAggregationJob",
    "run_daily_classification",
    "run_weekly_aggregation",
]
