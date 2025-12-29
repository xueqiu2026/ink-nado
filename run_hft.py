
import asyncio
import os
import argparse
from decimal import Decimal
from dotenv import load_dotenv

from exchanges.nado import NadoClient
from trading_bot import TradingConfig
from hft_bot import HFTBot

def main():
    parser = argparse.ArgumentParser(description="Run Nado HFT Bot (Dual-Sided Pendulum)")
    parser.add_argument("--ticker", type=str, default="ETH", help="Trading Pair (e.g. ETH)")
    parser.add_argument("--quantity", type=float, default=0.04, help="Order Size in Base Asset (ETH)")
    parser.add_argument("--spread", type=float, default=0.0005, help="Spread from Mid Price (e.g. 0.0005 for 5bps)")
    
    args = parser.parse_args()
    
    # Load Credentials
    # Explictly look in current directory (where run_hft.py is)
    from pathlib import Path
    env_path = Path(".") / ".env"
    load_dotenv(dotenv_path=env_path)
    
    # Config
    # NadoClient loads keys from env itself
    
    # Use existing TradingConfig class to hold params
    config = TradingConfig(
        exchange="nado",
        ticker=args.ticker,
        contract_id=args.ticker, # Dummy
        quantity=Decimal(str(args.quantity)),
        take_profit=Decimal("100"),
        tick_size=Decimal("0.01"),
        direction="buy", # Dummy
        max_orders=10,
        wait_time=10,
        grid_step=Decimal(str(args.spread)),
        stop_price=Decimal(0),
        pause_price=Decimal(0),
        boost_mode=False
    )
    
    # Initialize Client
    try:
        client = NadoClient(config)
    except Exception as e:
        print(f"Failed to initialize client: {e}")
        return
    
    # Bot Config
    bot_config = {
        "spread": args.spread,
        "quantity": args.quantity
    }
    
    # Run
    bot = HFTBot(client, bot_config)
    
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        print("\nStopping HFT Bot...")

if __name__ == "__main__":
    main()
