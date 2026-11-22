"""Robinhood Chain meme-token scanner — core logic (stdlib + requests only).

Read-only. No wallet, no keys, no signing, no buy/sell.
Data sources (all free tier):
  - DexScreener public API (no key): new-token profiles + pair data.
  - GoPlus token_security for chain 4663 (no key): taxes, creator/dev hold,
    top holders, LP holders/lock state, open-source flag.
  - Sourcify v2 (no key): contract verification status for chain 4663.
  - Public Robinhood Chain RPC (rate-limited): eth_getCode + transfer
    simulation (honeypot proxy test). No archive RPC needed.
  - On-chain RPC (no key): sniper + bubble/insider from Transfer logs
    (eth_getLogs) — the source of truth for those two checks since
    2026-11-22 (GMGN free tier needs >=$100 account balance, so no key).
  - gmgn-cli (optional cross-check only): when a GMGN API key IS configured,
    GMGN sniper/bubble values are compared and the WORSE (higher) wins.
    Never scrape gmgn.ai web — it needs login and returns no structured data.

Strict thresholds (Mas Roy, 2026-11-22): a value PASSES only when it is
STRICTLY below the threshold (value < threshold). value >= threshold fails.
  dev hold 0% | tax 0% | top-10 holders < 10% | sniper < 8% |
  bubble/insider cluster < 8% | LP burn >= 90% or launchpad pons/trench.

Field names below come from LIVE API responses (DexScreener 2026-11-22,
GoPlus 4663 2026-11-22, Sourcify 4663 2026-11-22) and from the official
gmgn-token SKILL.md Response Field Reference. Nothing is guessed.
"""

import json
import os
import subprocess
import time
import urllib.request

import requests

CHAIN_ID = 4663
CHAIN_NAME = "robinhood"
RPC_URL = "https://rpc.mainnet.chain.robinhood.com"
DEX_API = "https://api.dexscreener.com"
GOPLUS_API = "https://api.gopluslabs.io/api/v1/token_security/4663"
SOURCIFY_API = "https://sourcify.dev/server/v2/contract/4663"

DEAD = "0x000000000000000000000000000000000000dead"
ZERO = "0x0000000000000000000000000000000000000000"

# Uniswap canonical addresses (same on every EVM chain) + Robinhood v4 manager
# (PoolManager address confirmed from live GoPlus dex data, 2026-11-22).
UNI_V2_FACTORY = "0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f"
UNI_V3_FACTORY = "0x1F98431c8aD98523631AE4a59f267346ea31F98431c"
UNI_V4_POOL_MANAGER = "0x8366a39cc670b4001a1121b8f6a443a643e40951"

# Addresses that hold supply but are NOT concentration: pools, lockers, burn.
POOL_LIKE_TAGS = {"", "pool"}
KNOWN_POOL_CONTRACTS = {UNI_V4_POOL_MANAGER.lower()}

HEADERS = {"User-Agent": "rh-scanner/1.0 (read-only)", "Accept": "application/json"}

# --- thresholds (strict: pass iff value < threshold) -----------------------
DEFAULTS = {
    "max_age_min": 25,
    "min_liq_usd": 3500.0,
    "min_buys_m5": 10,
    "max_dev_pct": 0.0,       # dev must hold effectively nothing (<0.01% dust tolerated)
    "dev_dust_pct": 0.01,
    "max_tax_pct": 0.0,       # buy/sell tax must be exactly 0
    "max_top10_pct": 10.0,    # strictly below 10
    "max_sniper_pct": 8.0,    # strictly below 8
    "max_bubble_pct": 8.0,    # strictly below 8
    "min_lp_burn_ratio": 0.90,
}
LAUNCHPAD_LP_OK = {"pons", "trench"}


# --- small helpers ---------------------------------------------------------
def _get(url, timeout=12):
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout)
        if r.status_code != 200:
            return False, f"HTTP {r.status_code}"
        return True, r.json()
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)[:120]


