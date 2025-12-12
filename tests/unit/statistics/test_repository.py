"""
Unit tests for StatisticsRepository.

Tests all repository methods with real file writes.
Files are kept for 5 seconds after tests complete for inspection.
"""
import asyncio
import json
import shutil
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Any

import pytest
import aiofiles
import pytz

# Ensure src is on path
import sys
PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
SRC_PATH = str(PROJECT_ROOT / "src")
if SRC_PATH not in sys.path:
    sys.path.insert(0, SRC_PATH)

from newsflash.infra.statistics.repository import StatisticsRepository
from newsflash.shared.statistics.models import (
    RecallRecord,
    SignalRecord,
)
from newsflash.domain.brokerage.models import MarketSession


# Test data directory - will be cleaned up after 5 seconds
TEST_TMP_DIR = None


@pytest.fixture
def test_tmp_dir():
    """Create temporary directory for tests, cleanup after 5 seconds."""
    global TEST_TMP_DIR
    tmpdir = tempfile.mkdtemp(prefix="statistics_test_")
    TEST_TMP_DIR = Path(tmpdir)
    yield TEST_TMP_DIR
    
    # Wait 1 second before cleanup (for file inspection)
    print(f"\n📁 Test files available at: {tmpdir}")
    print("⏳ Waiting 1 second before cleanup...")
    time.sleep(1)
    
    # Cleanup
    if TEST_TMP_DIR.exists():
        shutil.rmtree(TEST_TMP_DIR)
        print(f"✅ Cleaned up: {tmpdir}")


@pytest.fixture
def repository(test_tmp_dir):
    """Create repository instance for tests."""
    return StatisticsRepository(tmp_dir=test_tmp_dir)


@pytest.fixture
def sample_date():
    """Sample date for testing (2025-12-10 10:00 AM ET)."""
    et_tz = pytz.timezone("US/Eastern")
    return et_tz.localize(datetime(2025, 12, 10, 10, 0, 0))


@pytest.fixture
def sample_recall_record():
    """Sample recall record for testing."""
    now = datetime.now()
    return RecallRecord(
        article_id="test-article-123",
        title="Test Article Title",
        tickers=["AAPL", "TSLA"],
        session=MarketSession.PREMARKET,
        published_at=now,
        received_at=now,
        initial_nbbo={
            "bid": 175.50,
            "ask": 175.55,
            "spread": 0.05,
            "mid": 175.525
        },
        filter_reasons=["not_classified_imminent"]
    )


@pytest.fixture
def sample_signal_record():
    """Sample signal record for testing."""
    now = datetime.now()
    return SignalRecord(
        trade_id="order_test_123",
        article_id="test-article-456",
        ticker="AAPL",
        session=MarketSession.MARKET,
        executed_at=now,
        entry_price=175.50,
        entry_shares=10,
        entry_amount_usd=1755.00,
        entry_nbbo={
            "bid": 175.48,
            "ask": 175.52,
            "spread": 0.04,
            "mid": 175.50
        },
        ticker_metadata={
            "industry": "Consumer Electronics",
            "sector": "Technology",
            "market_cap_millions": 2800000.0,
            "price": 175.50,
            "exchange": "NASDAQ"
        }
    )


class TestPathCalculation:
    """Test file path calculation methods."""
    
    def test_get_session_file_path_premarket(self, repository, sample_date):
        """Test path calculation for premarket session."""
        path = repository._get_session_file_path("recall", "premarket", sample_date)
        
        # Verify path structure
        assert "recall" in str(path)
        assert "premarket" in str(path)
        assert "2025" in str(path)
        assert "12" in str(path)
        assert path.suffix == ".json"
        assert path.name == "premarket.json"
    
    def test_get_session_file_path_market_hours(self, repository, sample_date):
        """Test path calculation for market hours session."""
        path = repository._get_session_file_path("signal", "market_hours", sample_date)
        
        assert "signal" in str(path)
        assert "market_hours" in str(path)
        assert path.name == "market_hours.json"
    
    def test_get_session_file_path_postmarket(self, repository, sample_date):
        """Test path calculation for postmarket session."""
        path = repository._get_session_file_path("recall", "postmarket", sample_date)
        
        assert "postmarket" in str(path)
        assert path.name == "postmarket.json"
    
    def test_get_session_file_path_week_calculation(self, repository):
        """Test that week number is calculated correctly."""
        # Test with a known date (2025-12-10 is week 50)
        et_tz = pytz.timezone("US/Eastern")
        date = et_tz.localize(datetime(2025, 12, 10, 10, 0, 0))
        path = repository._get_session_file_path("recall", "premarket", date)
        
        assert "week_50" in str(path)
    
    def test_get_session_file_path_directory_structure(self, repository, sample_date):
        """Test that directory structure is created correctly."""
        path = repository._get_session_file_path("recall", "premarket", sample_date)
        
        # Verify all expected directory levels
        parts = path.parts
        assert "statistics" in parts
        assert "recall" in parts
        assert "2025" in parts
        assert "12" in parts or "week_50" in parts


