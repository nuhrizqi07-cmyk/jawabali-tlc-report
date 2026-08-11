# 📦 Laporan Harian TLC

Streamlit app untuk generate **laporan harian TLC** dari backup chat WhatsApp grup **NETWORK JAWABALI**.

## Cara Pakai

1. Export chat grup di WhatsApp: **Settings → Chats → Chat History → Export Chat** (tanpa media lebih cepat)
2. Upload file `.zip` hasil export di sidebar
3. Pilih tanggal yang tersedia (dropdown berisi semua tanggal dari history chat)
4. Lihat ringkasan + detail per cabang (PWO, CXP, KBM, BJN)
5. Klik **Download Laporan DOCX**

## Fitur

- ✅ Upload ZIP backup chat (max 200MB)
- ✅ Deteksi otomatis semua tanggal yang tersedia di chat
- ✅ Filter cabang (PWO, CXP, KBM, BJN — bisa pilih)
- ✅ Ringkasan TLC + Eskalasi per cabang
- ✅ Detail per item (AWB, Chat Pengirim, Ket, Client, Kendala, Butuh Bantuan)
- ✅ Download DOCX siap buka di Word/WPS

## Struktur

```
app.py           # Streamlit app utama
parser.py        # Parser backup chat (TLC blocks + eskalasi + tracking tanggal)
docx_report.py   # Generator DOCX
requirements.txt # Dependencies
```

## Deploy Lokal

```bash
python -m venv venv
./venv/bin/pip install -r requirements.txt
./venv/bin/streamlit run app.py
```
