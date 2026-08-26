"""
Parsing berbasis LLM (DeepSeek) untuk backup chat WhatsApp grup NETWORK JAWABALI.

Alur:
  parser.messages_for_date(text, tanggal)  -> pesan mentah 1 hari
  llm_parser.parse_day(api_key, tanggal, messages) -> list[dict] entries
      (format sama dgn output regex parser.parse_text / _emit_escal,
       siap dipakai app.py + docx_report.build_docx)

API key dibaca dari argumen, env DEEPSEEK_API_KEY, atau st.secrets.
"""

import json
import os
import re

import requests

API_URL = "https://api.deepseek.com/chat/completions"
DEFAULT_MODEL = "deepseek-chat"          # V3 — cepat & murah, cocok ekstraksi
REASONER_MODEL = "deepseek-reasoner"     # R1 — lebih teliti, lebih lambat

MAX_INPUT_CHARS = 40_000     # per request (aman utk context 64k token)
MAX_OUTPUT_TOKENS = 8_192    # batas output deepseek-chat
TIMEOUT = 180                # detik
TEMPERATURE = 0.0            # ekstraksi = deterministic

SYSTEM_PROMPT = """\
Kamu adalah extractor data presisi tinggi untuk laporan logistik.
Input: potongan chat WhatsApp dari grup operasional "NETWORK JAWABALI" \
(kurir/logistik Indonesia). Tugasmu: ekstrak SEMUA entry TLC dan eskalasi \
komplain menjadi JSON.

== FORMAT 1: Blok TLC digest ==
Blok yang diawali baris seperti `*TLC* : KODE` (KODE = kode cabang, mis. PWO, \
CXP, KBM, BJN; kadang diawali tanda kutip), lalu field-field:
```
*TLC* : PWO
*AWB* : IDE7001237332234
*Chat Pengirim* : <isi chat>
*Ket* : <keterangan>
```
Hasilkan satu objek: {"tipe": "tlc", "cabang": "PWO", "awb": "IDE7001237332234", \
"chat": "<isi>", "ket": "<ket>"}
Field yang kosong/tidak ada -> string kosong "". awb null jika tidak disebut.

== FORMAT 2: Eskalasi komplain ==
Diawali sapaan "Dear Team Cabang XXX" / "Dear Team XXX" / "dear team cabang XXX" \
(XXX = kode cabang, mungkin dibungkus bintang *XXX*). Variasi:
- Ada kode cabang di sapaan -> pakai kode itu.
- "Dear Team Cabang" polos tanpa kode -> cabang diambil dari tiap baris AWB \
di bawahnya (suffix setelah tab/dash/bintang).
- Broadcast ke banyak cabang ("Dear Team,", "Dear Team Network", "... dan Cabang", \
"... Selindo") -> JANGAN diekstrak (skip).

Isi blok eskalasi bisa memuat field:
```
Client : <nama client>
Kendala : <deskripsi kendala>
Butuh Bantuan : <permintaan>
Patokan : <patokan waktu/jam kirim>
```
Nomor resi (AWB) bisa:
- tunggal: baris "AWB : <resi>"
- daftar multi-AWB: baris "AWB :" kosong lalu daftar resi, satu per baris, \
tiap baris bisa "RESI<TAB>CABANG @nama", "RESI - CABANG", "RESI *CABANG*", \
atau "RESI" polos (warisi cabang header/pending). Kadang ada baris kode cabang \
berdiri sendiri (mis. "SOC @~.") yang berlaku utk resi-resi setelahnya.
Resi = 8-20 karakter alfanumerik dengan prefix huruf (mis. IDE700..., LNCH25...). \
JANGAN ambil nomor telepon sebagai resi.

Satu blok eskalasi multi-AWB hasilkan SATU objek dengan array "awbs":
{"tipe": "eskalasi", "cabang": "SRG|", "client": "...", "kendala": "...", \
"butuh_bantuan": "...", "patokan": "...", "awb": null, \
"awbs": [{"awb": "IDE700123...", "cabang": "BJN"}, ...]}
Untuk eskalasi AWB tunggal: isi "awb" langsung dan "awbs": [].

== ATURAN ==
- Abaikan pesan biasa, sapaan, salam, sticker, admin talk, dsb.
- cabang selalu UPPERCASE 2-4 huruf. Kalau tidak jelas -> "".
- Pertahankan teks asli field apa adanya (jangan parafrase).
- Balas HANYA JSON valid, tanpa penjelasan, format:
{"entries": [ {objek}, {objek}, ... ]}
- Jika tidak ada entry sama sekali: {"entries": []}\
"""