class TestSessionMapping:
    """Test session string to enum mapping."""
    
    def test_map_session_premarket(self, repository):
        """Test mapping premarket string to enum."""
        result = repository._map_session_to_enum("premarket")
        assert result == MarketSession.PREMARKET
    
    def test_map_session_market_hours(self, repository):
        """Test mapping market_hours string to enum."""
        result = repository._map_session_to_enum("market_hours")
        assert result == MarketSession.MARKET
    
    def test_map_session_postmarket(self, repository):
        """Test mapping postmarket string to enum."""
        result = repository._map_session_to_enum("postmarket")
        assert result == MarketSession.POSTMARKET
    
    def test_map_session_closed(self, repository):
        """Test mapping closed string to enum."""
        result = repository._map_session_to_enum("closed")
        assert result == MarketSession.CLOSED
    
    def test_map_session_unknown_defaults_to_market(self, repository):
        """Test that unknown session defaults to MARKET."""
        result = repository._map_session_to_enum("unknown_session")
        assert result == MarketSession.MARKET


class TestSessionTimes:
    """Test session time calculation."""
    
    def test_calculate_session_times_premarket(self, repository, sample_date):
        """Test premarket session times."""
        start, end = repository._calculate_session_times("premarket", sample_date)
        
        assert start.hour == 4
        assert start.minute == 0
        assert end.hour == 9
        assert end.minute == 30
    
    def test_calculate_session_times_market_hours(self, repository, sample_date):
        """Test market hours session times."""
        start, end = repository._calculate_session_times("market_hours", sample_date)
        
        assert start.hour == 9
        assert start.minute == 30
        assert end.hour == 16
        assert end.minute == 0
    
    def test_calculate_session_times_postmarket(self, repository, sample_date):
        """Test postmarket session times."""
        start, end = repository._calculate_session_times("postmarket", sample_date)
        
        assert start.hour == 16
        assert start.minute == 0
        assert end.hour == 20
        assert end.minute == 0


