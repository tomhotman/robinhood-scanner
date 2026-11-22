# RH Chain Ultra-Safe Scanner v3

Scanner + monitor meme-token di **Robinhood Chain (chain 4663)** dengan
screening seketat mungkin, sesuai spek Mas Roy (fitur-app.md Rev 3,
disetujui 2026-11-22). **Read-only — tanpa wallet, tanpa private key,
tanpa signing, tanpa buy/sell. Semua sumber gratis.**

## File

| File | Isi |
|---|---|
| `rh-chain-ultra-safe-scanner-v2.py` | Kode asli (AI-generated, BROKEN) — referensi saja, jangan dipakai |
| `review-code-v2.md` | Review kode v2: aman tapi tidak berfungsi + daftar defect |
| `fitur-app.md` | Daftar fitur Rev 2 (disetujui) |
| `scanner.py` | Mesin scan + audit + watchlist + alert (dipakai app & monitor) |
| `app.py` | Dashboard Streamlit v3 |
| `monitor.py` | Monitor background: 1 siklus scan + re-audit watchlist, tulis alert ke `state/alerts.jsonl` |
| `tests/test_scanner.py` | 34 unit/live test (strict threshold + on-chain + API live) |
| `requirements.txt` | `requests`, `streamlit` |
| `venv/` | Virtualenv (Streamlit 1.67.0) |
| `state/` | `watchlist.json`, `alerts.jsonl`, `last_scan.json`, `recap.json`, `blacklist.json` |

Skill audit: `~/workspace/custom-skills/robinhood-contract-audit/`
(adapatasi dari `bsc-contract-audit`, chain 4663).

## Cara jalan (lokal, gratis)

```bash
cd ~/workspace/robinhood-scanner
./venv/bin/streamlit run app.py        # dashboard di http://localhost:8501
python3 monitor.py                     # 1 siklus scan (untuk cron tiap 5-15 mnt)
python3 -m unittest discover -s tests  # 34 test
```

## Sumber data (semua gratis, tanpa key)

- **DexScreener** — `api.dexscreener.com/token-profiles/latest/v1` (token baru)
  + `/latest/dex/tokens/{ca}` (pair, likuiditas, volume, umur).
- **GoPlus** — `api.gopluslabs.io/api/v1/token_security/4663` (tax, creator,
  holder, LP lock/burn, owner). **Tidak ada field `is_honeypot` di chain
  4663** — honeypot diputuskan lewat simulasi transfer `eth_call` di RPC.
- **Sourcify v2** — `sourcify.dev/server/v2/contract/4663/{ca}` (verifikasi
  kontrak; HTTP 404 = tidak terverifikasi).
- **RPC publik** — `https://rpc.mainnet.chain.robinhood.com`
  (rate-limited; `eth_blockNumber` + simulasi transfer). **Butuh header
  User-Agent** (tanpa UA dibalas HTTP 403).
- **On-chain RPC (tanpa key)** — sniper + bubble/insider dihitung langsung
  dari log Transfer (`eth_getLogs`): pembeli di blok-blok awal setelah pair
  dibuat + klaster insider (transfer dari creator, penerima dari wallet
  sniper, wallet fresh). Blok kreasi dari event factory Uniswap v2/v3
  (`PairCreated`/`PoolCreated`) atau estimasi timestamp (v4, diverifikasi
  iteratif). Lihat `onchain-fallback-notes.md`.

## Sniper/bubble — dari on-chain (GMGN TIDAK dibutuhkan lagi)

Sejak 2026-11-22 malam, **sniper (< 8%) dan bubble/insider (< 8%)
dihitung dari data on-chain** (`onchain_sniper_bubble()` di `scanner.py`),
bukan dari GMGN. Alasannya: free tier GMGN ternyata mensyaratkan saldo
akun ≥ $100 ("Free tier requires a minimum balance of $100"), dan
pembuatan API key Mas Roy GAGAL dengan error "Insufficient balance"
(2026-11-22 21:07 WIB). Tanpa pengganti ini token tidak pernah bisa PASS.

