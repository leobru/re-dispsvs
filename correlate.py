#!/usr/bin/env python3
"""Correlate two BESM-6 binaries by unique matching 6-byte words.

Words that occur exactly once in each file give an unambiguous pair of
positions. Matches are walked in A-address order; each stretch of constant
delta (B index − A index) is reported as its own run.
"""
import argparse
from collections import defaultdict
from pathlib import Path
import sys

WORD = 6


def load_words(path):
    data = path.read_bytes()
    if len(data) % WORD:
        raise ValueError(f'{path}: length {len(data)} is not a multiple of {WORD}')
    return [data[i:i + WORD] for i in range(0, len(data), WORD)]


def word_octal(data):
    return f'{int.from_bytes(data, "big"):016o}'


def index_unique(words, skip=frozenset()):
    """Map word value -> index for values that appear exactly once."""
    positions = defaultdict(list)
    for index, word in enumerate(words):
        if word in skip:
            continue
        positions[word].append(index)
    return {word: spots[0] for word, spots in positions.items() if len(spots) == 1}


def correlate(words_a, words_b, skip_zero=True):
    skip = {bytes(WORD)} if skip_zero else frozenset()
    unique_a = index_unique(words_a, skip)
    unique_b = index_unique(words_b, skip)
    shared = sorted(set(unique_a) & set(unique_b), key=lambda w: unique_a[w])
    return [(unique_a[word], unique_b[word], word) for word in shared]


def delta_runs(pairs):
    """Split A-ordered pairs into runs where the delta stays the same."""
    runs = []
    for a, b, word in pairs:
        delta = b - a
        if not runs or runs[-1][0] != delta:
            runs.append((delta, [(a, b, word)]))
        else:
            runs[-1][1].append((a, b, word))
    return runs


def format_index(index, zone_base):
    if zone_base is None:
        return f'{index:05o}'
    zone = zone_base + index // 1024
    word = index % 1024
    return f'{zone:04o}:{word:04o}'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('a', type=Path, help='first binary (reference)')
    parser.add_argument('b', type=Path, help='second binary')
    parser.add_argument('--keep-zero', action='store_true',
                        help='do not ignore the all-zero word')
    parser.add_argument('--min-hits', type=int, default=1,
                        help='only report runs with at least this many unique matches')
    parser.add_argument('--top', type=int, default=20,
                        help='show at most this many runs in A-address order (0 = all)')
    parser.add_argument('--samples', type=int, default=5,
                        help='first and last N matches to print per run')
    parser.add_argument('--zone-base', type=lambda s: int(s, 8), default=None,
                        metavar='ZZZZ',
                        help='print word indices as octal zone:word from this base zone')
    args = parser.parse_args()

    words_a = load_words(args.a)
    words_b = load_words(args.b)
    pairs = correlate(words_a, words_b, skip_zero=not args.keep_zero)

    print(f'{args.a}: {len(words_a)} words')
    print(f'{args.b}: {len(words_b)} words')
    print(f'unique matching words: {len(pairs)}'
          f'{" (zero word ignored)" if not args.keep_zero else ""}')
    if not pairs:
        return 1

    runs = [(delta, matches) for delta, matches in delta_runs(pairs)
            if len(matches) >= args.min_hits]
    if args.top:
        runs = runs[:args.top]
    print(f'delta runs in A-address order (B index − A index), min hits {args.min_hits}:')
    for delta, matches in runs:
        count = len(matches)
        print(f'  delta {delta:+o}: {count} matches')
        n = args.samples
        if count <= 2 * n:
            shown = matches
            gap = 0
        else:
            shown = matches[:n] + matches[-n:]
            gap = count - 2 * n
        for i, (a, b, word) in enumerate(shown):
            if gap and i == n:
                print(f'    ... {gap} more ...')
            print(f'    A {format_index(a, args.zone_base)}  '
                  f'B {format_index(b, args.zone_base)}  {word_octal(word)}')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError) as error:
        print(error, file=sys.stderr)
        sys.exit(2)
