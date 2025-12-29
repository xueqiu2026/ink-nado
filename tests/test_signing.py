"""
Final signature verification script with DISCOVERED parameters
"""
import os
from dotenv import load_dotenv
from eth_account import Account
from web3 import Web3

load_dotenv()

# Test account captured data
ORDER = {
    "sender": "0x29adc18f38f8057034ba398491f40d4fbe605c4464656661756c740000000000",
    "amount": "-1000000000000000",
    "expiration": "1766289664316",
    "nonce": "1852089040165143475",
    "priceX18": "2943900000000000000000"
}
# Expected Signature
FRONTEND_SIG = "0x2c2bcd09fc22adfb3c026ae52361d5fc84c1be92a569c4195e965b6095abf6100d5a56e42650525e2fbe72d9a29daf6aefdddfe3e2c12b1bcf959f2fd21307d51c"

PRODUCT_ID = 4
CHAIN_ID = 57073

def gen_vc(pid):
    return "0x" + pid.to_bytes(20, "big").hex()

def main():
    pk = os.getenv('NADO_PRIVATE_KEY')
    print(f"Testing with wallet: {Account.from_key(pk).address}")
    
    # CORRECT DOMAIN PARAMS found via Source Code Analysis
    domain = {
        "name": "Exchange", 
        "version": "1.0.0", 
        "chainId": CHAIN_ID, 
        "verifyingContract": Web3.to_checksum_address(gen_vc(PRODUCT_ID))
    }
    
    # CORRECT TYPES found via Source Code & Docs
    types = {
        "Order": [
            {"name": "sender", "type": "bytes32"},
            {"name": "priceX18", "type": "int128"},
            {"name": "amount", "type": "int128"},
            {"name": "expiration", "type": "uint64"},
            {"name": "nonce", "type": "uint64"}
        ]
    }
    
    # MESSAGE
    msg = {
        "sender": bytes.fromhex(ORDER["sender"][2:]),
        "priceX18": int(ORDER["priceX18"]),
        "amount": int(ORDER["amount"]),
        "expiration": int(ORDER["expiration"]),
        "nonce": int(ORDER["nonce"])
    }
    
    # SIGN
    signed = Account.sign_typed_data(pk, domain, {"Order": types["Order"]}, msg)
    our_sig = "0x" + signed.signature.hex()
    
    print(f"Frontend: {FRONTEND_SIG}")
    print(f"Ours:     {our_sig}")
    
    if our_sig.lower() == FRONTEND_SIG.lower():
        print("✅ MATCH! Signature logic is confirmed.")
    else:
        print("❌ MISMATCH. Still failing.")

if __name__ == "__main__":
    main()