def _rpc(method, params, timeout=20):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method,
                       "params": params}).encode()
    req = urllib.request.Request(
        RPC_URL, body, {"content-type": "application/json",
                        "user-agent": "rh-scanner/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.load(resp)
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)[:120]
    if "error" in payload:
        return False, str(payload["error"].get("message", "rpc error"))[:120]
    return True, payload["result"]


def _is_hex_addr(a):
    return (isinstance(a, str) and len(a) == 42 and a.startswith("0x")
            and all(c in "0123456789abcdefABCDEF" for c in a[2:]))


def _lt(value, threshold):
    """Strict-below: True only when value is a number strictly < threshold."""
    return value is not None and value < threshold


def _now_wib():
    """Current time in WIB (UTC+7), independent of the host's timezone.

    Streamlit Cloud containers run on UTC, so time.localtime() would label
    a UTC clock reading as +07:00 (7 hours behind WIB). Compute WIB from
    UTC explicitly instead.
    """
    return time.strftime("%Y-%m-%dT%H:%M:%S+07:00",
                         time.gmtime(time.time() + 7 * 3600))


# --- DexScreener -----------------------------------------------------------
def fetch_new_profiles():
    """token-profiles/latest/v1 -> Robinhood token addresses (newest first).

    Returns (ok, addresses, note). Only tokens that HAVE a DexScreener profile
    appear here — that is a known free-API limitation, not a bug.
    """
    ok, data = _get(f"{DEX_API}/token-profiles/latest/v1", timeout=15)
    if not ok:
        return False, [], f"DexScreener profiles: {data}"
    rh = [p.get("tokenAddress") for p in data
          if p.get("chainId") == CHAIN_NAME and p.get("tokenAddress")]
    return True, rh, f"{len(rh)} profil Robinhood dari {len(data)} profil terbaru"


def fetch_pairs(addresses):
    """Batch /latest/dex/tokens/{csv} (max 30/call) -> Robinhood pairs."""
    pairs, notes = [], []
    for i in range(0, len(addresses), 30):
        chunk = ",".join(addresses[i:i + 30])
        ok, data = _get(f"{DEX_API}/latest/dex/tokens/{chunk}", timeout=15)
        if not ok:
            notes.append(f"DexScreener tokens: {data}")
            continue
        for p in data.get("pairs", []) or []:
            if p.get("chainId") == CHAIN_NAME:
                pairs.append(p)
    return pairs, notes


def search_symbol(symbol):
    """DexScreener search — used by the copycat check (same symbol, other CA)."""
    ok, data = _get(f"{DEX_API}/latest/dex/search?q={symbol}", timeout=12)
    if not ok:
        return []
    out = []
    for p in data.get("pairs", []) or []:
        if p.get("chainId") == CHAIN_NAME:
            bt = p.get("baseToken", {}) or {}
            out.append((bt.get("address", "").lower(), bt.get("symbol", "")))
    return out


# --- GoPlus (chain 4663, no key) -------------------------------------------
def fetch_goplus(address):
    """Returns (ok, normalized dict, note). Field names verified live 2026-11-22.

    Real fields: buy_tax/sell_tax/transfer_tax (decimal strings, "0" = 0%),
    cannot_buy ("0"/"1"), creator_address/creator_balance/creator_percent,
    honeypot_with_same_creator, is_in_dex, is_open_source ("0"/"1"),
    holder_count, holders[{address,tag,is_contract,balance,percent,is_locked}],
    lp_holder_count, lp_holders[{address,percent,NFT_list,is_locked}],
    lp_total_supply, owner_address ("" = no owner), token_name/token_symbol,
    total_supply, dex[{liquidity_type,name,pool_manager,pair}].
    NOTE: no is_honeypot field is returned for 4663 — honeypot is judged by
    cannot_buy + tax + the RPC transfer simulation below.
    """
    ok, data = _get(f"{GOPLUS_API}?contract_addresses={address.lower()}", timeout=15)
    if not ok:
        return False, {}, f"GoPlus: {data}"
    res = (data.get("result") or {}).get(address.lower())
    if not res:
        return False, {}, "GoPlus: token tidak ditemukan"
    return True, res, "ok"


def _fnum(v, default=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def goplus_top10_pct(gp):
    """Top-10 holder % EXCLUDING pools/lockers/burn/PoolManager.

    GoPlus `percent` is a 0-1 ratio. Holders tagged as contracts that are the
    v4 PoolManager (or any known pool contract) are skipped, as is the burn
    address. Returns (pct_or_None, holders_used).
    """
    holders = gp.get("holders") or []
    if not holders:
        return None, 0
    pool_like = KNOWN_POOL_CONTRACTS | {DEAD.lower(), ZERO.lower()}
    scored = []
    for h in holders:
        addr = (h.get("address") or "").lower()
        if addr in pool_like:
            continue
        if h.get("is_contract") == 1 and addr == UNI_V4_POOL_MANAGER.lower():
            continue
        pct = _fnum(h.get("percent"))
        if pct is not None:
            scored.append(pct * 100.0)
    scored.sort(reverse=True)
    if not scored:
        return None, 0
    return round(sum(scored[:10]), 4), min(len(scored), 10)


def goplus_lp_state(gp):
    """LP lock state from lp_holders. Returns dict with is_locked/burned/unknown."""
    lps = gp.get("lp_holders") or []
    if not lps:
        return {"state": "unknown", "detail": "tidak ada data lp_holders"}
    for lp in lps:
        addr = (lp.get("address") or "").lower()
        if addr == DEAD.lower():
            return {"state": "burned", "detail": "LP dibakar ke dead address"}
        if str(lp.get("is_locked")) == "1":
            return {"state": "locked", "detail": "LP terkunci (is_locked=1)"}
    return {"state": "unlocked",
            "detail": f"{len(lps)} pemegang LP, tidak ada yang terkunci/dibakar"}


# --- Sourcify (chain 4663, no key) -----------------------------------------
def fetch_sourcify(address):
    ok, data = _get(f"{SOURCIFY_API}/{address.lower()}", timeout=12)
    if not ok:
        # Sourcify v2 returns HTTP 404 for an unverified contract — that is a
        # valid answer ("not verified"), not a transport failure.
        if data == "HTTP 404":
            return True, None, "unverified (Sourcify 404)"
        return False, None, f"Sourcify: {data}"
    match = data.get("match")
    return True, match, f"match={match}"


# --- RPC: code check + transfer simulation ---------------------------------
def rpc_has_code(address):
    ok, res = _rpc("eth_getCode", [address, "latest"])
    if not ok:
        return False, f"RPC: {res}"
    return True, (res not in ("0x", "0x0", ""))


def _keccak_selector(sig):
    """Minimal keccak for the transfer(address,uint256) selector.

    transfer(address,uint256) = 0xa9059cbb (well-known constant, verified
    against the BSC skill selector table). No hashing needed here.
    """
    return "a9059cbb"


def rpc_simulate_transfer(token, holder, amount_wei=1):
    """eth_call token.transfer(dead, 1 wei) FROM a real holder.

    A successful call means that holder CAN transfer (no transfer gate).
    A revert means transfers are blocked for that holder — honeypot-like.
    No state changes: eth_call never writes. Returns (ok, note).
    """
    if not _is_hex_addr(holder) or not _is_hex_addr(token):
        return False, "alamat holder/token tidak valid"
    data = "0x" + _keccak_selector("transfer(address,uint256)") \
        + DEAD[2:].rjust(64, "0") + format(amount_wei, "x").rjust(64, "0")
    ok, res = _rpc("eth_call", [{"from": holder, "to": token, "data": data},
                                "latest"])
    if not ok:
        return False, f"eth_call revert/gagal: {res}"
    return True, "transfer dari holder berhasil (simulasi)"


# --- GMGN via gmgn-cli (optional; needs API key) ---------------------------
_GMGN_CHECK = None  # None=unchecked, True=configured, False=missing/unconfigured


def gmgn_status():
    """Runs `gmgn-cli config --check` ONCE per process. Returns (ready, note).

    The CLI needs GMGN_API_KEY in ~/.config/gmgn/.env (free plan exists:
    5 req/s leaky bucket). This function never asks for a key and never
    prints one. If not ready, all GMGN-only checks report UNKNOWN.
    """
    global _GMGN_CHECK
    if _GMGN_CHECK is not None:
        return _GMGN_CHECK
    try:
        proc = subprocess.run(["gmgn-cli", "config", "--check"],
                              capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        _GMGN_CHECK = (False, "gmgn-cli belum terinstal")
        return _GMGN_CHECK
    except Exception as exc:  # noqa: BLE001
        _GMGN_CHECK = (False, f"gmgn-cli check gagal: {exc}"[:100])
        return _GMGN_CHECK
    if proc.returncode == 0:
        _GMGN_CHECK = (True, "gmgn-cli terkonfigurasi")
    else:
        _GMGN_CHECK = (False, "gmgn-cli butuh API key (GMGN_API_KEY belum diset)")
    return _GMGN_CHECK


def gmgn_call(sub, address, extra=None):
    """Run one gmgn-cli token sub-command --chain robinhood --raw.

    Returns (ok, parsed_json, note). Field names used downstream are ONLY
    those in the official gmgn-token SKILL.md Response Field Reference.
    """
    ready, note = gmgn_status()
    if not ready:
        return False, {}, note
    if not _is_hex_addr(address):
        return False, {}, "alamat token tidak valid"
    cmd = ["gmgn-cli", "token", sub, "--chain", "robinhood",
           "--address", address, "--raw"] + (extra or [])
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except Exception as exc:  # noqa: BLE001
        return False, {}, f"gmgn-cli gagal jalan: {exc}"[:100]
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "")[:150]
        return False, {}, f"gmgn-cli exit {proc.returncode}: {err}"
    try:
        return True, json.loads(proc.stdout), "ok"
    except Exception:
        return False, {}, "gmgn-cli: output bukan JSON"


def gmgn_fetch_all(address):
    """token info + security (+ holders --tag sniper/dev) for one token.

    Verified field mapping (gmgn-token SKILL.md):
      info.dev.creator_token_status: hold/sell
      info.stat.creator_hold_rate (0-1), info.stat.top_10_holder_rate (0-1),
      info.stat.top_rat_trader_percentage, info.stat.top_bundler_trader_percentage,
      info.stat.fresh_wallet_rate, info.wallet_tags_stat.smart_wallets,
      info.wallet_tags_stat.sniper_wallets, info.launchpad, info.locked_ratio,
      info.link.twitter_username/website/description
      security.is_honeypot ("yes"/"no" — documented BSC/Base only; may be
        absent on robinhood), security.buy_tax/sell_tax (0.03 = 3%),
      security.open_source, security.owner_renounced ("yes"/"no"/"unknown"),
      security.top_10_holder_rate, security.creator_balance_rate,
      security.rug_ratio (0-1), security.rat_trader_amount_rate,
      security.bundler_trader_amount_rate, security.sniper_count,
      security.burn_status ("burn")
      holders --tag sniper: list[].amount_percentage (0-1 ratio) -> sniper %
      holders --tag dev: creator wallets still holding.
    """
    out = {"ok": False, "info": {}, "security": {}, "sniper_pct": None,
           "sniper_wallets": 0, "dev_hold_wallets": [], "note": ""}
    ok, info, note = gmgn_call("info", address)
    if not ok:
        out["note"] = f"info: {note}"
        return out
    out["info"] = info if isinstance(info, dict) else {}
    ok, sec, note = gmgn_call("security", address)
    if ok and isinstance(sec, dict):
        out["security"] = sec
    ok, snip, note = gmgn_call("holders", address,
                               ["--tag", "sniper", "--limit", "100"])
    if ok and isinstance(snip, dict):
        lst = snip.get("list") or []
        out["sniper_wallets"] = len(lst)
        tot = sum(_fnum(w.get("amount_percentage"), 0) or 0 for w in lst)
        out["sniper_pct"] = round(tot * 100.0, 4)
    out["ok"] = True
    out["note"] = "ok"
    return out


# --- On-chain sniper + bubble (RPC only, no GMGN key needed) --------------
# Replaces the GMGN-only sniper/bubble checks. GMGN (when a key is ever
# configured) is kept only as a cross-check: the WORSE of on-chain vs GMGN
# wins (fail-closed). Verified live 2026-11-22:
#   - Blockscout REST API is Cloudflare-walled (even with a browser UA).
#   - Public RPC works (eth_blockNumber/eth_getLogs) but needs a User-Agent
#     (urllib default UA gets HTTP 403) and is rate-limited.
#   - Block time ~0.113 s (measured over 1000 blocks).
#   - GoPlus `holders` returns only the TOP 10 holders — current holdings of
#     wallets outside it are resolved with eth_call balanceOf (capped), and
#     the ignored remainder is bounded (see _resolve_current).
#   - MINTGOAT's pair is Uniswap v4 (labels ["v4"]) — v4 swaps move tokens
#     through the PoolManager, so buys are Transfer events with
#     from == pair OR from == PoolManager.

def _keccak_f1600(state):
    RC = [0x0000000000000001, 0x0000000000008082, 0x800000000000808A,
          0x8000000080008000, 0x000000000000808B, 0x0000000080000001,
          0x8000000080008081, 0x8000000000008009, 0x000000000000008A,
          0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
          0x000000008000808B, 0x800000000000008B, 0x8000000000008089,
          0x8000000000008003, 0x8000000000008002, 0x8000000000000080,
          0x000000000000800A, 0x800000008000000A, 0x8000000080008081,
          0x8000000000008080, 0x0000000080000001, 0x8000000080008008]
    R = [[0, 36, 3, 41, 18], [1, 44, 10, 45, 2], [62, 6, 43, 15, 61],
         [28, 55, 25, 21, 56], [27, 20, 39, 8, 14]]
    MASK = (1 << 64) - 1

    def rol(x, n):
        return ((x << n) | (x >> (64 - n))) & MASK if n else x

    for rnd in range(24):
        c = [state[x] ^ state[x + 5] ^ state[x + 10] ^ state[x + 15]
             ^ state[x + 20] for x in range(5)]
        d = [c[(x - 1) % 5] ^ rol(c[(x + 1) % 5], 1) for x in range(5)]
        for x in range(5):
            for y in range(5):
                state[x + 5 * y] ^= d[x]
        b = [0] * 25
        for x in range(5):
            for y in range(5):
                b[y + 5 * ((2 * x + 3 * y) % 5)] = rol(state[x + 5 * y], R[x][y])
        for x in range(5):
            for y in range(5):
                state[x + 5 * y] = (b[x + 5 * y]
                                    ^ ((~b[(x + 1) % 5 + 5 * y])
                                       & b[(x + 2) % 5 + 5 * y]))
        state[0] ^= RC[rnd]
    return state


def _keccak256(data: bytes) -> bytes:
    """Keccak-256 (pre-NIST, as Ethereum uses). stdlib-only.

    Self-checked at import against the well-known Transfer topic below.
    """
    rate = 136  # bytes for Keccak-256
    state = [0] * 25
    off = 0
    while off + rate <= len(data):
        block = data[off:off + rate]
        for i in range(rate // 8):
            state[i] ^= int.from_bytes(block[i * 8:(i + 1) * 8], "little")
        _keccak_f1600(state)
        off += rate
    rem = data[off:]
    buf = bytearray(rem) + bytes([0x01]) + bytes(rate - len(rem) - 1)
    buf[-1] |= 0x80
    for i in range(rate // 8):
        state[i] ^= int.from_bytes(buf[i * 8:(i + 1) * 8], "little")
    _keccak_f1600(state)
    out = b""
    for i in range(4):
        out += state[i].to_bytes(8, "little")
    return out


TRANSFER_TOPIC = "0x" + _keccak256(b"Transfer(address,address,uint256)").hex()
PAIR_CREATED_TOPIC = "0x" + _keccak256(
    b"PairCreated(address,address,address,uint256)").hex()
POOL_CREATED_TOPIC = "0x" + _keccak256(
    b"PoolCreated(address,address,uint24,int24,address)").hex()
# Guard: if this ever fails, the keccak implementation regressed — every
# on-chain check below would be wrong, so fail loudly at import.
assert TRANSFER_TOPIC == (
    "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"), \
    "keccak256 broken: Transfer topic mismatch"

# Sniper window: buys from the pair in [creation-60, creation+40] blocks.
# The -60 buffer absorbs pair-creation-block estimation error (buys cannot
# happen before creation, so it is harmless); +40 covers a late estimate.
# First-block buyers (<= creation+10) are the bot-scale cohort.
ONCHAIN_SNIPER_BUFFER = 60
ONCHAIN_SNIPER_END = 40
ONCHAIN_FIRST_BLOCK_END = 10
ONCHAIN_BUBBLE_WINDOW_SEC = 60
ONCHAIN_LOG_CHUNK = 300
ONCHAIN_MAX_LOGS = 10000
ONCHAIN_MAX_NONCE_CHECKS = 5
ONCHAIN_MAX_BALANCE_CALLS = 10
ONCHAIN_FRESH_NONCE_MAX = 3
ONCHAIN_MATERIAL_BOUGHT_PCT = 0.1   # balanceOf a missing wallet if it bought >= this
ONCHAIN_IGNORED_MAX_PCT = 1.0       # bound on ignored (immaterial) holdings
ONCHAIN_PAIR_LOOKUP_RANGE = 20000   # blocks back for factory event lookup

_BLOCK_TIME_CACHE = {"value": None, "at": 0.0}


def _pad_addr(addr):
    """0x-address -> 32-byte topic hex (0x + 64 chars)."""
    return "0x" + addr[2:].rjust(64, "0")


def _topic_addr(topic):
    """Last 40 hex chars of a 32-byte topic -> lowercase address, or None."""
    if not isinstance(topic, str) or len(topic) < 42:
        return None
    return ("0x" + topic[-40:]).lower()


def _chain_head():
    """(latest_block, latest_ts, block_time_seconds) or (None, None, None).

    Block time is measured over the last 1000 blocks and cached 10 minutes
    (chain-wide, stable on this chain).
    """
    now = time.time()
    ok, latest = _rpc("eth_blockNumber", [])
    if not ok:
        return None, None, None
    try:
        ln = int(latest, 16)
    except (TypeError, ValueError):
        return None, None, None
    ok, blk = _rpc("eth_getBlockByNumber", [hex(ln), False])
    if not ok:
        return None, None, None
    try:
        ts = int(blk["timestamp"], 16)
    except (TypeError, ValueError, KeyError):
        return None, None, None
    bt = _BLOCK_TIME_CACHE["value"]
    if not bt or now - _BLOCK_TIME_CACHE["at"] > 600:
        back = max(ln - 1000, 0)
        ok2, blk2 = _rpc("eth_getBlockByNumber", [hex(back), False])
        try:
            t2 = int(blk2["timestamp"], 16) if ok2 else None
            n = ln - back
            bt = (ts - t2) / n if (t2 is not None and n and ts > t2) else None
        except (TypeError, ValueError, KeyError):
            bt = None
        if not bt or bt <= 0 or bt > 30:
            bt = None
        else:
            _BLOCK_TIME_CACHE.update({"value": bt, "at": now})
    return ln, ts, bt


def _rpc_block_ts(block):
    ok, res = _rpc("eth_getBlockByNumber", [hex(block), False])
    if not ok:
        return None
    try:
        return int(res["timestamp"], 16)
    except (TypeError, ValueError, KeyError):
        return None


def _rpc_nonce(address):
    ok, res = _rpc("eth_getTransactionCount", [address, "latest"])
    if not ok:
        return None
    try:
        return int(res, 16)
    except (TypeError, ValueError):
        return None


def _rpc_balance_of(token, wallet):
    data = "0x70a08231" + wallet[2:].rjust(64, "0")
    ok, res = _rpc("eth_call", [{"to": token, "data": data}, "latest"])
    if not ok or not isinstance(res, str):
        return None
    try:
        return int(res, 16)
    except (TypeError, ValueError):
        return None


def _rpc_total_supply(token):
    ok, res = _rpc("eth_call", [{"to": token, "data": "0x18160ddd"},
                                "latest"])
    if not ok or not isinstance(res, str):
        return None
    try:
        return int(res, 16)
    except (TypeError, ValueError):
        return None


def _rpc_logs(address, topics, from_block, to_block, chunk=ONCHAIN_LOG_CHUNK):
    """eth_getLogs in chunks. Returns (ok, logs, note).

    On a range/size error the chunk is halved and retried; on a rate-limit
    error it waits 2 s and retries the same chunk once. Any other failure
    returns (False, [], reason) — the caller reports UNKNOWN, never a guess.
    """
    logs = []
    start = max(from_block, 0)
    guard = 0
    while start <= to_block:
        guard += 1
        if guard > 60:
            return False, [], "terlalu banyak chunk log"
        end = min(start + chunk - 1, to_block)
        ok, res = _rpc("eth_getLogs", [{"fromBlock": hex(start),
                                        "toBlock": hex(end),
                                        "address": address,
                                        "topics": topics}])
        if not ok:
            msg = str(res).lower()
            if ("rate" in msg or "429" in msg or "too many" in msg) \
                    and chunk == ONCHAIN_LOG_CHUNK:
                # rate limit: wait once, retry the same chunk as-is
                time.sleep(2)
                ok2, res2 = _rpc("eth_getLogs", [{"fromBlock": hex(start),
                                                  "toBlock": hex(end),
                                                  "address": address,
                                                  "topics": topics}])
                if ok2 and isinstance(res2, list):
                    logs.extend(res2)
                    start = end + 1
                    time.sleep(0.15)
                    continue
                return False, [], f"eth_getLogs rate-limited: {res2}"[:120]
            if ("range" in msg or "limit" in msg or "exceed" in msg) \
                    and chunk > 25:
                chunk = max(chunk // 2, 25)
                continue
            return False, [], f"eth_getLogs gagal: {res}"[:120]
        if not isinstance(res, list):
            return False, [], "eth_getLogs: respons bukan list"
        logs.extend(res)
        if len(logs) > ONCHAIN_MAX_LOGS:
            return False, [], f"terlalu banyak log (>{ONCHAIN_MAX_LOGS})"
        start = end + 1
        time.sleep(0.15)
    return True, logs, f"{len(logs)} log"


def find_pair_creation_block(token, pair_address, created_ts_ms, labels):
    """Pair creation block. Returns (block:int|None, exact:bool, note).

    1. Exact: v2 PairCreated / v3 PoolCreated factory event (skipped for
       v4 pairs — no factory event to query). 2. Fallback: timestamp
       estimation with measured block time + one verify/adjust round.
    """
    token_l = token.lower()
    pair_l = (pair_address or "").lower()
    labels = {str(x).lower() for x in (labels or [])}
    ln, latest_ts, bt = _chain_head()
    if ln is None:
        return None, False, "RPC blockNumber gagal"
    from_block = max(ln - ONCHAIN_PAIR_LOOKUP_RANGE, 0)
    if "v4" not in labels:
        for factory, topic, pair_pos in (
                (UNI_V2_FACTORY, PAIR_CREATED_TOPIC, 3),
                (UNI_V3_FACTORY, POOL_CREATED_TOPIC, 5)):
            for topics in ([topic, [_pad_addr(token_l)], None],
                           [topic, None, [_pad_addr(token_l)]]):
                ok, logs, _note = _rpc_logs(factory, topics, from_block, ln,
                                            chunk=10000)
                if not ok:
                    continue
                best = None
                for lg in logs:
                    tps = lg.get("topics") or []
                    if len(tps) <= pair_pos:
                        continue
                    if pair_l and _topic_addr(tps[pair_pos]) != pair_l:
                        continue
                    try:
                        bn = int(lg.get("blockNumber", "0x0"), 16)
                    except (TypeError, ValueError):
                        continue
                    best = bn if best is None else min(best, bn)
                if best is not None:
                    return best, True, f"event factory blok {best}"
    # timestamp estimation fallback
    if not created_ts_ms:
        return None, False, "tidak ada pairCreatedAt & event tidak ketemu"
    if not bt:
        return None, False, "tidak bisa mengukur block time"
    created_sec = int(created_ts_ms) // 1000
    est = ln - int((latest_ts - created_sec) / bt)
    est = max(min(est, ln), 0)
    # Verify by reading the block timestamp at the estimate and re-adjusting
    # (up to 3 rounds; block time drifts, so one round can still leave ~300
    # blocks of error on a hours-old token — verified live on MINTGOAT:
    # round 1 left 331 blocks, rounds 2-3 converge to ~0).
    for _ in range(3):
        ts_est = _rpc_block_ts(est)
        if ts_est is None:
            return None, False, "tidak bisa membaca blok estimasi"
        if abs(ts_est - created_sec) <= 10:
            break
        bt2 = (latest_ts - ts_est) / (ln - est) if ln != est else bt
        if bt2 <= 0:
            return None, False, "block time tidak valid"
        est = max(min(est + int((created_sec - ts_est) / bt2), ln), 0)
    return est, False, f"estimasi timestamp (blok {est})"


def onchain_sniper_bubble(pair, gp, cfg=None):
    """Sniper + bubble/insider from on-chain Transfer logs (no GMGN key).

    Returns dict: {ok, sniper_pct, sniper_wallets, bubble_pct,
    bubble_wallets, bought_pct, creation_block, exact_block, logs_count,
    note, source}. ok=False means BOTH checks are UNKNOWN (fail-closed:
    the token must not PASS on missing data).
    """
    cfg = {**DEFAULTS, **(cfg or {})}
    out = {"ok": False, "sniper_pct": None, "sniper_wallets": 0,
           "bubble_pct": None, "bubble_wallets": 0, "bought_pct": None,
           "creation_block": None, "exact_block": False, "logs_count": 0,
           "note": "", "source": "on-chain RPC"}
    ca = (pair.get("baseToken", {}) or {}).get("address", "")
    pair_addr = (pair.get("pairAddress", "") or "").lower()
    labels = pair.get("labels") or []
    if not _is_hex_addr(ca) or not pair_addr:
        out["note"] = "pair/token address tidak valid"
        return out
    creator = (gp.get("creator_address") or "").lower()
    # totalSupply HARUS dalam raw units (skala sama dengan nilai log dan
    # balanceOf). GoPlus total_supply dalam satuan token (1e9 untuk
    # MINTGOAT) — JANGAN dipakai di sini; totalSupply on-chain (1e27)
    # adalah penyebut yang benar. Kalau gagal -> UNKNOWN (fail-closed).
    total = _rpc_total_supply(ca)
    if not total or total <= 0:
        out["note"] = "totalSupply tidak diketahui"
        return out
    created_ms = pair.get("pairCreatedAt") or 0
    block, exact, note = find_pair_creation_block(ca, pair_addr, created_ms,
                                                  labels)
    if block is None:
        out["note"] = f"blok kreasi tidak ketemu: {note}"
        return out
    out["creation_block"] = block
    out["exact_block"] = exact
    ln, _ts, bt = _chain_head()
    if ln is None:
        out["note"] = "tidak bisa membaca head chain"
        return out
    if not bt or bt <= 0:
        bt = 0.12
    window_60s = min(max(int(ONCHAIN_BUBBLE_WINDOW_SEC / bt), 50), 1000)
    from_block = max(block - ONCHAIN_SNIPER_BUFFER, 0)
    to_block = min(block + window_60s, ln)
    ok, logs, note = _rpc_logs(ca, [TRANSFER_TOPIC], from_block, to_block)
    if not ok:
        out["note"] = f"log transfer gagal: {note}"
        return out
    out["logs_count"] = len(logs)
    sellers = {pair_addr, UNI_V4_POOL_MANAGER.lower()}
    pool_like = sellers | {DEAD.lower(), ZERO.lower()}
    if creator:
        pool_like.add(creator)
    buys = {}       # buyer -> amount in tight sniper window
    buys60 = {}     # buyer -> amount in full 60s window
    transfer_in = {}  # recipient -> amount received from creator
    from_snipers = {}  # recipient -> amount received from a sniper wallet
    first_block_buyers = set()
    sniper_end = block + ONCHAIN_SNIPER_END
    for lg in logs:
        try:
            bn = int(lg.get("blockNumber", "0x0"), 16)
            tps = lg.get("topics") or []
            frm = _topic_addr(tps[1]) if len(tps) > 1 else None
            to = _topic_addr(tps[2]) if len(tps) > 2 else None
            val = int(lg.get("data", "0x0"), 16)
        except (TypeError, ValueError, IndexError):
            continue
        if not frm or not to or val <= 0:
            continue
        if frm in sellers and to not in pool_like:
            buys60[to] = buys60.get(to, 0) + val
            if block - ONCHAIN_SNIPER_BUFFER <= bn <= sniper_end:
                buys[to] = buys.get(to, 0) + val
                if bn <= block + ONCHAIN_FIRST_BLOCK_END:
                    first_block_buyers.add(to)
        if creator and frm == creator and to not in pool_like:
            transfer_in[to] = transfer_in.get(to, 0) + val
    if not buys60:
        out["note"] = ("tidak ada pembelian dari pair di window log "
                       "(blok kreasi mungkin meleset)")
        return out
    # amounts acquired in-window per wallet (buy + transfer-in), for the
    # materiality test when resolving current holdings.
    amounts = {}
    for w, v in buys60.items():
        amounts[w] = amounts.get(w, 0) + v
    for w, v in transfer_in.items():
        amounts[w] = amounts.get(w, 0) + v
    # current holdings: GoPlus top-10 map first, then balanceOf for material
    # wallets missing from it (cap), immaterial misses bounded and ignored.
    holders_map = {}
    for h in (gp.get("holders") or []):
        a = (h.get("address") or "").lower()
        pct = _fnum(h.get("percent"))
        if a and pct is not None:
            holders_map[a] = pct * 100.0

    def _resolve_current(wallets):
        """Current % held for `wallets`. Returns (pct|None, ok, note)."""
        total_pct = 0.0
        ignored_pct = 0.0
        calls = 0
        for w in wallets:
            if w in holders_map:
                total_pct += holders_map[w]
                continue
            bought_pct = amounts.get(w, 0) * 100.0 / total
            if bought_pct < ONCHAIN_MATERIAL_BOUGHT_PCT:
                ignored_pct += bought_pct
                continue
            if calls >= ONCHAIN_MAX_BALANCE_CALLS:
                return None, False, ("terlalu banyak wallet material di luar "
                                     "GoPlus top-10")
            bal = _rpc_balance_of(ca, w)
            calls += 1
            if bal is None:
                if bought_pct >= 0.5:
                    return None, False, f"balanceOf gagal untuk {w[:10]}…"
                ignored_pct += bought_pct
                continue
            total_pct += bal * 100.0 / total
        if ignored_pct >= ONCHAIN_IGNORED_MAX_PCT:
            return None, False, (f"diabaikan {ignored_pct:.2f}% (di luar "
                                 "GoPlus top-10)")
        return round(total_pct, 4), True, f"diabaikan {ignored_pct:.2f}%"

    # --- sniper: early buyers' CURRENT holdings ----------------------------
    sniper_pct, ok_s, note_s = _resolve_current(set(buys.keys()))
    if not ok_s:
        out["note"] = f"sniper tidak bisa dihitung: {note_s}"
        return out
    out["sniper_pct"] = sniper_pct
    out["sniper_wallets"] = len(buys)
    out["bought_pct"] = round(sum(buys.values()) * 100.0 / total, 4)
    # --- bubble: insider cluster -------------------------------------------
    # transfer_in already excludes pair/poolmanager/burn/zero/creator.
    cluster = set(transfer_in.keys())  # insider distribution counts too
    # recipients of direct transfers FROM a sniper wallet (insider network)
    sniper_set = set(buys.keys())
    for lg in logs:
        try:
            tps = lg.get("topics") or []
            frm = _topic_addr(tps[1]) if len(tps) > 1 else None
            to = _topic_addr(tps[2]) if len(tps) > 2 else None
            val = int(lg.get("data", "0x0"), 16)
        except (TypeError, ValueError, IndexError):
            continue
        if frm in sniper_set and to and to not in pool_like and val > 0:
            from_snipers[to] = from_snipers.get(to, 0) + val
    cluster |= set(from_snipers.keys())
    # fresh wallets among first-block buyers (nonce <= 3)
    fresh_checked = 0
    for w in sorted(first_block_buyers):
        if w in cluster or fresh_checked >= ONCHAIN_MAX_NONCE_CHECKS:
            continue
        nonce = _rpc_nonce(w)
        fresh_checked += 1
        if nonce is not None and nonce <= ONCHAIN_FRESH_NONCE_MAX:
            cluster.add(w)
    cluster.discard(creator)
    bubble_pct, ok_b, note_b = _resolve_current(cluster)
    if not ok_b:
        out["note"] = f"bubble tidak bisa dihitung: {note_b}"
        return out
    out["bubble_pct"] = bubble_pct
    out["bubble_wallets"] = len(cluster)
    out["ok"] = True
    out["note"] = (
        f"blok kreasi {block} ({'exact' if exact else 'estimasi'}), "
        f"{len(logs)} log, {len(buys)} sniper wallet "
        f"(beli {out['bought_pct']}% supply di window awal), "
        f"klaster {len(cluster)} wallet; {note_s}")
    return out


# --- audit engine ----------------------------------------------------------
# A check: {key, label, value (display string), passed (True/False/None),
#           source, weight}. passed=None means UNKNOWN (data missing).
# Verdict: PASS only if every hard check passes. Any hard check UNKNOWN ->
# INCOMPLETE (never shown as clean). Any hard check False -> FAIL.

CHECK_WEIGHTS = {
    "honeypot": 20, "tax": 10, "dev": 10, "top10": 10, "sniper": 8,
    "bubble": 8, "lp": 10, "verified": 8, "owner": 6, "copycat": 5,
    "deployer": 5,
}
HARD_CHECKS = {"honeypot", "tax", "dev", "top10", "sniper", "bubble", "lp",
               "verified", "owner"}


def _check(key, label, value, passed, source, weight=None):
    return {"key": key, "label": label, "value": value, "passed": passed,
            "source": source, "weight": CHECK_WEIGHTS.get(key, weight or 0)}


def audit_token(pair, gp_ok, gp, src_match, gmgn, blacklist, cfg=None,
                onchain=None):
    """Full strict audit of one DexScreener pair. Returns audit dict.

    pair: DexScreener pair object (chainId robinhood).
    gp: GoPlus result dict ({} if unavailable). src_match: Sourcify match.
    gmgn: gmgn_fetch_all() result (ok=False when CLI unconfigured).
    blacklist: set of lowercased creator addresses known to have rugged.
    cfg: threshold overrides (DEFAULTS otherwise).
    onchain: onchain_sniper_bubble() result (None = not computed). The
    on-chain sniper/bubble values are the source of truth; when GMGN data
    is also available the WORSE (higher) value wins (fail-closed cross-check).
    """
    cfg = {**DEFAULTS, **(cfg or {})}
    ca = (pair.get("baseToken", {}) or {}).get("address", "")
    checks = []

    # --- honeypot: GoPlus cannot_buy + taxes + RPC transfer simulation ------
    if gp_ok:
        cannot_buy = str(gp.get("cannot_buy", "")) == "1"
        if cannot_buy:
            checks.append(_check("honeypot", "Honeypot (tidak bisa beli)",
                                 "cannot_buy=1", False, "GoPlus"))
        else:
            # transfer simulation from the largest non-pool holder
            holder = None
            for h in (gp.get("holders") or []):
                a = (h.get("address") or "").lower()
                if a not in KNOWN_POOL_CONTRACTS and a != DEAD.lower():
                    if h.get("is_contract") != 1:
                        holder = h.get("address")
                        break
            if holder:
                sim_ok, sim_note = rpc_simulate_transfer(ca, holder)
                checks.append(_check("honeypot", "Honeypot (simulasi transfer)",
                                     "transfer holder OK" if sim_ok else sim_note,
                                     sim_ok, "RPC eth_call"))
            else:
                checks.append(_check("honeypot", "Honeypot (simulasi transfer)",
                                     "tidak ada holder EOA untuk simulasi",
                                     None, "RPC eth_call"))
    else:
        checks.append(_check("honeypot", "Honeypot", "GoPlus tidak merespons",
                             None, "GoPlus"))
    # GMGN cross-check when available (is_honeypot documented BSC/Base only;
    # on robinhood it may be absent — treat absence as no signal, not a pass)
    if gmgn.get("ok"):
        hp = (gmgn.get("security") or {}).get("is_honeypot")
        if hp == "yes":
            for c in checks:
                if c["key"] == "honeypot":
                    c["passed"] = False
                    c["value"] = "GMGN: is_honeypot=yes"
                    c["source"] = "GMGN"

    # --- tax: must be exactly 0 --------------------------------------------
    if gp_ok:
        buy = _fnum(gp.get("buy_tax"))
        sell = _fnum(gp.get("sell_tax"))
        if buy is None or sell is None:
            checks.append(_check("tax", "Tax beli/jual", "data tax tidak ada",
                                 None, "GoPlus"))
        else:
            ok_tax = (buy == 0 and sell == 0)
            checks.append(_check("tax", "Tax beli/jual",
                                 f"beli {buy:g}% / jual {sell:g}%",
                                 ok_tax, "GoPlus"))
    elif gmgn.get("ok"):
        sec = gmgn.get("security") or {}
        buy, sell = _fnum(sec.get("buy_tax")), _fnum(sec.get("sell_tax"))
        if buy is None or sell is None:
            checks.append(_check("tax", "Tax beli/jual", "data tax tidak ada",
                                 None, "GMGN"))
        else:
            checks.append(_check("tax", "Tax beli/jual",
                                 f"beli {buy * 100:g}% / jual {sell * 100:g}%",
                                 buy == 0 and sell == 0, "GMGN"))
    else:
        checks.append(_check("tax", "Tax beli/jual", "tidak ada sumber data",
                             None, "—"))

    # --- dev hold: creator must hold effectively nothing --------------------
    creator = ""
    if gp_ok:
        creator = (gp.get("creator_address") or "").lower()
        dev_pct = _fnum(gp.get("creator_percent"))
        if dev_pct is None:
            checks.append(_check("dev", "Dev hold", "data creator tidak ada",
                                 None, "GoPlus"))
        else:
            checks.append(_check("dev", "Dev hold",
                                 f"{dev_pct:g}% (creator {creator[:10]}…)",
                                 dev_pct < cfg["dev_dust_pct"], "GoPlus"))
    elif gmgn.get("ok"):
        info = gmgn.get("info") or {}
        dev = info.get("dev") or {}
        creator = (dev.get("creator_address") or "").lower()
        rate = _fnum((info.get("stat") or {}).get("creator_hold_rate"))
        status = dev.get("creator_token_status")
        if rate is None:
            checks.append(_check("dev", "Dev hold",
                                 f"status={status or '?'}", None, "GMGN"))
        else:
            checks.append(_check("dev", "Dev hold",
                                 f"{rate * 100:.4f}% (status {status or '?'})",
                                 rate * 100 < cfg["dev_dust_pct"], "GMGN"))
    else:
        checks.append(_check("dev", "Dev hold", "tidak ada sumber data",
                             None, "—"))

    # --- top-10 holders (strict < 10%) --------------------------------------
    top10, n_used = (None, 0)
    if gp_ok:
        top10, n_used = goplus_top10_pct(gp)
    if top10 is None and gmgn.get("ok"):
        for src in (gmgn.get("security") or {}, gmgn.get("info") or {}):
            r = _fnum(src.get("top_10_holder_rate"))
            if r is not None:
                top10, n_used = round(r * 100.0, 4), 10
                break
    if top10 is None:
        checks.append(_check("top10", "Top 10 holder gabungan",
                             "data holder tidak ada", None, "GoPlus/GMGN"))
    else:
        checks.append(_check("top10", "Top 10 holder gabungan",
                             f"{top10}% (dari {n_used} holder)",
                             _lt(top10, cfg["max_top10_pct"]), "GoPlus/GMGN"))

    # --- sniper (strict < 8%) — on-chain utama, GMGN cross-check ------------
    oc_pct = (onchain or {}).get("sniper_pct") \
        if (onchain or {}).get("ok") else None
    gm_pct = gmgn.get("sniper_pct") \
        if gmgn.get("ok") and gmgn.get("sniper_pct") is not None else None
    if oc_pct is not None and gm_pct is not None:
        val = max(oc_pct, gm_pct)
        checks.append(_check(
            "sniper", "Sniper (blok awal)",
            f"{val}% (on-chain {oc_pct}% / GMGN {gm_pct}%, "
            f"{(onchain or {}).get('sniper_wallets', 0)} wallet)",
            _lt(val, cfg["max_sniper_pct"]), "on-chain RPC + GMGN"))
    elif oc_pct is not None:
        checks.append(_check(
            "sniper", "Sniper (blok awal)",
            f"{oc_pct}% ({(onchain or {}).get('sniper_wallets', 0)} wallet)",
            _lt(oc_pct, cfg["max_sniper_pct"]), "on-chain RPC"))
    elif gm_pct is not None:
        checks.append(_check("sniper", "Sniper (blok awal)",
                             f"{gm_pct}% ({gmgn['sniper_wallets']} wallet)",
                             _lt(gm_pct, cfg["max_sniper_pct"]),
                             "GMGN"))
    elif gmgn.get("ok"):
        checks.append(_check("sniper", "Sniper (blok awal)",
                             "GMGN tidak mengembalikan data sniper",
                             None, "GMGN"))
    else:
        checks.append(_check("sniper", "Sniper (blok awal)",
                             "tidak ada data sniper "
                             "(on-chain gagal + GMGN tidak ada)",
                             None, "on-chain RPC / GMGN"))

    # --- bubble / insider cluster (strict < 8%) ---------------------------
    # GMGN value: worst of rat / bundler / blended, as before.
    gm_bubble = None
    if gmgn.get("ok"):
        sec = gmgn.get("security") or {}
        stat = (gmgn.get("info") or {}).get("stat") or {}
        rat = _fnum(sec.get("rat_trader_amount_rate"))
        if rat is None:
            rat = _fnum(stat.get("top_rat_trader_percentage"))
        bun = _fnum(sec.get("bundler_trader_amount_rate"))
        if bun is None:
            bun = _fnum(stat.get("top_bundler_trader_percentage"))
        if rat is not None or bun is not None:
            r, b = (rat or 0) * 100.0, (bun or 0) * 100.0
            gm_bubble = (round(max(r, b, (r + b) * 0.7), 2), r, b)
    oc_bubble = (onchain or {}).get("bubble_pct") \
        if (onchain or {}).get("ok") else None
    if oc_bubble is not None and gm_bubble is not None:
        val = max(oc_bubble, gm_bubble[0])
        checks.append(_check(
            "bubble", "Bubble cluster (insider/bundler)",
            f"{val}% (on-chain {oc_bubble}% / GMGN {gm_bubble[0]}% "
            f"[rat {gm_bubble[1]:.1f}% / bundler {gm_bubble[2]:.1f}%], "
            f"{(onchain or {}).get('bubble_wallets', 0)} wallet)",
            _lt(val, cfg["max_bubble_pct"]), "on-chain RPC + GMGN"))
    elif oc_bubble is not None:
        checks.append(_check(
            "bubble", "Bubble cluster (insider/bundler)",
            f"{oc_bubble}% ({(onchain or {}).get('bubble_wallets', 0)} "
            f"wallet klaster)",
            _lt(oc_bubble, cfg["max_bubble_pct"]), "on-chain RPC"))
    elif gm_bubble is not None:
        checks.append(_check(
            "bubble", "Bubble cluster (insider/bundler)",
            f"{gm_bubble[0]}% (rat {gm_bubble[1]:.1f}% / bundler "
            f"{gm_bubble[2]:.1f}%)",
            _lt(gm_bubble[0], cfg["max_bubble_pct"]), "GMGN"))
    elif gmgn.get("ok"):
        checks.append(_check("bubble", "Bubble cluster (insider/bundler)",
                             "data rat/bundler tidak ada", None, "GMGN"))
    else:
        checks.append(_check("bubble", "Bubble cluster (insider/bundler)",
                             "tidak ada data bubble "
                             "(on-chain gagal + GMGN tidak ada)",
                             None, "on-chain RPC / GMGN"))

    # --- LP: burned / locked / launchpad ------------------------------------
    lp_detail = ""
    lp_ok = None
    if gp_ok:
        lp = goplus_lp_state(gp)
        lp_detail = lp["detail"]
        if lp["state"] in ("burned", "locked"):
            lp_ok = True
        elif lp["state"] == "unlocked":
            lp_ok = False
    if lp_ok is None and gmgn.get("ok"):
        # GoPlus had no LP data at all — only then consult GMGN. An explicit
        # GoPlus "unlocked" is never overridden by a GMGN ratio (fail-closed).
        info = gmgn.get("info") or {}
        launchpad = str(info.get("launchpad") or "").lower()
        if launchpad in LAUNCHPAD_LP_OK:
            lp_ok = True
            lp_detail = f"launchpad {launchpad} (LP dipegang launchpad)"
        elif _fnum(info.get("locked_ratio")) is not None \
                and _fnum(info.get("locked_ratio")) >= cfg["min_lp_burn_ratio"]:
            lp_ok = True
            lp_detail = f"locked_ratio {info['locked_ratio']}"
        elif gmgn.get("security", {}).get("burn_status") == "burn":
            lp_ok = True
            lp_detail = "burn_status=burn (GMGN)"
    if lp_ok is None:
        # last resort: launchpad from DexScreener pair labels is not reliable;
        # report unknown rather than guessing.
        lp_detail = lp_detail or "tidak ada data LP lock/burn"
    checks.append(_check("lp", "LP terkunci / dibakar",
                         lp_detail, lp_ok, "GoPlus/GMGN"))

    # --- contract verification ----------------------------------------------
    verified = None
    if src_match in ("exact_match", "match"):
        verified = True
        vtxt = f"Sourcify {src_match}"
    elif src_match is not None:
        verified = False
        vtxt = "Sourcify: tidak terverifikasi"
    elif gp_ok and str(gp.get("is_open_source")) == "1":
        verified = True
        vtxt = "GoPlus: open_source=1"
    elif gp_ok:
        verified = False
        vtxt = "GoPlus: is_open_source=0"
    else:
        vtxt = "tidak ada sumber verifikasi"
    checks.append(_check("verified", "Kontrak terverifikasi", vtxt, verified,
                         "Sourcify/GoPlus"))

    # --- owner renounced ------------------------------------------------------
    owner_ok, owner_txt = None, ""
    if gp_ok:
        ow = (gp.get("owner_address") or "").strip()
        if ow == "":
            owner_ok, owner_txt = True, "owner_address kosong (renounced)"
        else:
            owner_ok, owner_txt = False, f"owner aktif: {ow[:12]}…"
    if owner_ok is None and gmgn.get("ok"):
        ren = (gmgn.get("security") or {}).get("owner_renounced")
        if ren == "yes":
            owner_ok, owner_txt = True, "owner_renounced=yes (GMGN)"
        elif ren == "no":
            owner_ok, owner_txt = False, "owner_renounced=no (GMGN)"
    if owner_ok is None:
        owner_txt = owner_txt or "tidak ada sumber data owner"
    checks.append(_check("owner", "Owner renounced", owner_txt, owner_ok,
                         "GoPlus/GMGN"))

    # --- copycat: same symbol, other address on Robinhood --------------------
    symbol = (pair.get("baseToken", {}) or {}).get("symbol", "")
    if symbol:
        others = [a for a, s in search_symbol(symbol)
                  if a and a != ca.lower()]
        if others:
            checks.append(_check("copycat", "Token copycat (nama sama)",
                                 f"{len(others)} token lain bernama {symbol}",
                                 False, "DexScreener"))
        else:
            checks.append(_check("copycat", "Token copycat (nama sama)",
                                 "tidak ada token lain bernama sama", True,
                                 "DexScreener"))
    else:
        checks.append(_check("copycat", "Token copycat (nama sama)",
                             "simbol tidak ada", None, "DexScreener"))

    # --- deployer history / blacklist ----------------------------------------
    if creator and creator in blacklist:
        checks.append(_check("deployer", "Riwayat deployer",
                             "creator ada di BLACKLIST (pernah rug)",
                             False, "blacklist lokal"))
    elif creator and gp_ok and str(gp.get("honeypot_with_same_creator")) == "1":
        checks.append(_check("deployer", "Riwayat deployer",
                             "creator pernah membuat honeypot", False,
                             "GoPlus"))
    elif creator:
        checks.append(_check("deployer", "Riwayat deployer",
                             "tidak ada catatan rug", True, "GoPlus/blacklist"))
    else:
        checks.append(_check("deployer", "Riwayat deployer",
                             "creator tidak diketahui", None, "—"))

    # --- score + verdict ------------------------------------------------------
    score = 0
    for c in checks:
        if c["passed"] is True:
            score += c["weight"]
        elif c["passed"] is None:
            score += c["weight"] // 2  # unknown: half credit, never full
    score = max(0, min(100, score))
    failed_hard = [c for c in checks if c["key"] in HARD_CHECKS
                   and c["passed"] is False]
    unknown_hard = [c for c in checks if c["key"] in HARD_CHECKS
                    and c["passed"] is None]
    if failed_hard:
        verdict = "FAIL"
        reasons = [f"{c['label']}: {c['value']}" for c in failed_hard]
    elif unknown_hard:
        verdict = "INCOMPLETE"
        reasons = [f"{c['label']}: data tidak lengkap ({c['source']})"
                   for c in unknown_hard]
    else:
        verdict = "PASS"
        reasons = []
    # GMGN extra signals (not gates): smart money, fresh wallets, rug_ratio
    extra = {}
    if gmgn.get("ok"):
        info, sec = gmgn.get("info") or {}, gmgn.get("security") or {}
        wts = info.get("wallet_tags_stat") or {}
        extra = {
            "smart_wallets": wts.get("smart_wallets"),
            "renowned_wallets": wts.get("renowned_wallets"),
            "fresh_wallet_rate": _fnum((info.get("stat") or {})
                                      .get("fresh_wallet_rate")),
            "rug_ratio": _fnum(sec.get("rug_ratio")),
            "is_wash_trading": sec.get("is_wash_trading"),
            "launchpad": info.get("launchpad"),
            "twitter": (info.get("link") or {}).get("twitter_username"),
            "website": (info.get("link") or {}).get("website"),
        }
    return {"ca": ca, "checks": checks, "score": score, "verdict": verdict,
            "reasons": reasons, "extra": extra, "creator": creator}


# --- scan pipeline -----------------------------------------------------------
def _pair_metrics(pair, now_ms):
    created = pair.get("pairCreatedAt") or 0
    age_min = (now_ms - created) / 60000.0 if created else None
    liq = _fnum((pair.get("liquidity") or {}).get("usd"), 0) or 0
    tx5 = (pair.get("txns") or {}).get("m5") or {}
    buys = int(tx5.get("buys") or 0)
    sells = int(tx5.get("sells") or 0)
    vol5 = _fnum((pair.get("volume") or {}).get("m5"), 0) or 0
    price = _fnum((pair.get("priceUsd")), 0) or 0
    return age_min, liq, buys, sells, vol5, price


def scan_once(cfg=None, max_audit=12, blacklist=None, state_dir=None):
    """One full scan cycle. Returns a report dict (JSON-serializable).

    Stages: profiles -> pairs -> pre-filter (age/liq/buys) -> full audit.
    Never raises on a single token failing: per-token errors are logged.
    """
    cfg = {**DEFAULTS, **(cfg or {})}
    blacklist = blacklist or set()
    now_ms = time.time() * 1000
    rep = {"ts": _now_wib(),
           "sources": {}, "pairs_checked": 0, "prefiltered": 0,
           "audited": 0, "passed": [], "incomplete": [], "failed_log": [],
           "blacklist_add": []}
    ok, addrs, note = fetch_new_profiles()
    rep["sources"]["dexscreener_profiles"] = {"ok": ok, "note": note}
    if not ok:
        rep["sources"]["gmgn"] = {"ok": False,
                                  "note": gmgn_status()[1]}
        rep["sources"]["onchain"] = {"ok": False, "note": "scan tidak jalan"}
        return rep
    pairs, notes = fetch_pairs(addrs)
    rep["sources"]["dexscreener_pairs"] = {
        "ok": True, "note": f"{len(pairs)} pair Robinhood | " + "; ".join(notes)
        if notes else f"{len(pairs)} pair Robinhood"}
    rep["pairs_checked"] = len(pairs)
    ready, gnote = gmgn_status()
    rep["sources"]["gmgn"] = {"ok": ready, "note": gnote}
    # pre-filter
    cands = []
    for p in pairs:
        age, liq, buys, sells, vol5, price = _pair_metrics(p, now_ms)
        if age is None or age < 0 or age > cfg["max_age_min"]:
            continue
        if liq < cfg["min_liq_usd"]:
            continue
        if buys < cfg["min_buys_m5"] or buys <= sells:
            continue
        cands.append((p, age, liq, buys, sells, vol5, price))
    rep["prefiltered"] = len(cands)
    # audit (cap the number per cycle to respect free rate limits)
    onchain_ok_n = 0
    onchain_last_note = ""
    for p, age, liq, buys, sells, vol5, price in cands[:max_audit]:
        ca = (p.get("baseToken", {}) or {}).get("address", "")
        base = (p.get("baseToken", {}) or {})
        try:
            gp_ok, gp, gp_note = fetch_goplus(ca)
            src_ok, src_match, src_note = fetch_sourcify(ca)
            gmgn = gmgn_fetch_all(ca) if ready else {"ok": False}
            # on-chain sniper/bubble needs GoPlus (creator + holders +
            # totalSupply); without it the checks stay UNKNOWN (fail-closed).
            onchain = (onchain_sniper_bubble(p, gp, cfg)
                       if gp_ok else {"ok": False,
                                     "note": "GoPlus tidak ada"})
            if onchain.get("ok"):
                onchain_ok_n += 1
            elif onchain.get("note"):
                onchain_last_note = onchain["note"]
            audit = audit_token(p, gp_ok, gp, src_match, gmgn, blacklist,
                                cfg, onchain=onchain)
        except Exception as exc:  # noqa: BLE001
            rep["failed_log"].append(
                {"ca": ca, "reason": f"audit error: {exc}"[:150]})
            continue
        rep["audited"] += 1
        rec = {
            "name": base.get("name", "Unknown"),
            "symbol": base.get("symbol", "???"),
            "ca": ca,
            "age_min": round(age, 1),
            "liq_usd": liq,
            "vol_m5": vol5,
            "buys_m5": buys,
            "sells_m5": sells,
            "price_usd": price,
            "pair_address": p.get("pairAddress", ""),
            "dex": p.get("dexId", ""),
            "score": audit["score"],
            "verdict": audit["verdict"],
            "reasons": audit["reasons"],
            "checks": audit["checks"],
            "extra": audit["extra"],
            "creator": audit["creator"],
        }
        if audit["verdict"] == "PASS":
            rep["passed"].append(rec)
        elif audit["verdict"] == "INCOMPLETE":
            rep["incomplete"].append(rec)
        else:
            rep["failed_log"].append(
                {"ca": ca, "symbol": rec["symbol"],
                 "reasons": audit["reasons"]})
            honey = [c for c in audit["checks"] if c["key"] == "honeypot"
                     and c["passed"] is False]
            if honey and audit.get("creator"):
                rep["blacklist_add"].append(
                    {"creator": audit["creator"],
                     "reason": f"honeypot: {rec['symbol']} {ca[:12]}…"})
    rep["sources"]["onchain"] = {
        "ok": onchain_ok_n > 0,
        "note": f"{onchain_ok_n}/{rep['audited']} token dapat data on-chain "
                "(sniper/bubble via RPC)"
                + (f" — terakhir gagal: {onchain_last_note}"
                   if onchain_ok_n < rep["audited"] and onchain_last_note
                   else "")}
    return rep


# --- watchlist + alerts + recap (JSON state files) ---------------------------
def _load_json(path, default):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return default


def _save_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _append_jsonl(path, obj):
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(obj, ensure_ascii=False) + "\n")


def load_blacklist(state_dir):
    data = _load_json(os.path.join(state_dir, "blacklist.json"), {})
    return {k.lower() for k in data.keys()}


def save_blacklist(state_dir, blacklist, reasons):
    data = _load_json(os.path.join(state_dir, "blacklist.json"), {})
    for addr in blacklist:
        data.setdefault(addr.lower(), reasons.get(addr.lower(), ""))
    _save_json(os.path.join(state_dir, "blacklist.json"), data)


def re_audit_watchlist(state_dir, cfg=None, blacklist=None):
    """Re-check every watchlisted token; append alerts to alerts.jsonl.

    Alerts: dev sells (creator balance up vs entry is impossible — balance
    only falls; detect creator_token_status sell / creator_balance drop),
    LP unlock/removal, big-holder dumps (top10 up sharply), sniper exits
    (sniper % down sharply = snipers sold), price moves, honeypot re-check
    (transfer simulation), contract changes (tax/owner/verified flips).
    Returns list of alert dicts created this run.
    """
    cfg = {**DEFAULTS, **(cfg or {})}
    blacklist = blacklist or set()
    wl_path = os.path.join(state_dir, "watchlist.json")
    wl = _load_json(wl_path, {})
    alerts = []
    now = _now_wib()
    for ca, entry in list(wl.items()):
        try:
            ok, pairs, _ = (True, [], "")
            ok, data = _get(f"{DEX_API}/latest/dex/tokens/{ca}", timeout=12)
            pair = None
            if ok:
                for p in data.get("pairs", []) or []:
                    if p.get("chainId") == CHAIN_NAME:
                        pair = p
                        break
            gp_ok, gp, _ = fetch_goplus(ca)
            ready, _ = gmgn_status()
            gmgn = gmgn_fetch_all(ca) if ready else {"ok": False}
            src_ok, src_match, _ = fetch_sourcify(ca)
            if pair is None and not gp_ok:
                alerts.append({"ts": now, "ca": ca, "type": "data_hilang",
                               "detail": "pair + GoPlus tidak merespons"})
                continue
            new_alerts = []
            if pair is not None:
                price = _fnum(pair.get("priceUsd"), 0) or 0
                liq = _fnum((pair.get("liquidity") or {}).get("usd"), 0) or 0
                entry_price = entry.get("price_usd") or 0
                entry_liq = entry.get("liq_usd") or 0
                if entry_price and price:
                    chg = (price - entry_price) / entry_price * 100.0
                    if abs(chg) >= 50:
                        new_alerts.append(
                            ("harga_gerak_besar",
                             f"harga {chg:+.1f}% dari ${entry_price:.6g} ke "
                             f"${price:.6g}"))
                    entry["last_price_usd"] = price
                    entry["price_chg_pct"] = round(chg, 1)
                if entry_liq and liq and liq < entry_liq * 0.5:
                    new_alerts.append(
                        ("likuiditas_anjlok",
                         f"likuiditas ${entry_liq:,.0f} -> ${liq:,.0f}"))
                entry["last_liq_usd"] = liq
            if gp_ok:
                lp = goplus_lp_state(gp)
                prev_lp = entry.get("lp_state")
                if prev_lp in ("locked", "burned") and lp["state"] == "unlocked":
                    new_alerts.append(("lp_dibuka",
                                       "LP yang tadinya terkunci/dibakar "
                                       "sekarang tidak terkunci"))
                entry["lp_state"] = lp["state"]
                top10, _ = goplus_top10_pct(gp)
                if top10 is not None:
                    prev = entry.get("top10_pct")
                    if prev is not None and top10 > prev + 5:
                        new_alerts.append(
                            ("holder_besar_nambah",
                             f"top10 {prev}% -> {top10}%"))
                    entry["top10_pct"] = top10
                dev_pct = _fnum(gp.get("creator_percent"))
                if dev_pct is not None:
                    prev_dev = entry.get("dev_pct")
                    if prev_dev is not None and dev_pct > prev_dev + 1:
                        new_alerts.append(
                            ("dev_nambah_hold",
                             f"dev hold {prev_dev}% -> {dev_pct}%"))
                    entry["dev_pct"] = dev_pct
                # honeypot re-check via transfer simulation
                holder = None
                for h in (gp.get("holders") or []):
                    a = (h.get("address") or "").lower()
                    if a not in KNOWN_POOL_CONTRACTS and a != DEAD.lower() \
                            and h.get("is_contract") != 1:
                        holder = h.get("address")
                        break
                if holder:
                    sim_ok, _ = rpc_simulate_transfer(ca, holder)
                    if not sim_ok:
                        new_alerts.append(
                            ("honeypot_muncul",
                             "transfer holder GAGAL — token berubah jadi "
                             "honeypot?"))
            # contract changes: tax / owner flips
            if gp_ok:
                buy = _fnum(gp.get("buy_tax"))
                sell = _fnum(gp.get("sell_tax"))
                if buy is not None and (buy != 0 or sell != 0):
                    if entry.get("tax_ok", True):
                        new_alerts.append(
                            ("tax_berubah",
                             f"tax berubah: beli {buy:g}% / jual {sell:g}%"))
                    entry["tax_ok"] = False
                ow = (gp.get("owner_address") or "").strip()
                if ow and not entry.get("owner_seen"):
                    new_alerts.append(("owner_muncul",
                                       f"owner aktif terdeteksi: {ow[:12]}…"))
                    entry["owner_seen"] = True
            entry["last_check"] = now
            for typ, detail in new_alerts:
                alerts.append({"ts": now, "ca": ca, "symbol": entry.get("symbol"),
                               "type": typ, "detail": detail})
        except Exception as exc:  # noqa: BLE001
            alerts.append({"ts": now, "ca": ca, "type": "error",
                           "detail": f"re-audit gagal: {exc}"[:120]})
    wl_path_tmp = wl
    _save_json(wl_path, wl)
    if alerts:
        for a in alerts:
            _append_jsonl(os.path.join(state_dir, "alerts.jsonl"), a)
    return alerts


def update_watchlist_from_scan(state_dir, passed):
    """Add newly-passed tokens to the watchlist; return (added, already)."""
    wl_path = os.path.join(state_dir, "watchlist.json")
    wl = _load_json(wl_path, {})
    now = _now_wib()
    added, already = [], []
    for rec in passed:
        ca = rec["ca"].lower()
        if ca in wl:
            already.append(ca)
            continue
        wl[ca] = {
            "symbol": rec["symbol"], "name": rec["name"],
            "first_seen": now, "price_usd": rec["price_usd"],
            "liq_usd": rec["liq_usd"], "score": rec["score"],
            "creator": rec.get("creator", ""),
            "last_price_usd": rec["price_usd"],
            "last_liq_usd": rec["liq_usd"],
            "tax_ok": True, "owner_seen": False,
        }
        added.append(ca)
    _save_json(wl_path, wl)
    return added, already


def write_recap(state_dir, scan_rep, alerts):
    """Daily recap data: tokens passed today + watchlist snapshot."""
    recap_path = os.path.join(state_dir, "recap.json")
    recap = _load_json(recap_path, {"date": "", "passed_today": [],
                                    "alerts_today": 0})
    today = _now_wib()[:10]  # WIB date, not the host's local date
    if recap.get("date") != today:
        recap = {"date": today, "passed_today": [], "alerts_today": 0}
    seen = {p["ca"].lower() for p in recap["passed_today"]}
    for rec in scan_rep.get("passed", []):
        if rec["ca"].lower() not in seen:
            recap["passed_today"].append(
                {"ts": scan_rep["ts"], "symbol": rec["symbol"],
                 "ca": rec["ca"], "score": rec["score"],
                 "age_min": rec["age_min"], "liq_usd": rec["liq_usd"]})
    recap["alerts_today"] = recap.get("alerts_today", 0) + len(alerts)
    wl = _load_json(os.path.join(state_dir, "watchlist.json"), {})
    recap["watchlist"] = [
        {"symbol": v.get("symbol"), "ca": k, "score": v.get("score"),
         "price_chg_pct": v.get("price_chg_pct"),
         "first_seen": v.get("first_seen")}
        for k, v in wl.items()
    ]
    _save_json(recap_path, recap)
    return recap
