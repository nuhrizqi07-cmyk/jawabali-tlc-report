"""
Streamlit app: Laporan Harian TLC dari backup chat WhatsApp grup NETWORK JAWABALI.

Cara pakai:
1. Upload ZIP backup WhatsApp (hasil export chat grup)
2. Pilih metode parsing: LLM (DeepSeek) atau Regex cepat
3. Pilih tanggal yang tersedia (berdasarkan history di chat)
4. Lihat ringkasan + detail per cabang (PWO, CXP, KBM, BJN)
5. Download laporan DOCX
"""

import os

import streamlit as st

import parser
import docx_report
import llm_parser

st.set_page_config(page_title="Laporan Harian TLC", page_icon="📦", layout="wide")

CABANG_UTAMA = ['PWO', 'CXP', 'KBM', 'BJN']

st.title("📦 Laporan Harian TLC")
st.caption("Generate laporan harian dari backup chat WhatsApp grup NETWORK JAWABALI")

# ---------- Sidebar ----------
with st.sidebar:
    st.header("⚙️ Input")
    uploaded = st.file_uploader(
        "Upload ZIP backup chat WhatsApp",
        type=["zip"],
        help="Hasil export chat grup (Chat WhatsApp dengan ....zip)",
    )

    st.divider()
    st.markdown("**Metode parsing**")
    metode = st.radio(
        "Metode parsing",
        ["🤖 LLM", "⚡ Regex cepat"],
        label_visibility="collapsed",
        help="LLM = ekstraksi presisi via API. "
             "Regex = gratis & instan, tapi kaku terhadap variasi format.",
    )
    use_llm = metode.startswith("🤖")

    provider = "deepseek"
    provider_label = "DeepSeek"
    api_key = ""
    model = llm_parser.DEFAULT_MODEL
    if use_llm:
        provider = st.radio(
            "Provider LLM",
            ["DeepSeek", "OpenRouter"],
            horizontal=True,
            help="Pilih provider API. OpenRouter bisa dipakai bila tidak punya API DeepSeek.",
        ).lower()
        provider_label = "OpenRouter" if provider == "openrouter" else "DeepSeek"

        if provider == "deepseek":
            api_key = st.text_input(
                "DeepSeek API Key",
                value=os.getenv("DEEPSEEK_API_KEY", ""),
                type="password",
                placeholder="sk-...",
                help="Dapatkan di platform.deepseek.com — tersimpan hanya di sesi ini.",
            )
            model = st.selectbox(
                "Model",
                [llm_parser.DEFAULT_MODEL, llm_parser.REASONER_MODEL],
                format_func=lambda m: (
                    "deepseek-chat (cepat & murah)" if m == llm_parser.DEFAULT_MODEL
                    else "deepseek-reasoner (paling teliti)"
                ),
            )
        else:
            api_key = st.text_input(
                "OpenRouter API Key",
                value=os.getenv("OPENROUTER_API_KEY", ""),
                type="password",
                placeholder="sk-or-v1-...",
                help="Dapatkan di openrouter.ai — tersimpan hanya di sesi ini.",
            )
            model_ids = [m["id"] for m in llm_parser.OPENROUTER_MODELS]
            model_names = {m["id"]: m["name"] for m in llm_parser.OPENROUTER_MODELS}
            model = st.selectbox(
                "Model",
                model_ids,
                index=0,
                format_func=lambda m: model_names.get(m, m),
            )

    st.divider()
    st.markdown("**Cabang yang dilaporkan**")
    cabang_pilihan = st.multiselect(
        "Pilih cabang",
        CABANG_UTAMA,
        default=CABANG_UTAMA,
    )
    if not cabang_pilihan:
        st.warning("Pilih minimal 1 cabang.")

# ---------- State ----------
for _k, _v in {
    "raw_text": None,
    "entries": None,
    "dates": [],
    "llm_cache": {},
}.items():
    st.session_state.setdefault(_k, _v)

