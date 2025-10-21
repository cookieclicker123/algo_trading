#!/usr/bin/env python3
"""
Debug script to see what data is actually available from yfinance.
"""
import yfinance as yf
import pandas as pd

def debug_yfinance_data():
    """Debug what data is actually available from yfinance."""
    
    print("🔍 Debugging yfinance data structure...")
    
    # Test with AAPL
    ticker = yf.Ticker("AAPL")
    
    print("\n📊 QUARTERLY FINANCIALS COLUMNS:")
    try:
        quarterly_financials = ticker.quarterly_financials
        if quarterly_financials is not None and not quarterly_financials.empty:
            print("Available columns:", list(quarterly_financials.columns))
            print("\nFirst few rows:")
            print(quarterly_financials.head())
        else:
            print("No quarterly financials data available")
    except Exception as e:
        print(f"Error getting quarterly financials: {e}")
    
    print("\n💰 QUARTERLY INCOME STATEMENT COLUMNS:")
    try:
        quarterly_income = ticker.quarterly_income_stmt
        if quarterly_income is not None and not quarterly_income.empty:
            print("Available columns:", list(quarterly_income.columns))
            print("\nFirst few rows:")
            print(quarterly_income.head())
        else:
            print("No quarterly income statement data available")
    except Exception as e:
        print(f"Error getting quarterly income statement: {e}")
    
    print("\n📈 COMPANY INFO (relevant fields):")
    try:
        info = ticker.info
        relevant_fields = [
            'totalRevenue', 'revenue', 'grossMargins', 'profitMargins',
            'netIncome', 'earnings', 'marketCap', 'sector', 'industry'
        ]
        for field in relevant_fields:
            if field in info:
                print(f"{field}: {info[field]}")
    except Exception as e:
        print(f"Error getting company info: {e}")
    
    print("\n📊 RECENT PRICE DATA:")
    try:
        recent_data = ticker.history(period="1d", interval="1m")
        if recent_data is not None and not recent_data.empty:
            print(f"Latest price: ${recent_data['Close'].iloc[-1]:.2f}")
            print(f"Latest volume: {recent_data['Volume'].iloc[-1]:,}")
            if len(recent_data) >= 10:
                price_10min_ago = recent_data['Close'].iloc[-10]
                current_price = recent_data['Close'].iloc[-1]
                price_change = ((current_price - price_10min_ago) / price_10min_ago) * 100
                print(f"Price change (10min): {price_change:+.2f}%")
        else:
            print("No recent price data available")
    except Exception as e:
        print(f"Error getting recent price data: {e}")

if __name__ == "__main__":
    debug_yfinance_data()
