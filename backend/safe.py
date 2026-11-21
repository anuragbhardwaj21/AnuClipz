"""Mask words that get a reel removed, and mark where the voice should beep."""
from __future__ import annotations

import re

# Sexual and removal-level words. Game words such as kill are left alone.
WORDS = (
    'porn', 'porno', 'pornography', 'pornographic', 'p0rn', 'pr0n',
    'sex', 'sexual', 'sexy', 'nsfw', 'xxx',
    'nude', 'nudes', 'naked', 'nudity',
    'onlyfans', 'boob', 'boobs', 'tits', 'titty',
    'dick', 'cock', 'pussy', 'slut', 'whore',
    'rape', 'rapist', 'raping',
    'fuck', 'fucking', 'fucked', 'fucker', 'motherfucker',
    'nigga', 'nigger', 'faggot', 'fag', 'retard',
)
RISK = re.compile(r'\b(?:' + '|'.join(sorted(WORDS, key=len, reverse=True)) + r')\b', re.IGNORECASE)


def mask_word(word: str) -> str:
    if len(word) < 2:
        return '*'
    return word[0] + '*' + word[2:]


def censor(text: str) -> str:
    return RISK.sub(lambda match: mask_word(match.group(0)), str(text or ''))


def risky(text: str) -> bool:
    return RISK.search(str(text or '')) is not None


def beep_spans(words: list[dict], start: float, end: float) -> list[dict]:
    spans = []
    for word in words:
        at = float(word.get('start') or 0)
        if at < start or at >= end or not risky(str(word.get('text') or '')):
            continue
        begin = max(0.0, at - start)
        stop = max(begin + 0.12, float(word.get('end') or at + 0.25) - start)
        spans.append({'start': round(begin, 2), 'end': round(stop, 2)})
    return _merge(spans)


def spans_from_lines(lines: list[dict]) -> list[dict]:
    spans = []
    for line in lines:
        if not isinstance(line, dict) or not risky(str(line.get('text') or '')):
            continue
        begin = max(0.0, float(line.get('start') or 0))
        stop = max(begin + 0.12, float(line.get('end') or begin + 0.3))
        spans.append({'start': round(begin, 2), 'end': round(stop, 2)})
    return _merge(spans)


def beep_enable(spans: list[dict]) -> str:
    parts = []
    for span in _merge(spans):
        begin = max(0.0, float(span.get('start') or 0))
        stop = max(begin + 0.08, float(span.get('end') or begin + 0.2))
        parts.append(f'between(t,{begin:.2f},{stop:.2f})')
    return '+'.join(parts)


def _merge(spans: list[dict]) -> list[dict]:
    ordered = sorted(spans, key=lambda item: float(item['start']))
    merged: list[dict] = []
    for span in ordered:
        if merged and float(span['start']) <= float(merged[-1]['end']) + 0.05:
            merged[-1]['end'] = round(max(float(merged[-1]['end']), float(span['end'])), 2)
        else:
            merged.append({'start': round(float(span['start']), 2), 'end': round(float(span['end']), 2)})
    return merged
