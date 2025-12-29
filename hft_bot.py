"""
HFT 策略核心引擎
作用：连接多方组件，负责监听实时订单簿、执行交易策略、管理仓位风险。支持 'Booster Mode' 高频交易。
"""

import asyncio
import json
import time
import logging
import random
from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Optional, Tuple
import aiohttp

# Architecture Standardization
from exchanges.factory import ExchangeFactory
from exchanges.base import BaseExchangeClient
from trading_bot import TradingConfig
from pnl_tracker import PnLTracker

# Logger inherited from root or configured by caller
logger = logging.getLogger("HFTBot")

class LocalOrderBook:
    """Thread-safe local copy of the orderbook."""
    def __init__(self):
        self.bids: Dict[Decimal, Decimal] = {} # Price -> Size
        self.asks: Dict[Decimal, Decimal] = {}
        self.lock = asyncio.Lock()

    async def update(self, side: str, price: Decimal, size: Decimal):
        """Update a level safely."""
        async with self.lock:
            book = self.bids if side == 'buy' else self.asks
            if size == 0:
                book.pop(price, None)
            else:
                book[price] = size

    async def get_mid_price(self) -> Decimal:
        async with self.lock:
            if not self.bids or not self.asks:
                return Decimal(0)
            bb = max(self.bids.keys())
            ba = min(self.asks.keys())
            return (bb + ba) / 2

