# Daftar Fitur — App Scanner & Monitor Meme Token Robinhood Chain

- **Status:** REVISI 3 — 2026-11-22 malam. Dibangun (scanner v3, 22 Nov 20:44 WIB) + **sniper/bubble pindah ke on-chain RPC** (tanpa GMGN key) — lihat bagian 2 dan "Yang dibutuhkan". Spek Rev 2 disetujui Mas Roy 2026-11-22 20:24 WIB ("Udah pas, gas bikin").
- **Dasar:** spek Mas Roy 22 Nov 15:43 WIB + koreksi 19:30–19:34 WIB + hasil review code `rh-chain-ultra-safe-scanner-v2.py` (lihat `review-code-v2.md`).
- **Prinsip:** read-only (tanpa wallet, tanpa signing, tanpa beli/jual). Beli/jual di luar scope — keputusan terpisah.
- **Prinsip biaya: SEMUA GRATIS.** Free tier saja, tanpa API berbayar, tanpa hosting berbayar. (Mas Roy: "Gue gak ada duit. Orang niat mau nyari duit.")
- **Tanpa Telegram bot.** Notifikasi push ke HP lewat chat Muse ini.

## Aturan ambang — TEGAS (bukan kurang-lebih)

- Tanda `<` artinya **strictly below** — nilai HARUS di bawah ambang, bukan sama dengan dan bukan "kurang-lebih".
- Implementasi: token lolos hanya jika `nilai < ambang`; nilai `>= ambang` = gugur.
- Ambang final (default, bisa diubah di pengaturan):
  - Dev hold: **0%** (dev sudah tidak pegang suplai).
  - Tax beli/jual: **0%**.
  - Top 10 holder gabungan: **< 10%**.
  - Sniper (blok awal): **< 8%** — dihitung dari log Transfer on-chain
    (pembeli di blok-blok awal setelah pair dibuat).
  - Bubble/insider cluster: **< 8%** — klaster on-chain: transfer dari
    creator, penerima dari wallet sniper, wallet fresh (nonce ≤ 3).
  - LP: burn ≥ 90% ATAU dari launchpad `pons` / `trench`.

## Bentuk app

- Dashboard web (Streamlit, mobile-friendly) — dibuka dari HP/browser.
- Monitor background terpisah — jalan terus walau dashboard ditutup (untuk pantau 24/7 + kirim notifikasi).
- Notifikasi: push ke HP lewat chat Muse ini (aplikasi Muse di HP Mas Roy) + tampilan di dashboard.
- Mas Roy sudah punya app GMGN, DexScreener, CMC, dan CoinGecko di HP — tiap token di dashboard ada deep link ke GMGN/DexScreener supaya bisa di-cross-check langsung di HP.
- Hosting: Streamlit Community Cloud (gratis). Butuh repo GitHub — Mas Roy punya akun GitHub, repo belum ada; repo dibuat nanti saat deploy.
- Skill audit: logic audit BNB (skill `bsc-contract-audit` + `launch-curve-audit`) dijadikan skill juga untuk Robinhood Chain — logika sama, beda jaringan (RPC + explorer Blockscout, DEX Uniswap).

## 1. Scan token baru (sumber data)

- Feed token baru dari tiga sumber gratis sekaligus:
  - DexScreener `token-profiles/latest` (gratis),
  - Event `PairCreated` Uniswap via RPC publik (fallback),
  - Feed new-token GMGN via `gmgn-cli` (opsional — hanya kalau ada key).
- Jujur: tidak ada satu sumber gratis yang menangkap SEMUA token baru — DexScreener free hanya menampilkan token yang punya profil. Tiga sumber dipakai bareng supaya yang ketinggalan sedikit. Token yang beneran ada pasarnya (likuiditas + pembeli) pasti kecantol di DexScreener atau GMGN dalam 1–5 menit setelah pair-nya ada.
- Filter awal: umur ≤ 25 menit, likuiditas ≥ $3.500, buys ≥ 10 dan buys > sells dalam 5 menit.

## 2. Screening SE-KETAT MUNGKIN (per token, sebelum tampil)

Sumber: **on-chain RPC + DexScreener + GoPlus + Sourcify**. GMGN
(`gmgn-cli`) hanya cross-check opsional — kalau ada key, nilai GMGN
dibandingkan dengan nilai on-chain dan yang lebih buruk yang dipakai.
Tanpa batasan platform — sumber apa pun boleh dipakai selama hasilnya
bagus.

- Honeypot: simulasi jual via `eth_call` langsung ke RPC — gagal = gugur.
- Tax beli/jual: 0%.
- Dev hold: 0% (dompet creator teridentifikasi).
- Top 10 holder gabungan: < 10%.
- Sniper (blok awal): < 8% (jumlah dompet + % supply) — dari log Transfer
  on-chain, blok kreasi pair dari event factory Uniswap (v2/v3) atau
  estimasi timestamp (v4).
- Bubble/insider cluster: < 8% — dari data on-chain (transfer creator,
  penerima dari sniper, wallet fresh).
