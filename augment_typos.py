#!/usr/bin/env python3
import csv
import re
from pathlib import Path

IN = Path("kamus_surah_bersih.csv")
OUT = Path("kamus_surah_bersih_extended.csv")

def generate_variants(word):
    w = word.strip()
    if not w:
        return set()
    wlow = w.lower()
    variants = set()

    # base normalizations
    variants.add(wlow)
    variants.add(re.sub(r"['’`\"]", "", wlow))
    variants.add(re.sub(r"[-_]", " ", wlow))
    variants.add(re.sub(r"[\W_]+", "", wlow))
    variants.add(wlow.replace(" ", ""))

    # prefix/suffix heuristics for Arabic name patterns
    if wlow.startswith("al"):
        core = wlow[2:]
        core = core.lstrip(" -'\"")
        if core:
            variants.add(core)
            variants.add("al" + core)
            variants.add("al " + core)
            variants.add("el" + core)
            variants.add("ul" + core)

    # vowel variation (common ASR vowel shifts)
    vowels = "aeiou"
    for i, ch in enumerate(wlow):
        if ch in vowels:
            for sub in ["a", "e", "i", "o", "u", "ah", "aa", "ai", "au", "y"]:
                v = wlow[:i] + sub + wlow[i+1:]
                variants.add(v)

    # common letter confusions
    swaps = {"k":"q","q":"k","s":"z","z":"s","f":"p","p":"f","b":"v","v":"b","c":"k","g":"j"}
    for i, ch in enumerate(wlow):
        if ch in swaps:
            variants.add(wlow[:i] + swaps[ch] + wlow[i+1:])

    # remove double letters -> single
    variants.add(re.sub(r"(.)\1+", r"\1", wlow))

    # combine/split hyphen/space variants
    parts = re.split(r"[- ]+", wlow)
    if len(parts) > 1:
        variants.add("".join(parts))
        variants.add(" ".join(parts))

    # punctuation-stripped and short forms
    variants.add(re.sub(r"[^a-z0-9]", "", wlow))

    # keep reasonable length and remove empties
    cleaned = {v for v in variants if v and len(v) <= 40}
    return cleaned

def expand_row_typos(typo_field):
    items = [t.strip() for t in re.split(r",", typo_field) if t.strip()]
    out = set()
    for it in items:
        out.add(it)
        for v in generate_variants(it):
            out.add(v)
    # also add small human-like errors: repeated last syllable, dropped vowels
    extras = set()
    for v in list(out):
        if len(v) > 2:
            # drop vowels
            extras.add(re.sub(r"[aeiou]", "", v))
            # repeat last 2 chars
            extras.add(v + v[-2:])
    out.update(extras)
    # final cleanup: strip and dedupe, convert to readable forms (replace empty-with)
    final = []
    for v in sorted(out):
        vv = v.strip()
        if vv:
            final.append(vv)
    return ", ".join(final)

def main():
    if not IN.exists():
        print("Input file not found:", IN)
        return
    rows = []
    with IN.open(newline='', encoding='utf-8') as f:
        r = csv.DictReader(f)
        fieldnames = r.fieldnames
        for row in r:
            typo = row.get('typo_asr', '')
            expanded = expand_row_typos(typo)
            row['typo_asr'] = expanded
            rows.append(row)

    with OUT.open('w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for row in rows:
            w.writerow(row)

    print('Wrote extended CSV to', OUT)

if __name__ == '__main__':
    main()