class TestRecallOperations:
    """Test recall record operations."""
    
    @pytest.mark.asyncio
    async def test_append_recall_record_creates_file(
        self, repository, sample_recall_record, sample_date
    ):
        """Test that appending a recall record creates the file."""
        await repository.append_recall_record(
            record=sample_recall_record,
            session="premarket",
            date=sample_date
        )
        
        file_path = repository._get_session_file_path("recall", "premarket", sample_date)
        assert file_path.exists(), "File should be created"
    
    @pytest.mark.asyncio
    async def test_append_recall_record_stores_data(
        self, repository, sample_recall_record, sample_date
    ):
        """Test that recall record data is stored correctly."""
        await repository.append_recall_record(
            record=sample_recall_record,
            session="premarket",
            date=sample_date
        )
        
        file_path = repository._get_session_file_path("recall", "premarket", sample_date)
        async with aiofiles.open(file_path, 'r') as f:
            content = await f.read()
            data = json.loads(content)
            
            assert data["session"] == "premarket"
            assert len(data["records"]) == 1
            assert data["records"][0]["article_id"] == "test-article-123"
            assert data["records"][0]["title"] == "Test Article Title"
            assert data["records"][0]["tickers"] == ["AAPL", "TSLA"]
    
    @pytest.mark.asyncio
    async def test_append_recall_record_updates_summary(
        self, repository, sample_recall_record, sample_date
    ):
        """Test that summary is updated when appending records."""
        await repository.append_recall_record(
            record=sample_recall_record,
            session="premarket",
            date=sample_date
        )
        
        file_path = repository._get_session_file_path("recall", "premarket", sample_date)
        async with aiofiles.open(file_path, 'r') as f:
            content = await f.read()
            data = json.loads(content)
            
            assert data["summary"]["total_articles_tracked"] == 1
            assert "not_classified_imminent" in data["summary"]["filter_breakdown"]
            assert data["summary"]["filter_breakdown"]["not_classified_imminent"] == 1
            assert "AAPL" in data["summary"]["ticker_breakdown"]
            assert "TSLA" in data["summary"]["ticker_breakdown"]
    
    @pytest.mark.asyncio
    async def test_append_multiple_recall_records(
        self, repository, sample_recall_record, sample_date
    ):
        """Test appending multiple recall records."""
        # Append first record
        await repository.append_recall_record(
            record=sample_recall_record,
            session="premarket",
            date=sample_date
        )
        
        # Create and append second record
        record2 = RecallRecord(
            article_id="test-article-456",
            title="Second Article",
            tickers=["NVDA"],
            session=MarketSession.PREMARKET,
            published_at=datetime.now(),
            received_at=datetime.now()
        )
        await repository.append_recall_record(
            record=record2,
            session="premarket",
            date=sample_date
        )
        
        file_path = repository._get_session_file_path("recall", "premarket", sample_date)
        async with aiofiles.open(file_path, 'r') as f:
            content = await f.read()
            data = json.loads(content)
            
            assert len(data["records"]) == 2
            assert data["summary"]["total_articles_tracked"] == 2
    
    @pytest.mark.asyncio
    async def test_update_recall_record(
        self, repository, sample_recall_record, sample_date
    ):
        """Test updating an existing recall record."""
        # Append initial record
        await repository.append_recall_record(
            record=sample_recall_record,
            session="premarket",
            date=sample_date
        )
        
        # Update with price check
        await repository.update_recall_record(
            article_id="test-article-123",
            updates={
                "price_check_5min": {
                    "final_mid": 186.20,
                    "percent_change": 6.08,
                    "moved_1_percent": True
                },
                "price_checked_at": datetime.now()
            },
            session="premarket",
            date=sample_date
        )
        
        file_path = repository._get_session_file_path("recall", "premarket", sample_date)
        async with aiofiles.open(file_path, 'r') as f:
            content = await f.read()
            data = json.loads(content)
            
            record = next(r for r in data["records"] if r["article_id"] == "test-article-123")
            assert record["price_check_5min"]["moved_1_percent"] is True
            assert record["price_check_5min"]["percent_change"] == 6.08
            assert data["summary"]["articles_with_1_percent_move"] == 1
            assert data["summary"]["missed_opportunities"] == 1  # Has filter_reasons
    
    @pytest.mark.asyncio
    async def test_update_recall_record_missed_opportunity(
        self, repository, sample_date
    ):
        """Test that missed opportunities are counted correctly."""
        # Create record without filter reasons (would be traded)
        record = RecallRecord(
            article_id="test-traded",
            title="Traded Article",
            tickers=["AAPL"],
            session=MarketSession.PREMARKET,
            published_at=datetime.now(),
            received_at=datetime.now()
        )
        await repository.append_recall_record(
            record=record,
            session="premarket",
            date=sample_date
        )
        
        # Create record with filter reasons (missed)
        missed_record = RecallRecord(
            article_id="test-missed",
            title="Missed Article",
            tickers=["TSLA"],
            session=MarketSession.PREMARKET,
            published_at=datetime.now(),
            received_at=datetime.now(),
            filter_reasons=["not_classified_imminent"]
        )
        await repository.append_recall_record(
            record=missed_record,
            session="premarket",
            date=sample_date
        )
        
        # Update both with 1%+ moves
        for article_id in ["test-traded", "test-missed"]:
            await repository.update_recall_record(
                article_id=article_id,
                updates={
                    "price_check_5min": {
                        "final_mid": 200.0,
                        "percent_change": 2.0,
                        "moved_1_percent": True
                    }
                },
                session="premarket",
                date=sample_date
            )
        
        file_path = repository._get_session_file_path("recall", "premarket", sample_date)
        async with aiofiles.open(file_path, 'r') as f:
            content = await f.read()
            data = json.loads(content)
            
            # Only the one with filter_reasons should count as missed
            assert data["summary"]["articles_with_1_percent_move"] == 2
            assert data["summary"]["missed_opportunities"] == 1


