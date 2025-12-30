#!/usr/bin/env python3
import os
import sys
import asyncio
from pathlib import Path
from datetime import datetime
import pytz
from dotenv import load_dotenv

# Add src to path
PROJECT_ROOT = Path(__file__).parent.parent
SRC_PATH = str(PROJECT_ROOT / "src")
if SRC_PATH not in sys.path:
    sys.path.insert(0, SRC_PATH)

load_dotenv()

from alpaca.data.historical import StockHistoricalDataClient
from newsflash.shared.statistics.volume_analyzer import analyze_volume_around_event, format_volume_stats_for_notification

async def main():
    print("\n" + "=" * 80)
    print("VVPR FOUR-PILLAR SURGE ANALYSIS")
    print("=" * 80)
    
    api_key = os.getenv("ALPACA_KEY")
    api_secret = os.getenv("ALPACA_SECRET")
    
    if not api_key or not api_secret:
        print("❌ Error: ALPACA_KEY and ALPACA_SECRET must be set in environment")
        return

    client = StockHistoricalDataClient(api_key, api_secret)
    
    symbol = "VVPR"
    # From tmp/articles.json:
    # "published_at": "2025-12-30T13:23:00+00:00"
    # "websocket_received_at": "2025-12-30T13:23:01.786149"
    
    event_time = datetime(2025, 12, 30, 13, 23, 0, tzinfo=pytz.UTC)
    received_at = datetime(2025, 12, 30, 13, 23, 1, 786149, tzinfo=pytz.UTC)
    
    print(f"\n📍 Analyzing {symbol}")
    print(f"   Event Time (Pub): {event_time}")
    print(f"   Received At (Recv): {received_at}")
    
    try:
        analysis = await analyze_volume_around_event(
            client=client,
            symbol=symbol,
            event_time=event_time,
            received_at=received_at
        )
        
        print("\n📊 ANALYSIS RESULTS (FULL JSON):")
        import json
        print(json.dumps(analysis.to_dict(), indent=2))
        
        print(f"\n   Spread Compression: {analysis.spread_compression_pct if analysis.spread_compression_pct else 0:.1f}%")
        
        notification_text = format_volume_stats_for_notification(analysis)
        print("\n📱 TELEGRAM NOTIFICATION PREVIEW:")
        print("-" * 40)
        print(notification_text)
        print("-" * 40)
        
        if analysis.move_type == "SURGE":
            print("\n❌ FAILED: This should NOT have been a SURGE (according to USER).")
        else:
            print(f"\n✅ SUCCESS: Correctly classified as {analysis.move_type}. The 4-Pillar Gate prevented a false SURGE.")

    except Exception as e:
        print(f"\n❌ Error during analysis: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
