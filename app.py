"""
Streamlit app: Laporan Harian TLC dari backup chat WhatsApp grup NETWORK JAWABALI.

Cara pakai:
1. Upload ZIP backup WhatsApp (hasil export chat grup)
2. Pilih tanggal yang tersedia (berdasarkan history di chat)
3. Lihat ringkasan + detail per cabang (PWO, CXP, KBM, BJN)
4. Download laporan DOCX
"""

import streamlit as st

import parser
import docx_report

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
    st.markdown("**Cabang yang dilaporkan**")
    cabang_pilihan = st.multiselect(
        "Pilih cabang",
        CABANG_UTAMA,
        default=CABANG_UTAMA,
    )
    if not cabang_pilihan:
        st.warning("Pilih minimal 1 cabang.")

# ---------- State ----------
if "entries" not in st.session_state:
    st.session_state.entries = None
if "dates" not in st.session_state:
    st.session_state.dates = []

# ---------- Main ----------
if uploaded is None:
    st.info(
        "👆 **Upload ZIP backup chat WhatsApp** di sidebar untuk memulai.\n\n"
        "Backup diambil dari menu WhatsApp: **Settings → Chats → Chat History → Export Chat** "
        "(hasilnya berupa file `.zip` berisi `_chat.txt` + media)."
    )
    st.stop()

# Parse kalau file baru / belum diparse
key = f"{uploaded.name}:{uploaded.size}"
if st.session_state.entries is None or st.session_state.get("upload_key") != key:
    with st.spinner("Memproses backup chat (bisa ±5 detik untuk file besar)..."):
        try:
            text = parser.load_zip_bytes(uploaded.getvalue())
            entries = parser.parse_text(text)
            st.session_state.entries = entries
            st.session_state.dates = parser.available_dates(entries)
            st.session_state.upload_key = key
        except Exception as exc:
            st.error(f"Gagal membaca ZIP: {exc}")
            st.stop()

entries = st.session_state.entries
dates = st.session_state.dates

if not dates:
    st.error("Tidak ada pesan ber-timestamp ditemukan di chat ini.")
    st.stop()

# ---------- Info ringkas ----------
col1, col2, col3 = st.columns(3)
col1.metric("Total entry TLC", f"{sum(1 for e in entries if e['tipe']=='tlc'):,}")
col2.metric("Total eskalasi", f"{sum(1 for e in entries if e['tipe']=='eskalasi'):,}")
col3.metric("Hari aktif", f"{len(dates)}")

st.divider()

# ---------- Pilih tanggal ----------
st.subheader("📅 Pilih Tanggal")
default_idx = 0  # tanggal terbaru
col_a, col_b = st.columns([2, 1])
with col_a:
    tanggal = st.selectbox(
        "Tanggal tersedia (dari history chat):",
        dates,
        index=default_idx,
        format_func=lambda d: f"{d}  ({docx_report._fmt_tanggal(d)})",
    )
with col_b:
    st.caption("")
    if st.button("🔄 Parse ulang ZIP", use_container_width=True):
        st.session_state.entries = None
        st.rerun()

if not cabang_pilihan:
    st.stop()

# ---------- Filter ----------
items = parser.filter_by(entries, cabangs=cabang_pilihan, tanggal=tanggal)
n_tlc = sum(1 for e in items if e['tipe'] == 'tlc')
n_esc = sum(1 for e in items if e['tipe'] == 'eskalasi')

# ---------- Ringkasan ----------
st.subheader(f"📊 Ringkasan {docx_report._fmt_tanggal(tanggal)}")
st.caption(f"{len(items)} item ditemukan ({n_tlc} TLC, {n_esc} eskalasi) untuk cabang terpilih.")

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
st.caption("Dibuat otomatis dengan Streamlit · Sumber: backup chat WhatsApp grup NETWORK JAWABALI")
