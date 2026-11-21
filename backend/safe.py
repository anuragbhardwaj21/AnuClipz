"""Mask words that get a reel removed."""
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