class LLMParseError(Exception):
    pass


def _get_key(api_key=None):
    if api_key:
        return api_key.strip()
    key = os.getenv('DEEPSEEK_API_KEY', '')
    if key:
        return key.strip()
    try:
        import streamlit as st
        if 'DEEPSEEK_API_KEY' in st.secrets:
            return str(st.secrets['DEEPSEEK_API_KEY']).strip()
    except Exception:
        pass
    raise LLMParseError(
        'DeepSeek API key tidak ditemukan. Isi di sidebar atau set '
        'env DEEPSEEK_API_KEY.'
    )


def _chunk_messages(messages):
    """Pecah list pesan jadi chunk <= MAX_INPUT_CHARS (pecah di batas pesan)."""
    chunks, cur, size = [], [], 0
    for m in messages:
        n = len(m.get('body', '')) + 40
        if cur and size + n > MAX_INPUT_CHARS:
            chunks.append(cur)
            cur, size = [], 0
        cur.append(m)
        size += n
    if cur:
        chunks.append(cur)
    return chunks


def _render_messages(messages):
    lines = []
    for m in messages:
        sender = m.get('sender', '?').strip()
        waktu = m.get('waktu', '')
        body = m.get('body', '').rstrip()
        lines.append(f"[{waktu}] {sender}: {body}")
    return '\n'.join(lines)


def _extract_json(text):
    """Ambil objek JSON pertama dari balasan model (tahan code fence)."""
    text = text.strip()
    fence = re.search(r'```(?:json)?\s*(.*?)\s*```', text, re.S)
    if fence:
        text = fence.group(1).strip()
    start = text.find('{')
    end = text.rfind('}')
    if start == -1 or end == -1 or end <= start:
        raise LLMParseError(f'Balasan LLM bukan JSON: {text[:200]!r}')
    return json.loads(text[start:end + 1])


_AWB_PLAIN_RE = re.compile(r'^[A-Za-z]{2,4}[0-9][A-Za-z0-9]{5,}$')


def _clean_str(v):
    if v is None:
        return ''
    return str(v).strip().strip('*').strip()


def _is_awb(v):
    return bool(v) and bool(_AWB_PLAIN_RE.match(str(v)))


def _finalize_entry(e, tanggal):
    """Validasi + normalisasi satu entry mentah dari LLM."""
    tipe = str(e.get('tipe', '')).lower()
    if tipe not in ('tlc', 'eskalasi'):
        tipe = 'eskalasi' if 'eskala' in str(e.get('tipe', '')).lower() else 'tlc'

    def s(k):
        return _clean_str(e.get(k))

    base = {
        'tanggal': tanggal,
        'waktu': s('waktu'),
        'sender': s('sender'),
        'tipe': tipe,
        'cabang': s('cabang').upper(),
        'chat': s('chat'),
        'ket': s('ket'),
    }
    out = []
    if tipe == 'tlc':
        awb = e.get('awb')
        base['awb'] = str(awb).strip() if _is_awb(awb) else None
        out.append(base)
        return out

    # eskalasi
    base.update({
        'client': s('client'),
        'kendala': s('kendala'),
        'butuh_bantuan': s('butuh_bantuan'),
        'patokan': s('patokan'),
    })
    awbs = []
    raw_awbs = e.get('awbs') or []
    if isinstance(raw_awbs, dict):
        raw_awbs = [raw_awbs]
    for a in raw_awbs:
        if not isinstance(a, dict):
            continue
        awb = _clean_str(a.get('awb'))
        br = _clean_str(a.get('cabang')).upper()
        if _is_awb(awb):
            awbs.append({'awb': awb, 'cabang': br})
    single = e.get('awb')
    if not awbs and isinstance(single, str) and _is_awb(single):
        awbs = [{'awb': single.strip(), 'cabang': base['cabang']}]
    elif not awbs and isinstance(single, dict) and _is_awb(single.get('awb')):
        awbs = [{'awb': _clean_str(single.get('awb')),
                 'cabang': _clean_str(single.get('cabang')).upper()}]

    if awbs:
        hdr_cabang = base['cabang']
        for a in awbs:
            item = dict(base)
            item['awb'] = a['awb']
            item['cabang'] = a['cabang'] or hdr_cabang
            out.append(item)
    else:
        base['awb'] = None
        out.append(base)
    return out


