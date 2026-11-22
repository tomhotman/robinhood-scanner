# Review Code — RH Chain Ultra-Safe Scanner v2 (Streamlit)

- **Diterima:** 2026-11-22 17:44 WIB, dipaste Mas Roy di chat (belum dibilang asalnya dari mana).
- **File asli:** `rh-chain-ultra-safe-scanner-v2.py` (di folder yang sama).
- **Metode:** review statis saja — code TIDAK dijalankan, TIDAK diubah.
- **Hasil singkat:** AMAN dari sisi dana (tidak bisa mencuri), tapi TIDAK BERFUNGSI — dua sumber datanya salah alamat, jadi app tidak akan pernah menemukan token.

## 1. Keamanan — AMAN (tidak bisa curi dana)

- Tidak ada private key, seed phrase, mnemonic, keystore, atau alamat dompet di code.
- Tidak ada signing, approve, swap, transfer, atau koneksi wallet apa pun.
- Hanya `requests.get` (baca saja) ke gmgn.ai dan dexscreener.com. Tidak ada POST, tidak ada pengiriman data keluar.
- Tidak ada `eval`, `exec`, `subprocess`, atau download code dari luar.
- Dependency hanya `streamlit` + `requests` (standar).
- Tidak butuh `.env` / API key / secret apa pun untuk jalan.

### Satu lubang keamanan (sedang)

- `st.markdown(..., unsafe_allow_html=True)` + nama/simbol token dimasukkan mentah ke HTML: `{t['name']} (${t['symbol']})`.
- Nama token dikontrol pembuat token (siapa pun bisa menamai tokennya dengan payload HTML/JS, mis. `<img onerror=...>`).
- Risiko: injeksi HTML/JS (XSS) di browser orang yang membuka app.
- Perbaiki: `html.escape()` untuk nama/simbol, atau hapus `unsafe_allow_html=True`.

## 2. Fungsi — TIDAK BERFUNGSI (dua sumber data salah alamat)

### 2a. DexScreener: dipanggil ke homepage, bukan API

- Code: `requests.get("https://dexscreener.com")` lalu `.json()`.
- Itu halaman website (HTML), bukan API — `.json()` selalu gagal → `except` → `st.error("Gangguan koneksi pemindaian...")` dan hasil kosong.
- Fallback di dalam (`prof = requests.get(pairs_url).json()`) juga memanggil URL yang sama → gagal juga.
- Yang benar (dites live 22 Nov 2026, endpoint merespons):
  - Token terbaru: `https://api.dexscreener.com/token-profiles/latest/v1` → list `{url, chainId, tokenAddress, ...}` (bisa difilter `chainId == "robinhood"`).
  - Data pair per token: `https://api.dexscreener.com/latest/dex/tokens/<alamat1,alamat2,...>` → objek pair dengan field yang dipakai code (`pairCreatedAt`, `liquidity.usd`, `txns.m5`, `volume.m5`, `baseToken`).
- Catatan jujur: API gratis DexScreener TIDAK punya endpoint "semua pair baru per chain". `token-profiles/latest` hanya mengembalikan token yang punya profil (gambar/deskripsi) — bukan semua token baru. Untuk feed launch yang lengkap, perlu salah satu: feed new-token GMGN (gmgn-cli), event `PairCreated` Uniswap via RPC, atau DexScreener API berbayar.

### 2b. GMGN: URL malformed, bukan endpoint data

- Code: `token_url = f"https://gmgn.ai{ca}"` → hasilnya mis. `https://gmgn.ai0x1234...` (tidak ada path, tidak ada chain).
- `stat_url` isinya SAMA PERSIS dengan `token_url` — "statistik tambahan" itu cuma request duplikat ke URL rusak yang sama.
- Situs GMGN butuh login dan tidak mengembalikan JSON terstruktur dari web — cara resminya adalah `gmgn-cli` (skill resmi GMGN, sudah terkonfirmasi dukung chain `robinhood`, launchpad `trench` + `pons`).
- Nama field yang dipakai code (`is_honeypot`, `buy_tax`, `creator_token_status_pct`, `rat_trader_amount_rate`, `bundler_rate`, `fresh_wallet_rate`, `smart_degen_count`, `burn_ratio`, `launchpad`) adalah tebakan — harus diambil dari skema respons `gmgn-cli` yang asli, bukan ditebak.

### 2c. Link di UI juga rusak

- `gmgn_link = f"https://gmgn.ai{t['ca']}"` → seharusnya `https://gmgn.ai/robinhood/token/<ca>`.
- `dex_link = f"https://dexscreener.com{t['ca']}"` → seharusnya `https://dexscreener.com/robinhood/<ca>`.

### 2d. Tombol & CA di luar loop — hanya token terakhir

- `col1, col2` (tombol GMGN + DexScreener) dan `st.code(t["ca"])` berada DI LUAR `for t in tokens:`.
- Akibat: untuk semua kartu token yang tampil, tombolnya hanya mengarah ke token TERAKHIR, dan CA yang ditampilkan hanya CA token terakhir.
- Perbaiki: pindahkan tombol + `st.code` ke DALAM loop, per kartu token.