class WebSocketManager:
    """Manages WebSocket connections with Infinite Retry."""
    def __init__(self, client: BaseExchangeClient, bot: 'HFTBot'):
        self.client = client
        self.bot = bot
        self.base_url = getattr(client, 'ws_url', "wss://gateway.prod.nado.xyz/v1/ws")
        self.product_id = getattr(client, 'product_id', 4)
        
        self.public_ws = None
        self.session = None
        self.book = LocalOrderBook()
        self.stop_event = asyncio.Event()

    async def connect(self):
        self.stop_event.clear()
        asyncio.create_task(self._supervisor_loop())

    async def _supervisor_loop(self):
        """Infinite Retry Loop to maintain connection."""
        while not self.stop_event.is_set():
            try:
                await self._connect_and_listen()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"WS Supervisor Connection Lost: {e}. Retrying in 5s...")
                await asyncio.sleep(5)

    async def _connect_and_listen(self):
        self.session = aiohttp.ClientSession()
        
        # 1. Fetch Account ID if missing (Critical for Private Stream)
        if not getattr(self.client, 'account_id', None):
            logger.info("Account ID missing, waiting for initialization...")
            if hasattr(self.client, '_load_account_info'):
                try: await self.client._load_account_info()
                except: pass
        
        account_id = getattr(self.client, 'account_id', 0)
        
        # 2. Build Authenticated URL & Headers
        url = "wss://gateway.prod.nado.xyz/v1/ws"
        timestamp = int(time.time() * 1000)
        
        headers = {
            "Origin": "https://app.nado.xyz",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
            "Sec-WebSocket-Version": "13",
            "X-edgeX-Api-Timestamp": str(timestamp)
        }
        
        # Stark Signature for Private Stream
        try:
            path = f"/api/v1/private/wsaccountId={account_id}"
            sign_content = f"{timestamp}GET{path}"
            
            if hasattr(self.client, '_stark_sign') and getattr(self.client, 'stark_private_key', None):
                from eth_utils import keccak
                msg_hash = keccak(text=sign_content)
                r, s = self.client._stark_sign(msg_hash, self.client.stark_private_key)
                headers["X-edgeX-Api-Signature"] = f"{r}{s}"
                logger.info(f"Generated WS Stark Signature for Account {account_id}")
        except Exception as e:
            logger.warning(f"Failed to generate WS signature: {e}")

        # Final URL with timestamp & accountId
        ws_url = f"{url}?timestamp={timestamp}"
        if account_id: ws_url += f"&accountId={account_id}"

        logger.info(f"Connecting WS: {ws_url}")
        try:
            self.public_ws = await self.session.ws_connect(ws_url, headers=headers, heartbeat=15)
            logger.info("⚡ WS Connection established!")
        except Exception as e:
            logger.error(f"❌ WS Connection failed: {e}")
            return
        
        # 3. Standard Subscriptions
        # High-frequency Taker ops need real-time depth and private fills
        await self.public_ws.send_json({"type": "subscribe", "channel": f"depth.{self.product_id}"})
        logger.info(f"Subscribed to depth.{self.product_id}")

        try:
            if hasattr(self.client, '_subaccount_to_bytes32'):
                sender = self.client._subaccount_to_bytes32(self.client.wallet_address, self.client.subaccount_name)
                await self.public_ws.send_json({"type": "subscribe", "channel": f"fills.{sender}"})
                logger.info(f"Subscribed to private fills for: {sender[:10]}...")
        except: pass
        
        # 4. Listen Loop
        logger.info("📡 WS Entering Listen Loop...")
        async for msg in self.public_ws:
            # VERY AGGRESSIVE LOGGING: Log everything that isn't silence
            if msg.type != aiohttp.WSMsgType.TEXT:
                logger.info(f"WS RX Metadata Type: {msg.type}")
                if msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    break
                continue

            raw = msg.data
            if not raw: continue
            
            try:
                data = json.loads(raw)
                msg_type = data.get('type', data.get('event', 'unknown'))
                
                # Diagnostics: Log everything except frequent depth
                if msg_type not in ('depth', 'book_depth', 'quote-event'):
                     logger.info(f"WS RX JSON: {msg_type} | Data: {raw[:300]}")
                elif time.time() % 60 < 2: 
                     logger.info(f"WS RX Depth Heartbeat (Channel: {data.get('channel')})")

                if msg_type == 'ping':
                    await self.public_ws.send_json({"type": "pong", "time": data.get('time')})
                    continue
                     
                # Routing
                if msg_type in ('depth', 'snapshot', 'book_depth') or (msg_type == 'quote-event' and 'depth' in data.get('channel', '')):
                    await self._handle_depth_update(data)
                elif msg_type in ('fill', 'match', 'order_update', 'trade', 'order', 'position', 'account'):
                    await self.bot._handle_fill_update(data)
                elif msg_type == 'error':
                    logger.error(f"WS Server Error: {data}")
                        
            except Exception as e:
                logger.error(f"WS Processing Error: {e}")
                
        logger.warning("WS Connection Loop ended. Supervisor will reconnect...")

    async def _handle_depth_update(self, msg_data):
        # Support both wrapped "data" and flat structure
        data = msg_data.get('data', msg_data)
        
        # Debug Log first few updates
        if random.random() < 0.05:
            logger.info(f"WS Raw Depth: {str(msg_data)[:200]}")
            
        bids = data.get('bids', [])
        asks = data.get('asks', [])
        
        for p, s in bids:
            # Nado Sends X18 Strings -> Float conversion required
            p_val = Decimal(str(p)) / Decimal(10**18)
            s_val = Decimal(str(s)) / Decimal(10**18)
            await self.book.update('buy', p_val, s_val)
            
        for p, s in asks:
            p_val = Decimal(str(p)) / Decimal(10**18)
            s_val = Decimal(str(s)) / Decimal(10**18)
            await self.book.update('sell', p_val, s_val)

    async def close(self):
        self.stop_event.set()
        if self.public_ws: await self.public_ws.close()
        if self.session: await self.session.close()