**GMGN sekarang opsional, hanya cross-check:** kalau suatu hari
`GMGN_API_KEY` tersedia, nilai GMGN dibandingkan dengan nilai on-chain
dan yang **lebih buruk (lebih tinggi) yang dipakai** (fail-closed). Tanpa
key, on-chain adalah satu-satunya sumber dan token **bisa PASS** di
atasnya. Jangan jalankan `gmgn-cli config` — output-nya pernah tidak lolos
safety check; jalur manual gmgn.ai/ai tetap didokumentasikan di
`gmgn-key-steps.md` kalau nanti dibutuhkan.

## Aturan ketat (lolos hanya jika nilai < ambang)

| Cek | Ambang | Sumber |
|---|---|---|
| Dev hold | ~0% (dust 0,01%) | GoPlus / GMGN |
| Tax beli/jual | 0% | GoPlus / GMGN |
| Top 10 holder | < 10% | GoPlus / GMGN |
| Sniper | < 8% | on-chain RPC (+ GMGN cross-check) |
| Bubble/insider | < 8% | on-chain RPC (+ GMGN cross-check) |
| LP | dibakar / terkunci ≥ 90% / launchpad pons/trench | GoPlus / GMGN |
| Honeypot | simulasi transfer holder via RPC harus sukses | RPC eth_call |
| Kontrak terverifikasi | Sourcify match / GoPlus open_source | Sourcify / GoPlus |
| Owner | renounced | GoPlus / GMGN |
| Copycat | tidak ada token lain bernama sama (Robinhood) | DexScreener |
| Deployer | tidak di blacklist, tidak pernah buat honeypot | GoPlus / blacklist lokal |

Skor 0–100 (bobot per cek), verdict **PASS / INCOMPLETE / FAIL**.
INCOMPLETE = ada cek keras yang datanya tidak ada → tidak ditampilkan
sebagai bersih.

## Notifikasi

**Tidak ada Telegram/email.** Monitor menulis alert ke
`state/alerts.jsonl` + rekap harian ke `state/recap.json`; parent agent
(Bodhi) yang menyampaikan ke chat Mas Roy sebagai push notification HP
biasa. Dashboard hanya untuk detail. Jenis alert: `token_lolos`,
`dev_nambah_hold`, `lp_dibuka`, `holder_besar_nambah`,
`honeypot_muncul`, `tax_berubah`, `owner_muncul`,
`likuiditas_anjlok`, `harga_gerak_besar`, `data_hilang`.

## Deploy (langkah berikut — belum dilakukan)

Butuh repo GitHub (Mas Roy punya akun, belum ada repo; `gh` belum login):

1. `gh repo create robinhood-scanner --public` (atau buat repo di github.com)
2. Push folder ini (tanpa `venv/` dan `state/` — lihat `.gitignore` bila ada)
3. Streamlit Community Cloud (gratis) → New app → repo → `app.py`
   → Deploy. Monitor jalan via cron di VM / GitHub Actions (gratis tier).

## Test (2026-11-22, semua lolos)

- `python3 -m unittest discover -s tests` — **34/34 OK** (13 strict
  threshold + 4 LP state + 5 live API + 12 on-chain: keccak, batas 8%
  sniper/bubble, balanceOf fallback, data tidak lengkap → UNKNOWN,
  GMGN cross-check, estimasi blok kreasi, integrasi scan_once).
- Live audit MINTGOAT (`0xe5A80f…`): verdict **FAIL skor 77** — honeypot
  sim PASS, tax 0 PASS, dev 0% PASS, top10 1,13% PASS, owner renounced
  PASS, **sniper on-chain 0,7% (1 wallet) PASS, bubble on-chain 0,0%
  PASS**, tapi **LP tidak terkunci** + **kontrak tidak terverifikasi** +
  **ada copycat**. Sniper/bubble kini ditentukan (bukan UNKNOWN) tanpa
  GMGN key — token bisa mencapai verdict PASS/FAIL penuh.
- Live monitor cycle: DexScreener OK (2 profil Robinhood, 1 pair),
  pre-filter 0 (pair sudah > 25 mnt), exit 0, state files tertulis.
- AppTest headless: judul + status line + warning empty-result tampil,
  tanpa exception (3,5 dtk).
