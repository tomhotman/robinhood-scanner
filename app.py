"""RH Chain Ultra-Safe Scanner v3 — dashboard (Streamlit).

Rebuilt from the reviewed v2 code. Fixes vs v2:
  - DexScreener via the real API (api.dexscreener.com), never the homepage.
  - Sniper/bubble from on-chain Transfer logs (RPC) — no GMGN key needed;
    GMGN via gmgn-cli only as an optional cross-check (never web scrape).
    Checks with missing data show UNKNOWN, never a guessed pass.
  - STRICT thresholds: pass only if value < threshold (dev 0%, tax 0%,
    top10 <10%, sniper <8%, bubble <8%).
  - Full check panel per token (every check: value + pass/fail + 0-100 score).
  - GMGN/DexScreener links + copy-CA INSIDE the token loop.
  - No raw HTML with token data (native widgets escape automatically);
    token names/symbols are never injected into markup.
  - Data-source status line so a failed fetch is never mistaken for
    "no tokens".
  - Watchlist with re-audit + alerts, persisted in state/.

Read-only. No wallet, no keys, no signing, no buy/sell. Free tier only.
"""

import json
import os
import sys
import time

import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import scanner  # noqa: E402

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.path.join(BASE_DIR, "state")
os.makedirs(STATE_DIR, exist_ok=True)

st.set_page_config(
    page_title="RH Chain Ultra-Safe Scanner v3",
    page_icon="🛡️",
    layout="centered",
)

st.title("🛡️ Robinhood Chain Safe Screener v3")
st.caption("Audit Ketat: Dev 0% • Top 10 <10% • Sniper <8% • Bubble <8% • "
           "Tax 0% • Read-Only (tanpa wallet)")

# --- sidebar: strict thresholds (defaults = Mas Roy's spec) ---------------
with st.sidebar:
    st.header("⚙️ Parameter Keamanan Ketat")
    st.caption("Lolos hanya jika nilai < ambang (tegas, bukan kurang-lebih).")
    st.slider("Umur Maksimal Token (Menit)", 5, 60, 25, key="max_age_min")
    st.number_input("Likuiditas Minimal ($)", value=3500, step=500,
                    key="min_liq")
    st.number_input("Maksimal Dev Hold (%)", value=0.0, step=0.1,
                    format="%.2f", key="max_dev_pct")
    st.slider("Maksimal Top 10 Holders (%)", 1, 25, 10, key="max_top10_pct")
    st.slider("Maksimal Sniper (%)", 1, 20, 8, key="max_sniper_pct")
    st.slider("Maksimal Bubble / Insider (%)", 1, 20, 8, key="max_bubble_pct")
    st.number_input("Maksimal Buy/Sell Tax (%)", value=0.0, step=0.5,
                    format="%.1f", key="max_tax")
    st.toggle("Auto-Scan Setiap 60 Detik", value=True, key="auto_refresh")
    st.slider("Maks Token Diaudit per Scan", 3, 20, 12, key="max_audit")


def _cfg():
    ss = st.session_state
    return {
        "max_age_min": ss.get("max_age_min", 25),
        "min_liq_usd": float(ss.get("min_liq", 3500)),
        "max_dev_pct": float(ss.get("max_dev_pct", 0.0)),
        "max_top10_pct": float(ss.get("max_top10_pct", 10)),
        "max_sniper_pct": float(ss.get("max_sniper_pct", 8)),
        "max_bubble_pct": float(ss.get("max_bubble_pct", 8)),
        "max_tax_pct": float(ss.get("max_tax", 0.0)),
    }


