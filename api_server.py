import os
"""
API 控制中心 (FastAPI)
作用：连接前端 UI 与后端引擎。负责启动/停止机器人、执行紧急平仓和撤单指令、以及提供实时状态查询。
"""

import sys
import time
# Add current directory to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import asyncio
import logging
from typing import Dict, Optional
from decimal import Decimal
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
import aiohttp
from pydantic import BaseModel
from dotenv import load_dotenv

# App Logic
from exchanges.nado import NadoClient
from hft_bot import HFTBot, TradingConfig

# Logging Setup (Queue for WS streaming)
log_queue = asyncio.Queue()

class QueueHandler(logging.Handler):
    def emit(self, record):
        msg = self.format(record)
        asyncio.create_task(log_queue.put(msg))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S',
    handlers=[
        logging.FileHandler("bot_debug.log", mode='w', encoding='utf-8'),
        logging.StreamHandler(),
        QueueHandler()
    ]
)
logger = logging.getLogger("API")

# State
# State
# State
bot_instance: Optional[HFTBot] = None
last_trading_config: Optional[TradingConfig] = None
query_client: Optional[NadoClient] = None


# Models
class StartConfig(BaseModel):
    ticker: str = "ETH"
    quantity: float = 0.05
    spread: float = 0.0005
    interval: int = 5
    boost_mode: bool = False
    max_exposure: float = 200.0  # Default 200 USD limit

def save_last_config(config: TradingConfig):
    try:
        import json
        data = {
            "ticker": config.ticker,
            "contract_id": config.contract_id,
            "quantity": float(config.quantity),
            "spread": float(config.grid_step),
            "interval": config.wait_time,
            "boost_mode": config.boost_mode,
            "max_exposure": 200.0 # Placeholder or extract from config if added
        }
        with open("last_config.json", "w") as f:
            json.dump(data, f)
        logger.info("✅ Configuration persisted to last_config.json")
    except Exception as e:
        logger.error(f"Failed to save config: {e}")

def load_last_config() -> Optional[TradingConfig]:
    try:
        import json
        if not os.path.exists("last_config.json"):
            return None
        with open("last_config.json", "r") as f:
            data = json.load(f)
        
        return TradingConfig(
            exchange="nado",
            ticker=data.get("ticker", "ETH"),
            contract_id=data.get("contract_id", "4"),
            quantity=Decimal(str(data.get("quantity", 0.05))),
            take_profit=Decimal("100"),
            tick_size=Decimal("0.01"),
            direction="buy",
            max_orders=10,
            wait_time=data.get("interval", 5),
            grid_step=Decimal(str(data.get("spread", 0.0005))),
            stop_price=Decimal(0),
            pause_price=Decimal(0),
            boost_mode=data.get("boost_mode", False)
        )
    except Exception as e:
        logger.error(f"Failed to load config: {e}")
        return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Load Env
    load_dotenv()
    
    # Load Last Config
    global last_trading_config
    last_trading_config = load_last_config()
    if last_trading_config:
        logger.info(f"📁 Restored last configuration for {last_trading_config.ticker}")
        
    yield
    # Cleanup
    if bot_instance:
        await bot_instance.stop()

app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/status")
def get_status():
    if bot_instance and bot_instance.running:
        return {"status": "running", "cycle": bot_instance.cycle_count}
    return {"status": "stopped"}

@app.get("/stats")
async def get_stats():
    """Retrieve real-time stats from the bot instance."""
    if not bot_instance or not bot_instance.running:
        return {
            "equity": 0,
            "pnl": 0,
            "volume": 0,
            "volume_rate_min": 0,
            "health": 100,
            "liq_price": 0,
            "active_pos": 0,
            "trades": []
        }
    
    try:
        stats = await bot_instance.pnl.update()
        
        # Seed history from official Indexer if live list is empty (e.g. after restart)
        if not bot_instance.trade_history:
            try:
                bot_instance.trade_history = await bot_instance.client.get_historical_trades(limit=20)
            except Exception as e:
                logger.warning(f"Indexer Seed Failed: {e}")

        # Calculate volume rate (sliding window 1 min)
        now = time.time()
        vol_1m = sum(Decimal(str(t['size'])) * Decimal(str(t['price'])) for t in bot_instance.trade_history if (now - float(t.get('ts', 0))) < 60)
        
        # Merge with pnl tracker stats
        return {
            **stats,
            "volume_rate_min": float(vol_1m),
            "trades": bot_instance.trade_history[:50]
        }
    except Exception as e:
        logger.error(f"Stats Error: {e}")
        return {"error": str(e)}

