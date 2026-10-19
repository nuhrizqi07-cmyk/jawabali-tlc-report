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
- ✅ **2 mode parsing:**
  - 🤖 **LLM (DeepSeek / OpenRouter)** — ekstraksi paling presisi, tahan variasi format chat. API key DeepSeek dari [platform.deepseek.com](https://platform.deepseek.com) atau OpenRouter dari [openrouter.ai](https://openrouter.ai) (isi di sidebar atau env `DEEPSEEK_API_KEY` / `OPENROUTER_API_KEY`). Hasil di-cache per tanggal, jadi ganti cabang/filter tidak memanggil API ulang.
  - ⚡ **Regex cepat** — gratis & instan tanpa internet
- ✅ Filter cabang (PWO, CXP, KBM, BJN — bisa pilih)
- ✅ Ringkasan TLC + Eskalasi per cabang
- ✅ Detail per item (AWB, Chat Pengirim, Ket, Client, Kendala, Butuh Bantuan)
- ✅ Download DOCX siap buka di Word/WPS

## Struktur

```
app.py           # Streamlit app utama
parser.py        # Parser regex (TLC blocks + eskalasi + tracking tanggal)
llm_parser.py    # Ekstraksi via DeepSeek (mode LLM)
docx_report.py   # Generator DOCX
requirements.txt # Dependencies
```

## Deploy Lokal

```bash
python -m venv venv
./venv/bin/pip install -r requirements.txt

# Opsional: set API key agar tidak perlu isi di sidebar tiap kali
export DEEPSEEK_API_KEY=sk-...
# atau
export OPENROUTER_API_KEY=sk-or-v1-...

./venv/bin/streamlit run app.py
```

