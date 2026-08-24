"""
Parser backup chat WhatsApp grup NETWORK JAWABALI.
Mengekstrak entry TLC (blok digest) + eskalasi komplain per tanggal & cabang.

Input : file .txt hasil extract ZIP backup WhatsApp (atau bytes ZIP)
Output: list[dict] dengan key:
    tanggal, waktu, sender, tipe ('tlc'|'eskalasi'), cabang, awb,
    chat, ket, client, kendala, butuh_bantuan, patokan

Catatan format eskalasi (verified):
  1. "Dear Team Cabang *SRG*"  (kode pakai bintang)       -> lama
  2. "Dear Team Cabang SRG"    (kode tanpa bintang)
  3. "Dear Team SRG"           (tanpa kata "Cabang")
  4. "Dear team cabang SRG"    (lowercase)
  5. "Dear Team Cabang" polos  -> cabang diambil dari tiap baris AWB (multi-AWB)
  6. Broadcast (bukan 1 cabang): "Dear Team,", "Dear Team", "... dan Network",
     "... dan Cabang", "... Selindo"  -> DI-SKIP

Multi-AWB: baris "AWB :" kosong diikuti daftar AWB, tiap baris bisa:
    - "IDE7001237332234<TAB>BJN @satoto"  (cabang setelah tab)
    - "KDR8500216128584 - *WNO* @Rizal"   (cabang setelah dash/bintang)
    - "IDE7001175175561"                  (polos -> waris cabang header)
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

# Eskalasi terstruktur (field di dalam blok eskalasi)
AWB_TAB_RE = re.compile(r'^AWB\s*:?\s*(\S+)', re.I)          # "AWB\t: XXX" / "AWB : XXX"
AWB_EMPTY_RE = re.compile(r'^AWB\s*:?\s*$', re.I)            # "AWB :" kosong -> multi-AWB
CLIENT_RE = re.compile(r'^Client\s*:?\s*(.*)', re.I)
KENDALA_RE = re.compile(r'^Kendala\s*:?\s*(.*)', re.I)
BUTUH_RE = re.compile(r'^Butuh\s+Bantuan\s*:?\s*(.*)', re.I)
PATOKAN_RE = re.compile(r'^\*Patokan\*\s*:?\s*(.*)')

# Baris AWB dalam daftar multi-AWB
#   "IDE7001237332234\tBJN @satoto" (tab) / "KDR8500216128584 - *WNO*" (dash)
#   "LNCH2512A8IIAENR *SJO* @Ahmadi" (spasi + bintang) / "CGK8153421100146" (polos)
AWB_ITEM_RE = re.compile(
    r'^(?P<awb>[A-Za-z0-9]{8,20})'
    r'(?:\s*-\s*\*?(?P<dash>[A-Za-z0-9]{2,4})\*?'
    r'|\s*\t\s*(?P<tab>[A-Za-z0-9]{2,4})?'
    r'|\s+\*?(?P<space>[A-Z]{2,4})\*?)?'
    r'\s*(?:@.*)?$'
)

# Baris "cabang" berdiri sendiri di atas AWB: "SOC @~."
BRANCH_LINE_RE = re.compile(r'^([A-Z]{2,4})\s*@')

# Prefix AWB: 2-4 huruf diikuti angka (mis. LNCH, EZI, KEI, IDE, IG, IC, CGK).
# Nomor telepon (6283..., 895...) selalu diawali angka -> otomatis tersaring.
COURIER_PREFIX_RE = re.compile(r'^[A-Za-z]{2,4}[0-9]')


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


def _match_dear(body):
    """Deteksi header eskalasi 'Dear Team ...'.
    Return (is_broadcast, code) atau None bila bukan baris Dear Team.
    is_broadcast=True -> bukan 1 cabang (skip).
    code=None -> 'Dear Team Cabang' polos (cabang dari AWB suffix)."""
    if not re.search(r'\bDear\b', body):
        return None
    if not re.search(r'\b[Tt]eam\b|\btim\b', body):
        return None
    prefix = body.split('@')[0].strip()
    low = prefix.lower()

    # Broadcast: bukan satu cabang tertentu
    if 'network' in low or 'selindo' in low:
        return (True, None)
    if re.search(r'\bdan\b', low):
        return (True, None)

    # Coba ekstrak kode (2-4 huruf BESAR), setelah "Team"/"tim" [+ "Cabang"]
    # Huruf besar saja agar tidak salah tangkap "cabang"/"tertera" saat backtracking.
    m = re.search(
        r'Dear\s+(?:[Tt]eam|tim)\s+(?:[Cc]abang\s+)?\*?([A-Z]{2,4})\*?',
        prefix,
    )
    if m:
        return (False, m.group(1))

    # Tanpa kode
    if 'cabang' in low:
        # "Dear Team Cabang" polos -> eskalasi multi-AWB (cabang dari AWB)
        return (False, None)
    # "Dear Team" / "Dear Team," polos -> broadcast
    return (True, None)


def _new_tlc(cur_ts, code):
    return {
        'tanggal': cur_ts['tanggal'],
        'waktu': cur_ts['waktu'],
        'sender': cur_ts['sender'],
        'tipe': 'tlc',
        'cabang': code,
        'awb': None,
        'chat': '',
        'ket': '',
    }


def _new_escal(cur_ts, code):
    return {
        'tanggal': cur_ts['tanggal'],
        'waktu': cur_ts['waktu'],
        'sender': cur_ts['sender'],
        'tipe': 'eskalasi',
        'cabang': code,
        'awb': None,             # awb tunggal
        'awbs': [],              # daftar multi-AWB: [{'awb':.., 'cabang':..}, ..]
        'chat': '',
        'ket': '',
        'client': '',
        'kendala': '',
        'butuh_bantuan': '',
        'patokan': '',
        'reading_awb_list': False,
        'pending_branch': None,
    }


def _emit_escal(cur_escal):
    """Ubah blok eskalasi menjadi 1..N entry (pecah per AWB bila multi-AWB)."""
    base = {
        'tanggal': cur_escal['tanggal'],
        'waktu': cur_escal['waktu'],
        'sender': cur_escal['sender'],
        'tipe': 'eskalasi',
        'chat': '',
        'ket': '',
        'client': cur_escal['client'],
        'kendala': cur_escal['kendala'],
        'butuh_bantuan': cur_escal['butuh_bantuan'],
        'patokan': cur_escal['patokan'],
    }
    awbs = cur_escal['awbs']
    if awbs:
        out = []
        for a in awbs:
            e = dict(base)
            e['awb'] = a['awb']
            e['cabang'] = (a['cabang'] or cur_escal['cabang'])
            out.append(e)
        return out
    e = dict(base)
    e['awb'] = cur_escal['awb']
    e['cabang'] = cur_escal['cabang']
    return [e]


def parse_text(text):
    """Parse seluruh teks chat -> list[dict] entries."""
    entries = []
    cur_ts = None          # timestamp pesan pembungkus terakhir
    cur_block = None       # blok TLC yang sedang diisi
    cur_escal = None       # blok eskalasi yang sedang diisi

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
                entries.extend(_emit_escal(cur_escal))
                cur_escal = None
            cur_ts = ts
            # Baris timestamp yang juga memuat konten -> fall-through ke logika konten
            body_stripped = ts['body'].strip()
            if not body_stripped:
                continue
            stripped = body_stripped

        if cur_ts is None:
            continue

        # --- Blok TLC digest ---
        m = TLC_START_RE.match(stripped)
        if m:
            if cur_block:
                entries.append(cur_block)
            cur_block = _new_tlc(cur_ts, m.group(1).upper())
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
            # lanjut ke pengecekan eskalasi? TLC block tidak punya "Dear" di dalam,
            # tapi demi keamanan tetap lanjut (tidak `continue`).
            # (fall-through)

        # --- Eskalasi komplain ---
        dear = _match_dear(stripped)
        if dear is not None:
            broadcast, code = dear
            if broadcast:
                # jangan buat eskalasi (bukan 1 cabang)
                continue
            if cur_escal:
                entries.extend(_emit_escal(cur_escal))
            cur_escal = _new_escal(cur_ts, code)
            continue

        if cur_escal:
            # Field AWB
            if AWB_EMPTY_RE.match(stripped):
                cur_escal['reading_awb_list'] = True
                continue
            m_awb = AWB_TAB_RE.match(stripped)
            if m_awb:
                val = m_awb.group(1)
                if val:
                    cur_escal['awb'] = val
                else:
                    cur_escal['reading_awb_list'] = True
                continue
            # Field client/kendala/butuh/patokan -> berhenti baca daftar AWB
            m7 = CLIENT_RE.match(stripped)
            if m7 and m7.group(1).strip():
                cur_escal['reading_awb_list'] = False
                cur_escal['client'] = m7.group(1).strip()
                continue
            m8 = KENDALA_RE.match(stripped)
            if m8 and m8.group(1).strip():
                cur_escal['reading_awb_list'] = False
                cur_escal['kendala'] = m8.group(1).strip()
                continue
            m9 = BUTUH_RE.match(stripped)
            if m9 and m9.group(1).strip():
                cur_escal['reading_awb_list'] = False
                cur_escal['butuh_bantuan'] = m9.group(1).strip()
                continue
            m10 = PATOKAN_RE.match(stripped)
            if m10:
                cur_escal['reading_awb_list'] = False
                cur_escal['patokan'] = m10.group(1).strip()
                continue

            # AWB langsung (tanpa header "AWB :") mis. "LNCH2512A8IIAENR *SJO* @..."
            m_loose = AWB_ITEM_RE.match(stripped)
            if m_loose and COURIER_PREFIX_RE.match(stripped):
                awb = m_loose.group('awb')
                branch = (m_loose.group('dash') or m_loose.group('tab')
                          or m_loose.group('space')
                          or cur_escal['pending_branch'] or None)
                if branch:
                    branch = branch.upper()
                cur_escal['awbs'].append({'awb': awb, 'cabang': branch})
                cur_escal['pending_branch'] = None
                continue

            # Baris dalam daftar multi-AWB
            if cur_escal['reading_awb_list']:
                # "SOC @~." -> ingat cabang, terapkan ke AWB berikutnya
                m_br = BRANCH_LINE_RE.match(stripped)
                if m_br:
                    cur_escal['pending_branch'] = m_br.group(1)
                    continue
                m_item = AWB_ITEM_RE.match(stripped)
                if m_item and COURIER_PREFIX_RE.match(stripped):
                    awb = m_item.group('awb')
                    branch = (m_item.group('dash') or m_item.group('tab')
                              or m_item.group('space')
                              or cur_escal['pending_branch'] or None)
                    if branch:
                        branch = branch.upper()
                    cur_escal['awbs'].append({'awb': awb, 'cabang': branch})
                    cur_escal['pending_branch'] = None
                    continue
                # Bukan AWB -> mungkin baris kosong / teks lain, hentikan list bila
                # bukan karakter AWB (supaya tidak salah tangkap nomor telepon dll).
                if m_item is None:
                    cur_escal['reading_awb_list'] = False

            # Akhiri eskalasi saat ada penutup
            if stripped.lower().startswith('terima kasih'):
                entries.extend(_emit_escal(cur_escal))
                cur_escal = None
                continue

    # sisa blok di akhir file
    if cur_block:
        entries.append(cur_block)
    if cur_escal:
        entries.extend(_emit_escal(cur_escal))

    return entries


def load_zip_bytes(zip_bytes):
    """Baca .txt dari dalam bytes ZIP -> teks."""
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
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
