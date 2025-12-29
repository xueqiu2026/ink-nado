"""
Nado Protocol 交易所客户端 (SDK 标准化版)
作用：负责与 Nado 交易所的所有交互，包括 REST API 请求、WebSocket 实时数据流、以及符合官方标准的 EIP-712 签名。
"""

import os
import asyncio
import json
import traceback
import time
import websockets
import aiohttp
from decimal import Decimal
from typing import Dict, Any, List, Optional, Tuple

from eth_account import Account
from eth_account.messages import encode_typed_data
from web3 import Web3

from .base import BaseExchangeClient, OrderResult, OrderInfo, query_retry
from helpers.logger import TradingLogger

class NadoClient(BaseExchangeClient):
    """Nado exchange client implementation."""

    def __init__(self, config: Dict[str, Any]):
        """Initialize Nado client."""
        super().__init__(config)

        # Nado credentials from environment
        self.private_key = os.getenv('NADO_PRIVATE_KEY')
        self.network = os.getenv('NADO_NETWORK', 'mainnet')
        
        if self.network == 'mainnet':
            # Updated based on user logs and verification
            self.gateway_url = "https://gateway.prod.nado.xyz/v1"
            self.ws_url = "wss://gateway.prod.nado.xyz/v1/ws"
            self.archive_url = "https://archive.prod.nado.xyz/v1"
            self.chain_id = 57073 # Ink Mainnet Chain ID
        else:
            self.gateway_url = "https://gateway.test.nado.xyz/v1"
            self.ws_url = "wss://gateway.test.nado.xyz/v1/ws"
            self.archive_url = "https://archive.test.nado.xyz/v1"
            self.chain_id = 763373 # Ink Sepolia Chain ID (Verify if changed)
        
        print(f"[NADO] Initialized with Network: {self.network}")
        print(f"[NADO] Gateway URL: {self.gateway_url}")

        if not self.private_key:
            raise ValueError("NADO_PRIVATE_KEY must be set in environment variables")

        self.logger = TradingLogger(exchange="nado", ticker=self.config.ticker, log_to_console=False)
        
        # Setup Account
        try:
             if self.private_key.startswith("0x"):
                 self.account = Account.from_key(self.private_key)
             else:
                 self.account = Account.from_key("0x" + self.private_key)
             self.wallet_address = self.account.address
             self.subaccount_name = os.getenv('NADO_SUBACCOUNT_NAME', 'default')
             print(f"[NADO] Subaccount: {self.subaccount_name}")
        except Exception as e:
            self.logger.log(f"Failed to initialize Nado Account: {e}", "ERROR")
            raise

        # Runtime cache
        self.product_id: Optional[int] = None
        self.verifying_contract: Optional[str] = None
        self.endpoint_addr: Optional[str] = None
        
        # Persistence & Control
        self._session = None
        self._ws_session = None
        self._ws_connection = None
        self._order_update_handler = None
        self._ws_task: Optional[asyncio.Task] = None
        self._ws_stop = asyncio.Event()

        # V3 Resilience: Position Cache & Anti-Glitch
        self._pos_cache: Optional[Decimal] = None
        self._zero_balance_strikes = 0
        self._STRIKE_THRESHOLD = 3


    def get_exchange_name(self) -> str:
        return "nado"

    def _validate_config(self) -> None:
        if not os.getenv('NADO_PRIVATE_KEY'):
            raise ValueError("Missing NADO_PRIVATE_KEY")

    # ---------------------------
    # Signing Helper (EIP-712)
    # ---------------------------
    
    def _get_verifying_contract(self, product_id: int) -> str:
        """Generate verifying contract address from product ID (20 bytes)."""
        # Logic from Nado docs: should use address(productId) i.e: 20 bytes hex of product ID
        # e.g. product 18 -> 0x00...12
        hex_val = hex(product_id)[2:]
        padded = hex_val.zfill(40) # 20 bytes = 40 hex chars
        return Web3.to_checksum_address("0x" + padded)

    def _sign_order(self, order_dict: Dict, product_id: int) -> str:
        """Sign order using EIP-712. 
        Audit V3: Always use 5-field struct (sender, priceX18, amount, expiration, nonce).
        The appendix is included in the JSON payload but EXCLUDED from the signature.
        """
        vc_address = self._get_verifying_contract(product_id)
        
        domain = {
            "name": "Nado", 
            "version": "0.0.1", 
            "chainId": int(self.chain_id), 
            "verifyingContract": vc_address
        }
        
        # Types definition - SDK Order Struct always has 6 fields
        types = {
            "Order": [
                {"name": "sender", "type": "bytes32"},
                {"name": "priceX18", "type": "int128"},
                {"name": "amount", "type": "int128"},
                {"name": "expiration", "type": "uint64"},
                {"name": "nonce", "type": "uint64"},
                {"name": "appendix", "type": "uint128"}
            ]
        }
        
        sign_message = {
            "sender": bytes.fromhex(order_dict["sender"][2:]),
            "priceX18": int(order_dict["priceX18"]),
            "amount": int(order_dict["amount"]),
            "expiration": int(order_dict["expiration"]),
            "nonce": int(order_dict["nonce"]),
            "appendix": int(order_dict.get("appendix", 0))
        }
        
        signed_message = Account.sign_typed_data(
            self.private_key, 
            domain_data=domain, 
            message_types=types, 
            message_data=sign_message
        )
        return "0x" + signed_message.signature.hex()

    def _subaccount_to_bytes32(self, address: str, name: str) -> str:
        """Generate subaccount ID bytes32.
        
        Format: 20 bytes address + 12 bytes subaccount name (ascii encoded, right-padded)
        Example: 0x798394ac59886caf0feb15cfcdc8f86ea7b0503064656661756c740000000000
                 ^ address (20 bytes = 40 hex chars) ^ "default" in hex (padded to 12 bytes = 24 hex)
        """
        # Remove 0x prefix from address
        addr_hex = address[2:] if address.startswith("0x") else address
        
        # Convert subaccount name to hex (ascii encoding)
        name_hex = name.encode('ascii').hex()
        
        # Pad name to 12 bytes (24 hex chars) - right pad with zeros
        name_hex_padded = name_hex.ljust(24, '0')
        
        # Combine: address (20 bytes) + name (12 bytes) = 32 bytes total
        # CRITICAL: Standardized to lowercase for consistency across signing and querying in V3.
        return addr_hex.lower() + name_hex_padded

    # ---------------------------
    # REST API Helpers
    # ---------------------------

    async def _post(self, endpoint: str, payload: Dict = None) -> Dict:
        """Helper for POST requests."""
        url = f"{self.gateway_url}{endpoint}"
        
        # Use persistent session if available (initialized in connect())
        # Fallback to a temporary one if called before connect
        session = self._session if self._session and not self._session.closed else aiohttp.ClientSession()
        
        try:
            headers = {
                "Content-Type": "application/json",
                "User-Agent": "Mozilla/5.0",
                "Origin": "https://app.nado.xyz"
            }
            
            data_str = json.dumps(payload, separators=(',', ':')) if payload else None
            self.logger.log(f"FORENSIC_PAYLOAD: {data_str}", "INFO")
            
            timeout = aiohttp.ClientTimeout(total=30)
            
            async with session.post(url, data=data_str, headers=headers, timeout=timeout) as resp:
                text = await resp.text()
                if resp.status != 200:
                    self.logger.log(f"API {endpoint} Reject {resp.status}: {text[:200]}", "ERROR")
                    raise ValueError(f"API Error {resp.status}: {text}")
                return await resp.json()
        except Exception as e:
            self.logger.log(f"Connection error (POST): {e}", "ERROR")
            raise
        finally:
            if session != self._session:
                await session.close()

    async def _archive_post(self, endpoint: str, payload: Dict = None) -> Dict:
        """Helper for Indexer/Archive POST requests."""
        url = f"{self.archive_url}{endpoint}"
        session = self._session if self._session and not self._session.closed else aiohttp.ClientSession()
        
        try:
            # Indexer requires gzip/br/deflate compatibility headers usually
            headers = {
                "Content-Type": "application/json",
                "User-Agent": "NadoBot/1.0",
                "Accept-Encoding": "gzip, deflate, br"
            }
            
            async with session.post(url, json=payload, headers=headers) as resp:
                if resp.status != 200:
                    return {}
                return await resp.json()
        except Exception as e:
            self.logger.log(f"Archive Request Failed: {e}", "ERROR")
            return {}
        finally:
            if session != self._session:
                await session.close()

    async def _get(self, endpoint: str) -> Dict:
        """Helper for GET requests."""
        url = f"{self.gateway_url}{endpoint}"
        session = self._session if self._session and not self._session.closed else aiohttp.ClientSession()
        
        try:
            headers = {"User-Agent": "NadoBot/1.0"}
            async with session.get(url, headers=headers) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    self.logger.log(f"API GET Error {resp.status} on {url}: {text[:200]}", "ERROR")
                    return {}
                return await resp.json()
        except Exception as e:
            self.logger.log(f"Connection error (GET): {e}", "ERROR")
            return {}
        finally:
            if session != self._session:
                await session.close()

    # ---------------------------
    # Core Trading Methods
    # ---------------------------

    async def get_contract_attributes(self) -> Tuple[str, Decimal]:
        """Fetch product info and gateway contracts from API."""
        try:
            # 1. Fetch all products
            res = await self._post("/query", {"type": "all_products"})
            data = res.get('data', {})
            products = data.get('spot_products', []) + data.get('perp_products', [])
            
            # 2. Fetch global contracts (Endpoint Address)
            # Required by SDK for non-order signing operations
            res_contracts = await self._post("/query", {"type": "contracts"})
            if res_contracts.get('status') == 'success':
                self.endpoint_addr = res_contracts['data']['endpoint_addr']
                self.logger.log(f"Initialized SDK Endpoint: {self.endpoint_addr}", "INFO")
            
            target_ticker = self.config.ticker.upper()
            # Normalize user ticker (ETH -> ETH-PERP)
            if not target_ticker.endswith("-PERP"):
                target_ticker += "-PERP"
                
            # Debug: Simply print what we found or fallback
            # We need to MATCH ticker to ID.
            # But the response usually doesn't have "ticker". It has "token" address.
            # We will use the Symbols query separately or hardcode based on the Dump.
            
            # For now, let's just log what we got and fallback to 4 (ETH-PERP assumption) or 1 (ETH-SPOT?)
            # after this function returns, the Verify script will dump the full JSON.
            # So here we just avoid crashing.
            
            self.logger.log(f"Fetched {len(products)} products.", "INFO")
            
            # Temporary: Allow fallback to 4 until we analyze the dump
            if not self.product_id:
                 self.product_id = 4
                 self.config.contract_id = "4"
                 self.config.tick_size = Decimal("0.1")

        except Exception as e:
            self.logger.log(f"Failed to fetch products/contracts: {e}. Using Hardcoded defaults.", "ERROR")
            self.product_id = 4
            self.endpoint_addr = "0x05ec92d78ed421f3d3ada77ffde167106565974e" # Ink Mainnet Backup
            
        return self.config.contract_id, self.config.tick_size

    async def get_available_pairs(self) -> List[Dict[str, Any]]:
        """Fetch all tradable symbols and their IDs."""
        try:
            res = await self._post("/query", {"type": "symbols"})
            data = res.get('data', {}).get('symbols', {})
            
            pairs = []
            for symbol, info in data.items():
                # Filter for PERP only
                if info.get('type') == 'perp':
                     pairs.append({
                         "symbol": symbol,
                         "id": int(info.get('product_id')),
                         "price_increment": info.get('price_increment_x18'),
                         "min_size": info.get('min_size')
                     })
            return sorted(pairs, key=lambda x: x['symbol'])
            
        except Exception as e:
            self.logger.log(f"Failed to fetch pairs: {e}", "ERROR")
            return []
            
    async def _get_execution_price(self, direction) -> Decimal:
        """Fetch current market price (Oracle/Mark)."""
        try:
            # CORRECT: Use /query {type: all_products} which contains oracle prices
            res = await self._post("/query", {"type": "all_products"})
            data = res.get('data', {})
            
            # Combine
            products = data.get('spot_products', []) + data.get('perp_products', [])
            
            # Default fallback if not found
            price = None
            
            for p in products:
                if int(p.get('product_id', 0)) == int(self.product_id):
                    # Oracle price is in oracle_price_x18
                    px18 = p.get('oracle_price_x18')
                    if px18:
                        price = Decimal(str(px18)) / Decimal(10**18)
                    break
            
            if price is None:
                raise ValueError(f"Product {self.product_id} not found in all_products")
                
            return price
            
        except Exception as e:
            self.logger.log(f"Price fetch failed ({e}).", "WARNING")
            raise e
            # return Decimal("3300") # UNSAFE: Removed to prevent bad orders

    async def get_depth(self, product_id: int = None) -> Dict:
        """Fetch Level 2 Orderbook Depth."""
        pid = product_id or self.product_id
        if not pid:
            raise ValueError("Product ID required for Depth")
            
        # Based on doc map: Query > Market > Depth?
        # CORRECT: "market_liquidity" (from nado_doc_map.md Line 128)
        try:
            payload = {
                "type": "market_liquidity",
                "product_id": pid,
                "depth": 5
            }
            res = await self._post("/query", payload)
            
            # Debug Log
            # self.logger.log(f"Depth Raw: {str(res)[:100]}", "DEBUG")

            # Case A: Standard Wrapper {data: {bids: ...}}
            data = res.get('data', {})
            if 'bids' in data:
                return data
            
            # Case B: Root Level {bids: ...} (As seen in probe)
            if 'bids' in res:
                return res
            
            # Case C: Empty/Error
            return {}
            
        except Exception as e:
            self.logger.log(f"Depth fetch failed: {e}", "ERROR")
            return {}



    def _build_appendix(self, 
                        is_reduce_only: bool = False, 
                        order_type: int = 0, # 0=Limit, 1=IOC, 2=FOK, 3=PostOnly
                        is_isolated: bool = False,
                        trigger_type: int = 0) -> int:
        """Construct order appendix bitmask from SDK specifications."""
        # Bits Layout: [Trigger(2)][Reduce(1)][Type(2)][Iso(1)][Ver(8)]
        # Ver: 0-7, Iso: 8, Type: 9-10, Reduce: 11, Trigger: 12-13
        res = 1 # Version 1
        
        if is_isolated:
            res |= (1 << 8)
            
        res |= (order_type << 9)
        
        if is_reduce_only:
            res |= (1 << 11)
            
        res |= (trigger_type << 12)
        
        return res

    async def place_open_order(self, contract_id: str, quantity: Decimal, direction: str, price: Decimal = None, order_type: int = 0) -> OrderResult:
        try:
            if price:
                current_price = price
            else:
                current_price = await self._get_execution_price(direction)
            
            self.logger.log(f"Placing order: Product={self.product_id}, Price={current_price}, Amount={quantity}, Dir={direction}, Type={order_type}", "INFO")

            # 1. Prepare Sender
            sender_str = self._subaccount_to_bytes32(self.wallet_address, self.subaccount_name)
            
            # 2. Prepare Timestamps/Nonce
            # Error 2011 Fix: Aggressive buffers.
            # Expiration: +1h (3600s) to be safe.
            # Nonce: +10s to compensate for local clock lag and network latency.
            now_sec = time.time()
            future_ms = int((now_sec + 3600) * 1000)
            nonce = (int((now_sec + 10) * 1000) << 20) + 12345
            
            # 3. Prepare Amounts (x18)
            price_x18 = int(Decimal(str(current_price)) * Decimal("1000000000000000000"))
            amount_x18 = int(Decimal(str(quantity)) * Decimal("1000000000000000000"))
            if direction == 'sell':
                amount_x18 = -amount_x18
                
            # 4. Build Appendix
            # Standard Limit Order: Type 0 (default)
            # Support IOC if order_type is passed
            appendix = self._build_appendix(is_reduce_only=False, order_type=order_type)

            # 5. Build Order Dict (for Signing)
            order_msg = {
                "sender": "0x" + sender_str, # Signing still needs 0x
                "priceX18": price_x18,
                "amount": amount_x18,
                "expiration": future_ms,
                "nonce": nonce,
                "appendix": appendix
            }
            
            # 6. Sign
            signature = self._sign_order(order_msg, self.product_id)
            
            # 7. Construct Payload (Strings for API)
            payload_order = {
                "sender": sender_str, # Payload does NOT want 0x
                "priceX18": str(price_x18),
                "amount": str(amount_x18),
                "expiration": str(future_ms),
                "nonce": str(nonce),
                "appendix": str(appendix)
            }
            
            tx_payload = {
                "place_orders": {
                    "orders": [{
                        "product_id": self.product_id,
                        "order": payload_order,
                        "signature": signature
                    }]
                }
            }
            
            # 8. Execute via REST
            # Note: We use existing _post helper which handles headers/session
            res = await self._post("/execute", tx_payload)
            
            # 9. Handle Response
            with open("debug_response.json", "w") as df:
                json.dump(res, df, indent=2)
                
            if res.get('status') == 'success':
                if res.get('data') and len(res['data']) > 0:
                    digest = res['data'][0].get('digest')
                    if digest:
                        self.logger.log(f"Order placed! Digest: {digest}", "INFO")
                        return OrderResult(success=True, order_id=digest)
                    else:
                         error_msg = f"Item Error: {res['data'][0].get('error', 'Unknown')}"
                else:
                    error_msg = f"Success but no data: {json.dumps(res)}"
            else:
                error_msg = res.get('error', 'Unknown error')
                
            self.logger.log(f"Order rejected: {error_msg} (Full: {res})", "ERROR")
            return OrderResult(success=False, error_message=str(error_msg))

        except Exception as e:
            self.logger.log(f"Place Order Failed: {e}", "ERROR")
            # traceback.print_exc()
            return OrderResult(success=False, error_message=str(e))

    async def place_market_order(self, contract_id: str, quantity: Decimal, direction: str) -> OrderResult:
        """
        Place a Market Order (IOC + Aggressive Price).
        Slippage: 5%
        """
        try:
            # 1. Get Base Price
            base_price = await self._get_execution_price(direction)
            
            # 2. Apply Slippage (Aggressive Re-pricing)
            if direction == 'buy':
                # Buy high to ensure fill
                exec_price = base_price * Decimal("1.05") 
            else:
                # Sell low to ensure fill
                exec_price = base_price * Decimal("0.95")
                
            # Round to tick
            exec_price = self.round_to_tick(exec_price)
            
            self.logger.log(f"MARKET {direction.upper()} {quantity} @ {exec_price} (IOC)", "INFO")
            
            # 3. Execute via place_open_order with IOC (Type 1)
            return await self.place_open_order(contract_id, quantity, direction, price=exec_price, order_type=1)
            
        except Exception as e:
             self.logger.log(f"Market Order Failed: {e}", "ERROR")
             return OrderResult(success=False, error_message=str(e))

    async def place_batch_open_orders(self, orders_data: List[Tuple[Decimal, str, Decimal]]) -> OrderResult:
        """
        Place multiple orders in a single atomic transaction.
        Args:
            orders_data: List of (quantity, direction, price) tuples.
                         price can be None to use current execution price (not recommended for batch).
        """
        try:
            self.logger.log(f"Placing BATCH of {len(orders_data)} orders", "INFO")
            
            # Common Data
            sender_str = self._subaccount_to_bytes32(self.wallet_address, self.subaccount_name)
            now_sec = time.time()
            future_ms = int((now_sec + 3600) * 1000)
            
            batched_orders_payload = []
            
            for i, (quantity, direction, price) in enumerate(orders_data):
                # 1. Price/Amount Prep
                if price is None:
                    # Fetching price sequentially in batch is slow, better to pass it in.
                    price = await self._get_execution_price(direction)
                
                price_x18 = int(Decimal(str(price)) * Decimal("1000000000000000000"))
                amount_x18 = int(Decimal(str(quantity)) * Decimal("1000000000000000000"))
                if direction == 'sell':
                    amount_x18 = -amount_x18
                
                # 2. Nonce (Increment for each order to be safe)
                nonce = (int((now_sec + 10 + i) * 1000) << 20) + 12345
                
                # 3. Appendix
                appendix = self._build_appendix(is_reduce_only=False, order_type=0)
                
                # 4. Message for Signing
                order_msg = {
                    "sender": "0x" + sender_str,
                    "priceX18": price_x18,
                    "amount": amount_x18,
                    "expiration": future_ms,
                    "nonce": nonce,
                    "appendix": appendix
                }
                
                # 5. Sign
                signature = self._sign_order(order_msg, self.product_id)
                
                # 6. Payload Construction
                payload_order = {
                    "sender": sender_str,
                    "priceX18": str(price_x18),
                    "amount": str(amount_x18),
                    "expiration": str(future_ms),
                    "nonce": str(nonce),
                    "appendix": str(appendix)
                }
                
                batched_orders_payload.append({
                    "product_id": self.product_id,
                    "order": payload_order,
                    "signature": signature
                })
            
            # 7. Final Bundle
            tx_payload = {
                "place_orders": {
                    "orders": batched_orders_payload
                }
            }
            
            # 8. Execute
            res = await self._post("/execute", tx_payload)
            
            if res.get('status') == 'success':
                digests = [o.get('digest') for o in res.get('data', [])]
                self.logger.log(f"Batch Success! Digests: {digests}", "INFO")
                return OrderResult(success=True, order_id=str(digests))
            else:
                error_msg = res.get('error', 'Unknown error')
                self.logger.log(f"Batch Rejected: {error_msg} (Full: {res})", "ERROR")
                return OrderResult(success=False, error_message=str(error_msg))

        except Exception as e:
            self.logger.log(f"Batch Order Failed: {e}", "ERROR")
            return OrderResult(success=False, error_message=str(e))

    def _sign_cancellation(self, cancellation_dict: Dict) -> str:
        """Sign cancellation using EIP-712.
        
        Note: Unlike Order placement which uses product-specific verifying contracts,
        all other execute operations (Cancel, Withdraw, etc.) use the global Endpoint address.
        """
        vc_address = self.endpoint_addr if self.endpoint_addr else self._get_verifying_contract(0)
        
        domain = {
            "name": "Nado", 
            "version": "0.0.1", 
            "chainId": int(self.chain_id),
            "verifyingContract": vc_address
        }
        
        types = {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"}
            ],
            "Cancellation": [
                {"name": "sender", "type": "bytes32"},
                {"name": "productIds", "type": "uint32[]"},
                {"name": "digests", "type": "bytes32[]"},
                {"name": "nonce", "type": "uint64"}
            ]
        }
        
        # Ensure types
        def to_bytes32(val):
            if isinstance(val, str):
                clean = val[2:] if val.startswith("0x") else val
                return bytes.fromhex(clean)
            return val

        msg = {
            "sender": to_bytes32(cancellation_dict["sender"]),
            "productIds": [int(p) for p in cancellation_dict["productIds"]],
            "digests": [to_bytes32(d) for d in cancellation_dict["digests"]],
            "nonce": int(cancellation_dict["nonce"])
        }
        
        signed = Account.sign_typed_data(
            self.private_key, 
            domain_data=domain, 
            message_types={"Cancellation": types["Cancellation"]}, 
            message_data=msg
        )
        return "0x" + signed.signature.hex()

    async def cancel_orders(self, digests: List[str], product_ids: List[int] = None) -> OrderResult:
        """Batch cancel orders, optionally from different products."""
        if not digests:
            return OrderResult(success=True)
            
        try:
            self.logger.log(f"Cancelling {len(digests)} orders...", "INFO")
            
            sender_str = self._subaccount_to_bytes32(self.wallet_address, self.subaccount_name)
            now_sec = time.time()
            nonce = (int((now_sec + 20) * 1000) << 20) + 12345
            
            # Group by product
            if not product_ids:
                 p_ids = [self.product_id] * len(digests)
            else:
                 p_ids = product_ids
            
            cancellation = {
                "sender": "0x" + sender_str,
                "productIds": p_ids,
                "digests": digests,
                "nonce": nonce
            }
            
            signature = self._sign_cancellation(cancellation)
            
            payload = {
                "tx": {
                    "sender": sender_str,
                    "productIds": p_ids,
                    "digests": [d[2:] if d.startswith("0x") else d for d in digests],
                    "nonce": str(nonce)
                },
                "signature": signature
            }
            
            tx_payload = {
                "cancel_orders": payload
            }
            
            res = await self._post("/execute", tx_payload)
            
            if res.get('status') == 'success':
                 self.logger.log("Cancellation Success!", "INFO")
                 return OrderResult(success=True)
            else:
                 error = res.get('error', 'Unknown')
                 self.logger.log(f"Cancel Failed: {error}", "ERROR")
                 return OrderResult(success=False, error_message=str(error))
                 
        except Exception as e:
            self.logger.log(f"Cancel Exception: {e}", "ERROR")
            return OrderResult(success=False, error_message=str(e))

    async def cancel_all_orders(self, contract_id: Optional[str] = None) -> OrderResult:
        """Cancel all orders for a specific product or all products for the subaccount if None."""
        try:
            target_ids = [contract_id] if contract_id else []
            
            # If no target_id, we need to find what products have orders
            # Robust way: query subaccount_orders with product_id 0 (if supported) or scan all known products
            # Better way: and most reliable - use the info we have or a list of common ones.
            # But wait, Nado/Vertex usually allows querying with Product 0 to get some info.
            # Let's use get_active_orders logic but modified.
            
            if not target_ids:
                self.logger.log("🔍 [Safety] Identifying active orders across all products...", "INFO")
                # Instead of a complex scan, we query the global 'subaccount_info' to find products with orders
                sender = self._subaccount_to_bytes32(self.wallet_address, self.subaccount_name)
                res = await self._post("/query", {"type": "subaccount_info", "subaccount": "0x" + sender})
                
                # Nado usually has list of orders in subaccount_info if depth is requested?
                # No, they have open_interest. 
                # Better: Scan known product range [1-100] is too slow.
                # Optimized Scan: ETH (4), BTC (2), SOL (34)
                all_found_orders = []
                for pid in [4, 2, 34]:
                    ords = await self.get_active_orders(str(pid))
                    all_found_orders.extend(ords)
                
                if not all_found_orders:
                    self.logger.log("✅ No orders found across checked products.", "INFO")
                    return OrderResult(success=True)
                
                digests = [o.order_id for o in all_found_orders]
                p_ids = [self.product_id] * len(digests) # Need to map correctly but for now...
                # Extract pids from OrderInfo if we had them. OrderInfo doesn't have pid yet.
                # Let's just batch cancel.
                return await self.cancel_orders(digests)
            else:
                orders = await self.get_active_orders(target_ids[0])
                if not orders:
                    return OrderResult(success=True)
                
                digests = [o.order_id for o in orders]
                return await self.cancel_orders(digests)
                
        except Exception as e:
            self.logger.log(f"Global Cancel All Failed: {e}", "ERROR")
            return OrderResult(success=False, error_message=str(e))

    async def cancel_order(self, order_id: str) -> OrderResult:
        return await self.cancel_orders([order_id])

    async def get_active_orders(self, contract_id: str) -> List[OrderInfo]:
        """Fetch active orders for product."""
        try:
            sender = self._subaccount_to_bytes32(self.wallet_address, self.subaccount_name)
            payload = {
                "type": "subaccount_orders",
                "sender": sender,
                "product_id": int(contract_id) if contract_id else self.product_id
            }
            res = await self._post("/query", payload)
            # self.logger.log(f"DEBUG: subaccount_orders raw res: {res}", "DEBUG")
            data = res.get('data', {}) if 'data' in res else res
            
            # If for some reason 'orders' is not present but data is a list?
            # Or if it's nested differently.
            orders_list = data.get('orders', [])
            if not orders_list and isinstance(data, list):
                orders_list = data
                
            self.logger.log(f"🔍 get_active_orders: 获取到 {len(orders_list)} 条原始订单数据", "INFO")
                
            orders = []
            for o in orders_list:
                try:
                    # Nado API returns { "order": { "amount": ..., "priceX18": ... }, "digest": "..." }
                    details = o.get('order', o)
                    amount_str = details.get('amount', o.get('amount'))
                    price_str = details.get('priceX18', details.get('price_x18', o.get('priceX18', o.get('price_x18'))))
                    digest = o.get('digest', details.get('digest'))
                    
                    if amount_str is None or price_str is None:
                        self.logger.log(f"Skipping order due to missing amount or price. Data: {o}", "WARNING")
                        continue
                        
                    amount = int(amount_str)
                    price_x18 = int(price_str)
                    
                    orders.append(OrderInfo(
                        order_id=digest,
                        side="buy" if amount > 0 else "sell",
                        size=Decimal(str(abs(amount))) / Decimal(10**18),
                        price=Decimal(str(price_x18)) / Decimal(10**18),
                        status="open",
                        filled_size=Decimal("0")
                    ))
                except Exception as e:
                    self.logger.log(f"Mapping error for order: {e} | Data: {o}", "ERROR")
                
            return orders
        except Exception as e:
            self.logger.log(f"Get Active Orders Failed: {e}", "ERROR")
            return []


    async def get_account_positions(self) -> Decimal:
        """Fetch current position size with Zero-Balance Glitch protection."""
        try:
            sender = self._subaccount_to_bytes32(self.wallet_address, self.subaccount_name)
            payload = {
                "type": "subaccount_info",
                "subaccount": sender
            }
            res = await self._post("/query", payload)
            
            # Handle response
            data = res.get('data', {})
            perp_balances = data.get('perp_balances', [])
            
            current_pos = Decimal("0")
            found = False
            for p in perp_balances:
                if int(p.get('product_id')) == int(self.product_id):
                    amt_x18 = Decimal(p['balance']['amount'])
                    current_pos = amt_x18 / Decimal(10**18)
                    found = True
                    break
            
            # --- Anti-Glitch Logic ---
            if found and current_pos == 0 and self._pos_cache is not None and self._pos_cache != 0:
                self._zero_balance_strikes += 1
                if self._zero_balance_strikes < self._STRIKE_THRESHOLD:
                    self.logger.log(f"⚠️ [ANTI-GLITCH] API reported 0 balance, but cache is {self._pos_cache}. Strike {self._zero_balance_strikes}/{self._STRIKE_THRESHOLD}. Holding state.", "WARNING")
                    return self._pos_cache
                else:
                    self.logger.log(f"🚨 [ANTI-GLITCH] Confirmed 0 balance after {self._STRIKE_THRESHOLD} strikes.", "INFO")
            
            if current_pos != 0 or not found:
                self._zero_balance_strikes = 0
                
            self._pos_cache = current_pos
            return current_pos
        except Exception as e:
            self.logger.log(f"Get Position Failed: {e}", "ERROR")
            return self._pos_cache if self._pos_cache is not None else Decimal("0")

    async def get_historical_trades(self, limit: int = 50) -> List[Dict]:
        """Fetch matches from official Indexer."""
        try:
            sender = self._subaccount_to_bytes32(self.wallet_address, self.subaccount_name)
            payload = {
                "type": "matches",
                "subaccount": sender,
                "limit": limit
            }
            res = await self._archive_post("/query", payload)
            
            matches = res.get('data', {}).get('matches', [])
            trades = []
            for m in matches:
                # Standardize SDK match to local trade format
                p_id = int(m.get('product_id', 0))
                amt = Decimal(m['amount']) / Decimal(10**18)
                px = Decimal(m['price']) / Decimal(10**18)
                ts = int(m.get('timestamp', 0))
                
                trades.append({
                    "ts": ts,
                    "time": datetime.fromtimestamp(ts).strftime("%H:%M:%S"),
                    "side": "buy" if amt > 0 else "sell",
                    "size": float(abs(amt)),
                    "price": float(px),
                    "product_id": p_id
                })
            return trades
        except Exception as e:
            self.logger.log(f"Get Historical Trades Failed: {e}", "ERROR")
            return []

    async def get_order_info(self, order_id: str) -> Optional[OrderInfo]:
        return None  # Placeholder implementation

    async def place_close_order(self, contract_id, quantity, price, side):
        return await self.place_open_order(contract_id, quantity, side)

    # ---------------------------
    # ---------------------------
    # WebSocket Implementation (aiohttp) - Optional
    # ---------------------------
    async def connect(self):
        """Establish WebSocket connection. Non-fatal if fails."""
        try:
            # Initialize persistent REST session for api_server compatibility and efficiency
            if not self._session or self._session.closed:
                self._session = aiohttp.ClientSession()

            # Use aiohttp to avoid 'websockets' library issues on Python 3.14
            headers = {
                "Origin": "https://app.nado.xyz",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Sec-WebSocket-Version": "13",
            }
            self.logger.log(f"Connecting to WS: {self.ws_url}", "INFO")
            
            self._ws_session = aiohttp.ClientSession()
            self._ws_connection = await self._ws_session.ws_connect(self.ws_url, headers=headers, heartbeat=20)
            
            self._ws_stop.clear()
            self.logger.log("WebSocket Connected (aiohttp)", "INFO")
            
            # Start heartbeat or listen loop if needed
            self._ws_task = asyncio.create_task(self._ws_loop())
            
        except Exception as e:
            self.logger.log(f"WS Connect Failed: {e}. Continuing in REST-only mode.", "WARNING")
            if self._ws_session:
                await self._ws_session.close()
            # DO NOT raise - allow REST-only operation

    async def _ws_loop(self):
        """Keep connection alive and listen."""
        try:
            while not self._ws_stop.is_set():
                if not self._ws_connection:
                    break
                try:
                    # Receive message with timeout
                    msg = await self._ws_connection.receive(timeout=25.0)
                    
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        # self.logger.log(f"WS Received: {msg.data[:100]}", "DEBUG")
                        pass # Process data here
                        
                    elif msg.type == aiohttp.WSMsgType.CLOSED:
                        self.logger.log("WS Closed", "WARNING")
                        break
                    elif msg.type == aiohttp.WSMsgType.ERROR:
                        self.logger.log("WS Error", "ERROR")
                        break
                        
                except asyncio.TimeoutError:
                    # Heartbeat handled by aiohttp usually, but we can verify aliveness
                    pass
                except Exception as e:
                    self.logger.log(f"WS Loop Error: {e}", "WARNING")
                    break
        except Exception as e:
            self.logger.log(f"WS Loop Crash: {e}", "ERROR")

    async def disconnect(self):
        self._ws_stop.set()
        if self._ws_connection:
            await self._ws_connection.close()
        if self._ws_session:
            await self._ws_session.close()
        if self._session:
            await self._session.close()
        if self._ws_task:
            self._ws_task.cancel()

    def setup_order_update_handler(self, handler):
        self._order_update_handler = handler
