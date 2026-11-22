# Catatan pengganti on-chain (kalau GMGN tidak dipakai)

Dicek 2026-11-22 21:08 WIB, sebelum Mas Roy memilih jalur.
**DIBANGUN 2026-11-22 malam** — `onchain_sniper_bubble()` di `scanner.py`,
terintegrasi ke `audit_token` + `scan_once` + `app.py`. Status: jalan,
divalidasi live di MINTGOAT (lihat bawah).

## Temuan

- **Blockscout REST API TIDAK bisa di-curl** — `robinhoodchain.blockscout.com/api/v2/...`
  mengembalikan halaman challenge Cloudflare ("Just a moment..."), bahkan dengan
  User-Agent browser. Jadi jangan andalkan REST Blockscout dari script/cron.
- **RPC publik JALAN** — `https://rpc.mainnet.chain.robinhood.com` merespons
  `eth_blockNumber` (contoh: `0x73172d6`). Jalur on-chain yang viable adalah
  query log langsung via RPC.
- **RPC butuh header User-Agent** — tanpa UA dibalas HTTP 403 (urllib default
  UA diblok; `_rpc` di `scanner.py` selalu set UA, curl juga jalan).
- **Block time ~0,11 dtk** — diukur 0,113 dtk (1000 blok terakhir) dan
  0,111 dtk (100.000 blok terakhir), 2026-11-22 ~21:10 WIB. Chain sangat
  cepat: jendela 60 dtk ≈ 500+ blok.
- **GoPlus `holders` hanya TOP 10** — `holder_count` bisa 16 tapi array
  `holders[]` cuma 10 entri (MINTGOAT). Wallet di luar top-10 harus
  diselesaikan via `eth_call balanceOf` (dibatasi 10 call) — lihat
  "Jebakan skala totalSupply" di bawah.
- **Pair v4: penjual = PoolManager** — di Uniswap v4 token keluar lewat
  `PoolManager 0x8366a39cc670b4001a1121b8f6a443a643e40951`, bukan dari
  alamat pair. Pair v4 juga **tidak punya event factory** (`PairCreated`/
  `PoolCreated` hanya v2/v3) — blok kreasi harus diestimasi dari timestamp.
- **Estimasi blok kreasi butuh iterasi** — satu putaran verify/adjust masih
  meleset ~331 blok pada token berumur ~3 jam (MINTGOAT: estimasi awal
  120589081 vs blok asli 120588750), karena block time berubah-ubah.
  Implementasi iterasi sampai 3 putaran (berhenti kalau selisih ≤ 10 dtk)
  → MINTGOAT mendarat di 120588787 (37 blok dari aslinya, di dalam buffer
  −60 blok). Token baru (≤ 25 mnt) jauh lebih akurat karena block time
  stabil dalam rentang pendek.

## Jebakan skala totalSupply (BUG yang sempat ada — sudah diperbaiki)

GoPlus `total_supply` dalam **satuan token** (MINTGOAT: 1.000.000.000),
sementara nilai log Transfer dan `balanceOf` dalam **raw units**
(1e27 untuk 1e9 token × 18 desimal). Membagi keduanya mencampur skala
dan menghasilkan persen 1e18× terlalu besar (`bought_pct` sempat
7,687e17%). **Penyebut yang benar: `totalSupply` on-chain (raw units)** —
`sniper_pct`/`bubble_pct` dari map GoPlus (sudah %) tidak kena bug ini,
tapi jalur `balanceOf` dan `bought_pct` kena. Test
`test_balanceof_fallback_for_wallet_outside_top10` menjaga ini.

## Rencana pengganti cek sniper + bubble (tanpa GMGN, gratis)

- **Sniper (<8%):** dari `eth_getLogs` event `Transfer` token — dompet yang beli
  di blok-blok awal setelah pair dibuat (mis. 10 blok pertama / 5 menit pertama),
  hitung % supply yang mereka pegang. Perlu blok pair creation (dari
  `PairCreated` Uniswap / `pool.creation_timestamp` DexScreener).
- **Bubble/insider (<8%):** klaster dompet dari data transfer — dompet yang
  didanai dari sumber yang sama (native transfer pertama yang sama), dompet
  fresh (umur < 7 hari), dan pola beli barengan di awal. Data dari
  `eth_getLogs` + `eth_getBalance`/`eth_call` secukupnya; batasi jumlah
  request karena RPC publik rate-limited.
- Ambang tetap TEGAS (`<`, bukan `<=`), fail-closed: kalau data log tidak
  lengkap → cek UNKNOWN → token tidak PASS.
- Perlu dites live dulu di token yang sudah dikenal (mis. MINTGOAT
  `0xe5a80faf3bd9bc852305014f5e62cb356a0a330f`) sebelum dipakai di screening.

## Validasi live (2026-11-22 malam)

- **MINTGOAT** (pair v4, quote GLD, dibuat ~2,9 jam sebelum tes):
  blok kreasi terestimasi 120588787 (asli 120588750), 3 log Transfer,
  sniper **0,7% (1 wallet)** — pembeli pertama 0x683d65ad… beli 0,7687%
  supply di blok kreasi, masih pegang ~0,7%; bubble **0,0% (0 wallet)**.
  Audit penuh tanpa GMGN: verdict **FAIL skor 77** (LP tidak terkunci +
  kontrak tidak terverifikasi + copycat) — sniper/bubble **ditentukan,
  bukan UNKNOWN**. Token kini bisa mencapai PASS/FAIL penuh tanpa key GMGN.
- **Keccak-256 murni-Python terverifikasi** — topik Transfer
  `0xddf252ad…` dan hash string kosong cocok dengan konstanta yang dikenal.
- **Test: 34/34 lolos** (22 lama + 12 baru: keccak, batas 8% sniper/bubble,
  fallback balanceOf, data tidak lengkap → UNKNOWN, cross-check GMGN,
  estimasi blok kreasi, integrasi scan_once).
- **Monitor live:** exit 0, status sumber on-chain tercatat di
  `state/last_scan.json` (termasuk alasan gagal terakhir bila ada).
- **App:** Streamlit HTTP 200 (smoke test lokal, port 8502).

## Batasan yang tersisa

- **RPC rate-limited & tanpa UA = 403** — `_rpc_logs` mengecilkan chunk
  dan retry sekali; kalau tetap gagal → UNKNOWN (fail-closed).
- **Estimasi blok kreasi (v4) tidak exact** — token yang sudah berjam-jam
  bisa meleset puluhan blok; buffer −60 blok + jendela 60 dtk menutupinya
  untuk token baru, tapi token launch lambat (tidak ada beli di 60 dtk
  pertama) akan UNKNOWN — aman (tidak lolos), tapi berpotensi kelewat.
- **Kecepatan per token ~7–12 dtk** (log + totalSupply + nonce checks) —
  siklus 12 token bisa ~2 menit; monitor cron 5–15 menit masih aman.
- **GMGN tetap opsional** — kalau key ada, nilai GMGN vs on-chain, yang
  lebih buruk menang. Tanpa key, on-chain saja sudah cukup untuk PASS.
- **Belum divalidasi di token baru yang ramai** — MINTGOAT sepi (16 holder,
  3 log). Token baru dengan ratusan transfer di menit pertama belum dites
  live; batas `ONCHAIN_MAX_LOGS=10000` dan cap `balanceOf` 10 call
  melindungi dari ledakan request, tapi angka sniper/bubble di token ramai
  belum teramati.
