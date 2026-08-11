"""
Parser backup chat WhatsApp grup NETWORK JAWABALI.
Mengekstrak entry TLC (blok digest) + eskalasi komplain per tanggal & cabang.

Input : file .txt hasil extract ZIP backup WhatsApp (atau bytes ZIP)
Output: list[dict] dengan key:
    tanggal, waktu, sender, tipe ('tlc'|'eskalasi'), cabang, awb,
    chat, ket, client, kendala, butuh_bantuan, patokan
"""

import io
import re
import zipfile
from datetime import datetime

# Baris timestamp pesan: "11/08/26 09.58 - +62 882-1670-7461: ..."
TS_RE = re.compile(
    r'^(\d{2})/(\d{2})/(\d{2}) (\d{2})\.(\d{2}) - ([^:]+):\s*(.*)$'
)
# Format alternatif: "11/08/26, 09.58 - ..." (koma)
TS_RE_COMMA = re.compile(
    r'^(\d{2})/(\d{2})/(\d{2}), (\d{2})\.(\d{2}) - ([^:]+):\s*(.*)$'
)

# Blok TLC digest: baris diawali kutip lalu "*TLC* : KODE"
TLC_START_RE = re.compile(r'^"?\*TLC\*\s*:\s*([A-Za-z0-9]{2,4})')
AWB_RE = re.compile(r'\*AWB\*\s*:\s*(\S+)')
CHAT_RE = re.compile(r'\*Chat Pengirim\*\s*:\s*(.*)')
KET_RE = re.compile(r'\*Ket\*\s*:\s*(.*)')
SEP_RE = re.compile(r'^\*[-—*]{5,}\*?$')

# Eskalasi: "Dear Team Cabang *KODE*" atau "Dear team *KODE*"
DEAR_RE = re.compile(r'Dear\s+Team(?:\s+Cabang)?\s+\*([A-Za-z0-9]{2,4})\*')
# Eskalasi terstruktur
AWB_TAB_RE = re.compile(r'^AWB\s*:?\s*(\S+)', re.I)
CLIENT_RE = re.compile(r'^Client\s*:?\s*(.*)', re.I)
KENDALA_RE = re.compile(r'^Kendala\s*:?\s*(.*)', re.I)
BUTUH_RE = re.compile(r'^Butuh\s+Bantuan\s*:?\s*(.*)', re.I)
PATOKAN_RE = re.compile(r'^\*Patokan\*\s*:?\s*(.*)')
MOBILE_RE = re.compile(r'^\+\d[\d\s-]*$')


def _parse_ts(line):
    m = TS_RE.match(line) or TS_RE_COMMA.match(line)
    if not m:
        return None
    d, mo, y, h, mi, sender, body = m.groups()
    try:
        dt = datetime(2000 + int(y), int(mo), int(d), int(h), int(mi))
    except ValueError:
        return None
    return {
        'tanggal': f'{d}/{mo}/{y}',
        'waktu': f'{h}.{mi}',
        'dt': dt,
        'sender': sender.strip(),
        'body': body,
    }


def _is_ts_line(line):
    return bool(TS_RE.match(line) or TS_RE_COMMA.match(line))


def _norm_date_key(dmy):
    d, mo, y = dmy.split('/')
    return f'{y}-{mo}-{d}'


