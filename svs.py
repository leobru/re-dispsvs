#!/usr/bin/env python3
"""Assemble DISPAK-SVS with BEMSH, link with RVS, compare disk zones."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
ZONE = 6144


def run(command, build, env, output=None):
    if output:
        with output.open('w') as stream:
            subprocess.run(command, cwd=build, env=env, stdout=stream,
                           stderr=subprocess.STDOUT, check=True, timeout=120)
    else:
        subprocess.run(command, cwd=build, env=env, check=True, timeout=120,
                       stdout=subprocess.DEVNULL)


def load_sources(source, local=None):
    result = scan_sources(source)
    if not result:
        raise ValueError(f'No assembly modules found in {source}')
    if local is not None and local.exists():
        result.update(scan_sources(local))
    return result


def scan_sources(source):
    if not source.is_dir():
        raise ValueError(f'Source directory does not exist: {source}')
    result = {}
    for path in sorted(source.glob('*.bemsh')):
        # СЛИЙКА is an alternative СЛОЙКА, outside the archived load map.
        if path.name in ('слийка.bemsh', 'slijka.bemsh',
                         'э71-samples.bemsh', 'e71-samples.bemsh'):
            continue
        match = re.search(r'^([^*\s]+)\s+(?:СТАРТ|CTAPT|START)\b',
                          path.read_text(), re.M | re.I)
        if match:
            name = match[1].upper()
            if name in result:
                raise ValueError(f'Duplicate module {name}: {result[name]}, {path}')
            result[name] = path
    return result


def load_map(path):
    groups = []
    for line in path.read_text().splitlines():
        if not line.strip() or line.startswith('#'):
            continue
        match = re.fullmatch(r'МДЛ   (.{6})(42[0-7]{4})(.*)', line)
        if not match:
            raise ValueError(f'Invalid load map line: {line}')
        name, location, commands = match.groups()
        target = re.search(r'ЗОНГП 43([0-7]{4})\s+([0-7]+)', commands)
        if target:
            groups.append(dict(zone=int(target[1], 8), length=int(target[2], 8), modules=[]))
        if not groups:
            raise ValueError('Load map starts without ЗОНГП')
        groups[-1]['modules'].append(dict(name=name.strip(), location=location,
                                          commands=commands.replace('/.', '^')))
    if not groups:
        raise ValueError('Empty load map')
    return groups


def source_cards(text):
    """A1 ignores newlines; BEMSH ends a card at 80 characters or ^."""
    cards = []
    for line in text.splitlines():
        line = line or '*'
        if line.startswith('*'):
            # Preserve long comments as multiple comment cards.
            chunks = [line[i:i + 79] for i in range(1, len(line), 79)] or ['']
            lines = ['*' + chunk for chunk in chunks]
        else:
            if len(line) > 80:
                raise ValueError('Non-comment source card exceeds 80 characters')
            lines = [line]
        cards.extend(line + ('^' if len(line) < 80 else '') + '\n' for line in lines)
    return ''.join(cards)


def listing_extent(listing):
    """Return (start, end) object zones from a successful listing, or None."""
    text = listing.read_text()
    errors = re.findall(r'^ЧИСЛО ОШИБОК=(\d+)\.', text, re.M)
    written = re.search(r'ЗАПИСАН\s+В\s+ЗОНЫ\s+С\s+44([0-7]{4})\s+ПО\s+44([0-7]{4})', text)
    if not errors or any(int(n) for n in errors) or not written:
        return None
    return int(written[1], 8), int(written[2], 8)


def assemble(name, path, zone, build, env):
    listing = build / f'{path.stem}.lst'
    cached = listing_extent(listing) if listing.exists() else None
    objects = build / '2221'
    if (cached and cached[0] == zone and listing.stat().st_mtime >= path.stat().st_mtime
            and objects.exists() and objects.stat().st_size > 0):
        print(f'{name}: up to date at 44{cached[0]:04o}-{cached[1]:04o}', flush=True)
        return cached[1] + 1
    deck = f'''шифр 419999^
трак 64^
лент 30(2048-6200)^
лент 42(2113)^
лент 44(2221-ЗП)^
ацп 64^
росп 0^
врем 240^
лист 0-37^
вход 4000^
е
в 4000
к 00 010 4003
к 15 24 04000
к 00 066 0001 00 000 0100
с 3000 67
в 14000
а1
ВВД$$$^
'''
    deck += source_cards(path.read_text())
    # ЧТКОМП loads pre-compiled macros from 2113:1170; ПВВ brings its own.
    compiler = '' if name == 'ПВВ' else 'ЧТКОМП421170^\n'
    deck += f'''КВЧ$$$^
ТРН$$$^
{compiler}0-0^
ЗОНМОД44{zone:04o} 0010^
КНЦ$$$^
_$ЕКОНЕЦ
'''
    job = listing.with_suffix('.b6')
    job.write_text(deck)
    run(['dispak', job.name], build, env, listing)
    written = listing_extent(listing)
    if written is None:
        raise ValueError(f'{name}: assembly failed; see {listing}')
    if written[0] != zone:
        raise ValueError(f'{name}: unexpected object placement')
    print(f'{name}: assembled at 44{zone:04o}-{written[1]:04o}', flush=True)
    return written[1] + 1


def scratch_disk(build, name, wipe):
    path = build / name
    if path.is_symlink():
        raise ValueError(f'Refusing scratch disk symlink: {path}')
    if wipe or not path.exists():
        path.write_bytes(b'')


def build_image(args, build, env):
    sources = load_sources(args.source, ROOT / 'src')
    groups = load_map(args.loadmap)
    (build / 'manifest.json').unlink(missing_ok=True)
    (build / 'verify.txt').unlink(missing_ok=True)
    # Keep object disk 2221 for incremental assembly; always rebuild linked 2222.
    scratch_disk(build, '2221', wipe=False)
    scratch_disk(build, '2222', wipe=True)
    locations = {}
    failures = {}
    zone = 0
    for group in groups:
        for module in group['modules']:
            name = module['name']
            if name in sources and name not in locations:
                start = zone
                try:
                    zone = assemble(name, sources[name], zone, build, env)
                except ValueError as error:
                    failures[name] = str(error)
                    print(error, flush=True)
                    continue
                locations[name] = f'44{start:04o}'
    lines = []
    for group in groups:
        for module in group['modules']:
            name = module['name']
            module['source'] = str(sources[name]) if name in locations else None
            module['assembly_error'] = failures.get(name)
            module['location'] = locations.get(name, module['location'])
            lines.append(f"МДЛ   {name:6}{module['location']}{module['commands']}^")
    (build / 'rvs.src').write_text('\n'.join(lines) + '\n')
    job = build / 'rvs.b6'
    job.write_text('''шифр 419999^
лен 67(2248)42(2113)^
лен 44(2221)43(2222-зп)^
росп 0^
вход 1000^
е
в 1000
к 00 070 1003
к 00 070 1004
к 00 30 70000
с 0010 3400 0067 0105
с 0010 3500 0067 0106
в 2000
а1
''' + '\n'.join(lines) + '\nконец ^\n_$\nеконец\n')
    run(['dispak', job.name], build, env, build / 'rvs.log')
    log = (build / 'rvs.log').read_text(encoding='utf-8-sig')
    validate_rvs(log, groups)
    (build / 'manifest.json').write_text(json.dumps(groups, ensure_ascii=False, indent=2) + '\n')
    used = set(locations)
    unused = sorted(set(sources) - used - set(failures))
    print(f'Assembled {len(used)} modules; {len(failures)} failed. Sources outside load map: {", ".join(unused)}')
    (build / 'assembly-errors.txt').write_text('\n'.join(failures.values()) + '\n')
    return bool(failures)


def validate_rvs(log, groups):
    # RVS can print diagnostics and still return a successful emulator status.
    allowed = (r'Р В С :.*', r'ЗОНГП=43[0-7]{4} .*', r'КОНЕЦ ЗАДАЧИ',
               r'[0-7]{5} +00 074 0000\s+\*74')
    unexpected = [line for line in log.splitlines()
                  if line.strip() and not any(re.fullmatch(pattern, line.strip())
                                             for pattern in allowed)]
    if unexpected or 'КОНЕЦ ЗАДАЧИ' not in log:
        raise ValueError('RVS diagnostics or incomplete run; see build/rvs.log: '
                         + '; '.join(unexpected[:3]))
    for group in groups:
        if not re.search(rf"ЗОНГП=43{group['zone']:04o}\b", log):
            raise ValueError(f"RVS did not write zone {group['zone']:04o}; see build/rvs.log")


def word_octal(data):
    return f'{int.from_bytes(data, "big"):016o}'


def differing_words(gold, silver, start, limit=10):
    """Return lines describing the first `limit` differing 6-byte words."""
    word_offsets = sorted({i // 6 for i, (a, b) in enumerate(zip(gold, silver)) if a != b})
    lines = []
    for index in word_offsets[:limit]:
        offset = index * 6
        zone = start + offset // ZONE
        word = offset % ZONE // 6
        lines.append(f'{zone:04o} word {word:04o}: {word_octal(gold[offset:offset + 6])} '
                     f'{word_octal(silver[offset:offset + 6])}')
    return lines


def verify(args, build, env):
    groups = json.loads((build / 'manifest.json').read_text())
    mismatches = 0
    failed = [m['name'] for g in groups for m in g['modules'] if m.get('assembly_error')]
    report = []
    for path in build.glob('*.diff'):
        path.unlink()
    for group in groups:
        start, length = group['zone'], group['length']
        blobs = []
        for volume, prefix in ((args.gold, 'G'), ('2222', 'S')):
            path = build / f'{prefix}{start:04o}-L{length}'
            path.unlink(missing_ok=True)
            run(['besmtool', 'dump', volume, f'--start=0{start:o}',
                 f'--length={length}', f'--to-file={path}'], build, env)
            data = path.read_bytes()
            if len(data) != ZONE * length:
                raise ValueError(f'Incomplete disk dump: {path}')
            blobs.append(data)
        diffs = [i for i, (a, b) in enumerate(zip(*blobs)) if a != b]
        names = ','.join(m['name'] for m in group['modules'])
        fallback = [m['name'] for m in group['modules'] if m['source'] is None]
        message = f'{start:04o}+{length}: {names}: '
        if diffs:
            mismatches += 1
            first = diffs[0]
            words = len({offset // 6 for offset in diffs})
            message += (f'DIFF {len(diffs)} bytes, {words} words; first zone {start + first // ZONE:04o}, '
                        f'word {first % ZONE // 6:04o}, byte {first % 6}')
            detail = '# zone word:   G (golden)       S (built)\n'
            detail += '\n'.join(differing_words(*blobs, start)) + '\n'
            for module in group['modules']:
                (build / f"{module['name']}.diff").write_text(detail)
        else:
            message += 'MATCH'
        if fallback:
            message += f" [2113 objects: {','.join(fallback)}]"
        errors = [m['name'] for m in group['modules'] if m.get('assembly_error')]
        if errors:
            message += f" [ASSEMBLY FAILED: {','.join(errors)}]"
        print(message, flush=True)
        report.append(message)
    summary = f'{len(groups) - mismatches}/{len(groups)} groups match volume {args.gold}; {mismatches} differ.'
    summary += f' {len(failed)} assembly failures.'
    print(summary)
    (build / 'verify.txt').write_text('\n'.join(report + [summary]) + '\n')
    return 2 if failed else int(bool(mismatches))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['build', 'verify', 'all'])
    parser.add_argument('--source', type=Path, default=ROOT / '../besm6.github.io/sources/dispak-svs')
    parser.add_argument('--loadmap', type=Path, default=ROOT / 'loadmap.txt')
    parser.add_argument('--gold', default='2153')
    args = parser.parse_args()
    args.source = args.source.resolve()
    build = ROOT / 'build'
    build.mkdir(exist_ok=True)
    env = dict(os.environ, BESM6_PATH=str(build) + ':' + os.environ.get('BESM6_PATH', '/usr/local/share/besm6'))
    try:
        with (build / '.lock').open('w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return execute(args, build, env)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(error, file=sys.stderr)
        return 2


def execute(args, build, env):
    try:
        if args.command in ('build', 'all'):
            failed = build_image(args, build, env)
            if args.command == 'build':
                return 2 if failed else 0
        if args.command in ('verify', 'all'):
            return verify(args, build, env)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(error, file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
