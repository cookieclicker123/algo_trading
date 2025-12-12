"""
Integration tests for statistics repository.

Tests end-to-end file I/O operations with real file writes.
Files are kept for 5 seconds after tests complete for inspection.
"""
import asyncio
import json
import shutil
import tempfile
import time
from datetime import datetime
from pathlib import Path

import pytest
import aiofiles

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
    tmpdir = tempfile.mkdtemp(prefix="statistics_integration_test_")
    TEST_TMP_DIR = Path(tmpdir)
    yield TEST_TMP_DIR
    
    # Wait 1 second before cleanup (for file inspection)
    print(f"\n📁 Integration test files available at: {tmpdir}")
    print("⏳ Waiting 1 second before cleanup...")
    time.sleep(1)
    
    # Cleanup
    if TEST_TMP_DIR.exists():
        shutil.rmtree(TEST_TMP_DIR)
        print(f"✅ Cleaned up: {tmpdir}")


@pytest.mark.asyncio
async def test_repository_append_recall_record(test_tmp_dir):
    """Test appending a recall record to a file."""
    repo = StatisticsRepository(tmp_dir=test_tmp_dir)
    test_date = datetime.now()
    
    # Create a recall record
    record = RecallRecord(
        article_id="test-123",
        title="Test Article",
        tickers=["AAPL"],
        session=MarketSession.PREMARKET,
        published_at=test_date,
        received_at=test_date,
        initial_nbbo={"bid": 175.50, "ask": 175.55, "spread": 0.05, "mid": 175.525}
    )
    
    # Append record
    await repo.append_recall_record(
        record=record,
        session="premarket",
        date=test_date
    )
    
    # Verify file was created
    file_path = repo._get_session_file_path("recall", "premarket", test_date)
    assert file_path.exists(), "Recall file should exist"
    
    # Load and verify content
    async with aiofiles.open(file_path, 'r') as f:
        content = await f.read()
        data = json.loads(content)
        
        assert data["session"] == "premarket"
        assert len(data["records"]) == 1
        assert data["records"][0]["article_id"] == "test-123"
        assert data["summary"]["total_articles_tracked"] == 1


@pytest.mark.asyncio
async def test_repository_append_signal_record(test_tmp_dir):
    """Test appending a signal record to a file."""
    repo = StatisticsRepository(tmp_dir=test_tmp_dir)
    test_date = datetime.now()
    
    # Create a signal record
    record = SignalRecord(
        trade_id="order_abc123",
        article_id="test-456",
        ticker="AAPL",
        session=MarketSession.MARKET,
        executed_at=test_date,
        entry_price=175.50,
        entry_shares=10,
        entry_amount_usd=1755.00,
        entry_nbbo={"bid": 175.48, "ask": 175.52, "spread": 0.04, "mid": 175.50}
    )
    
    # Append record
    await repo.append_signal_record(
        record=record,
        session="market_hours",
        date=test_date
    )
    
    # Verify file was created
    file_path = repo._get_session_file_path("signal", "market_hours", test_date)
    assert file_path.exists(), "Signal file should exist"
    
    # Load and verify content
    async with aiofiles.open(file_path, 'r') as f:
        content = await f.read()
        data = json.loads(content)
        
        assert data["session"] == "market"
        assert len(data["records"]) == 1
        assert data["records"][0]["trade_id"] == "order_abc123"
        assert data["summary"]["total_trades"] == 1


@pytest.mark.asyncio
async def test_repository_update_recall_record(test_tmp_dir):
    """Test updating an existing recall record."""
    repo = StatisticsRepository(tmp_dir=test_tmp_dir)
    test_date = datetime.now()
    
    # Create and append initial record
    record = RecallRecord(
        article_id="test-789",
        title="Test Article",
        tickers=["TSLA"],
        session=MarketSession.POSTMARKET,
        published_at=test_date,
        received_at=test_date
    )
    
    await repo.append_recall_record(
        record=record,
        session="postmarket",
        date=test_date
    )
    
    # Update with price check result
    await repo.update_recall_record(
        article_id="test-789",
        updates={
            "price_check_5min": {
                "final_mid": 250.00,
                "percent_change": 2.5,
                "moved_1_percent": True
            },
            "price_checked_at": datetime.now()
        },
        session="postmarket",
        date=test_date
    )
    
    # Verify update
    file_path = repo._get_session_file_path("recall", "postmarket", test_date)
    async with aiofiles.open(file_path, 'r') as f:
        content = await f.read()
        data = json.loads(content)
        
        updated_record = next(r for r in data["records"] if r["article_id"] == "test-789")
        assert updated_record["price_check_5min"]["moved_1_percent"] is True
        assert updated_record["price_check_5min"]["percent_change"] == 2.5
        assert data["summary"]["articles_with_1_percent_move"] == 1


@pytest.mark.asyncio
async def test_end_to_end_recall_workflow(test_tmp_dir):
    """Test complete recall workflow: append, update, verify summary."""
    repo = StatisticsRepository(tmp_dir=test_tmp_dir)
    test_date = datetime.now()
    
    # Append multiple records
    for i in range(3):
        record = RecallRecord(
            article_id=f"test-{i}",
            title=f"Article {i}",
            tickers=["AAPL"],
            session=MarketSession.PREMARKET,
            published_at=test_date,
            received_at=test_date,
            filter_reasons=["not_classified_imminent"] if i > 0 else []
        )
        await repo.append_recall_record(record, "premarket", test_date)
    
    # Update two with 1%+ moves
    for i in [1, 2]:
        await repo.update_recall_record(
            article_id=f"test-{i}",
            updates={
                "price_check_5min": {
                    "final_mid": 200.0,
                    "percent_change": 2.0,
                    "moved_1_percent": True
                }
            },
            session="premarket",
            date=test_date
        )
    
    # Verify final state
    file_path = repo._get_session_file_path("recall", "premarket", test_date)
    async with aiofiles.open(file_path, 'r') as f:
        content = await f.read()
        data = json.loads(content)
        
        assert data["summary"]["total_articles_tracked"] == 3
        assert data["summary"]["articles_with_1_percent_move"] == 2
        assert data["summary"]["missed_opportunities"] == 2  # Both had filter_reasons
