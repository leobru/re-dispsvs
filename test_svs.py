"""Regression checks for card framing and successful-looking linker failures."""
import contextlib
import io
import json
import os
import tempfile
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path
import unittest

import svs


class InfrastructureTests(unittest.TestCase):
    def test_local_module_overrides_upstream_by_start_name(self):
        with tempfile.TemporaryDirectory() as directory:
            upstream = Path(directory) / 'upstream'
            local = Path(directory) / 'src'
            upstream.mkdir()
            local.mkdir()
            (upstream / 'адап.bemsh').write_text('ПВВ СТАРТ ’30000’\n')
            other = upstream / 'качка.bemsh'
            other.write_text('КАЧКА СТАРТ ’72000’\n')
            self.assertEqual(svs.load_sources(upstream, local)['ПВВ'], upstream / 'адап.bemsh')
            override = local / 'adapter.bemsh'
            override.write_text('ПВВ СТАРТ ’30000’\n* local changes\n')
            sources = svs.load_sources(upstream, local)
            self.assertEqual(sources['ПВВ'], override)
            self.assertEqual(sources['КАЧКА'], other)

    def test_full_card_does_not_create_extra_record(self):
        line = ' ПО АНВЫП' + ' ' * 70 + '4'
        self.assertEqual(len(line), 80)
        self.assertEqual(svs.source_cards(line + '\n СЧ 0\n'), line + '\n СЧ 0^\n')

    def test_long_comments_stay_comments(self):
        comment = '*' + 'Я' * 175
        cards = svs.source_cards(comment).splitlines()
        self.assertTrue(all(line.startswith('*') and len(line) <= 80 for line in cards))
        self.assertEqual(''.join(line[1:].removesuffix('^') for line in cards), comment[1:])
        with self.assertRaises(ValueError):
            svs.source_cards(' СЧ ' + 'Я' * 80)

    def test_loadmap_keeps_following_sector_modules_in_group(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'map'
            path.write_text('МДЛ   ТУПР  421411/.НАЗ   024000/.ЗОНГП 430520    01\n'
                            'МДЛ   БОП2  421412/.НАЗ НСХ24400\n')
            group, = svs.load_map(path)
            self.assertEqual((group['zone'], group['length']), (0o520, 1))
            self.assertEqual([m['name'] for m in group['modules']], ['ТУПР', 'БОП2'])

    def test_linker_diagnostics_are_failures_even_with_zone_report(self):
        log = ('Р В С :01/78\n'
               'ЗОНГП=430520  СВ.ЗОНА=  430521  АМИН=24000 АМАКС=25324 АСВОБ=00000\n'
               ' КОНЕЦ ЗАДАЧИ\n 71406  00 074 0000\t*74\n')
        svs.validate_rvs(log, [{'zone': 0o520}])
        with self.assertRaises(ValueError):
            svs.validate_rvs(log + 'НЕОПРЕДЕЛЕННОЕ ИМЯ\n', [{'zone': 0o520}])
        with self.assertRaises(ValueError):
            svs.validate_rvs(log, [{'zone': 0o521}])

    def compare_fixture(self, gold, silver, modules=None):
        with tempfile.TemporaryDirectory() as directory:
            build = Path(directory)
            modules = modules or [dict(name='ТЕСТ', source='test.bemsh', assembly_error=None)]
            manifest = [dict(zone=0o520, length=2, modules=modules)]
            (build / 'manifest.json').write_text(json.dumps(manifest))
            (build / 'STALE.diff').write_text('old\n')

            def dump(command, *_):
                dest = Path(next(arg.split('=', 1)[1] for arg in command
                                 if arg.startswith('--to-file=')))
                dest.write_bytes(gold if command[2] == '2153' else silver)

            with patch('svs.run', side_effect=dump), contextlib.redirect_stdout(io.StringIO()):
                status = svs.verify(SimpleNamespace(gold='2153'), build, {})
            diffs = {path.name: path.read_text() for path in build.glob('*.diff')}
            return status, (build / 'verify.txt').read_text(), diffs

    def test_comparison_reports_first_word_of_next_zone(self):
        gold = bytes(svs.ZONE * 2)
        silver = bytearray(gold)
        silver[svs.ZONE] = 1
        status, report, diffs = self.compare_fixture(gold, silver)
        self.assertEqual(status, 1)
        self.assertIn('DIFF 1 bytes, 1 words; first zone 0521, word 0000, byte 0', report)
        self.assertEqual(diffs, {
            'ТЕСТ.diff': '# zone word:   G (golden)       S (built)\n'
                         '0521 word 0000: 0000000000000000 0020000000000000\n'})
        status, report, diffs = self.compare_fixture(gold, gold)
        self.assertEqual(status, 0)
        self.assertIn(': MATCH', report)
        self.assertEqual(diffs, {})

    def test_multiple_differing_bytes_in_one_word_count_once(self):
        gold = bytes(svs.ZONE * 2)
        silver = bytearray(gold)
        for offset in (0, 1, 5, 6, svs.ZONE + 5):
            silver[offset] = 1
        status, report, diffs = self.compare_fixture(gold, silver)
        self.assertEqual(status, 1)
        self.assertIn('DIFF 5 bytes, 3 words;', report)
        self.assertEqual(diffs['ТЕСТ.diff'].splitlines(), [
            '# zone word:   G (golden)       S (built)',
            '0520 word 0000: 0000000000000000 0020040000000001',
            '0520 word 0001: 0000000000000000 0020000000000000',
            '0521 word 0000: 0000000000000000 0000000000000001',
        ])

    def test_diff_file_lists_first_ten_words_for_each_module(self):
        gold = bytes(svs.ZONE * 2)
        silver = bytearray(gold)
        for word in range(12):
            silver[word * 6 + 5] = word + 1
        modules = [dict(name='АВОСТ', source='a.bemsh', assembly_error=None),
                   dict(name='ВИСП', source='b.bemsh', assembly_error=None)]
        status, _, diffs = self.compare_fixture(gold, silver, modules)
        self.assertEqual(status, 1)
        expected = '# zone word:   G (golden)       S (built)\n' + '\n'.join(
            f'0520 word {word:04o}: 0000000000000000 {word + 1:016o}'
            for word in range(10)) + '\n'
        self.assertEqual(diffs, {'АВОСТ.diff': expected, 'ВИСП.diff': expected})

    def test_short_dump_is_not_a_match(self):
        with self.assertRaisesRegex(ValueError, 'Incomplete disk dump'):
            self.compare_fixture(bytes(svs.ZONE), bytes(svs.ZONE))

    def write_listing(self, path, start, end, errors=0):
        path.write_text(f'ЧИСЛО ОШИБОК={errors}.\n'
                        f'МОДУЛЬ  ТЕСТ    ЗАПИСАН  В  ЗОНЫ  С 44{start:04o}  ПО  44{end:04o}\n')

    def test_listing_extent_requires_zero_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'mod.lst'
            self.write_listing(path, 0, 1)
            self.assertEqual(svs.listing_extent(path), (0, 1))
            self.write_listing(path, 0, 1, errors=2)
            self.assertIsNone(svs.listing_extent(path))

    def test_assemble_skips_when_listing_is_newer_than_source(self):
        with tempfile.TemporaryDirectory() as directory:
            build = Path(directory)
            source = build / 'mod.bemsh'
            source.write_text('ТЕСТ СТАРТ ’1’\n')
            listing = build / 'mod.lst'
            self.write_listing(listing, 5, 6)
            (build / '2221').write_bytes(b'x')
            # Listing must be newer than the source.
            older = source.stat().st_mtime - 10
            os.utime(source, (older, older))
            with patch('svs.run') as run, contextlib.redirect_stdout(io.StringIO()) as out:
                nxt = svs.assemble('ТЕСТ', source, 5, build, {})
            self.assertEqual(nxt, 7)
            run.assert_not_called()
            self.assertIn('up to date at 440005-0006', out.getvalue())

    def test_assemble_rebuilds_when_source_is_newer(self):
        with tempfile.TemporaryDirectory() as directory:
            build = Path(directory)
            source = build / 'mod.bemsh'
            source.write_text('ТЕСТ СТАРТ ’1’\n')
            listing = build / 'mod.lst'
            self.write_listing(listing, 0, 0)
            older = listing.stat().st_mtime - 10
            os.utime(listing, (older, older))
            (build / '2221').write_bytes(b'x')

            def fake_run(command, cwd, env, output=None):
                self.write_listing(output, 0, 0)

            with patch('svs.run', side_effect=fake_run), contextlib.redirect_stdout(io.StringIO()) as out:
                nxt = svs.assemble('ТЕСТ', source, 0, build, {})
            self.assertEqual(nxt, 1)
            self.assertIn('assembled at 440000-0000', out.getvalue())
            self.assertTrue((build / 'mod.b6').exists())

    def test_assemble_rebuilds_when_cached_zone_does_not_match(self):
        with tempfile.TemporaryDirectory() as directory:
            build = Path(directory)
            source = build / 'mod.bemsh'
            source.write_text('ТЕСТ СТАРТ ’1’\n')
            listing = build / 'mod.lst'
            self.write_listing(listing, 0, 0)
            older = source.stat().st_mtime - 10
            os.utime(source, (older, older))
            (build / '2221').write_bytes(b'x')

            def fake_run(command, cwd, env, output=None):
                self.write_listing(output, 3, 4)

            with patch('svs.run', side_effect=fake_run), contextlib.redirect_stdout(io.StringIO()):
                nxt = svs.assemble('ТЕСТ', source, 3, build, {})
            self.assertEqual(nxt, 5)

    def test_build_preserves_object_disk_and_wipes_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build = root / 'build'
            src = root / 'src'
            upstream = root / 'upstream'
            build.mkdir()
            src.mkdir()
            upstream.mkdir()
            (build / '2221').write_bytes(b'objects')
            (build / '2222').write_bytes(b'stale')
            (upstream / 'empty.bemsh').write_text('UNUSED СТАРТ ’1’\n')
            loadmap = root / 'loadmap.txt'
            loadmap.write_text('МДЛ   ТУПР  421411/.НАЗ   024000/.ЗОНГП 430520    01\n')
            def fake_run(command, cwd, env, output=None):
                if output is not None:
                    output.write_text('')

            args = SimpleNamespace(source=upstream, loadmap=loadmap)
            with patch('svs.ROOT', root), patch('svs.run', side_effect=fake_run), \
                    patch('svs.validate_rvs'), contextlib.redirect_stdout(io.StringIO()):
                status = svs.build_image(args, build, {})
            self.assertFalse(status)
            self.assertEqual((build / '2221').read_bytes(), b'objects')
            self.assertEqual((build / '2222').read_bytes(), b'')

    def test_full_diff_marks_zero_holes_and_lists_every_word(self):
        gold = bytearray(svs.ZONE)
        silver = bytearray(svs.ZONE)
        gold[0:6] = b'\x00\x00\x00\x00\x00\x01'
        gold[6:12] = b'\x00\x00\x00\x00\x00\x02'
        silver[6:12] = b'\x00\x00\x00\x00\x00\x03'
        text, n_diffs, n_holes = svs.full_diff_report(bytes(gold), bytes(silver), 0o475)
        self.assertEqual((n_diffs, n_holes), (2, 1))
        self.assertEqual(text.splitlines()[:5], [
            '# word-index  zone:word   G (golden)       S (built)',
            '# total differing words: 2',
            '# zero-holes (S=0, G≠0): 1',
            '    0  0475:0000  0000000000000001  0000000000000000  ZERO-HOLE',
            '    1  0475:0001  0000000000000002  0000000000000003',
        ])

    def test_diff_command_resolves_stem_and_writes_build_file(self):
        with tempfile.TemporaryDirectory() as directory:
            build = Path(directory)
            gold = build / 'G0475-L2'
            silver = build / 'S0475-L2'
            gdata = bytearray(svs.ZONE * 2)
            sdata = bytearray(svs.ZONE * 2)
            gdata[0o70 * 6 + 5] = 1
            gold.write_bytes(gdata)
            silver.write_bytes(sdata)
            args = SimpleNamespace(operands=['0475-L2'], output=None, start=None)
            with contextlib.redirect_stdout(io.StringIO()) as out:
                status = svs.diff_dumps(args, build)
            self.assertEqual(status, 1)
            report = (build / 'G0475-L2.diff').read_text()
            self.assertIn('ZERO-HOLE', report)
            self.assertIn('0475:0070', report)
            self.assertIn('1 differing words, 1 zero-holes', out.getvalue())


class FlattenTests(unittest.TestCase):
    SAMPLE = (
        "ИПМ МАКРО-БЕМШ ВЕР.06/78      ВИСП     СТР 0001\n"
        "*МЕТ*\n"
        "ВИСП1  12066В   Е1     00162 А  ШГ     00026СА  000000 00000\n"
        "0ЛИТ   12335    ВШГ    12244В   М4     00004 А  D05723 05723\n"
        "***********\n"
    )

    def test_extracts_entries_and_skips_externals(self):
        import flatten
        entries, mod = flatten.symbols_from_listing(self.SAMPLE)
        self.assertEqual(mod, 'ВИСП')
        text = flatten.format_sym(entries, mod)
        self.assertEqual(text.splitlines(), [
            '12066 0 ВИСП1 entry ВИСП',
            '00162 0 Е1',
            '12244 0 ВШГ entry ВИСП',
            '00004 0 М4',
            '05723 1 D05723',
        ])
        # СА external ШГ skipped; 0ЛИТ skipped
        self.assertNotIn(' 0 ШГ', text)
        self.assertNotIn('0ЛИТ', text)


if __name__ == '__main__':
    unittest.main()
