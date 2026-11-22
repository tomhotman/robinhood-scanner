import streamlit as st
import requests
import time
from datetime import datetime

# --- KONFIGURASI HALAMAN ---
st.set_page_config(
    page_title="RH Chain Ultra-Safe Scanner v2",
    page_icon="🛡️",
    layout="centered"
)

# --- CSS KUSTOM MOBILE ---
st.markdown("""
<style>
    .token-card {
        background-color: #111827;
        border: 1px solid #10B981;
        border-radius: 12px;
        padding: 16px;
        margin-bottom: 14px;
        color: #F9FAFB;
    }
    .badge-safe {
        background-color: #065F46;
        color: #A7F3D0;
        padding: 3px 8px;
        border-radius: 6px;
        font-size: 12px;
        font-weight: bold;
    }
    .metric-box {
        font-size: 13px;
        color: #D1D5DB;
        margin-top: 8px;
        line-height: 1.6;
    }
    .security-grid {
        background-color: #1F2937;
        padding: 10px;
        border-radius: 8px;
        margin-top: 8px;
        font-size: 12.5px;
    }
</style>
""", unsafe_allow_html=True)

st.title("🛡️ Robinhood Chain Safe Screener v2")
st.caption("Audit Ketat: Dev 0% • Anti-Bubble Cluster • Anti-Sniper • Read-Only")

# --- SIDEBAR PENGATURAN ULTRA-KETAT ---
with st.sidebar:
    st.header("⚙️ Parameter Keamanan Ketat")
    max_age_min = st.slider("Umur Maksimal Token (Menit)", 5, 60, 25)
    min_liq = st.number_input("Likuiditas Minimal ($)", value=3500, step=500)
    
    st.subheader("🔒 Filter Distribusi & Dompet")
    max_dev_pct = st.number_input("Maksimal Dev Hold (%)", value=0.0, step=0.5, format="%.1f")
    max_top10_pct = st.slider("Maksimal Top 10 Holders (%)", 5, 25, 15)
    max_sniper_pct = st.slider("Maksimal Sniper Blok Awal (%)", 0, 20, 8)
    max_bubble_pct = st.slider("Maksimal Bubble / Insider Cluster (%)", 0, 20, 8)
    max_tax = st.number_input("Maksimal Buy/Sell Tax (%)", value=0.0, step=0.5, format="%.1f")
    
    auto_refresh = st.toggle("Auto-Scan Setiap 15 Detik", value=True)

