#!/usr/bin/env python3
"""
Diagnostic script to check why auto-trading isn't working.

Run this to identify the issue:
    python scripts/diagnose_auto_trade.py
"""
import os
import sys
import json
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

# Add src to path
PROJECT_ROOT = Path(__file__).parent.parent
SRC_PATH = str(PROJECT_ROOT / "src")
if SRC_PATH not in sys.path:
    sys.path.insert(0, SRC_PATH)

load_dotenv()

def check_env_vars():
    """Check required environment variables."""
    print("=" * 80)
    print("1. ENVIRONMENT VARIABLES CHECK")
    print("=" * 80)
    
    required_vars = {
        "AUTO_TRADING_ENABLED": os.getenv("AUTO_TRADING_ENABLED", "true"),
        "AUTO_TRADE_AMOUNT_USD": os.getenv("AUTO_TRADE_AMOUNT_USD", "100.0"),
        "ALPACA_KEY": os.getenv("ALPACA_KEY"),
        "ALPACA_SECRET": os.getenv("ALPACA_SECRET"),
        "TELEGRAM_ENABLED": os.getenv("TELEGRAM_ENABLED", "false"),
        "TELEGRAM_BOT_TOKEN": os.getenv("TELEGRAM_BOT_TOKEN"),
    }
    
    issues = []
    for var, value in required_vars.items():
        if var in ["ALPACA_KEY", "ALPACA_SECRET", "TELEGRAM_BOT_TOKEN"]:
            status = "✅ SET" if value else "❌ NOT SET"
            display = f"{value[:10]}..." if value else "NOT SET"
        else:
            status = "✅ SET" if value else "❌ NOT SET"
            display = value
        
        print(f"   {var:25} = {display:20} {status}")
        
        if var in ["ALPACA_KEY", "ALPACA_SECRET"] and not value:
            issues.append(f"❌ {var} is required for auto-trading")
        if var == "AUTO_TRADING_ENABLED" and value.lower() != "true":
            issues.append(f"⚠️  AUTO_TRADING_ENABLED is '{value}' (should be 'true')")
    
    if issues:
        print("\n   ISSUES FOUND:")
        for issue in issues:
            print(f"   {issue}")
    else:
        print("\n   ✅ All environment variables look good!")
    
    return len(issues) == 0


def check_logs():
    """Check recent logs for key messages."""
    print("\n" + "=" * 80)
    print("2. LOG ANALYSIS")
    print("=" * 80)
    
    # Find today's log file (use current date)
    today = datetime.now()
    week_num = today.isocalendar()[1]
    log_file = PROJECT_ROOT / "tmp" / "audit_logs" / str(today.year) / f"{today.month:02d}" / f"week_{week_num:02d}" / f"{today.strftime('%Y-%m-%d')}.log"
    
    # Also check yesterday's log if today's doesn't exist
    if not log_file.exists():
        yesterday = datetime.now().replace(day=today.day - 1)
        week_num_yesterday = yesterday.isocalendar()[1]
        log_file = PROJECT_ROOT / "tmp" / "audit_logs" / str(yesterday.year) / f"{yesterday.month:02d}" / f"week_{week_num_yesterday:02d}" / f"{yesterday.strftime('%Y-%m-%d')}.log"
    
    if not log_file.exists():
        print(f"   ⚠️  Log file not found: {log_file}")
        print("   (This might be normal if server hasn't started today)")
        return
    
    print(f"   Reading: {log_file}")
    
    with open(log_file, 'r') as f:
        lines = f.readlines()
    
    print(f"   Total log lines: {len(lines)}")
    print(f"   Checking last 500 lines...\n")
    
    recent_lines = lines[-500:] if len(lines) > 500 else lines
    
    # Key messages to check
    checks = {
        "AutoTradeService initialized": False,
        "AutoTradeService started": False,
        "Alpaca Connection Manager connected": False,
        "Failed to connect to Alpaca": False,
        "🎯 AUTO-TRADE: Received ArticleClassified event": False,
        "🤖 AUTO-TRADE: Processing IMMINENT article": False,
        "🚀 AUTO-TRADING: Publishing trade request": False,
        "Trade execution completed": False,
        "✅ Trade Executed": False,
        "⏭️ AUTO-TRADE SKIPPED": False,
    }
    
    for line in recent_lines:
        line_lower = line.lower()
        for check, _ in checks.items():
            if check.lower() in line_lower:
                checks[check] = True
                # Show the actual log line
                if "AUTO-TRADE" in check or "Trade" in check:
                    print(f"   ✅ Found: {check}")
                    print(f"      {line.strip()[:120]}")
    
    print("\n   Summary:")
    for check, found in checks.items():
        status = "✅" if found else "❌"
        print(f"   {status} {check}")
    
    # Check for errors
    error_lines = [l for l in recent_lines if "error" in l.lower() or "exception" in l.lower() or "failed" in l.lower()]
    if error_lines:
        print(f"\n   ⚠️  Found {len(error_lines)} error/exception lines:")
        for err in error_lines[-5:]:  # Show last 5 errors
            print(f"      {err.strip()[:120]}")