## 3. Minus lain vs spek Mas Roy (22 Nov 15:43 WIB)

| Spek | Status di code |
|---|---|
| GMGN + DexScreener | Dicoba, tapi dua-duanya salah alamat → tidak ada data |
| Narasi CoinGecko/CMC | TIDAK ADA SAMA SEKALI — tidak ada satu pun pemanggilan ke CoinGecko/CMC (wajar untuk token baru yang belum listing, tapi code tidak mencoba sama sekali) |
| Dev hold ~0% | Gate ada (default 0%, bagus), sumber datanya rusak |
| Sniper <10% | Gate ada (default 8%), sumber datanya rusak |
| Top 10 holder gabungan <10% | Gate ada, TAPI default slider 15% — lebih longgar dari spek. Ubah default jadi 10 |
| Token baru dicek umur (menit) | Ada (filter `age_min`, default 25 mnt) |
| Notifikasi dari app | TIDAK ADA — hanya `st.toast` di layar. Tidak ada push (Telegram/dll) |
| Token lolos tetap dipantau | TIDAK ADA — tidak ada watchlist/persistensi; tiap run hanya scan token baru |
| Bubble cluster (insider/bundler) | Gate ada (default 8%) — ide bagus, di luar spek, tapi sumber datanya rusak |
| Launchpad | Daftar `["flap", "klik", "noxa", "pons"]` — `trench` (launchpad Robinhood) KETINGGALAN |
| `renounced` | Pakai field gaya Solana (`renounced_mint`, `renounced_freeze_account`) — tidak berlaku di EVM/Robinhood Chain. Variabelnya juga tidak pernah dipakai di gate maupun tampilan (dead code) |
| Penanganan error | Gagal fetch = pesan yang sama dengan "tidak ada token lolos" — tidak bisa dibedakan. Tambahkan status: DexScreener OK? GMGN OK? Berapa pair dicek? |

## 4. Yang bagus di code ini (adil dicatat)

- Desain read-only — tidak ada jalan untuk memindahkan dana.
- Default fail-closed yang benar: `is_honeypot` default True, tax default 100, dev/top10 default 100 (kalau data tidak ada, token gugur, bukan lolos).
- Logika gate-nya sesuai spek (dev, top 10, sniper, bubble, LP, tax).
- Pre-filter masuk akal: umur, likuiditas minimal, buys > sells di 5 menit.
- Tampilan mobile-friendly, auto-refresh 15 detik.

## 5. Yang dibutuhkan

### Untuk sekadar menjalankan
- `pip install streamlit requests`, lalu `streamlit run rh-chain-ultra-safe-scanner-v2.py`. Tanpa key, tanpa wallet, tanpa secret.
- Tapi tanpa perbaikan di bawah, app hanya akan menampilkan error koneksi + "Belum ada token..." selamanya.

### Untuk memperbaiki (wajib)
1. DexScreener → pakai `api.dexscreener.com` (token-profiles/latest + latest/dex/tokens).
2. GMGN → pakai `gmgn-cli` (skill resmi GMGN) untuk chain `robinhood`; ambil nama field dari skema respons aslinya.
3. Perbaiki link: `gmgn.ai/robinhood/token/<ca>`, `dexscreener.com/robinhood/<ca>`.
4. Pindahkan tombol + CA ke dalam loop per token.
5. Default Top 10 → 10%; tambahkan `trench` ke daftar launchpad; hapus field `renounced` gaya Solana.
6. Escape nama/simbol token (`html.escape`) atau hapus `unsafe_allow_html`.
7. Tambahkan status sumber data (OK/gagal, jumlah pair dicek) supaya gagal fetch tidak disangka "tidak ada token".

### Untuk memenuhi spek penuh (tambahan)
8. Notifikasi push: butuh Telegram Bot token (atau channel lain) — Streamlit toast saja tidak cukup.
9. Watchlist + audit ulang: simpan token yang lolos (file/DB) dan cek ulang tiap refresh — termasuk alert kalau dev mulai jual atau LP dicabut.
10. Pantau 24/7: app Streamlit hanya jalan saat halamannya dibuka; Streamlit Cloud tidur saat idle. Butuh runner background (cron/service) terpisah untuk monitor terus-menerus.
11. GMGN API key: dicek saat install skill `gmgn-cli` (langkah `gmgn-cli config --check`) — belum bisa dipastikan dari review ini apakah key diperlukan untuk endpoint token.
12. Wallet: TIDAK dibutuhkan untuk monitoring. Baru dibutuhkan kalau nanti mau beli (ETH di chain 4663) — itu keputusan terpisah.

## 6. Keputusan akhir

- **Aman dipakai?** Ya — dari sisi dana, code ini tidak bisa mencuri apa pun. Satu-satunya perbaikan keamanan: escape nama token (XSS).
- **Bisa langsung dipakai?** Tidak — harus diperbaiki dulu bagian sumber datanya (poin 5, nomor 1–7), kalau tidak app tidak akan pernah menemukan token.
- **Asal code:** belum dijawab Mas Roy — tanyakan (buatan sendiri / grup Telegram / dibeli / fork) karena itu mengubah tingkat kecurigaan review.
