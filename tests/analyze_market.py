import asyncio
import time
import os
import aiohttp
from decimal import Decimal
import statistics

# Configuration
GATEWAY_URL = "https://gateway.prod.nado.xyz/v1"
PRODUCT_ID = 4  # ETH-PERP
ITERATIONS = 5

async def measure_latency(session):
    print("\n--- Network Latency Test (REST) ---")
    latencies = []
    for i in range(ITERATIONS):
        start = time.perf_counter()
        async with session.get(f"{GATEWAY_URL}/time") as resp:
            await resp.text()
        end = time.perf_counter()
        lat_ms = (end - start) * 1000
        latencies.append(lat_ms)
        print(f"Request {i+1}: {lat_ms:.2f} ms")
        await asyncio.sleep(0.5)
    
    avg_lat = statistics.mean(latencies)
    print(f"-> Average API Latency: {avg_lat:.2f} ms")
    return avg_lat

async def analyze_orderbook(session):
    print(f"\n--- Order Book Analysis (Product {PRODUCT_ID}) ---")
    # Fetch Order Book (Snapshot)
    # Endpoint might be /depth or via /query. Based on api_learning, /query -> all_products contains book info? No, we need Depth.
    # Nado typically uses specific endpoint for depth. Let's try likely candidates or fall back to /query.
    # checking nado.py to see how it gets depth... it doesn't currently.
    # Let's inspect products_list.json again to see if 'book_info' is real-time.
    # Actually, standard Nado API often has /depth?product_id=X
    
    # Strategy: Send a clearly invalid type to trigger the backend to list valid variants in the error message.
    url = f"{GATEWAY_URL}/query"
    params = {"type": "SHOW_ME_THE_TYPES", "product_id": PRODUCT_ID}
    
    print("Sending invalid query to discover valid types from error message...")
    try:
        async with session.post(url, json=params) as resp:
            text = await resp.text()
            print(f"Server Response ({resp.status}):")
            print("---------------------------------------------------")
            print(text) # Print EVERYTHING
            print("---------------------------------------------------")
            return

    except Exception as e:
        print(f"Error fetching types: {e}")
        return
    print(f"Depth Fetch Time: {(end-start)*1000:.2f} ms")
    
    # Analyze
    bids = data.get('bids', [])
    asks = data.get('asks', [])
    
    if not bids or not asks:
        print("Order book empty!")
        return

    best_bid = Decimal(bids[0]['price']) / 10**18
    best_ask = Decimal(asks[0]['price']) / 10**18
    mid_price = (best_bid + best_ask) / 2
    spread = best_ask - best_bid
    spread_pct = (spread / mid_price) * 100
    
    print(f"Best Bid: {best_bid:.2f}")
    print(f"Best Ask: {best_ask:.2f}")
    print(f"Spread:   {spread:.2f} ({spread_pct:.4f}%)")
    
    # Calculate Liquidity for 100 USDT, 500 USDT, 1000 USDT
    print("\n--- Impact Analysis (Slippage) ---")
    
    def calculate_slippage(orders, target_usdt, side_name):
        accumulated_vol = Decimal(0)
        weighted_price_sum = Decimal(0)
        last_price = Decimal(0)
        
        for level in orders:
            price = Decimal(level['price']) / 10**18
            # Size is likely X18 too? Let's assume so based on API convention
            # But wait, size is usually base asset amount (ETH).
            size_eth = Decimal(level['size']) / 10**18
            val_usdt = size_eth * price
            
            fill_val = min(val_usdt, target_usdt - accumulated_vol)
            weighted_price_sum += fill_val * price
            accumulated_vol += fill_val
            last_price = price
            
            if accumulated_vol >= target_usdt:
                break
                
        if accumulated_vol < target_usdt:
            print(f"WARNING: Not enough liquidity for {target_usdt} USDT {side_name}")
            return None
            
        avg_fill_price = weighted_price_sum / target_usdt
        slippage = abs(avg_fill_price - mid_price) / mid_price * 100
        print(f"Size {target_usdt:>4} USDT | Avg Price: {avg_fill_price:.2f} | Slippage: {slippage:.4f}% ({side_name})")

    for size in [100, 500, 1000, 5000]:
        calculate_slippage(asks, size, "Buy")
        calculate_slippage(bids, size, "Sell")

async def main():
    async with aiohttp.ClientSession() as session:
        await measure_latency(session)
        await analyze_orderbook(session)

if __name__ == "__main__":
    asyncio.run(main())
