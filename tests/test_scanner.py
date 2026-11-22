"""Tests for scanner.py — Robinhood Chain Ultra-Safe Scanner v3.

Run:  python3 -m unittest discover -s tests -v
Live tests (marked) hit the real DexScreener/GoPlus/Sourcify/RPC APIs.
"""

import os
import sys
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import scanner

NOW_MS = int(time.time() * 1000)


def make_pair(age_min=10, liq=50000, buys=5, sells=1):
    return {
        "chainId": "robinhood", "dexId": "uniswap",
        "pairAddress": "0xPAIR00000000000000000000000000000000000001",
        "baseToken": {"address": "0xTOKEN000000000000000000000000000000000001",
                      "name": "Test Token", "symbol": "TEST"},
        "quoteToken": {"symbol": "WETH"},
        "pairCreatedAt": NOW_MS - int(age_min * 60000),
        "liquidity": {"usd": liq}, "volume": {"m5": 100},
        "txns": {"m5": {"buys": buys, "sells": sells}},
        "priceUsd": "0.001",
    }


def make_gp(holder_pct=0.009, dev_pct="0.000000", buy_tax="0", sell_tax="0"):
    # GoPlus holders[].percent is a 0-1 RATIO string ("0.009" = 0.9%).
    holders = [{"address": f"0xH{i:039d}", "percent": str(holder_pct),
                "is_locked": 0, "is_contract": 0} for i in range(1, 11)]
    return {
        "buy_tax": buy_tax, "sell_tax": sell_tax, "transfer_tax": "0",
        "cannot_buy": "0", "total_supply": "1000000000",
        "creator_address": "0xCREATOR0000000000000000000000000000000001",
        "creator_percent": dev_pct, "holder_count": 100,
        "holders": holders,
        "lp_holders": [{"address": "0x000000000000000000000000000000000000dEaD",
                        "is_locked": 1, "NFT_list": []}],
        "owner_address": "", "is_open_source": "1",
        "honeypot_with_same_creator": "0", "dex": [],
    }


def make_gmgn(sniper_pct=5.0, bubble_rat=0.01, bubble_bun=0.01):
    return {
        "ok": True,
        "security": {"is_honeypot": "no", "buy_tax": 0, "sell_tax": 0,
                     "top_10_holder_rate": 0.09, "creator_balance_rate": 0,
                     "rug_ratio": 0.05, "rat_trader_amount_rate": bubble_rat,
                     "bundler_trader_amount_rate": bubble_bun,
                     "sniper_count": 2, "burn_status": "burn",
                     "owner_renounced": "yes"},
        "info": {"dev": {"creator_address":
                         "0xCREATOR0000000000000000000000000000000001",
                         "creator_token_status": "sell"},
                 "stat": {"creator_hold_rate": 0, "top_10_holder_rate": 0.09,
                          "top_rat_trader_percentage": bubble_rat,
                          "top_bundler_trader_percentage": bubble_bun,
                          "fresh_wallet_rate": 0.1},
                 "wallet_tags_stat": {"smart_wallets": 1, "sniper_wallets": 2},
                 "launchpad": "", "locked_ratio": 0.95, "link": {}},
        "sniper_pct": sniper_pct, "sniper_wallets": 2, "holders": [],
    }