class TestSignalOperations:
    """Test signal record operations."""
    
    @pytest.mark.asyncio
    async def test_append_signal_record_creates_file(
        self, repository, sample_signal_record, sample_date
    ):
        """Test that appending a signal record creates the file."""
        await repository.append_signal_record(
            record=sample_signal_record,
            session="market_hours",
            date=sample_date
        )
        
        file_path = repository._get_session_file_path("signal", "market_hours", sample_date)
        assert file_path.exists(), "File should be created"
    
    @pytest.mark.asyncio
    async def test_append_signal_record_stores_data(
        self, repository, sample_signal_record, sample_date
    ):
        """Test that signal record data is stored correctly."""
        await repository.append_signal_record(
            record=sample_signal_record,
            session="market_hours",
            date=sample_date
        )
        
        file_path = repository._get_session_file_path("signal", "market_hours", sample_date)
        async with aiofiles.open(file_path, 'r') as f:
            content = await f.read()
            data = json.loads(content)
            
            assert data["session"] == "market"
            assert len(data["records"]) == 1
            assert data["records"][0]["trade_id"] == "order_test_123"
            assert data["records"][0]["ticker"] == "AAPL"
            assert data["records"][0]["entry_price"] == 175.50
    
    @pytest.mark.asyncio
    async def test_append_signal_record_updates_summary(
        self, repository, sample_signal_record, sample_date
    ):
        """Test that summary is updated when appending signal records."""
        await repository.append_signal_record(
            record=sample_signal_record,
            session="market_hours",
            date=sample_date
        )
        
        file_path = repository._get_session_file_path("signal", "market_hours", sample_date)
        async with aiofiles.open(file_path, 'r') as f:
            content = await f.read()
            data = json.loads(content)
            
            assert data["summary"]["total_trades"] == 1
            assert data["summary"]["average_spread_at_entry"] == 0.04
            assert "AAPL" in data["summary"]["ticker_breakdown"]
            assert "Consumer Electronics" in data["summary"]["industry_breakdown"]
            assert "Technology" in data["summary"]["sector_breakdown"]
    
    @pytest.mark.asyncio
    async def test_append_signal_record_with_profit(
        self, repository, sample_date
    ):
        """Test signal record with profit updates summary correctly."""
        profitable_record = SignalRecord(
            trade_id="order_profit",
            ticker="AAPL",
            session=MarketSession.MARKET,
            executed_at=datetime.now(),
            entry_price=175.50,
            entry_shares=10,
            entry_amount_usd=1755.00,
            profit_loss_usd=27.00,
            profit_loss_percent=1.54
        )
        
        await repository.append_signal_record(
            record=profitable_record,
            session="market_hours",
            date=sample_date
        )
        
        file_path = repository._get_session_file_path("signal", "market_hours", sample_date)
        async with aiofiles.open(file_path, 'r') as f:
            content = await f.read()
            data = json.loads(content)
            
            assert data["summary"]["profitable_trades"] == 1
            assert data["summary"]["losing_trades"] == 0
            assert data["summary"]["total_profit_loss_usd"] == 27.00
    
    @pytest.mark.asyncio
    async def test_append_signal_record_with_loss(
        self, repository, sample_date
    ):
        """Test signal record with loss updates summary correctly."""
        losing_record = SignalRecord(
            trade_id="order_loss",
            ticker="TSLA",
            session=MarketSession.MARKET,
            executed_at=datetime.now(),
            entry_price=200.00,
            entry_shares=5,
            entry_amount_usd=1000.00,
            profit_loss_usd=-15.50,
            profit_loss_percent=-0.78
        )
        
        await repository.append_signal_record(
            record=losing_record,
            session="market_hours",
            date=sample_date
        )
        
        file_path = repository._get_session_file_path("signal", "market_hours", sample_date)
        async with aiofiles.open(file_path, 'r') as f:
            content = await f.read()
            data = json.loads(content)
            
            assert data["summary"]["profitable_trades"] == 0
            assert data["summary"]["losing_trades"] == 1
            assert data["summary"]["total_profit_loss_usd"] == -15.50
    
    @pytest.mark.asyncio
    async def test_append_multiple_signal_records_average_spread(
        self, repository, sample_date
    ):
        """Test that average spread is calculated correctly across multiple records."""
        # First record with spread 0.04
        record1 = SignalRecord(
            trade_id="order_1",
            ticker="AAPL",
            session=MarketSession.MARKET,
            executed_at=datetime.now(),
            entry_price=175.50,
            entry_shares=10,
            entry_amount_usd=1755.00,
            entry_nbbo={"spread": 0.04}
        )
        
        # Second record with spread 0.06
        record2 = SignalRecord(
            trade_id="order_2",
            ticker="TSLA",
            session=MarketSession.MARKET,
            executed_at=datetime.now(),
            entry_price=200.00,
            entry_shares=5,
            entry_amount_usd=1000.00,
            entry_nbbo={"spread": 0.06}
        )
        
        await repository.append_signal_record(record1, "market_hours", sample_date)
        await repository.append_signal_record(record2, "market_hours", sample_date)
        
        file_path = repository._get_session_file_path("signal", "market_hours", sample_date)
        async with aiofiles.open(file_path, 'r') as f:
            content = await f.read()
            data = json.loads(content)
            
            # Average should be (0.04 + 0.06) / 2 = 0.05
            assert data["summary"]["average_spread_at_entry"] == 0.05