class HFTBot:
    def __init__(self, config_dict: dict, ws_manager=None, client=None):
        self.config = config_dict
        self.running = False
        self.ws_manager = ws_manager
        
        # Architecture Standardization: Use Factory
        if client:
            self.client = client
        else:
            # We assume config_dict matches TradingConfig schema
            # Extract exchange name, default to 'nado'
            exchange_name = config_dict.get('exchange', 'nado')
            self.client = ExchangeFactory.create_exchange(exchange_name, config_dict)
            
        self.pnl = PnLTracker(self.client)
        self.cycle_count = 0
        
        # State Tracking for UI
        self.active_orders = []
        self.trade_history = []
        self.max_exposure_usd = Decimal(str(config_dict.get('max_exposure', 200))) 
        self.current_pos_notional = Decimal("0")
        
        self.consecutive_errors = 0
        self.MAX_LEVERAGE = Decimal("5.0")
        self.product_id = int(getattr(self.client, 'product_id', 4))
        self._stats_task: Optional[asyncio.Task] = None
            
    async def start(self):
        # Initialize Product
        try:
             if hasattr(self.client, 'get_contract_attributes'):
                await self.client.get_contract_attributes() # type: ignore
        except:
             pass 
             
        logger.info(f"Starting HFT Bot (Prod: {getattr(self.client, 'product_id', 'Unknown')})...")
        self.running = True
        
        if not self.ws_manager:
            self.ws_manager = WebSocketManager(self.client, self)
            
        await self.ws_manager.connect()
        self._stats_task = asyncio.create_task(self._stats_loop())

        # V3 HARD PURGE: Standardizing to ensure no legacy orders remain
        logger.info("⚡ [V3] 正在执行全量启动清空 (Atomic Startup Purge)...")
        try:
            pid = getattr(self.client, 'product_id', 4)
            await self.client.cancel_all_orders(str(pid))
            logger.info("⏳ [V3] 进入启动宁静期 (Calm Start Delay)...")
            await asyncio.sleep(3) 
            
            # Populate initial position cache to avoid first-cycle glitch
            if hasattr(self.client, 'get_account_positions'):
                initial_pos = await self.client.get_account_positions()
                logger.info(f"📊 [V3] 初始持仓同步成功: {initial_pos} ETH")
        except Exception as e:
            logger.error(f"Startup purge/sync failed: {e}")
            
        
        # DUAL ENGINE DISPATCH
        if self.config.get('boost_mode', False):
            logger.info("🔥 ENGINE START: BOOSTER MODE (TAKER) 🔥")
            self._strategy_task = asyncio.create_task(self._run_booster_strategy())
        else:
            logger.info("🛡️ ENGINE START: MAKER MODE (BRACKET) 🛡️")
            self._strategy_task = asyncio.create_task(self._run_maker_strategy())

    async def _handle_fill_update(self, data):
        """Handle real-time fill event for position and volume tracking."""
        try:
            fill = data.get('data', data)
            pid = int(fill.get('product_id', 0))
            if pid == self.product_id:
                amt = Decimal(str(fill.get('amount', 0)))
                px = Decimal(str(fill.get('price', 0))) / Decimal(10**18)
                
                # Proactive position cache update
                if hasattr(self.client, '_pos_cache') and self.client._pos_cache is not None:
                    self.client._pos_cache += amt
                    logger.info(f"⚡ [WS FILL] Real-time Pos Update: {self.client._pos_cache} ETH (Change: {amt})")
                
                # Record Volume & Trade History
                self.pnl.add_volume(abs(amt), px)
                trade = {
                    "ts": time.time(),
                    "time": datetime.now().strftime("%H:%M:%S"),
                    "side": "buy" if amt > 0 else "sell",
                    "size": float(abs(amt)),
                    "price": float(px),
                    "id": fill.get('order_id', 'unknown')
                }
                self.trade_history.insert(0, trade)
                self.trade_history = self.trade_history[:50] # Keep last 50
                
            if hasattr(self.client, '_zero_balance_strikes'):
                self.client._zero_balance_strikes = 0
                
        except Exception as e:
            logger.error(f"Error handling fill update: {e}")

    async def get_mid_price(self) -> Optional[Decimal]:
        # Bot's mid price comes from its ws_manager's book
        if not self.ws_manager: return Decimal(0)
        
        mp = await self.ws_manager.book.get_mid_price()
        if mp and mp > 0:
            return mp
            
        # Fallback to REST if WS is silent
        try:
            if hasattr(self.client, 'get_depth'):
                logger.warning("WS Mid 0: Polling REST Depth Fallback...")
                pid = getattr(self.client, 'product_id', 4)
                data = await self.client.get_depth(pid)
                     
                # Handle wrapped 'data' if present (API quirk)
                data = data.get('data', data)
                    
                bids = data.get('bids', [])
                asks = data.get('asks', [])
                
                if not bids or not asks:
                    return Decimal(0)
                    
                for p, s in bids: 
                    p_val = Decimal(str(p)) / Decimal(10**18)
                    s_val = Decimal(str(s)) / Decimal(10**18)
                    await self.ws_manager.book.update('buy', p_val, s_val)
                    
                for p, s in asks: 
                    p_val = Decimal(str(p)) / Decimal(10**18)
                    s_val = Decimal(str(s)) / Decimal(10**18)
                    await self.ws_manager.book.update('sell', p_val, s_val)
                
                return await self.ws_manager.book.get_mid_price()
        except Exception as e:
            logger.error(f"Price Fallback Failed: {e}")
            
        return Decimal(0)

    async def _stats_loop(self):
        """Background task to keep PnL and account stats fresh."""
        while self.running:
            try:
                await self.pnl.update()
                await asyncio.sleep(10)
            except Exception as e:
                logger.error(f"Stats Update Error: {e}")
                await asyncio.sleep(5)


    async def _run_maker_strategy(self):
        """Standard Bracket Strategy (Cancel -> Check Risk -> Place Limit)."""
        logger.info("Maker Mode: Standard Safety Checks & Limit Orders")
        while self.running:
            try:
                self.cycle_count += 1
                
                # 1. Price Discovery
                mp = await self.get_mid_price()
                if not mp or mp == 0:
                     if self.cycle_count % 5 == 0:
                         logger.info(f"Maker: 等待价格数据... (Waiting for Price) [Cycle {self.cycle_count}]")
                     await asyncio.sleep(1)
                     continue
                
                logger.info(f"Maker Cycle {self.cycle_count} | Mid: {mp}")

                # 2. Cleanup Active Orders (Optimized Refresh)
                pid = getattr(self.client, 'product_id', 4)
                active_orders = await self.client.get_active_orders(str(pid))
                self.active_orders = active_orders # Sync to UI
                
                if active_orders:
                    try:
                        # Threshold: Only refresh if price has drifted > 0.05%
                        drift_limit = Decimal("0.0005") # 0.05%
                        first_o = active_orders[0]
                        price_drift = abs(Decimal(str(first_o.price)) - mp) / mp
                        
                        if price_drift > drift_limit:
                            logger.info(f"Maker: Price drifted {price_drift:.4%}. Replacing orders.")
                            ids = [o.order_id for o in active_orders]
                            await self.client.cancel_orders(ids)
                            self.active_orders = []
                            await asyncio.sleep(0.5) 
                        else:
                            # Orders still good, sleep and skip placement
                            await asyncio.sleep(max(1, self.config.get('interval', 5)))
                            continue
                    except Exception as e:
                        logger.error(f"Refresh failed: {e}")

                # 3. Execution Logic
                position = Decimal("0")
                try: 
                     if hasattr(self.client, 'get_account_positions'):
                         position = await self.client.get_account_positions()
                         self.current_pos_notional = abs(position * mp)
                except Exception as e:
                     logger.warning(f"Position fetch failed: {e}")
                
                # SAFETY 1: Hard Circuit Breaker on Errors
                if self.consecutive_errors > 5:
                    logger.error("🚨 EMERGENCY STOP: Too many consecutive failures. Shutting down for safety.")
                    self.running = False
                    break

                # SAFETY 2: Exposure Check (Configurable)
                if self.current_pos_notional > self.max_exposure_usd:
                    if self.cycle_count % 5 == 0:
                        logger.warning(f"WAIT: Max Exposure Reached! Pos Val: {self.current_pos_notional:.2f} USD > Limit: {self.max_exposure_usd} USD (Config Limit)")
                    await asyncio.sleep(5)
                    continue
                    
                # SAFETY 3: Hard Leverage Cap (Dynamic based on Equity)
                # Formula: Max Allowed Value = Current Equity * MAX_LEVERAGE
                equity = Decimal(str(self.pnl.current_equity))
                if equity > 0:
                    hard_limit_usd = equity * self.MAX_LEVERAGE
                    if self.current_pos_notional >= hard_limit_usd:
                        if self.cycle_count % 5 == 0:
                            logger.warning(f"🛑 杠杆熔断 (Leverage Cap): 当前价值 {self.current_pos_notional:.2f}U 已达权益 {equity:.2f}U 的 {self.MAX_LEVERAGE} 倍上限。")
                        await asyncio.sleep(10)
                        continue
                else:
                    # If equity is 0 or negative (risk of liq), STOP and WAIT
                    logger.error(f"🚨 风险警告: 账户权益异常 ({equity})，停止下单。")
                    await asyncio.sleep(10)
                    continue

                spread = Decimal(str(self.config.get('spread', 0.0005)))
                qty = Decimal(str(self.config.get('quantity', 0.01)))
                
                # Order Size Limit: Don't place if single order exceeds exposure? 
                # Actually, quantity * price should be checked
                order_value = qty * mp
                if order_value + self.current_pos_notional > self.max_exposure_usd:
                     logger.warning(f"SKIPPED: Order value {order_value:.2f} would exceed Max Exposure")
                     await asyncio.sleep(5)
                     continue

                tick_size = getattr(self.client.config, 'tick_size', Decimal("0.1")) 
                
                # Rounding
                raw_bid = mp * (1 - spread)
                raw_ask = mp * (1 + spread)
                # Quantize using tick_size
                bid_price = (raw_bid / tick_size).quantize(Decimal("1")) * tick_size
                ask_price = (raw_ask / tick_size).quantize(Decimal("1")) * tick_size

                logger.info(f"Maker: Placing {qty} @ B:{bid_price} A:{ask_price}")
                 
                orders = [
                    (qty, "buy", bid_price),
                    (qty, "sell", ask_price)
                ]
                
                res = await self.client.place_batch_open_orders(orders)
                if res.success:
                    logger.info(f"✅ 挂单成功 (Orders Placed): {qty} ETH")
                else:
                    logger.error(f"❌ 挂单被拒绝 (Rejected): {res.error_message}")
                
                # logger.info(f"Batch Res: {res}")
                # Success: reset error counter
                self.consecutive_errors = 0
                await asyncio.sleep(max(2, self.config.get('interval', 5)))
                
            except Exception as e:
                self.consecutive_errors += 1
                logger.error(f"Maker Loop Error ({self.consecutive_errors}): {e}")
                await asyncio.sleep(2)

    async def _run_booster_strategy(self):
        """
        Boost Mode: Stateful Taker Strategy.
        Logic: Open(IOC) -> Wait Fill -> Close(IOC).
        """
        logger.info("Booster Mode: Aggressive IOC Churning")
        
        # State Machine
        STATE_OPEN = "OPEN"
        STATE_CLOSE = "CLOSE"
        current_state = STATE_OPEN
        
        qty = Decimal(str(self.config.get('quantity', 0.01)))
        
        while self.running:
            try:
                self.cycle_count += 1
                
                # We do NOT check active orders. We assume IOC handles it.
                # We do NOT check price from WS. We invoke market order.
                
                cid = getattr(self.client, 'product_id', "4")
                
                if current_state == STATE_OPEN:
                    # Place Market BUY
                    logger.info(f"Boost: 🟢 OPENING LONG {qty}")
                    res = await self.client.place_market_order(str(cid), qty, "buy")
                    
                    if res.success:
                        # Assume filled (IOC). Flip to Close.
                        # Ideally check `filled_size` if API returned it, but `OrderResult` might not have it populated for sync REST
                        # For Boost V1, we assume fill.
                        current_state = STATE_CLOSE
                        # Add stats volume? Not yet, we blindly assume.
                    else:
                        logger.error(f"Boost Open Failed: {res.error_message}")
                        await asyncio.sleep(1)
                        
                elif current_state == STATE_CLOSE:
                    # Place Market SELL
                    logger.info(f"Boost: 🔴 CLOSING LONG {qty}")
                    res = await self.client.place_market_order(str(cid), qty, "sell")
                    
                    if res.success:
                         current_state = STATE_OPEN
                    else:
                         logger.error(f"Boost Close Failed: {res.error_message}")
                         await asyncio.sleep(1)
                
                # Nap to avoid rate limits?
                await asyncio.sleep(0.5) 
                
            except Exception as e:
                logger.error(f"Booster Loop Error: {e}")
                await asyncio.sleep(1)

    async def stop(self):
        logger.info("🛑 [V3] Stopping HFT Bot (Atomic Termination)...")
        self.running = False
        
        # 1. Force-cancel strategy tasks to prevent 'Zombie' cycles
        if hasattr(self, '_strategy_task') and self._strategy_task:
            self._strategy_task.cancel()
            logger.info("🧹 Strategy task cancelled.")
        
        if hasattr(self, '_stats_task') and self._stats_task:
            self._stats_task.cancel()
            logger.info("🧹 Stats task cancelled.")

        # 2. Cleanup WebSockets
        if self.ws_manager:
            await self.ws_manager.close()
        
        # 3. Final Exchange Purge (Atomic Terminator)
        try:
             prod_id = getattr(self.client, 'product_id', 4)
             if hasattr(self.client, 'cancel_all_orders'):
                 logger.info(f"🧼 [Atomic Terminator] Executing final order purge for product {prod_id}...")
                 await self.client.cancel_all_orders(str(prod_id))
                 logger.info("🧼 Final order purge complete.")
        except Exception as e:
             logger.error(f"Final purge failed: {e}")
