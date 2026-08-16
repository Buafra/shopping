"""What kind of thing is being searched for.

Only one question matters here so far: is this a PC component? Component
specialists are where the cheap stock is, but they carry nothing else, so
querying them for a kettle wastes the search budget and returns noise. Tagging
those stores `pc_parts` and matching the query against this module keeps them
out of searches they cannot answer.

Detection is deliberately generous. A false positive costs a few seconds on
stores that return nothing; a false negative costs the shopper the cheapest
listing, which is the entire point of the app.
"""

from __future__ import annotations

import re

# Product families. Matched as whole words so "ram" does not fire on "ramen"
# and "case" is not a PC chassis just because someone wants a phone case.
PC_PART_TERMS = {
    "gpu", "cpu", "psu", "ssd", "hdd", "nvme", "ram", "vram", "apu",
    "motherboard", "mainboard", "mobo", "processor", "cooler", "heatsink",
    "chassis", "graphics", "videocard", "dimm", "sodimm", "aio", "watercooling",
    "overclock", "thermal", "pcie", "sata", "atx", "itx", "matx",
}

# Multi-word forms, matched as substrings.
PC_PART_PHRASES = (
    "graphics card", "video card", "power supply", "gaming pc", "pc case",
    "cpu cooler", "liquid cooler", "air cooler", "thermal paste",
    "solid state drive", "hard drive", "memory kit", "ram kit",
    "gaming monitor", "pc build", "computer case", "case fan",
)

# Brand-and-model families. These are the strongest signal there is: nobody
# types "rtx 4070" or "7800x3d" unless they are buying a component.
PC_PART_PATTERNS = (
    r"\brtx\s?\d{3,4}",                 # RTX 4070, RTX3080
    r"\bgtx\s?\d{3,4}",                 # GTX 1660
    r"\brx\s?\d{3,4}\b",                # RX 7900
    r"\barc\s?[ab]\d{3}",               # Intel Arc A770
    r"\bryzen\s?\d",                    # Ryzen 7
    r"\bthreadripper\b",
    r"\bcore\s?i[3579]\b",              # Core i7
    r"\bcore\s?ultra\b",
    r"\bi[3579][- ]?\d{4,5}[a-z]{0,2}\b",   # i5-12400F
    r"\b\d{4}x3d\b",                    # 7800X3D
    r"\bddr[45]\b",
    r"\b[bxzh]\d{3}[a-z]?\s?(?:motherboard|chipset|mobo)?\b(?=.*(?:mother|chipset|mobo|am5|am4|lga))",
    r"\bam[45]\b",
    r"\blga\s?\d{3,4}\b",
    r"\b(?:radeon|geforce|nvidia|amd|intel)\b",
    r"\b\d+\s?tb\s+(?:ssd|nvme|hdd)\b",
)

_WORD = re.compile(r"[a-z0-9]+")
_COMPILED = tuple(re.compile(p, re.I) for p in PC_PART_PATTERNS)


def looks_like_pc_part(query: str) -> bool:
    """True when the search is for a computer component."""
    text = (query or "").lower().strip()
    if not text:
        return False

    if any(phrase in text for phrase in PC_PART_PHRASES):
        return True
    if set(_WORD.findall(text)) & PC_PART_TERMS:
        return True
    return any(pattern.search(text) for pattern in _COMPILED)


#: tag -> predicate. A store tagged here is used only when its predicate says
#: the query is relevant to it.
TAG_MATCHERS = {
    "pc_parts": looks_like_pc_part,
}


def store_matches_query(tags: tuple[str, ...], query: str) -> bool:
    """Whether a store's tags allow it to take part in this search.

    An untagged store is general-purpose and always used, which keeps every
    store that worked before working exactly as it did.
    """
    if not tags:
        return True
    return any(
        TAG_MATCHERS[tag](query) for tag in tags if tag in TAG_MATCHERS
    )
