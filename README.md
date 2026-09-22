# re-dispsvs

Rebuild the DISPAK-SVS load map and compare its contents with volume **2153**.
Sources in `src/` take precedence over `../besm6.github.io/sources/dispak-svs`;
generated files stay in `build/`. Overrides are matched by the module's `СТАРТ`
name, so a local file may have a different filename. `--source` changes the
upstream fallback directory; local overrides still take precedence.

```sh
make                 # assemble, link, compare; also: ./compile.sh
make build           # assemble and link only
make verify          # compare the existing build again
make test            # infrastructure regression tests, no emulator needed
make clean           # remove generated files
```

Requires Python 3.9+, `dispak`, `besmtool`, and volumes 2048, 2113, 2248, 2153.
Images are found through `BESM6_PATH` (default `/usr/local/share/besm6`). The
emulator also needs write access to its normal `~/.besm6` input queue.

```sh
BESM6_PATH=/path/to/images make
./compile.sh --source /path/to/dispak-svs
python3 svs.py verify --gold 2153
```

The scripts return 0 on success, 1 for comparison differences, and 2 for
assembly/link/tool errors. `make` itself returns 2 when a recipe fails.
Assembly errors are collected: the diagnostic build still links and compares,
but it never reports overall success with a failed compilation.

## Build and disk layout

This follows `../re-dispak/asm.pl`, `mkloc.pl`, `rvs.pl`, and `verify.pl`:

1. Discover module names from `СТАРТ`, rather than filenames (for example,
   `disp70.bemsh` defines `ДИСП70`, and `pvv.bemsh` defines `ПВВ`). Local
   `src/` files use Latin filenames in the same style as `../re-dispak`.
2. Compile mapped sources with BEMSH from **2113:1170**, writing `ЗОНМОД`
   objects to scratch volume **build/2221**, logical unit 44. Allocate successive
   zones using the assembler's actual written extent. Skip reassembly when
   `build/<stem>.lst` exists, is newer than the source, reports a successful
   write at the expected zone, and object disk **2221** is non-empty. For `ПВВ`
   (`pvv.bemsh`), omit `ЧТКОМП`: that card loads pre-compiled macros from
   2113:1170, and `ПВВ` defines its own.
3. Run RVS from **2248:0105–0106**, using the local `loadmap.txt` copied from
   the source directory. Replace archived object locations with new locations
   for successfully compiled modules. Preserve `НАЗ`, `НС`, module ordering,
   and groups sharing output zones. RVS writes **build/2222**, logical unit 43.
4. Use `besmtool dump` to compare each mapped range against **2153**. Compare
   complete 6144-byte zones, including padding. Report differing byte and
   6-byte word counts (each word with any differing byte is counted once)
   and the first zone/word/byte offset. Zone and word addresses are octal;
   lengths/counts and the zero-based byte offset within a word are decimal.

Both scratch disks start empty on a clean tree. Object disk **2221** is kept
across builds so up-to-date modules can skip reassembly; linked output **2222**
is cleared every build. Volume 2153 is only read during comparison. Golden dumps
are refreshed on each verification. The scripts check assembler summaries and
RVS output as well as subprocess status; a successful emulator exit alone is
insufficient. A lock prevents simultaneous script runs from sharing the scratch
disks.

The load map contains modules without corresponding source files. These are
read from their original locations on **2113**, and explicitly labelled
`[2113 objects: ...]` in the comparison. Failed compilations also use their
archived objects to allow the remaining diagnostic comparison to finish, with
an additional `ASSEMBLY FAILED` marker and exit status 2. Consequently, a match
for an archived-object group is not evidence of a successful source rebuild.

## Source handling and scope

Edit local copies in `src/`; upstream sources remain untouched. The initial
109 copies cover all source modules in differing load-map groups. When multiple
modules share a group, all their sources are included because the comparison
identifies a linked group rather than assigning each difference to a module.
The 15 differing archived modules without available sources cannot be copied.
Builds never refresh or overwrite local copies. Modules in matching groups
continue to use upstream sources unless a local override is added.

BEMSH's A1 input ends a card after 80 characters or a
`^` terminator. The generator avoids an extra terminator after full cards and
splits long comments into comment cards. Overlong non-comment cards are rejected.

`slojka.bemsh` is selected for `СЛОЙКА`; the alternate `слийка.bemsh` /
`slijka.bemsh` and the standalone `э71-samples.bemsh` / `e71-samples.bemsh`
are excluded. Files without a `СТАРТ` module (including macro libraries and
job wrappers) are not compiled independently.
Discovered modules absent from the load map are reported and left out; this
currently includes `АС`, `ЗН1167`, and `ТРУБКА`. The result covers the mapped
ranges, not a complete bootable system disk.

## Artifacts and initial baseline

- `build/*.b6`, `build/*.lst`: generated jobs and assembly listings.
- `build/rvs.src`, `build/rvs.b6`, `build/rvs.log`: resolved link map, job, log.
- `build/manifest.json`: module locations, source provenance, assembly failures.
- `build/assembly-errors.txt`: failed module list.
- `build/G*-L*`, `build/S*-L*`: golden and reconstructed binary ranges.
- `build/verify.txt`: complete comparison report.

The 2026-09-20 run assembled **all 115** mapped source modules with zero errors,
including `ПВВ` without `ЧТКОМП`. Another 31 modules lack sources.
All **87** groups were linked: **19 match, 68 differ**. Among the 59 groups
built entirely from source, three match: `0441` (`АУМОД1,АУМОД2`), `0520`
(`ТУПР,БОП2,ТАБКОД`), and `0577` (`НРКОД`). The other 16 matches use archived
objects. The full initial report is saved in `baseline-2153.txt`.

The remaining binary differences require investigation. `python3 svs.py build`
returned 0; `python3 svs.py verify` returned 1 for differences. Eight card/map/linker,
source-override, and comparison regressions are available through `make test`.
