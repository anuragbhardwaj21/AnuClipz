"""Word times from YouTube's own captions. No speech model."""
import json
import re
import urllib.request

from unidecode import unidecode

VOWELS = {
    'अ': 'a', 'आ': 'a', 'इ': 'i', 'ई': 'i', 'उ': 'u', 'ऊ': 'u', 'ऋ': 'ri',
    'ए': 'e', 'ऐ': 'ai', 'ओ': 'o', 'औ': 'au', 'ॐ': 'om',
}
CONSONANTS = {
    'क': 'k', 'ख': 'kh', 'ग': 'g', 'घ': 'gh', 'ङ': 'ng',
    'च': 'ch', 'छ': 'chh', 'ज': 'j', 'झ': 'jh', 'ञ': 'ny',
    'ट': 't', 'ठ': 'th', 'ड': 'd', 'ढ': 'dh', 'ण': 'n',
    'त': 't', 'थ': 'th', 'द': 'd', 'ध': 'dh', 'न': 'n',
    'प': 'p', 'फ': 'ph', 'ब': 'b', 'भ': 'bh', 'म': 'm',
    'य': 'y', 'र': 'r', 'ल': 'l', 'व': 'v', 'श': 'sh', 'ष': 'sh', 'स': 's', 'ह': 'h',
}
MATRAS = {
    'ा': 'a', 'ि': 'i', 'ी': 'i', 'ु': 'u', 'ू': 'u', 'ृ': 'ri',
    'े': 'e', 'ै': 'ai', 'ो': 'o', 'ौ': 'au', 'ं': 'n', 'ँ': 'n', 'ः': 'h',
}
NUKTA = {'क': 'q', 'ख': 'kh', 'ग': 'g', 'ज': 'z', 'ड': 'r', 'ढ': 'rh', 'फ': 'f', 'य': 'y'}


def romanize(text: str) -> str:
    out: list[str] = []
    chars = list(text)
    index = 0
    while index < len(chars):
        char = chars[index]
        if char in VOWELS:
            out.append(VOWELS[char])
            index += 1
            continue
        if char not in CONSONANTS:
            out.append(unidecode(char) if not char.isascii() else char)
            index += 1
            continue
        base = CONSONANTS[char]
        index += 1
        if index < len(chars) and chars[index] == '़':
            base = NUKTA.get(char, base)
            index += 1
        if index < len(chars) and chars[index] == '्':
            out.append(base)
            index += 1
            continue
        if index < len(chars) and chars[index] in MATRAS:
            out.append(base + MATRAS[chars[index]])
            index += 1
            if index < len(chars) and chars[index] in {'ं', 'ँ'}:
                out.append('n')
                index += 1
            continue
        nxt = chars[index] if index < len(chars) else ''
        out.append(base if nxt in {'', ' ', '\n', '.', ',', '!', '?'} else base + 'a')
    return ''.join(out)


def to_latin(text: str) -> str:
    raw = str(text or '')
    if not raw or raw.isascii():
        return raw
    letters = re.sub(r'\s+', ' ', romanize(raw)).strip()
    if 'गेम' in raw:
        letters = re.sub(r'\bgem\b', 'game', letters)
    return letters or raw


def from_info(info: dict) -> list[dict] | None:
    auto = info.get('automatic_captions') or {}
    manual = info.get('subtitles') or {}
    lang = info.get('language') or ''
    originals = [key for key in auto if key.endswith('-orig')]
    key = None
    if lang and f'{lang}-orig' in auto:
        key = f'{lang}-orig'
    elif lang and lang in auto:
        key = lang
    elif len(originals) == 1:
        key = originals[0]
    elif 'en-orig' in auto:
        key = 'en-orig'
    elif originals:
        key = originals[0]
    group = auto
    if not key:
        key = lang if lang in manual else None
        group = manual
    tracks = group.get(key) or [] if key else []
    track = next((item for item in tracks if item.get('ext') == 'json3' and item.get('url')), None)
    if not track:
        return None
    request = urllib.request.Request(track['url'], headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(request, timeout=120) as response:
        data = json.loads(response.read(40_000_000))
    words: list[dict] = []
    skipping = False
    for event in data.get('events') or []:
        pieces = [item for item in event.get('segs') or [] if (item.get('utf8') or '').strip()]
        if not pieces:
            continue
        start_ms = float(event.get('tStartMs') or 0)
        duration_ms = float(event.get('dDurationMs') or 0)
        timed = len(pieces) > 1 or 'tOffsetMs' in pieces[0]
        for piece in pieces:
            parts = piece['utf8'].split()
            for index, word in enumerate(parts):
                if skipping:
                    if word.endswith((']', ')')):
                        skipping = False
                    continue
                if word[:1] in '[(' and not re.fullmatch(r'[\[(].*[\])]', word):
                    skipping = True
                    continue
                if re.fullmatch(r'[\[(].*[\])]', word) or word in ('[', ']', '__', '>>', '-'):
                    continue
                at = start_ms + float(piece.get('tOffsetMs') or 0) if timed else start_ms + duration_ms * index / max(1, len(parts))
                spoken = to_latin(word)
                if spoken:
                    words.append({'start': round(at / 1000, 2), 'text': spoken})
    words.sort(key=lambda item: item['start'])
    for index, word in enumerate(words):
        nxt = words[index + 1]['start'] if index + 1 < len(words) else word['start'] + 0.4
        word['end'] = round(max(word['start'] + 0.08, min(nxt, word['start'] + 0.6)), 2)
    return words or None