def check_audit_trail():
    """Check today's audit trail for IMMINENT classifications."""
    print("\n" + "=" * 80)
    print("3. AUDIT TRAIL CHECK")
    print("=" * 80)
    
    # Use current date
    today = datetime.now()
    week_num = today.isocalendar()[1]
    audit_file = PROJECT_ROOT / "tmp" / "classification_audit_trail" / str(today.year) / f"{today.month:02d}" / f"week_{week_num:02d}" / f"{today.strftime('%Y-%m-%d')}.json"
    
    # Also check yesterday's audit file if today's doesn't exist
    if not audit_file.exists():
        yesterday = datetime.now().replace(day=today.day - 1)
        week_num_yesterday = yesterday.isocalendar()[1]
        audit_file = PROJECT_ROOT / "tmp" / "classification_audit_trail" / str(yesterday.year) / f"{yesterday.month:02d}" / f"week_{week_num_yesterday:02d}" / f"{yesterday.strftime('%Y-%m-%d')}.json"
        if audit_file.exists():
            print(f"   (Using yesterday's audit file: {yesterday.strftime('%Y-%m-%d')})")
    
    if not audit_file.exists():
        print(f"   ⚠️  Audit file not found: {audit_file}")
        return
    
    with open(audit_file, 'r') as f:
        entries = json.load(f)
    
    print(f"   Found {len(entries)} IMMINENT classifications today")
    
    if entries:
        print(f"   Latest entry:")
        latest = entries[-1]
        print(f"      Article: {latest.get('article_title', 'N/A')[:60]}")
        print(f"      Tickers: {latest.get('article_tickers', [])}")
        print(f"      Classified at: {latest.get('classified_at', 'N/A')}")
        print(f"      Has trade_details: {bool(latest.get('trade_details'))}")
        
        # Check if any have trade details
        with_trades = [e for e in entries if e.get('trade_details')]
        if with_trades:
            print(f"\n   ✅ {len(with_trades)} entries have trade details")
        else:
            print(f"\n   ⚠️  No entries have trade_details - trades might not be executing")


def check_config():
    """Check configuration values."""
    print("\n" + "=" * 80)
    print("4. CONFIGURATION CHECK")
    print("=" * 80)
    
    try:
        from newsflash.config import settings
        
        print(f"   AUTO_TRADING_ENABLED: {settings.AUTO_TRADING_ENABLED}")
        print(f"   AUTO_TRADE_AMOUNT_USD: {settings.AUTO_TRADE_AMOUNT_USD}")
        print(f"   PAPER_TRADING: {settings.PAPER_TRADING}")
        print(f"   CLASSIFICATION_ENABLED: {settings.CLASSIFICATION_ENABLED}")
        print(f"   TELEGRAM_ENABLED: {settings.TELEGRAM_ENABLED}")
        
        if not settings.AUTO_TRADING_ENABLED:
            print("\n   ❌ AUTO_TRADING_ENABLED is False - auto-trading is disabled!")
        
    except Exception as e:
        print(f"   ⚠️  Error checking config: {e}")


def main():
    """Run all diagnostics."""
    print("\n" + "=" * 80)
    print("AUTO-TRADE DIAGNOSTICS")
    print("=" * 80)
    print(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    
    env_ok = check_env_vars()
    check_logs()
    check_audit_trail()
    check_config()
    
    print("\n" + "=" * 80)
    print("RECOMMENDATIONS")
    print("=" * 80)
    
    if not env_ok:
        print("1. Fix environment variables (see issues above)")
    
    print("2. Check server logs for:")
    print("   - '✅ Alpaca Connection Manager connected'")
    print("   - '🎯 AUTO-TRADE: Received ArticleClassified event'")
    print("   - '🚀 AUTO-TRADING: Publishing trade request'")
    print("   - '✅ Trade Executed'")
    
    print("\n3. If Alpaca connection is failing:")
    print("   - Verify ALPACA_KEY and ALPACA_SECRET are correct")
    print("   - Check network connectivity")
    print("   - Verify paper trading account is active")
    
    print("\n4. If AutoTradeService is not receiving events:")
    print("   - Check if AutoTradeService is started")
    print("   - Verify event bus is working")
    print("   - Check if classification is publishing ArticleClassified events")
    
    print("\n" + "=" * 80)


if __name__ == "__main__":
    main()
