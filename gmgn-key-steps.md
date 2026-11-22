# Cara dapat API key GMGN (gratis) — langkah untuk Mas Roy

Sumber: blog resmi GMGN (gmgn.ai/blog/gmgn-skills-for-ai-agents) + docs.gmgn.ai.
Key-nya GRATIS; yang bayar cuma fee trading 1% — dan kita TIDAK trading, jadi tidak ada biaya.

## Langkah (di HP / browser Mas Roy)

1. Buka **https://gmgn.ai/ai** (ketik sendiri, jangan klik link dari grup Telegram — banyak situs palsu mirip GMGN).
2. Login dengan akun GMGN (Telegram / Google / email / wallet).
3. Ke **GMGN API Management** → buat API key baru:
   - Generate key pair Ed25519 dulu (bisa pakai `openssl genpkey -algorithm ed25519`, atau Binance Asymmetric Key Generator).
   - Upload **public key**-nya saja ke form GMGN. **Private key TIDAK PERNAH dikirim ke siapa pun, TIDAK PERNAH ditempel di chat.**
   - Pilih permission **read-only** (kita cuma baca data, tidak trading).
   - Tambahkan IPv4 address ke whitelist (kalau diminta).
   - Maks 3 key per akun.
4. GMGN menampilkan **API key identifier** (mis. `gmgn_...`).

## Cara key sampai ke Bodhi (AMAN — jangan ditempel di chat)

- Mas Roy masukkan API key lewat **Secure Vault** (link aman yang Bodhi berikan), bukan di chat.
- Bodhi simpan ke `~/.config/gmgn/.env` sebagai `GMGN_API_KEY=...`, lalu jalankan `gmgn-cli config --check` untuk verifikasi.
- Token commands (info/security/pool/holders/traders) menurut skill GMGN pakai API key saja, tanpa signature — jadi private key Ed25519 TIDAK dibutuhkan untuk scanner ini.

## Keypair Ed25519 (dibuat Bodhi 2026-11-22 21:05 WIB)

- Public key (diberikan ke Mas Roy untuk ditempel di form GMGN):
  ```
  -----BEGIN PUBLIC KEY-----
  MCowBQYDK2VwAyEAFgpo6XnexOO5DpZ/iTeK99NXoRBIRrr7KReJqoSC6Mw=
  -----END PUBLIC KEY-----
  ```
- Private key: `~/.config/gmgn/ed25519_private.pem` (mode 600) — TIDAK PERNAH dibagikan, TIDAK PERNAH ditempel di chat.
- Catatan: `~/.config/gmgn/keypair.pem` (dibuat `gmgn-cli config` 20:51 WIB, output-nya tidak lolos safety check) TIDAK dipakai.

## Yang diaktifkan key ini

- Cek **sniper** + **bubble/insider** di scanner (saat ini UNKNOWN tanpa key).
- Tanpa key, token tidak bisa PASS (fail-closed) — scanner tetap jalan pakai DexScreener + GoPlus + RPC.

## Dua jalur pemakaian key (diputuskan saat key sudah jadi)

1. **Bodhi panggil API GMGN langsung (tanpa pernah lihat key):** key disimpan di konektor aman `custom.gmgn` (Secure Vault). Bodhi memanggil `openapi.gmgn.ai` lewat helper yang memakai surrogate — runtime yang menukar dengan key asli. Cocok untuk riset/cek yang Bodhi jalankan sendiri.
2. **Scanner/monitor mandiri (cron/Streamlit):** key TIDAK bisa diambil dari konektor oleh app yang jalan sendiri. Jalurnya: saat deploy ke Streamlit Cloud, Mas Roy sendiri yang memasukkan key ke **Streamlit Secrets** di dashboard Streamlit (bukan lewat chat, bukan lewat Bodhi). Sampai deploy, scanner jalan dengan DexScreener + GoPlus + RPC, cek sniper/bubble tetap UNKNOWN.