# --- FUNGSI AUDIT MENDALAM GMGN (TERMASUK BUBBLE & SNIPER) ---
def audit_token_gmgn_deep(ca: str):
    """
    Mengambil data audit kontrak, distribusi top holder, sniper %, dan bubble/insider cluster.
    """
    token_url = f"https://gmgn.ai{ca}"
    stat_url = f"https://gmgn.ai{ca}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36",
        "Accept": "application/json"
    }
    try:
        resp = requests.get(token_url, headers=headers, timeout=8)
        if resp.status_code != 200:
            return None
        data = resp.json().get("data", {}).get("token", {})
        if not data:
            return None

        # Ambil statistik tambahan (Sniper & Insider/Bubble cluster) jika tersedia
        stat_data = {}
        try:
            stat_resp = requests.get(stat_url, headers=headers, timeout=6)
            if stat_resp.status_code == 200:
                stat_data = stat_resp.json().get("data", {}) or {}
        except Exception:
            pass

        # 1. Keamanan Kontrak Dasar
        is_honeypot = bool(data.get("is_honeypot", True))
        buy_tax = float(data.get("buy_tax", 100) or 0) * 100
        sell_tax = float(data.get("sell_tax", 100) or 0) * 100
        renounced = bool(data.get("renounced_mint", False) and data.get("renounced_freeze_account", False))
        lp_burned = float(data.get("burn_ratio", 0) or 0) >= 0.90
        is_launchpad = data.get("launchpad", "") in ["flap", "klik", "noxa", "pons"]

        # 2. Distribusi Dev & Top 10
        dev_hold = float(data.get("creator_token_status_pct", 100) or 0) * 100
        top10_hold = float(data.get("top_10_holder_rate", 100) or 0) * 100

        # 3. Pemeriksaan Sniper (Pembeli Blok Pertama yang masih memegang token)
        sniper_count = int(stat_data.get("top_snipers_count", data.get("sniper_count", 0)) or 0)
        sniper_hold_pct = float(stat_data.get("sniper_hold_rate", data.get("sniper_hold_rate", 0)) or 0) * 100

        # 4. Pemeriksaan Bubble Holder (Dompet terhubung / Rat Trader / Bundler Cluster)
        insider_pct = float(stat_data.get("rat_trader_amount_rate", data.get("rat_trader_amount_rate", 0)) or 0) * 100
        bundler_pct = float(stat_data.get("bundler_trader_amount_rate", data.get("bundler_rate", 0)) or 0) * 100
        fresh_wallet_pct = float(stat_data.get("fresh_wallet_rate", 0) or 0) * 100

        # Total risiko klaster gelembung (gabungan dompet terhubung/insider & bundler)
        bubble_cluster_pct = round(max(insider_pct, bundler_pct, (insider_pct + bundler_pct) * 0.7), 2)

        return {
            "is_honeypot": is_honeypot,
            "buy_tax": buy_tax,
            "sell_tax": sell_tax,
            "dev_hold": dev_hold,
            "top10_hold": top10_hold,
            "sniper_count": sniper_count,
            "sniper_hold_pct": sniper_hold_pct,
            "bubble_cluster_pct": bubble_cluster_pct,
            "fresh_wallet_pct": fresh_wallet_pct,
            "renounced": renounced,
            "lp_safe": lp_burned or is_launchpad,
            "launchpad": data.get("launchpad", "DEX Direct"),
            "smart_money_buys": int(data.get("smart_degen_count", 0) or 0)
        }
    except Exception:
        return None

# --- FUNGSI PEMINDAI DEXSCREENER ROBINHOOD ---
def fetch_new_robinhood_tokens():
    pairs_url = "https://dexscreener.com"
    found_tokens = []
    now_ms = time.time() * 1000

    try:
        res = requests.get("https://dexscreener.com", timeout=8)
        pairs = res.json().get("pairs", []) if res.status_code == 200 else []

        if not pairs:
            prof = requests.get(pairs_url, timeout=8).json()
            rh_cas = [p["tokenAddress"] for p in prof if p.get("chainId") == "robinhood"][:15]
            if rh_cas:
                res2 = requests.get(f"https://dexscreener.com{','.join(rh_cas)}", timeout=8)
                pairs = res2.json().get("pairs", []) if res2.status_code == 200 else []

        for p in pairs:
            if p.get("chainId") != "robinhood":
                continue

            created_at = p.get("pairCreatedAt", 0)
            if not created_at:
                continue

            age_min = (now_ms - created_at) / 60000
            if age_min > max_age_min or age_min < 0:
                continue

            liq = float(p.get("liquidity", {}).get("usd", 0) or 0)
            if liq < min_liq:
                continue

            txns_m5 = p.get("txns", {}).get("m5", {})
            buys_m5 = int(txns_m5.get("buys", 0))
            sells_m5 = int(txns_m5.get("sells", 0))

            if buys_m5 < 10 or buys_m5 <= sells_m5:
                continue

            ca = p.get("baseToken", {}).get("address", "")
            audit = audit_token_gmgn_deep(ca)
            if not audit:
                continue

            # --- GERBANG PENYARINGAN ULTRA-KETAT (TERMASUK BUBBLE & SNIPER) ---
            if audit["is_honeypot"]:
                continue
            if audit["buy_tax"] > max_tax or audit["sell_tax"] > max_tax:
                continue
            if audit["dev_hold"] > max_dev_pct:
                continue
            if audit["top10_hold"] > max_top10_pct:
                continue
            if audit["sniper_hold_pct"] > max_sniper_pct:
                continue
            if audit["bubble_cluster_pct"] > max_bubble_pct:
                continue
            if not audit["lp_safe"]:
                continue

            found_tokens.append({
                "name": p.get("baseToken", {}).get("name", "Unknown"),
                "symbol": p.get("baseToken", {}).get("symbol", "???"),
                "ca": ca,
                "age_min": round(age_min, 1),
                "liq": liq,
                "vol_m5": float(p.get("volume", {}).get("m5", 0) or 0),
                "buys_m5": buys_m5,
                "sells_m5": sells_m5,
                "audit": audit
            })
    except Exception as e:
        st.error(f"Gangguan koneksi pemindaian: {e}")

    return found_tokens

