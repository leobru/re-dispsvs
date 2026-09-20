"""Regression checks for card framing and successful-looking linker failures."""
import contextlib
import io
import json
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

    def compare_fixture(self, gold, silver):
        with tempfile.TemporaryDirectory() as directory:
            build = Path(directory)
            manifest = [dict(zone=0o520, length=2, modules=[dict(
                name='ТЕСТ', source='test.bemsh', assembly_error=None)])]
            (build / 'manifest.json').write_text(json.dumps(manifest))

            def dump(command, *_):
                dest = Path(next(arg.split('=', 1)[1] for arg in command
                                 if arg.startswith('--to-file=')))
                dest.write_bytes(gold if command[2] == '2153' else silver)

            with patch('svs.run', side_effect=dump), contextlib.redirect_stdout(io.StringIO()):
                status = svs.verify(SimpleNamespace(gold='2153'), build, {})
            return status, (build / 'verify.txt').read_text()

    def test_comparison_reports_first_word_of_next_zone(self):
        gold = bytes(svs.ZONE * 2)
        silver = bytearray(gold)
        silver[svs.ZONE] = 1
        status, report = self.compare_fixture(gold, silver)
        self.assertEqual(status, 1)
        self.assertIn('DIFF 1 bytes, 1 words; first zone 0521, word 0000, byte 0', report)
        status, report = self.compare_fixture(gold, gold)
        self.assertEqual(status, 0)
        self.assertIn(': MATCH', report)

    def test_multiple_differing_bytes_in_one_word_count_once(self):
        gold = bytes(svs.ZONE * 2)
        silver = bytearray(gold)
        for offset in (0, 1, 5, 6, svs.ZONE + 5):
            silver[offset] = 1
        status, report = self.compare_fixture(gold, silver)
        self.assertEqual(status, 1)
        self.assertIn('DIFF 5 bytes, 3 words;', report)

    def test_short_dump_is_not_a_match(self):
        with self.assertRaisesRegex(ValueError, 'Incomplete disk dump'):
            self.compare_fixture(bytes(svs.ZONE), bytes(svs.ZONE))


if __name__ == '__main__':
    unittest.main()
