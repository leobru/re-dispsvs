#!/usr/bin/env python3
"""Generate .sym files from BEMSH listings (re-dispak flatten.pl).

Reads the *МЕТ* symbol table at the end of a listing (8 columns × 16 chars)
and emits lines consumed by disbesm6 / merge.pl / extern.pl:

    AAAAA F NAME
    AAAAA F NAME entry MODULE

Flags: 0 normal, 1 if name is D#####, 2 if name is А#####.
Externals marked СА in the listing are skipped.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

COL_WIDTH = 16
N_COLS = 8
CELL = re.compile(r'([^ ]+) +([0-7]{5})')
AUTO_D = re.compile(r'^D[0-7]{5}$')
AUTO_A = re.compile(r'^А[0-7]{5}$')
MODULE = re.compile(r'^ИПМ.* {3}([^ ]+) {2}')
MET = '*МЕТ*'
MET_END = '**********'


def module_name(lines: list[str]) -> str | None:
    for line in lines:
        match = MODULE.match(line)
        if match:
            return match[1]
    return None


def met_rows(lines: list[str]) -> list[str]:
    """Return raw *МЕТ* table rows (between *МЕТ* and **********)."""
    start = None
    for i, line in enumerate(lines):
        if MET in line:
            start = i + 1
            break
    if start is None:
        raise ValueError('listing has no *МЕТ* symbol table')
    rows = []
    for line in lines[start:]:
        if line.startswith(MET_END):
            return rows
        rows.append(line.rstrip('\n'))
    raise ValueError('listing *МЕТ* table is not terminated by **********')


def cells(rows: list[str]):
    """Yield cells in column-major order, matching flatten.pl."""
    for col in range(N_COLS):
        for row in rows:
            # Pad so short lines still expose empty trailing columns.
            padded = row.ljust(N_COLS * COL_WIDTH)
            yield padded[col * COL_WIDTH:(col + 1) * COL_WIDTH]


def symbols_from_listing(text: str) -> tuple[list[tuple[str, int, str, bool]], str | None]:
    """Return ((addr, flags, name, is_entry)... , module) in flatten.pl order."""
    lines = text.splitlines()
    mod = module_name(lines)
    rows = met_rows(lines)
    out: list[tuple[str, int, str, bool]] = []
    for cell in cells(rows):
        if re.search(r'СА *$', cell):
            continue
        match = CELL.search(cell)
        if not match:
            continue
        name, addr = match[1], match[2]
        if addr == '00000' or name in ('0ЛИТ', '000000'):
            continue
        flags = 0
        if AUTO_D.match(name):
            flags = 1
        elif AUTO_A.match(name):
            flags = 2
        is_entry = bool(re.search(r' [0-7]{5}В', cell))
        out.append((addr, flags, name, is_entry))
    if any(e[3] for e in out) and mod is None:
        raise ValueError('entry symbol without module name in listing header')
    return out, mod


def format_sym(entries: list[tuple[str, int, str, bool]], mod: str | None) -> str:
    lines = []
    for addr, flags, name, is_entry in entries:
        line = f'{addr} {flags} {name}'
        if is_entry:
            line += f' entry {mod}'
        lines.append(line)
    return '\n'.join(lines) + ('\n' if lines else '')


def flatten_file(path: Path) -> str:
    entries, mod = symbols_from_listing(path.read_text(encoding='utf-8', errors='replace'))
    return format_sym(entries, mod)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('listings', nargs='+', type=Path, help='BEMSH .lst files')
    parser.add_argument('-o', '--output', type=Path,
                        help='output file (single listing) or directory (many)')
    parser.add_argument('--stdout', action='store_true',
                        help='write to stdout (default for a single listing)')
    parser.add_argument('--sort', action='store_true',
                        help='sort by address, then name (default: listing order)')
    args = parser.parse_args(argv)

    paths = []
    for item in args.listings:
        if item.is_dir():
            paths.extend(sorted(item.glob('*.lst')))
        else:
            paths.append(item)
    if not paths:
        print('no listings found', file=sys.stderr)
        return 2

    multi = len(paths) > 1
    if multi and args.stdout:
        print('--stdout is only valid with a single listing', file=sys.stderr)
        return 2

    status = 0
    for path in paths:
        try:
            entries, mod = symbols_from_listing(
                path.read_text(encoding='utf-8', errors='replace'))
        except (OSError, ValueError) as error:
            print(f'{path}: {error}', file=sys.stderr)
            status = 1
            continue
        if args.sort:
            entries.sort(key=lambda e: (int(e[0], 8), e[2]))
        text = format_sym(entries, mod)

        if args.stdout or (not multi and args.output is None):
            sys.stdout.write(text)
            continue

        if args.output is None:
            out = path.with_suffix('.sym')
        elif multi or args.output.is_dir() or str(args.output).endswith('/'):
            out_dir = args.output
            out_dir.mkdir(parents=True, exist_ok=True)
            out = out_dir / f'{path.stem}.sym'
        else:
            out = args.output

        out.write_text(text, encoding='utf-8')
        print(f'{path.name}: {len(entries)} symbols -> {out}', flush=True)

    return status


if __name__ == '__main__':
    sys.exit(main())
