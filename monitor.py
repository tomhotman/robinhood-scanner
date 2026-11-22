"""RH Chain monitor — one scan cycle + watchlist re-audit.

Designed to run on a schedule (cron every 5-15 minutes) on any machine with
Python 3 + requests. It NEVER sends Telegram/email/push itself: every alert
is appended to state/alerts.jsonl (and recap data to state/recap.json) for
the parent agent to deliver to Mas Roy's chat (real phone push via Muse).

Usage:
    python3 monitor.py [--state-dir state] [--max-audit 12]

Read-only. No wallet, no keys, no signing, no buy/sell. Free tier only.
Exit 0 = cycle completed (even with zero tokens); exit 2 = scan sources failed.
"""

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import scanner  # noqa: E402

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state-dir", default=os.path.join(BASE_DIR, "state"))
    ap.add_argument("--max-audit", type=int, default=12)
    args = ap.parse_args()
    os.makedirs(args.state_dir, exist_ok=True)

    blacklist = scanner.load_blacklist(args.state_dir)
    rep = scanner.scan_once(max_audit=args.max_audit, blacklist=blacklist)
    scanner._save_json(os.path.join(args.state_dir, "last_scan.json"), rep)

    new_alerts = []
    now = rep["ts"]
    # newly passed tokens -> alert + watchlist
    added, _ = scanner.update_watchlist_from_scan(args.state_dir, rep["passed"])
    for rec in rep["passed"]:
        if rec["ca"].lower() in added:
            new_alerts.append({
                "ts": now, "ca": rec["ca"], "symbol": rec["symbol"],
                "type": "token_lolos",
                "detail": (f"LOLOS semua cek ketat — skor {rec['score']}/100, "
                           f"umur {rec['age_min']} mnt, liq "
                           f"${rec['liq_usd']:,.0f}, "
                           f"buys/sells 5m {rec['buys_m5']}/{rec['sells_m5']}, "
                           f"dev/top10/sniper/bubble sesuai ambang"),
            })
    # watchlist re-audit -> alerts
    wl_alerts = scanner.re_audit_watchlist(args.state_dir, blacklist=blacklist)
    new_alerts.extend(wl_alerts)
    # auto-blacklist creators of honeypot-failed tokens
    bl_reasons = {}
    for b in rep.get("blacklist_add", []):
        bl_reasons[b["creator"].lower()] = b["reason"]
    if bl_reasons:
        blacklist |= set(bl_reasons)
        scanner.save_blacklist(args.state_dir, blacklist, bl_reasons)
        print(f"  blacklist +{len(bl_reasons)} creator (honeypot)")
    for a in new_alerts:
        scanner._append_jsonl(os.path.join(args.state_dir, "alerts.jsonl"), a)
    scanner.write_recap(args.state_dir, rep, new_alerts)

    print(f"[{now}] scan: pairs={rep['pairs_checked']} "
          f"prefiltered={rep['prefiltered']} audited={rep['audited']} "
          f"passed={len(rep['passed'])} incomplete={len(rep['incomplete'])} "
          f"alerts={len(new_alerts)}")
    for a in new_alerts:
        print(f"  ALERT {a['type']} {a.get('symbol', '')} {a['ca'][:12]}… "
              f"— {a['detail'][:100]}")
    if not rep["sources"].get("dexscreener_profiles", {}).get("ok"):
        print("  WARNING: DexScreener tidak merespons — hasil kosong bukan "
              "berarti tidak ada token.")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