- LP: burn ≥ 90% ATAU dari launchpad `pons` / `trench`.
- Kontrak terverifikasi di Blockscout; token palsu/copycat dicek dari alamatnya, bukan namanya.
- Cek token copycat: token lain dengan nama/simbol sama + alamat berbeda.
- Riwayat deployer: apakah dompet creator pernah mendeploy token rug/honeypot sebelumnya — kalau pernah, gugur.
- Blacklist deployer: dompet creator yang pernah rug otomatis gugur tanpa dicek lagi.
- Tanggal kedaluwarsa kunci LP (bukan cuma "terkunci", tapi sampai kapan).
- Fresh wallet %: berapa % supply dipegang dompet baru (umur < 7 hari).
- Tren holder: jumlah holder naik/turun sejak token muncul.
- Verifikasi source code kontrak di Blockscout (terverifikasi / tidak).
- **Skor gabungan 0–100 per token** (ala skill audit BSC): satu angka hasil semua cek di atas, supaya gampang dibaca dan dibandingkan.
- **Hasil pemeriksaan LENGKAP ditampilkan per token** — semua cek dari on-chain RPC, GoPlus, DexScreener, Sourcify (GMGN hanya bila ada key) — nilai + lolos/gagal per cek + skor gabungan, bukan cuma badge "BERSIH". Supaya Mas Roy tau persis kenapa token itu lolos.
- Token yang gugur tidak tampil di daftar utama, tapi tercatat di log beserta alasan gugurnya per cek.
- Default fail-closed: kalau data tidak ada / tidak bisa dibaca, token gugur (bukan lolos).

## 3. Narasi & konteks

- Token baru: link X/website dari GMGN + profil DexScreener.
- Token established (sudah listing): CoinGecko / CMC / CryptoRank (harga, market cap, volume).
- Smart money: jumlah smart money yang beli (dari GMGN).
- Cek sosial/narasi: umur akun X token, website masih aktif atau tidak.

## 4. Pantauan lanjutan (watchlist)

- Token yang lolos screening masuk watchlist otomatis.
- Dicek ulang berkala: harga, likuiditas, holder, dev, sniper.
- **Cek honeypot berkala**: honeypot bisa diaktifkan belakangan oleh owner — token yang awalnya bersih dicek terus masih bisa dijual atau tidak.
- **Peringatan perubahan kontrak**: owner ganti pajak, tambah blacklist, atau buka kunci LP → langsung alert.
- **Alert harga**: naik atau turun sekian persen dari harga saat token lolos screening.
- Alert kalau: dev mulai jual, LP dicabut / dibuka kuncinya, holder besar jual banyak, sniper keluar bareng-bareng, likuiditas turun drastis, harga gerak besar.
- Token bisa dihapus dari watchlist secara manual.

## 5. Notifikasi (tanpa Telegram bot) — PUSH KE HP, BUKAN CUMA DI WEB

- Alert masuk sebagai NOTIFIKASI HP BIASA lewat chat Muse ini (aplikasi Muse di HP Mas Roy): saat token baru lolos screening atau ada alert watchlist, monitor mengirim pesan ke chat ini → HP berbunyi seperti notifikasi chat biasa. TIDAK perlu membuka website untuk dapat alert.
- Dashboard hanya untuk melihat detail (kartu token, panel pemeriksaan, log) — bukan tempat satu-satunya alert muncul.
- Di dashboard: toast + daftar alert + **bunyi saat token lolos** (kalau dashboard sedang dibuka).
- **Rekap harian ke chat**: token apa saja yang lolos hari itu + performa watchlist.
- Tidak ada Telegram bot, tidak ada push berbayar.

## 6. Dashboard (tampilan)

- Kartu per token: nama, simbol, umur, likuiditas, volume 5m, buys/sells, dev hold, top 10, tax, sniper, bubble cluster, launchpad, smart money + **skor gabungan 0–100**.
- **Panel hasil pemeriksaan lengkap per token** — semua cek on-chain RPC + GoPlus + DexScreener + Sourcify dengan nilai dan status lolos/gagal (mirip panel security GMGN + info pair DexScreener).
- Tombol per token: buka di GMGN, buka di DexScreener, salin CA.
- Status sumber data: DexScreener OK/gagal, **on-chain RPC OK/gagal**, GMGN OK/gagal, jumlah pair dicek, waktu scan terakhir — supaya gagal fetch tidak disangka "tidak ada token".
- Log token gugur (alasan gugur per cek).
- Pengaturan (sidebar): semua ambang di atas bisa diubah — umur maks, likuiditas min, dev, top 10, sniper, bubble, tax — plus auto-refresh on/off.

## 7. Keamanan

- Read-only, tanpa wallet / key / secret di code.
- Nama/simbol token di-escape (anti-XSS).
- Tidak ada perintah beli/jual di app ini.

## Di luar scope (nanti, kalau Mas Roy minta)

- Beli/jual otomatis atau semi-otomatis (butuh wallet berisi ETH chain 4663 + persetujuan eksplisit per aksi).
- Copy-trading, limit order, stop loss.
- Prediksi harga / skor "potensi naik".

## Yang dibutuhkan dari Mas Roy sebelum bangun

- Persetujuan daftar fitur revisi ini (atau koreksi lagi).
- Repo GitHub: akun sudah ada, repo belum — dibuat nanti saat deploy ke Streamlit Cloud (gratis).
- **GMGN API key TIDAK dibutuhkan lagi** — sniper/bubble sudah dihitung dari on-chain RPC (gratis, tanpa key). Alasan: free tier GMGN ternyata mensyaratkan saldo akun ≥ $100 dan pembuatan key Mas Roy gagal 2026-11-22 21:07 WIB ("Insufficient balance"). GMGN hanya cross-check opsional kalau suatu hari ada key.
- TIDAK butuh: token bot Telegram, API berbayar, hosting berbayar.
