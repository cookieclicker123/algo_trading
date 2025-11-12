import pytest

from newsflash.services.article_processor import ArticleProcessor


class DummyTelegram:
    enabled_1 = False
    enabled_2 = False
    test_mode = True

    async def send_notification(self, *_, **__):
        return None


class DummyClassifier:
    enabled = False

    async def classify_article(self, *_):
        raise NotImplementedError


def build_processor():
    return ArticleProcessor(
        telegram_notifier=DummyTelegram(),
        classifier=DummyClassifier(),
        storage=None,
        auto_trade_service=None,
    )


@pytest.mark.asyncio
async def test_rejects_holding_company_by_name(monkeypatch):
    processor = build_processor()

    async def fake_fundamentals(_):
        return {
            "primary_exchange": "NASDAQ",
            "market_cap": processor._min_market_cap + 1,
            "average_volume_30d": processor._min_average_volume + 1000,
            "industry": "Information Technology Services",
            "company_name": "SS&C Technologies Holdings, Inc.",
        }

    monkeypatch.setattr(processor._yf, "get_fundamental_data", fake_fundamentals)

    assert not await processor._is_tradeable_ticker("SSNC")


@pytest.mark.asyncio
async def test_allows_non_holding_company(monkeypatch):
    processor = build_processor()

    async def fake_fundamentals(_):
        return {
            "primary_exchange": "NASDAQ",
            "market_cap": processor._min_market_cap + 1,
            "average_volume_30d": processor._min_average_volume + 1000,
            "industry": "Semiconductors",
            "company_name": "Advanced Micro Devices, Inc.",
        }

    monkeypatch.setattr(processor._yf, "get_fundamental_data", fake_fundamentals)

    assert await processor._is_tradeable_ticker("AMD")


@pytest.mark.asyncio
async def test_whitelisted_holding_company_allowed(monkeypatch):
    processor = build_processor()

    async def fake_fundamentals(_):
        return {
            "primary_exchange": "NASDAQ",
            "market_cap": processor._min_market_cap + 1,
            "average_volume_30d": processor._min_average_volume + 1000,
            "industry": "Travel Services",
            "company_name": "Booking Holdings Inc.",
        }

    monkeypatch.setattr(processor._yf, "get_fundamental_data", fake_fundamentals)

    assert await processor._is_tradeable_ticker("BKNG")

