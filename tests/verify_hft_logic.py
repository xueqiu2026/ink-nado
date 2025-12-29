
import asyncio
import os
import logging
from decimal import Decimal
from dotenv import load_dotenv
from exchanges.nado import NadoClient
from hft_bot import WebSocketManager

# Dummy Config Class
class TradingConfig:
    def __init__(self, exchange, ticker, quantity, interval, leverage, take_profit, max_orders, strategy, wait_time, grid_step):
        self.exchange = exchange
        self.ticker = ticker
        self.contract_id = ticker # Hack for nado.py usage if needed
        self.quantity = quantity
        self.interval = interval
        self.leverage = leverage
        self.take_profit = take_profit
        self.max_orders = max_orders
        self.strategy = strategy
        self.wait_time = wait_time
        self.grid_step = grid_step


# Simple logger
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("VerifyHFT")

async def verify():
    load_dotenv()
    pk = os.getenv("NADO_PRIVATE_KEY")
    addr = os.getenv("WALLET_ADDRESS")
    
    # Config
    cfg = TradingConfig("nado", "ETH", Decimal("0.04"), 10, 10, Decimal("0"), 10, "test", 10, Decimal("0"))
    client = NadoClient(cfg)
    client.product_id = 4 # Force ETH-PERP ID
    
    # 1. Test WebSocket Connection & parsing
    print("\n--- 1. Testing WebSocket ---")
    ws_man = WebSocketManager(client)
    await ws_man.connect()
    
    # Wait for a few messages
    print("Waiting 5s for WS messages...")
    await asyncio.sleep(5)
    
    ws_man.close()
    print("WS Test Complete. (Check logs for 'WS Quote' or errors)")

    # 2. Test Batch Order
    print("\n--- 2. Testing Batch Order ---")
    # Get Price
    price = await client._get_execution_price("buy")
    p = Decimal(price)
    
    # Place far from market to avoid fill (limit only)
    # Bid much lower, Ask much higher
    bid = p * Decimal("0.8")
    ask = p * Decimal("1.2")
    qty = Decimal("0.05") # Safe size > 100 USDT
    
    orders = [
        (qty, "buy", bid),
        (qty, "sell", ask)
    ]
    
    print(f"Placing Batch: Buy {bid:.2f}, Sell {ask:.2f} (Current: {p:.2f})")
    
    res = await client.place_batch_open_orders(orders)
    if res.success:
        print(f"SUCCESS: Batch placed! IDs: {res.order_id}")
    else:
        print(f"FAILURE: {res.error_message}")

if __name__ == "__main__":
    asyncio.run(verify())