# ---------- Main ----------
if uploaded is None:
    st.info(
        "👆 **Upload ZIP backup chat WhatsApp** di sidebar untuk memulai.\n\n"
        "Backup diambil dari menu WhatsApp: **Settings → Chats → Chat History → Export Chat** "
        "(hasilnya berupa file `.zip` berisi `_chat.txt` + media)."
    )
    st.stop()

# Baca ZIP sekali -> teks mentah + daftar tanggal (scan cepat, tanpa parse blok)
key = f"{uploaded.name}:{uploaded.size}"
if st.session_state.raw_text is None or st.session_state.get("upload_key") != key:
    with st.spinner("Memproses backup chat (bisa ±5 detik untuk file besar)..."):
        try:
            text = parser.load_zip_bytes(uploaded.getvalue())
            st.session_state.raw_text = text
            st.session_state.dates = parser.available_dates_from_text(text)
            st.session_state.entries = None
            st.session_state.llm_cache = {}
            st.session_state.upload_key = key
        except Exception as exc:
            st.error(f"Gagal membaca ZIP: {exc}")
            st.stop()

raw_text = st.session_state.raw_text
dates = st.session_state.dates

if not dates:
    st.error("Tidak ada pesan ber-timestamp ditemukan di chat ini.")
    st.stop()

if use_llm and not api_key:
    env_name = "OPENROUTER_API_KEY" if provider == "openrouter" else "DEEPSEEK_API_KEY"
    st.warning(
        f"🔑 Masukkan **{provider_label} API Key** di sidebar untuk mode LLM "
        f"(atau set env `{env_name}`). Atau ganti ke **Regex cepat**."
    )
    st.stop()

# ---------- Pilih tanggal ----------
st.subheader("📅 Pilih Tanggal")
col_a, col_b = st.columns([2, 1])
with col_a:
    tanggal = st.selectbox(
        "Tanggal tersedia (dari history chat):",
        dates,
        index=0,  # tanggal terbaru
        format_func=lambda d: f"{d}  ({docx_report._fmt_tanggal(d)})",
    )
with col_b:
    st.caption("")
    if st.button("🔄 Parse ulang ZIP", use_container_width=True):
        st.session_state.entries = None
        st.session_state.llm_cache = {}
        st.rerun()

if not cabang_pilihan:
    st.stop()

# ---------- Ekstraksi item ----------
items = []
llm_info = ""

if use_llm:
    ckey = f"{st.session_state.upload_key}|{tanggal}|{provider}|{model}"
    if ckey not in st.session_state.llm_cache:
        msgs = parser.messages_for_date(raw_text, tanggal)
        if not msgs:
            st.error(f"Tidak ada pesan pada {tanggal} di chat ini.")
            st.stop()
        with st.status(f"🤖 {provider_label} ({model}) mengekstrak {len(msgs)} pesan…") as status:
            prog = st.progress(0.0, text="Mengirim ke API…")
            try:
                def _cb(i, n):
                    prog.progress(i / n, text=f"Chunk {i}/{n}")

                day_entries, stats = llm_parser.parse_day(
                    api_key, tanggal, msgs, provider=provider, model=model, on_progress=_cb,
                )
            except llm_parser.LLMParseError as exc:
                status.update(label="❌ Gagal", state="error")
                st.error(str(exc))
                st.stop()
            status.update(label="✅ Ekstraksi selesai", state="complete")
        st.session_state.llm_cache[ckey] = (day_entries, stats)

    day_entries, stats = st.session_state.llm_cache[ckey]
    items = parser.filter_by(day_entries, cabangs=cabang_pilihan)
    pt, ct = stats.get('prompt_tokens') or 0, stats.get('completion_tokens') or 0
    llm_info = (
        f"· 🤖 {provider_label} {model}: {pt:,} token in / {ct:,} token out "
        f"(cache per tanggal)"
    )
else:
    if st.session_state.entries is None:
        with st.spinner("Memproses backup chat (bisa ±5 detik untuk file besar)..."):
            st.session_state.entries = parser.parse_text(raw_text)
    items = parser.filter_by(
        st.session_state.entries, cabangs=cabang_pilihan, tanggal=tanggal,
    )