def render_dashboard():
    """One scan + full render. Re-run by the auto-refresh fragment."""
    cfg = _cfg()
    # NOTE: the scanner's dev gate is a dust tolerance (dev_dust_pct 0.01%);
    # the sidebar value above 0 loosens it only if the user raises it.
    now = time.time()
    last = st.session_state.get("last_scan")
    if last is None or now - st.session_state.get("last_scan_at", 0) > 60:
        with st.spinner("Memindai Robinhood Chain…"):
            rep = scanner.scan_once(
                cfg, max_audit=st.session_state.get("max_audit", 12),
                blacklist=scanner.load_blacklist(STATE_DIR))
        st.session_state["last_scan"] = rep
        st.session_state["last_scan_at"] = now
        added, _ = scanner.update_watchlist_from_scan(STATE_DIR,
                                                      rep["passed"])
        if added:
            scanner.write_recap(STATE_DIR, rep, [])
    else:
        rep = last

    # --- data-source status line (never confuse failure with "no tokens") --
    src = rep.get("sources", {})
    dex_ok = src.get("dexscreener_profiles", {}).get("ok")
    gmgn = src.get("gmgn", {})
    onchain = src.get("onchain", {})
    audited = rep.get("audited", 0)
    if onchain.get("ok"):
        onchain_bit = "On-chain RPC: ✅ OK"
    elif audited == 0:
        onchain_bit = "On-chain RPC: — (belum ada token diaudit)"
    else:
        onchain_bit = "On-chain RPC: ⚠️ " + onchain.get("note", "?")
    # GMGN is optional since on-chain sniper/bubble replaced it: never warn
    # when it is simply not configured.
    gmgn_bit = ("GMGN: ✅ OK (cross-check aktif)" if gmgn.get("ok")
                else "GMGN: opsional (tidak dipakai)")
    status_bits = [
        f"DexScreener: {'✅ OK' if dex_ok else '❌ GAGAL'}",
        onchain_bit,
        gmgn_bit,
        f"Pair dicek: {rep.get('pairs_checked', 0)}",
        f"Lolos pre-filter: {rep.get('prefiltered', 0)}",
        f"Diaudit: {audited}",
        f"Scan terakhir: {rep.get('ts', '-')}",
    ]
    st.info(" | ".join(status_bits))

    if not dex_ok:
        st.error("DexScreener tidak merespons — hasil scan kosong BUKAN "
                 "berarti tidak ada token. Coba lagi beberapa saat.")
        return

    passed = rep.get("passed", [])
    incomplete = rep.get("incomplete", [])

    if passed:
        st.toast(f"🚨 Ditemukan {len(passed)} Token Bersih!", icon="🛡️")
        # best-effort beep (browser may block autoplay until user interacts)
        try:
            st.components.v1.html(
                "<audio autoplay><source src='data:audio/wav;base64,"
                "UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAIA+AAACABAAZGF0YQAAAAA=' "
                "type='audio/wav'></audio>",
                height=0)
        except Exception:
            pass

    if not passed and not incomplete:
        st.warning("Belum ada token baru yang lolos filter ketat. "
                   "Status sumber data di atas OK — memang belum ada yang "
                   "lolos, bukan error.")
    else:
        st.success(f"✅ {len(passed)} token LOLOS semua cek ketat "
                   f"(skor 0–100 di tiap kartu).")

    # --- passed tokens: full check panel, links INSIDE the loop -----------
    for t in passed:
        # st.* widgets escape text automatically; token names/symbols are
        # never injected into raw HTML anywhere in this app.
        title = (f"{t['name']} (${t['symbol']}) — Skor {t['score']}/100")
        with st.expander(title, expanded=True):
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Umur", f"{t['age_min']} mnt")
            c2.metric("Likuiditas", f"${t['liq_usd']:,.0f}")
            c3.metric("Vol 5m", f"${t['vol_m5']:,.0f}")
            c4.metric("Beli/Jual 5m", f"{t['buys_m5']} / {t['sells_m5']}")
            st.markdown("**Hasil pemeriksaan lengkap:**")
            rows = []
            for c in t["checks"]:
                mark = "✅" if c["passed"] is True else (
                    "❌" if c["passed"] is False else "❓")
                rows.append({"Cek": c["label"], "Hasil": c["value"],
                             "Status": mark, "Sumber": c["source"]})
            st.table(rows)
            if t.get("extra"):
                ex = t["extra"]
                st.caption(
                    f"Smart money: {ex.get('smart_wallets')} wallet • "
                    f"KOL: {ex.get('renowned_wallets')} • "
                    f"Fresh wallet: {ex.get('fresh_wallet_rate')} • "
                    f"Rug ratio: {ex.get('rug_ratio')} • "
                    f"Launchpad: {ex.get('launchpad') or '-'} • "
                    f"X: {ex.get('twitter') or '-'} • "
                    f"Web: {ex.get('website') or '-'}")
            # links + CA — INSIDE the token loop (v2 bug: they were outside)
            gmgn_link = f"https://gmgn.ai/robinhood/token/{t['ca']}"
            dex_link = f"https://dexscreener.com/robinhood/{t['ca']}"
            b1, b2 = st.columns(2)
            b1.link_button("⚡ Buka di GMGN", gmgn_link,
                           use_container_width=True)
            b2.link_button("📈 Buka di DexScreener", dex_link,
                           use_container_width=True)
            st.code(t["ca"], language="text")

    # --- incomplete: candidates missing data (never shown as clean) --------
    if incomplete:
        st.subheader("⏳ Kandidat — data belum lengkap (BUKAN token bersih)")
        st.caption("Token-token ini lolos pre-filter tapi ada cek keras yang "
                   "datanya tidak tersedia dari sumber gratis "
                   "(RPC/GoPlus/GMGN). Tidak ditampilkan sebagai bersih.")
        for t in incomplete:
            title = (f"{t['name']} (${t['symbol']}) "
                     f"— Skor {t['score']}/100")
            with st.expander(title):
                for r in t["reasons"]:
                    st.write("• " + r)
                rows = []
                for c in t["checks"]:
                    mark = "✅" if c["passed"] is True else (
                        "❌" if c["passed"] is False else "❓")
                    rows.append({"Cek": c["label"], "Hasil": c["value"],
                                 "Status": mark, "Sumber": c["source"]})
                st.table(rows)
                st.code(t["ca"], language="text")

    # --- failed log ----------------------------------------------------------
    failed = rep.get("failed_log", [])
    if failed:
        with st.expander(f"🗑️ Log token gugur ({len(failed)})"):
            for f in failed[:50]:
                st.write(f"• `{f.get('ca', '')[:12]}…` "
                         f"({f.get('symbol', '')}): "
                         + "; ".join(f.get("reasons", [])[:4]))

    # --- watchlist + recent alerts ------------------------------------------
    st.subheader("👀 Watchlist — token lolos yang terus dipantau")
    wl = scanner._load_json(os.path.join(STATE_DIR, "watchlist.json"), {})
    if not wl:
        st.caption("Watchlist masih kosong — token yang lolos scan akan "
                   "masuk otomatis ke sini.")
    else:
        for ca, v in wl.items():
            st.write(f"• **{v.get('symbol', '')}** — "
                     f"skor {v.get('score')} • masuk {v.get('first_seen')} • "
                     f"harga ±{v.get('price_chg_pct', 0)}% • "
                     f"cek terakhir {v.get('last_check', '-')}")
    alerts_path = os.path.join(STATE_DIR, "alerts.jsonl")
    recent_alerts = []
    if os.path.exists(alerts_path):
        with open(alerts_path, encoding="utf-8") as fh:
            lines = fh.readlines()[-20:]
        for ln in lines:
            try:
                recent_alerts.append(json.loads(ln))
            except Exception:
                pass
    if recent_alerts:
        st.subheader("🔔 Alert terakhir")
        for a in reversed(recent_alerts):
            st.write(f"• [{a.get('ts')}] **{a.get('type')}** "
                     f"{a.get('symbol', '')} — {a.get('detail')}")


# Auto-refresh via a Streamlit fragment (non-blocking): the fragment re-runs
# itself every 60 s; the sidebar toggle switches it off.
if st.session_state.get("auto_refresh", True):
    st.fragment(run_every=60)(render_dashboard)()
else:
    render_dashboard()