def parse_text(text):
    """Parse seluruh teks chat -> list[dict] entries."""
    entries = []
    cur_ts = None          # timestamp pesan pembungkus terakhir
    cur_block = None       # blok TLC yang sedang diisi
    cur_escal = None       # blok eskalasi yang sedang diisi
    in_digest = False      # sedang di dalam pesan digest "Info berdasar TLC"

    lines = text.splitlines()

    for i, line in enumerate(lines):
        stripped = line.strip()

        ts = _parse_ts(line)
        if ts:
            # Pesan baru dimulai -> tutup blok/eskalasi dari pesan sebelumnya
            if cur_block:
                entries.append(cur_block)
                cur_block = None
            if cur_escal:
                entries.append(cur_escal)
                cur_escal = None
            cur_ts = ts
            in_digest = 'Info berdasar TLC' in ts['body']
            # Baris timestamp yang juga memuat konten (mis. "11/08/26 08.26 - A: Dear Team Cabang *BJN*")
            # -> proses body-nya sebagai baris konten (fall-through setelah set cur_ts)
            body_stripped = ts['body'].strip()
            if not body_stripped:
                continue
            stripped = body_stripped
            # jangan lanjut ke continue di bawah; fall-through ke logika konten

        if cur_ts is None:
            continue

        # --- Blok TLC digest ---
        m = TLC_START_RE.match(stripped)
        if m:
            if cur_block:
                entries.append(cur_block)
            cur_block = {
                'tanggal': cur_ts['tanggal'],
                'waktu': cur_ts['waktu'],
                'sender': cur_ts['sender'],
                'tipe': 'tlc',
                'cabang': m.group(1).upper(),
                'awb': None,
                'chat': '',
                'ket': '',
            }
            continue

        if cur_block:
            if SEP_RE.match(stripped):
                entries.append(cur_block)
                cur_block = None
                continue
            m2 = AWB_RE.search(stripped)
            if m2 and cur_block['awb'] is None:
                cur_block['awb'] = m2.group(1)
                continue
            m3 = CHAT_RE.search(stripped)
            if m3:
                cur_block['chat'] = m3.group(1).strip()
                continue
            m4 = KET_RE.search(stripped)
            if m4:
                cur_block['ket'] = m4.group(1).strip()
                continue

        # --- Eskalasi komplain ---
        m5 = DEAR_RE.search(stripped)
        if m5:
            if cur_escal:
                entries.append(cur_escal)
            cur_escal = {
                'tanggal': cur_ts['tanggal'],
                'waktu': cur_ts['waktu'],
                'sender': cur_ts['sender'],
                'tipe': 'eskalasi',
                'cabang': m5.group(1).upper(),
                'awb': None,
                'chat': '',
                'ket': '',
                'client': '',
                'kendala': '',
                'butuh_bantuan': '',
                'patokan': '',
            }
            continue

        if cur_escal:
            m6 = AWB_TAB_RE.match(stripped)
            if m6 and cur_escal['awb'] is None:
                cur_escal['awb'] = m6.group(1)
                continue
            m7 = CLIENT_RE.match(stripped)
            if m7 and m7.group(1).strip():
                cur_escal['client'] = m7.group(1).strip()
                continue
            m8 = KENDALA_RE.match(stripped)
            if m8 and m8.group(1).strip():
                cur_escal['kendala'] = m8.group(1).strip()
                continue
            m9 = BUTUH_RE.match(stripped)
            if m9 and m9.group(1).strip():
                cur_escal['butuh_bantuan'] = m9.group(1).strip()
                continue
            m10 = PATOKAN_RE.match(stripped)
            if m10:
                cur_escal['patokan'] = m10.group(1).strip()
                continue
            # akhiri eskalasi saat pesan selesai (timestamp baru) -> ditangani di atas
            # hentikan jika ada teks "Terima kasih" + baris kosong ganda
            if stripped.lower().startswith('terima kasih'):
                entries.append(cur_escal)
                cur_escal = None
                continue

    if cur_block:
        entries.append(cur_block)
    if cur_escal:
        entries.append(cur_escal)

    return entries


def load_zip_bytes(zip_bytes):
    """Baca .txt dari dalam bytes ZIP -> teks."""
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        # cari file .txt (nama biasanya "Chat WhatsApp dengan ....txt")
        txt_name = None
        for n in zf.namelist():
            if n.lower().endswith('.txt') and 'chat' in n.lower():
                txt_name = n
                break
        if txt_name is None:
            for n in zf.namelist():
                if n.lower().endswith('.txt'):
                    txt_name = n
                    break
        if txt_name is None:
            raise ValueError('Tidak ada file .txt di dalam ZIP')
        raw = zf.read(txt_name)
    return raw.decode('utf-8', errors='replace')


def load_zip_path(path):
    with open(path, 'rb') as f:
        return load_zip_bytes(f.read())


def available_dates(entries):
    """Daftar tanggal unik (desc) dari entries."""
    dates = sorted({e['tanggal'] for e in entries},
                   key=_norm_date_key, reverse=True)
    return dates


def filter_by(entries, cabangs=None, tanggal=None):
    out = entries
    if tanggal:
        out = [e for e in out if e['tanggal'] == tanggal]
    if cabangs:
        cabangs = {c.upper() for c in cabangs}
        out = [e for e in out if e['cabang'] in cabangs]
    return out
