import asyncio
import os
import time
from eth_account import Account
import aiohttp
from dotenv import load_dotenv

load_dotenv()

CHAIN_ID = 57073
GATEWAY_URL = "https://gateway.prod.nado.xyz/v1"

def to_bytes32(val: str) -> bytes:
    clean = val[2:] if val.startswith("0x") else val
    return bytes.fromhex(clean)

async def test_global_cancel():
    pk = os.getenv('NADO_PRIVATE_KEY')
    if not pk.startswith("0x"): pk = "0x" + pk
    account = Account.from_key(pk)
    address = account.address
    
    # 1. Get ghost digests
    sender = address[2:].lower() + "default".encode().hex()
    sender = sender.ljust(64, '0')
    
    # Check lowercase bucket
    async with aiohttp.ClientSession() as session:
        payload = {"type": "subaccount_orders", "sender": sender, "product_id": 4}
        async with session.post(f"{GATEWAY_URL}/query", json=payload) as resp:
            data = await resp.json()
            orders = data.get('data', {}).get('orders', [])
            if not orders:
                # Try uppercase
                sender = sender.upper()
                payload['sender'] = sender
                async with session.post(f"{GATEWAY_URL}/query", json=payload) as resp2:
                    data = await resp2.json()
                    orders = data.get('data', {}).get('orders', [])
        
        if not orders:
            print("No orders found to test cancellation.")
            return

        digest = orders[0].get('digest')
        print(f"Testing cancellation of: {digest} using product 0 domain...")

        now_sec = time.time()
        nonce = (int((now_sec + 30) * 1000) << 20) + 12345
        
        # Product 0 domain
        vc_address = "0x" + "0".zfill(40)
        domain = {
            "name": "Nado", "version": "0.0.1", "chainId": str(CHAIN_ID), "verifyingContract": vc_address
        }
        types = {
            "Cancellation": [
                {"name": "sender", "type": "bytes32"},
                {"name": "productIds", "type": "uint32[]"},
                {"name": "digests", "type": "bytes32[]"},
                {"name": "nonce", "type": "uint64"}
            ]
        }
        msg = {
            "sender": to_bytes32(sender), "productIds": [4], "digests": [to_bytes32(digest)], "nonce": nonce
        }
        
        signed = Account.sign_typed_data(pk, domain_data=domain, message_types={"Cancellation": types["Cancellation"]}, message_data=msg)
        
        tx_payload = {
            "cancel_orders": {
                "tx": {
                    "sender": sender, "productIds": [4], "digests": [digest[2:] if digest.startswith("0x") else digest], "nonce": str(nonce)
                },
                "signature": "0x" + signed.signature.hex()
            }
        }
        
        async with session.post(f"{GATEWAY_URL}/execute", json=tx_payload) as resp:
            res = await resp.json()
            print(f"Global Product 0 Cancellation Result: {res}")

if __name__ == "__main__":
    asyncio.run(test_global_cancel())