def parse_day(api_key, tanggal, messages, model=DEFAULT_MODEL, on_progress=None):
    """Ekstrak semua entry TLC/eskalasi dari pesan satu tanggal via DeepSeek.

    Return (entries, stats) — stats berisi total pemakaian token.
    """
    api_key = _get_key(api_key)
    messages = messages or []
    chunks = _chunk_messages(messages)

    all_entries, stats = [], {'prompt_tokens': 0, 'completion_tokens': 0}
    for i, ch in enumerate(chunks, 1):
        if on_progress:
            on_progress(i, len(chunks))
        body = (
            f"Berikut chat WhatsApp tanggal {tanggal} "
            f"({len(ch)} pesan). Ekstrak semua entry TLC & eskalasi.\n\n"
            f"=== CHAT ===\n{_render_messages(ch)}\n=== AKHIR CHAT ==="
        )
        # fallback: reasoner kadang menolak response_format json_object
        use_json_mode = model == DEFAULT_MODEL
        data, st_ = None, None
        for attempt in range(3):
            payload_model = model
            try:
                if attempt >= 1:
                    # retry: turunkan ke model default & json mode
                    payload_model = DEFAULT_MODEL
                    use_json_mode = True
                data, st_ = _request(api_key, payload_model, tanggal, body,
                                     json_mode=use_json_mode)
                break
            except (LLMParseError, requests.RequestException) as exc:
                if attempt == 2:
                    raise LLMParseError(
                        f'Gagal memanggil DeepSeek setelah 3 percobaan: {exc}'
                    )
        entries_raw = data.get('entries', []) if isinstance(data, dict) else []
        if not isinstance(entries_raw, list):
            entries_raw = []
        for e in entries_raw:
            if isinstance(e, dict):
                all_entries.extend(_finalize_entry(e, tanggal))
        stats['prompt_tokens'] += st_.get('prompt_tokens') or 0
        stats['completion_tokens'] += st_.get('completion_tokens') or 0
    return all_entries, stats


def _request(api_key, model, tanggal, body, json_mode=True):
    payload = {
        'model': model,
        'messages': [
            {'role': 'system', 'content': SYSTEM_PROMPT},
            {'role': 'user',
             'content': f'Tanggal laporan: {tanggal}\n\n{body}'},
        ],
        'temperature': TEMPERATURE,
        'max_tokens': MAX_OUTPUT_TOKENS,
        'stream': False,
    }
    if json_mode:
        payload['response_format'] = {'type': 'json_object'}
    resp = requests.post(
        API_URL,
        headers={
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
        },
        json=payload,
        timeout=TIMEOUT,
    )
    if resp.status_code != 200:
        raise LLMParseError(f'DeepSeek API {resp.status_code}: {resp.text[:300]}')
    data = resp.json()
    try:
        content = data['choices'][0]['message']['content']
    except (KeyError, IndexError):
        raise LLMParseError(f'Respon tak terduga dari DeepSeek: {str(data)[:300]}')
    usage = data.get('usage') or {}
    stats = {
        'prompt_tokens': usage.get('prompt_tokens'),
        'completion_tokens': usage.get('completion_tokens'),
    }
    return _extract_json(content), stats
