"""Script to close all open positions in IBKR.

Usage:
    uv run python scripts/close_all_positions.py
    uv run python scripts/close_all_positions.py --paper
"""

import argparse
import asyncio
from typing import List

from ib_insync import IB, Stock, Option, MarketOrder

from newsflash.services.ibkr_trading_service import IBKRTradingService
from newsflash.utils.logging_config import get_logger

logger = get_logger(__name__)


async def close_all_positions(paper: bool = False) -> None:
    """Close all open positions in IBKR."""
    service = IBKRTradingService(paper_trading=paper)
    try:
        await service.start()
        ib = await service._ensure_connected(timeout_seconds=30.0)
        
        if not ib:
            logger.error("Failed to connect to IBKR")
            return
        
        # Get all positions
        positions = ib.positions()
        
        if not positions:
            logger.info("No open positions found")
            return
        
        logger.info(f"Found {len(positions)} open position(s)")
        
        # Close each position
        for position in positions:
            contract = position.contract
            quantity = abs(int(position.position))
            
            if quantity == 0:
                continue
            
            action = "SELL" if position.position > 0 else "BUY"
            ticker = contract.symbol
            
            logger.info(
                f"Closing position: {ticker} {action} {quantity} shares/contracts",
                position=position.position,
                avg_cost=position.avgCost,
            )
            
            try:
                # Create market order to close
                order = MarketOrder(action, quantity)
                trade = ib.placeOrder(contract, order)
                
                # Wait for fill
                logger.info(f"Waiting for {ticker} order to fill...")
                await asyncio.sleep(2)
                
                if trade.isDone():
                    fill_price = trade.orderStatus.avgFillPrice or 0.0
                    filled = int(trade.orderStatus.filled or quantity)
                    logger.info(
                        f"✅ {ticker} position closed",
                        filled=filled,
                        fill_price=fill_price,
                    )
                else:
                    logger.warning(
                        f"⚠️ {ticker} order not yet filled, status: {trade.orderStatus.status}",
                    )
                    # Cancel if not filled quickly
                    try:
                        ib.cancelOrder(order)
                        logger.warning(f"Order for {ticker} cancelled")
                    except Exception as e:
                        logger.error(f"Error cancelling order for {ticker}: {e}")
                        
            except Exception as e:
                logger.error(
                    f"❌ Error closing position for {ticker}",
                    error=str(e),
                    exc_info=True,
                )
        
        logger.info("Finished closing all positions")
        
    except Exception as e:
        logger.error(f"Error in close_all_positions: {e}", exc_info=True)
    finally:
        await service.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description="Close all open positions in IBKR")
    parser.add_argument(
        "--paper",
        action="store_true",
        help="Use the paper-trading gateway (default is live port)",
    )
    args = parser.parse_args()
    
    asyncio.run(close_all_positions(paper=args.paper))


if __name__ == "__main__":
    main()