@app.post("/start")
async def start_bot(cfg: StartConfig):
    global bot_instance
    
    if bot_instance and bot_instance.running:
        logger.warning("Bot already running, attempting to stop first...")
        await bot_instance.stop()
        await asyncio.sleep(1)

    # Init Config
    t_cfg = TradingConfig(
        exchange="nado",
        ticker=cfg.ticker,
        contract_id="4" if "ETH" in cfg.ticker else cfg.ticker,
        quantity=Decimal(str(cfg.quantity)),
        take_profit=Decimal("100"),
        tick_size=Decimal("0.01"),
        direction="buy",
        max_orders=10,
        wait_time=cfg.interval,
        grid_step=Decimal(str(cfg.spread)),
        stop_price=Decimal(0),
        pause_price=Decimal(0),
        boost_mode=cfg.boost_mode
    )
    
    # Save for emergency use
    global last_trading_config
    last_trading_config = t_cfg
    save_last_config(t_cfg)

    
    try:
        client = NadoClient(t_cfg)
        
        # Product ID Resolution: Try to resolve from client BEFORE passing to bot config
        if hasattr(client, 'get_contract_attributes'):
             await client.get_contract_attributes()
             
        p_id = getattr(client, 'product_id', 4)

        bot_config = {
            "spread": cfg.spread,
            "quantity": cfg.quantity,
            "interval": cfg.interval,
            "ticker": cfg.ticker,
            "contract_id": str(p_id), # Numeric string for product ID
            "max_exposure": cfg.max_exposure,
            "take_profit": 100,
            "exchange": "nado",
            "boost_mode": cfg.boost_mode
        }
        
        # New Signature: HFTBot(config_dict, client=client)
        logger.info(f"🚀 Initializing HFTBot with config: {bot_config}")
        bot_instance = HFTBot(bot_config, client=client)
        
        # We need to explicitly run start task
        asyncio.create_task(bot_instance.start())
        
        return {"status": "started", "config": bot_config}
        
    except Exception as e:
        logger.error(f"Start failed: {e}")
        return {"error": str(e)}

@app.post("/stop")
async def stop_bot():
    if bot_instance:
        await bot_instance.stop()
    return {"status": "stopped"}

@app.get("/stats")
async def get_stats():
    if bot_instance:
        duration_min = (time.time() - bot_instance.pnl.start_time) / 60
        vol = float(bot_instance.pnl.volume_traded)
        rate = vol / duration_min if duration_min > 0 else 0
        
        s = {
            "pnl": float(bot_instance.pnl.session_pnl),
            "equity": float(bot_instance.pnl.current_equity),
            "volume": vol,
            "initial": float(bot_instance.pnl.initial_equity) if bot_instance.pnl.initial_equity else 0,
            "health": float(bot_instance.pnl.current_health),
            "liq_price": float(bot_instance.pnl.liq_price),
            "active_pos": float(bot_instance.pnl.active_pos),
            "volume_rate_min": rate,
            "active_orders": [
                {
                    "side": o.side,
                    "price": float(o.price),
                    "size": float(o.size),
                    "id": o.order_id
                } for o in bot_instance.active_orders
            ],
            "trades": [
                {
                    "side": t.get('side'),
                    "price": float(t.get('price')),
                    "size": float(t.get('size')),
                    "time": t.get('time')
                } for t in bot_instance.trade_history[-10:] # Last 10 trades
            ]
        }
        return s
    return {"error": "Bot not running"}

@app.get("/products")
async def get_products():
    """Fetch available trading pairs."""
    dummy_cfg = TradingConfig(exchange="nado", ticker="ETH", contract_id="4", quantity=Decimal("0"), take_profit=Decimal("1"), tick_size=Decimal("0.01"), direction="buy", max_orders=1, wait_time=5, grid_step=Decimal("0.01"), stop_price=Decimal(0), pause_price=Decimal(0), boost_mode=False)
    client = NadoClient(dummy_cfg)
    pairs = await client.get_available_pairs()
    return {"status": "success", "products": pairs}