# --- TAMPILAN UTAMA DASHBOARD ---
status_placeholder = st.empty()
status_placeholder.info(f"🔍 Memindai Robinhood Chain (Audit Bubble & Sniper Aktif)... Cek: {datetime.now().strftime('%H:%M:%S')}")

tokens = fetch_new_robinhood_tokens()

if not tokens:
    st.warning("Belum ada token baru yang lolos filter ketat (Dev, Top 10, Sniper, & Bubble Cluster). Terus memantau...")
else:
    st.toast(f"🚨 Ditemukan {len(tokens)} Token Bersih!", icon="🛡️")
    
    for t in tokens:
        a = t["audit"]
        gmgn_link = f"https://gmgn.ai{t['ca']}"
        dex_link = f"https://dexscreener.com{t['ca']}"

        st.markdown(f"""
        <div class="token-card">
            <div style="display:flex; justify-content:space-between; align-items:center;">
                <h3 style="margin:0; color:#34D399;">{t['name']} (${t['symbol']})</h3>
                <span class="badge-safe">✅ BERSIH & TERVERIFIKASI</span>
            </div>
            <div class="metric-box">
                ⏱️ <b>Umur:</b> {t['age_min']} mnt &nbsp;|&nbsp; 💧 <b>Likuiditas:</b> ${t['liq']:,.0f} &nbsp;|&nbsp; 📊 <b>Vol 5m:</b> ${t['vol_m5']:,.0f}<br>
                🟢 <b>Beli/Jual (5m):</b> {t['buys_m5']} / {t['sells_m5']} &nbsp;|&nbsp; 🚀 <b>Launchpad:</b> {a['launchpad'].upper()} &nbsp;|&nbsp; 🧠 <b>Smart Money:</b> {a['smart_money_buys']}
            </div>
            <div class="security-grid">
                👨‍💻 <b>Dev Hold:</b> {a['dev_hold']:.1f}% &nbsp;•&nbsp;
                🐋 <b>Top 10:</b> {a['top10_hold']:.1f}% &nbsp;•&nbsp;
                💸 <b>Tax:</b> {a['buy_tax']:.0f}%/{a['sell_tax']:.0f}%<br>
                🎯 <b>Sniper Hold:</b> {a['sniper_hold_pct']:.1f}% ({a['sniper_count']} wallet) &nbsp;•&nbsp;
                🫧 <b>Bubble Cluster (Insider/Bundler):</b> {a['bubble_cluster_pct']:.1f}%
            </div>
        </div>
        """, unsafe_allow_html=True)

col1, col2 = st.columns(2)
with col1:
    st.link_button("⚡ Buka & Cek Bubble Map di GMGN", gmgn_link, use_container_width=True)
with col2:
    st.link_button("📈 Lihat Grafik DexScreener", dex_link, use_container_width=True)
st.code(t["ca"], language="text")
if auto_refresh:
    time.sleep(15)
    st.rerun()
