
import time
from decimal import Decimal
import logging
from typing import Dict, Any

logger = logging.getLogger("PnLTracker")

class PnLTracker:
    def __init__(self, client):
        self.client = client
        self.initial_equity: Decimal = None
        self.start_time = time.time()
        self.current_equity = Decimal("0")
        self.session_pnl = Decimal("0")
        self.volume_traded = Decimal("0") # To be updated by bot
        
        # Advanced Stats
        self.current_health = Decimal("0")
        self.liq_price = 0
        self.active_pos = Decimal("0")
        
    async def update(self) -> Dict[str, Any]:
        """Fetch account state, calculate equity, and update PnL."""
        try:
            # 1. Fetch Subaccount Info
            sender = self.client._subaccount_to_bytes32(self.client.wallet_address, self.client.subaccount_name)
            payload = {"type": "subaccount_info", "subaccount": sender}
            res = await self.client._post("/query", payload)
            
            data = res.get('data', {})
            if not data:
                return {}
                
            # 2. Calculate Spot Balance (Product 0 = USDC usually)
            spot_val = Decimal("0")
            for s in data.get('spot_balances', []):
                if int(s.get('product_id')) == 0:
                    spot_val = Decimal(s['balance']['amount']) / Decimal(10**18)
                    break
            
            # 3. Calculate Perp Unrealized PnL
            # Equity = Spot + Sum(Pos * Price + v_quote)
            # wait, formula for Equity in Vertex:
            # Equity = Spot_Balance + Sum(v_quote_balance + (Position * OraclePrice))
            # Wait, v_quote_balance is the entry cost (negative if long, positive if short?)
            # Let's verify formula.
            # If Long 1 ETH @ 3000. Cost -3000 USDC. v_quote = -3000.
            # Value = (-3000) + (1 * CurrentPrice).
            # If CurrentPrice = 3100 -> -3000 + 3100 = +100.
            # Correct.
            
            perp_val = Decimal("0")
            
            # We need oracle prices for all products.
            # subaccount_info response *sometimes* includes oracle prices in `spot_products`/`perp_products`?
            # Looking at probe result: data['perp_products'] list contains 'oracle_price_x18'.
            # Yes! It's all there.
            
            prices = {}
            for p in data.get('perp_products', []):
                pid = int(p.get('product_id'))
                px = Decimal(p.get('oracle_price_x18', "0")) / Decimal(10**18)
                prices[pid] = px
                
            for p in data.get('perp_balances', []):
                pid = int(p.get('product_id'))
                amt = Decimal(p['balance']['amount']) / Decimal(10**18)
                v_quote = Decimal(p['balance']['v_quote_balance']) / Decimal(10**18)
                
                oracle_price = prices.get(pid, Decimal("0"))
                
                # Equity Contribution = v_quote + (amt * oracle)
                # Note: v_quote includes funding? 
                # Yes, v_quote is "virtual quote balance".
                
                term_val = v_quote + (amt * oracle_price)
                perp_val += term_val
                
            total_equity = spot_val + perp_val
            
            # --- Advanced Docs: Health & Liq Price ---
            # Health is already fetched in Step 1 (but not parsed completely)
            current_health = Decimal("0")
            if 'healths' in data:
                # Use health[0] (Maintenance) or health[1] (Initial)?
                # Usually we care about Maintenance for Liquidation.
                # data['healths'][0] is typically Maintenance.
                current_health = Decimal(data['healths'][0].get('health', 0)) / Decimal(10**18)
                
            # Calc Liq Price for the ACTIVE position (e.g. Product 4)
            # We need to know which product we are botting. 
            # The bot sets client.product_id.
            liq_price = 0
            active_pos = Decimal("0")
            
            # Find active position
            if self.client.product_id:
                for p in data.get('perp_balances', []):
                    if int(p.get('product_id')) == getattr(self.client, 'product_id', 4):
                        active_pos = Decimal(p['balance']['amount']) / Decimal(10**18)
                        break
            
            # Approx Liq Price = CurrentPrice - (Health / Position)
            # Only valid if position != 0
            if active_pos != 0 and self.client.product_id in prices:
                curr_px = prices[self.client.product_id]
                try:
                    liq_diff = current_health / active_pos
                    liq_price = float(curr_px - liq_diff)
                except:
                    liq_price = 0
            
            # 4. Update State
            if self.initial_equity is None:
                self.initial_equity = total_equity
                logger.info(f"Initialized Session Equity: {self.initial_equity:.2f} USDC")
                
            self.current_equity = total_equity
            self.session_pnl = self.current_equity - self.initial_equity
            self.current_health = current_health
            self.liq_price = liq_price
            self.active_pos = active_pos
            
            # 5. Return Stats
            stats = {
                "equity": float(self.current_equity),
                "pnl": float(self.session_pnl),
                "initial_equity": float(self.initial_equity),
                "roi_pct": float((self.session_pnl / self.initial_equity * 100) if self.initial_equity else 0),
                "volume": float(self.volume_traded),
                "duration_min": (time.time() - self.start_time) / 60,
                "health": float(current_health),
                "liq_price": float(liq_price),
                "active_pos": float(active_pos)
            }
            return stats
            
        except Exception as e:
            logger.error(f"PnL Update Error: {e}")
            return {}
            
    def add_volume(self, quantity_eth, price):
        """Accumulate traded volume."""
        val = Decimal(str(quantity_eth)) * Decimal(str(price))
        self.volume_traded += val