@app.post("/close_all")
async def close_all():
    """Panic Close: Market close entire position. Highest Priority."""
    
    active_client = None
    is_temp = False
    
    try:
        # 1. Determine which client to use
        if bot_instance and bot_instance.client and bot_instance.client._session and not bot_instance.client._session.closed:
            active_client = bot_instance.client
        elif last_trading_config:
            # Emergency: Create temp client
            logger.warning("[Panic] Creating Emergency Client for Close All")
            # Ensure we load env if not already
            load_dotenv()
            active_client = NadoClient(last_trading_config)
            # CRITICAL: Initialize session and resolve product info for fresh client
            await active_client.connect()
            await active_client.get_contract_attributes()
            is_temp = True
        else:
            return {"error": "No configuration found. Provide one via /start first."}

        # 2. Execute Panic Logic
        # A. Cancel All Orders First (Mandatory)
        logger.info("[Panic] Cancelling all orders...")
        await active_client.cancel_all_orders(str(active_client.product_id))
        await asyncio.sleep(1) # Wait for cancel propagation
        
        # B. Get Position (Decimal)
        pos_size = await active_client.get_account_positions()
        logger.info(f"[Panic] Current Position: {pos_size}")
        
        if abs(pos_size) < Decimal("0.0001"):
            if is_temp: await active_client.disconnect()
            return {"status": "no_position", "message": "Position is effectively zero"}
            
        # 3. Market Close (using standardized place_market_order)
        side = "sell" if pos_size > 0 else "buy"
        size = abs(pos_size)
        
        logger.info(f"[Panic] Closing {size} {side}")
        res = await active_client.place_market_order(
            str(active_client.product_id), 
            size, 
            side
        )
        
        if is_temp: await active_client.disconnect()
        
        if res.success:
            return {"status": "closed", "size": float(size), "side": side}
        else:
            return {"status": "error", "error": res.error_message}

    except Exception as e:
        if active_client and is_temp: 
             try: await active_client.disconnect()
             except: pass
        logger.error(f"[Panic] Endpoint Error: {e}")
        return {"status": "error", "error": str(e)}

@app.post("/cancel_all")
async def cancel_all():
    """Cancel all active orders. Highest Priority."""
    active_client = None
    is_temp = False
    
    try:
         # 1. Determine which client to use
        if bot_instance and bot_instance.client and bot_instance.client._session and not bot_instance.client._session.closed:
            active_client = bot_instance.client
        elif last_trading_config:
            logger.warning("[Panic] Creating Emergency Client for Cancel All")
            load_dotenv()
            active_client = NadoClient(last_trading_config)
            await active_client.connect()
            await active_client.get_contract_attributes()
            is_temp = True
        else:
            return {"error": "No configuration found. Provide one via /start first."}
            
        pid = getattr(active_client, 'product_id', 4)
        await active_client.cancel_all_orders(str(pid))
        
        if is_temp: await active_client.disconnect()
        return {"status": "cancelled"}
        
    except Exception as e:
        if active_client and is_temp:
             try: await active_client.disconnect()
             except: pass
        return {"status": "error", "error": str(e)}

@app.get("/account")
async def get_account_details():
    """Get detailed position and order info."""
    if bot_instance:
        # We need to fetch fresh info or use cached
        # Let's fetch fresh to be safe for a 'Detail' view
        try:
            # Re-use client logic
            orders = await bot_instance.client.get_open_orders(bot_instance.client.product_id)
            # Position info is already in pnl tracker stats roughly, but let's get exact entry price
            # We can read from bot_instance.pnl which might have `data` if we modifying it to store `data`
            # For now, let's just return what we have in pnl + orders
            
            return {
                "orders": orders, # List of orders
                "position": {
                    "size": float(bot_instance.pnl.active_pos),
                    "entry_price": 0, # PnL tracker doesn't parse entry price yet, todo?
                    "liq_price": float(bot_instance.pnl.liq_price),
                    "pnl": float(bot_instance.pnl.session_pnl) # This is session PnL, not unrealized PnL of position.
                }
            }
        except Exception as e:
            return {"error": str(e)}
    return {"error": "Bot not running"}

@app.get("/price/{ticker}")
async def get_price(ticker: str):
    """Fetch approximate market price for ticker."""
    try:
        # Resolve ID
        pid = 4
        if "BTC" in ticker: pid = 2
        elif "ETH" in ticker: pid = 4
        elif "SOL" in ticker: pid = 6
        
        url = "https://gateway.prod.nado.xyz/v1/query"
        payload = {"type": "market_liquidity", "product_id": pid, "depth": 5}
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0",
            "Origin": "https://app.nado.xyz"
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers) as resp:
                if resp.status != 200:
                    logger.warning(f"Price Fetch HTTP Error {resp.status}")
                    return {"price": 0}
                res = await resp.json()
        
        data = res.get('data', {})
        bids = data.get('bids', [])
        
        if not bids:
            return {"price": 0}
            
        # Bids are [price, size]
        best_bid = Decimal(bids[0][0]) / Decimal(10**18)
        return {"price": float(best_bid)}
        
    except Exception as e:
        logger.error(f"Price fetch failed: {e}")
        return {"price": 0}

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            # Simple Log Streaming
            log = await log_queue.get()
            await websocket.send_text(log)
    except WebSocketDisconnect:
        pass