n_tlc = sum(1 for e in items if e['tipe'] == 'tlc')
n_esc = sum(1 for e in items if e['tipe'] == 'eskalasi')

# ---------- Info ringkas ----------
col1, col2, col3 = st.columns(3)
col1.metric("TLC hari ini", f"{n_tlc:,}")
col2.metric("Eskalasi hari ini", f"{n_esc:,}")
col3.metric("Hari aktif", f"{len(dates)}")

st.divider()

# ---------- Ringkasan ----------
st.subheader(f"📊 Ringkasan {docx_report._fmt_tanggal(tanggal)}")
st.caption(f"{len(items)} item ({n_tlc} TLC, {n_esc} eskalasi) "
           f"untuk cabang terpilih {llm_info}")

if items:
    import pandas as pd
    rows = []
    for c in cabang_pilihan:
        ci = [e for e in items if e['cabang'] == c]
        rows.append({
            "Cabang": c,
            "TLC": sum(1 for e in ci if e['tipe'] == 'tlc'),
            "Eskalasi": sum(1 for e in ci if e['tipe'] == 'eskalasi'),
            "Total": len(ci),
        })
    st.dataframe(
        pd.DataFrame(rows),
        hide_index=True,
        width="stretch",
        column_config={
            "Cabang": st.column_config.TextColumn("Cabang", width="small"),
            "TLC": st.column_config.NumberColumn("TLC", width="small"),
            "Eskalasi": st.column_config.NumberColumn("Eskalasi", width="small"),
            "Total": st.column_config.NumberColumn("Total", width="small"),
        },
    )

    # ---------- Detail ----------
    st.subheader("📋 Detail")
    for c in cabang_pilihan:
        ci = [e for e in items if e['cabang'] == c]
        if not ci:
            continue
        with st.expander(f"{c} — {len(ci)} item", expanded=True):
            for idx, e in enumerate(ci, 1):
                badge = "🔵 TLC" if e['tipe'] == 'tlc' else "🟠 ESKALASI"
                st.markdown(f"**{idx}. {badge} | AWB: `{e['awb'] or '-'}` | {e['waktu']}**")
                if e['tipe'] == 'tlc':
                    if e['chat']:
                        st.markdown(f"- *Chat Pengirim:* {e['chat']}")
                    if e['ket']:
                        st.markdown(f"- *Ket:* {e['ket']}")
                else:
                    if e['client']:
                        st.markdown(f"- *Client:* {e['client']}")
                    if e['kendala']:
                        st.markdown(f"- *Kendala:* {e['kendala']}")
                    if e['butuh_bantuan']:
                        st.markdown(f"- *Butuh Bantuan:* {e['butuh_bantuan']}")
                    if e['patokan']:
                        st.markdown(f"- *Patokan:* {e['patokan']}")
                st.markdown("---")

    # ---------- Download DOCX ----------
    st.divider()
    st.subheader("📄 Download Laporan")
    try:
        docx_bytes = docx_report.build_docx(
            items, tanggal=tanggal, cabangs=cabang_pilihan
        )
        fname = docx_report.filename_for(tanggal)
        st.download_button(
            "⬇️ Download Laporan DOCX",
            data=docx_bytes,
            file_name=fname,
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            use_container_width=True,
        )
        st.caption(f"File: `{fname}` — siap dibuka di Word / WPS.")
    except Exception as exc:
        st.error(f"Gagal membuat DOCX: {exc}")

else:
    st.info("Tidak ada item TLC/eskalasi untuk tanggal & cabang terpilih.")

# ---------- Footer ----------
st.divider()
sumber = f"{provider_label} LLM" if use_llm else "regex parser"
st.caption(f"Dibuat otomatis dengan Streamlit · Sumber: backup chat WhatsApp "
           f"grup NETWORK JAWABALI · Parsing: {sumber}")