class TestStrictThresholds(unittest.TestCase):
    def setUp(self):
        self.patches = [
            patch.object(scanner, "rpc_simulate_transfer",
                         return_value=(True, "ok")),
            patch.object(scanner, "search_symbol", return_value=[]),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def _audit(self, gp=None, gmgn=None, src="match"):
        return scanner.audit_token(make_pair(), True, gp or make_gp(),
                                   src, gmgn if gmgn is not None else make_gmgn(),
                                   set())

    def _check(self, audit, key):
        return next(c for c in audit["checks"] if c["key"] == key)

    def test_all_clean_passes(self):
        a = self._audit()
        self.assertEqual(a["verdict"], "PASS", a["reasons"])
        self.assertEqual(a["score"], 100)

    def test_top10_exactly_10_fails_strict(self):
        # 10 holders x 1.0% (ratio 0.01) = exactly 10% -> must FAIL (strict <)
        a = self._audit(gp=make_gp(holder_pct=0.01))
        self.assertFalse(self._check(a, "top10")["passed"])
        self.assertEqual(a["verdict"], "FAIL")

    def test_top10_9_9_passes(self):
        a = self._audit(gp=make_gp(holder_pct=0.0099))
        self.assertTrue(self._check(a, "top10")["passed"])

    def test_sniper_exactly_8_fails_strict(self):
        a = self._audit(gmgn=make_gmgn(sniper_pct=8.0))
        self.assertFalse(self._check(a, "sniper")["passed"])
        self.assertEqual(a["verdict"], "FAIL")

    def test_sniper_7_9_passes(self):
        a = self._audit(gmgn=make_gmgn(sniper_pct=7.9))
        self.assertTrue(self._check(a, "sniper")["passed"])

    def test_bubble_exactly_8_fails_strict(self):
        a = self._audit(gmgn=make_gmgn(bubble_rat=0.08))
        self.assertFalse(self._check(a, "bubble")["passed"])

    def test_dev_half_percent_fails(self):
        a = self._audit(gp=make_gp(dev_pct="0.5"))
        self.assertFalse(self._check(a, "dev")["passed"])
        self.assertEqual(a["verdict"], "FAIL")

    def test_tax_nonzero_fails(self):
        a = self._audit(gp=make_gp(buy_tax="0.1"))
        self.assertFalse(self._check(a, "tax")["passed"])
        self.assertEqual(a["verdict"], "FAIL")

    def test_gmgn_missing_means_incomplete_not_pass(self):
        # GMGN unconfigured: sniper/bubble UNKNOWN -> INCOMPLETE, never PASS
        a = self._audit(gmgn={"ok": False})
        self.assertEqual(a["verdict"], "INCOMPLETE")
        self.assertIsNone(self._check(a, "sniper")["passed"])
        self.assertIsNone(self._check(a, "bubble")["passed"])
        self.assertLess(a["score"], 100)

    def test_unverified_contract_fails_verified_check(self):
        a = self._audit(gp=make_gp(), src=None)
        # GoPlus is_open_source=1 still verifies via GoPlus fallback
        self.assertTrue(self._check(a, "verified")["passed"])
        gp2 = make_gp()
        gp2["is_open_source"] = "0"
        a2 = self._audit(gp=gp2, src=None)
        self.assertFalse(self._check(a2, "verified")["passed"])

    def test_lp_unlocked_fails(self):
        gp = make_gp()
        gp["lp_holders"] = [{"address": "0xLOCKER00000000000000000000000000000001",
                             "is_locked": 0, "NFT_list": []}]
        a = self._audit(gp=gp)
        self.assertFalse(self._check(a, "lp")["passed"])

    def test_lt_helper(self):
        self.assertFalse(scanner._lt(10, 10))
        self.assertTrue(scanner._lt(9.99, 10))
        self.assertFalse(scanner._lt(None, 10))

    def test_blacklisted_creator_fails(self):
        a = scanner.audit_token(
            make_pair(), True, make_gp(), "match", make_gmgn(),
            {"0xcreator0000000000000000000000000000000001"})
        self.assertFalse(self._check(a, "deployer")["passed"])


class TestGoPlusLPState(unittest.TestCase):
    def test_burned(self):
        gp = {"lp_holders": [{"address": "0x000000000000000000000000000000000000dEaD",
                              "is_locked": 1, "NFT_list": []}]}
        self.assertEqual(scanner.goplus_lp_state(gp)["state"], "burned")

    def test_locked(self):
        gp = {"lp_holders": [{"address": "0xLOCKER1", "is_locked": 1,
                              "NFT_list": ["123"]}]}
        self.assertEqual(scanner.goplus_lp_state(gp)["state"], "locked")

    def test_unlocked(self):
        gp = {"lp_holders": [{"address": "0xEOA1", "is_locked": 0,
                              "NFT_list": []}]}
        self.assertEqual(scanner.goplus_lp_state(gp)["state"], "unlocked")

    def test_unknown(self):
        self.assertEqual(scanner.goplus_lp_state({})["state"], "unknown")


class TestOnChainChecks(unittest.TestCase):
    """On-chain sniper/bubble (no GMGN key) — mocked RPC.

    Fixture: token 1e9 supply (raw 1e27), v4 pair, creation block 119999000.
    GoPlus base holders 10 x 0.1% so extra big holders don't trip top10.
    """

    TOKEN = "0x1111111111111111111111111111111111111111"
    PAIR = "0x2222222222222222222222222222222222222222"
    CREATOR = "0x3333333333333333333333333333333333333333"
    SNIPER = "0x4444444444444444444444444444444444444444"
    CLUSTER = "0x5555555555555555555555555555555555555555"
    HEAD = 120000000
    CREATION = 119999000
    SUPPLY_RAW = 10 ** 27

    def setUp(self):
        self.patches = [
            patch.object(scanner, "rpc_simulate_transfer",
                         return_value=(True, "ok")),
            patch.object(scanner, "search_symbol", return_value=[]),
            patch.object(scanner, "_chain_head",
                         return_value=(self.HEAD, 1795350000, 0.12)),
            patch.object(scanner, "find_pair_creation_block",
                         return_value=(self.CREATION, False, "test")),
            patch.object(scanner, "_rpc_total_supply",
                         return_value=self.SUPPLY_RAW),
            patch.object(scanner, "_rpc_balance_of", return_value=0),
            patch.object(scanner, "_rpc_nonce", return_value=100),
        ]
        for p in self.patches:
            p.start()
        self.logs_patch = patch.object(scanner, "_rpc_logs")
        self.mock_logs = self.logs_patch.start()
        self.mock_logs.return_value = (True, [], "")

    def tearDown(self):
        self.logs_patch.stop()
        for p in self.patches:
            p.stop()

    def _log(self, bn, frm, to, val):
        return {"blockNumber": hex(bn),
                "topics": [scanner.TRANSFER_TOPIC,
                           scanner._pad_addr(frm), scanner._pad_addr(to)],
                "data": hex(val)}

    def _pair(self):
        p = make_pair()
        p["baseToken"] = {"address": self.TOKEN, "name": "Test Token",
                          "symbol": "TEST"}
        p["pairAddress"] = self.PAIR
        p["labels"] = ["v4"]
        return p

    def _gp(self, extra_holders=()):
        gp = make_gp(holder_pct=0.001)  # 10 x 0.1% = 1% top10 base
        gp["creator_address"] = self.CREATOR
        gp["holders"] = list(gp["holders"]) + [
            {"address": a, "percent": str(pct), "is_locked": 0,
             "is_contract": 0} for a, pct in extra_holders]
        return gp

    def _audit(self, onchain, gmgn=None, gp=None):
        return scanner.audit_token(
            self._pair(), True, gp or self._gp(), "match",
            gmgn if gmgn is not None else {"ok": False}, set(),
            onchain=onchain)

    def _check(self, audit, key):
        return next(c for c in audit["checks"] if c["key"] == key)

    def test_keccak_transfer_topic(self):
        self.assertEqual(
            scanner.TRANSFER_TOPIC,
            "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef")
        self.assertEqual(
            "0x" + scanner._keccak256(b"").hex(),
            "0xc5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470")

    def test_clean_token_passes_without_gmgn(self):
        # sniper buys 5% in the creation block, still holds 5%
        self.mock_logs.return_value = (
            True,
            [self._log(self.CREATION, self.PAIR, self.SNIPER, 5 * 10 ** 25)],
            "")
        oc = scanner.onchain_sniper_bubble(
            self._pair(), self._gp([(self.SNIPER, 0.05)]))
        self.assertTrue(oc["ok"], oc["note"])
        self.assertEqual(oc["sniper_pct"], 5.0)
        self.assertEqual(oc["bubble_pct"], 0.0)
        a = self._audit(oc, gp=self._gp([(self.SNIPER, 0.05)]))
        self.assertEqual(a["verdict"], "PASS", a["reasons"])
        self.assertEqual(a["score"], 100)
        self.assertEqual(self._check(a, "sniper")["source"], "on-chain RPC")

    def test_sniper_exactly_8_fails_strict_onchain(self):
        self.mock_logs.return_value = (
            True,
            [self._log(self.CREATION, self.PAIR, self.SNIPER, 8 * 10 ** 25)],
            "")
        oc = scanner.onchain_sniper_bubble(
            self._pair(), self._gp([(self.SNIPER, 0.08)]))
        self.assertEqual(oc["sniper_pct"], 8.0)
        a = self._audit(oc, gp=self._gp([(self.SNIPER, 0.08)]))
        self.assertFalse(self._check(a, "sniper")["passed"])
        self.assertEqual(a["verdict"], "FAIL")

    def test_sniper_7_9_passes_onchain(self):
        self.mock_logs.return_value = (
            True,
            [self._log(self.CREATION, self.PAIR, self.SNIPER,
                       79 * 10 ** 24)],
            "")
        oc = scanner.onchain_sniper_bubble(
            self._pair(), self._gp([(self.SNIPER, 0.079)]))
        self.assertEqual(oc["sniper_pct"], 7.9)
        a = self._audit(oc, gp=self._gp([(self.SNIPER, 0.079)]))
        self.assertTrue(self._check(a, "sniper")["passed"])
        self.assertEqual(a["verdict"], "PASS", a["reasons"])

    def test_bubble_exactly_8_fails_strict_onchain(self):
        # small sniper buy (immaterial) + creator -> CLUSTER 8%
        self.mock_logs.return_value = (
            True,
            [self._log(self.CREATION, self.PAIR, self.SNIPER, 5 * 10 ** 23),
             self._log(self.CREATION, self.CREATOR, self.CLUSTER,
                       8 * 10 ** 25)],
            "")
        oc = scanner.onchain_sniper_bubble(
            self._pair(), self._gp([(self.CLUSTER, 0.08)]))
        self.assertEqual(oc["bubble_pct"], 8.0)
        a = self._audit(oc, gp=self._gp([(self.CLUSTER, 0.08)]))
        self.assertFalse(self._check(a, "bubble")["passed"])
        self.assertEqual(a["verdict"], "FAIL")

    def test_bubble_7_9_passes_onchain(self):
        self.mock_logs.return_value = (
            True,
            [self._log(self.CREATION, self.PAIR, self.SNIPER, 5 * 10 ** 23),
             self._log(self.CREATION, self.CREATOR, self.CLUSTER,
                       79 * 10 ** 24)],
            "")
        oc = scanner.onchain_sniper_bubble(
            self._pair(), self._gp([(self.CLUSTER, 0.079)]))
        self.assertEqual(oc["bubble_pct"], 7.9)
        a = self._audit(oc, gp=self._gp([(self.CLUSTER, 0.079)]))
        self.assertTrue(self._check(a, "bubble")["passed"])
        self.assertEqual(a["verdict"], "PASS", a["reasons"])

    def test_balanceof_fallback_for_wallet_outside_top10(self):
        # sniper not in GoPlus top-10, bought 5% (material) -> balanceOf 4.5%
        self.mock_logs.return_value = (
            True,
            [self._log(self.CREATION, self.PAIR, self.SNIPER, 5 * 10 ** 25)],
            "")
        with patch.object(scanner, "_rpc_balance_of",
                          return_value=45 * 10 ** 24):
            oc = scanner.onchain_sniper_bubble(self._pair(), self._gp())
        self.assertTrue(oc["ok"], oc["note"])
        self.assertEqual(oc["sniper_pct"], 4.5)
        self.assertEqual(oc["bought_pct"], 5.0)

    def test_incomplete_logs_means_unknown_not_pass(self):
        self.mock_logs.return_value = (False, [], "rpc error")
        oc = scanner.onchain_sniper_bubble(self._pair(), self._gp())
        self.assertFalse(oc["ok"])
        a = self._audit(oc)
        self.assertIsNone(self._check(a, "sniper")["passed"])
        self.assertIsNone(self._check(a, "bubble")["passed"])
        self.assertEqual(a["verdict"], "INCOMPLETE")

    def test_no_buys_in_window_means_unknown(self):
        self.mock_logs.return_value = (True, [], "")
        oc = scanner.onchain_sniper_bubble(self._pair(), self._gp())
        self.assertFalse(oc["ok"])
        a = self._audit(oc)
        self.assertEqual(a["verdict"], "INCOMPLETE")

    def test_gmgn_worse_value_wins_crosscheck(self):
        self.mock_logs.return_value = (
            True,
            [self._log(self.CREATION, self.PAIR, self.SNIPER, 5 * 10 ** 25)],
            "")
        oc = scanner.onchain_sniper_bubble(
            self._pair(), self._gp([(self.SNIPER, 0.05)]))
        a = self._audit(oc, gmgn=make_gmgn(sniper_pct=9.0),
                        gp=self._gp([(self.SNIPER, 0.05)]))
        self.assertFalse(self._check(a, "sniper")["passed"])
        self.assertIn("9.0%", self._check(a, "sniper")["value"])

    def test_scan_once_integration_onchain_ok(self):
        # full scan_once path (profiles -> pairs -> prefilter -> audit)
        # with mocked sources: a fresh active token must PASS on on-chain
        # data alone, with no GMGN key configured.
        pair = self._pair()
        pair["pairCreatedAt"] = int(time.time() * 1000) - 10 * 60 * 1000
        pair["txns"] = {"m5": {"buys": 15, "sells": 2}}
        pair["liquidity"] = {"usd": 50000}
        self.mock_logs.return_value = (
            True,
            [self._log(self.CREATION, self.PAIR, self.SNIPER, 5 * 10 ** 25)],
            "")
        gp = self._gp([(self.SNIPER, 0.05)])
        with patch.object(scanner, "fetch_new_profiles",
                          return_value=(True, [self.TOKEN], "test")), \
             patch.object(scanner, "fetch_pairs",
                          return_value=([pair], [])), \
             patch.object(scanner, "fetch_goplus",
                          return_value=(True, gp, "ok")), \
             patch.object(scanner, "fetch_sourcify",
                          return_value=(True, "match", "ok")):
            rep = scanner.scan_once(max_audit=6, blacklist=set())
        self.assertEqual(rep["audited"], 1)
        self.assertTrue(rep["sources"]["onchain"]["ok"])
        self.assertEqual(len(rep["passed"]), 1)
        self.assertEqual(rep["passed"][0]["verdict"], "PASS")

    def test_creation_block_estimator_converges(self):
        # linear block time: estimator must land on the exact block
        self.patches[3].stop()  # unpatch find_pair_creation_block
        try:
            with patch.object(
                    scanner, "_rpc_block_ts",
                    side_effect=lambda bn: 1795350000 - (self.HEAD - bn) * 0.12):
                block, exact, note = scanner.find_pair_creation_block(
                    self.TOKEN, self.PAIR, 1795349880 * 1000, ["v4"])
            self.assertEqual(block, 119999000)
            self.assertFalse(exact)
        finally:
            self.patches[3].start()


class TestLiveAPIs(unittest.TestCase):
    """Live tests — hit the real free APIs. Skip if offline."""

    def test_dexscreener_profiles_live(self):
        ok, addrs, note = scanner.fetch_new_profiles()
        self.assertTrue(ok, note)
        self.assertGreater(len(addrs), 0)

    def test_goplus_mintgoat_live(self):
        ca = "0xe5A80faf3Bd9Bc852305014F5E62CB356A0A330F"
        ok, gp, note = scanner.fetch_goplus(ca)
        self.assertTrue(ok, note)
        self.assertEqual(gp.get("buy_tax"), "0")
        self.assertEqual(gp.get("creator_percent"), "0.000000")
        self.assertEqual(gp.get("owner_address"), "")

    def test_sourcify_mintgoat_unverified(self):
        ca = "0xe5A80faf3Bd9Bc852305014F5E62CB356A0A330F"
        ok, match, note = scanner.fetch_sourcify(ca)
        self.assertTrue(ok, note)
        self.assertIsNone(match)  # verified live: unverified

    def test_rpc_blocknumber_live(self):
        ok, result = scanner._rpc("eth_blockNumber", [])
        self.assertTrue(ok, result)
        self.assertTrue(str(result).startswith("0x"))

    def test_gmgn_status_reports_missing_key(self):
        ready, note = scanner.gmgn_status()
        self.assertFalse(ready)
        self.assertIn("API key", note)


if __name__ == "__main__":
    unittest.main()