class TestConcurrentOperations:
    """Test concurrent file operations."""
    
    @pytest.mark.asyncio
    async def test_concurrent_append_operations(
        self, repository, sample_date
    ):
        """Test that concurrent append operations work correctly."""
        # Create multiple records
        records = [
            RecallRecord(
                article_id=f"test-{i}",
                title=f"Article {i}",
                tickers=["AAPL"],
                session=MarketSession.PREMARKET,
                published_at=datetime.now(),
                received_at=datetime.now()
            )
            for i in range(10)
        ]
        
        # Append all concurrently
        tasks = [
            repository.append_recall_record(record, "premarket", sample_date)
            for record in records
        ]
        await asyncio.gather(*tasks)
        
        # Verify all records were appended
        file_path = repository._get_session_file_path("recall", "premarket", sample_date)
        async with aiofiles.open(file_path, 'r') as f:
            content = await f.read()
            data = json.loads(content)
            
            assert len(data["records"]) == 10
            assert data["summary"]["total_articles_tracked"] == 10


class TestFileLoading:
    """Test loading existing files."""
    
    @pytest.mark.asyncio
    async def test_load_existing_recall_file(
        self, repository, sample_recall_record, sample_date
    ):
        """Test loading an existing recall file."""
        # Create file manually
        await repository.append_recall_record(
            record=sample_recall_record,
            session="premarket",
            date=sample_date
        )
        
        # Load it again (should work)
        file_path = repository._get_session_file_path("recall", "premarket", sample_date)
        session_file = await repository._load_recall_file(file_path, "premarket", sample_date)
        
        assert len(session_file.records) == 1
        assert session_file.records[0].article_id == "test-article-123"
    
    @pytest.mark.asyncio
    async def test_load_nonexistent_file_creates_new(
        self, repository, sample_date
    ):
        """Test that loading a nonexistent file creates a new one."""
        file_path = repository._get_session_file_path("recall", "premarket", sample_date)
        
        # File shouldn't exist yet
        assert not file_path.exists()
        
        # Load should create new file
        session_file = await repository._load_recall_file(file_path, "premarket", sample_date)
        
        assert session_file.session == MarketSession.PREMARKET
        assert len(session_file.records) == 0
        assert session_file.summary["total_articles_tracked"] == 0
