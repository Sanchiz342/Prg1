# gitsight

**Hotspots, code owners and the bus factor of any git repository**, from one fast Rust binary.

```text
$ gitsight            # run inside a clone of ripgrep (path shortened, output trimmed)
gitsight ~/src/ripgrep @ 91f5d22ef48b

History         2,086 commits by 498 authors over 10 years (2016-02-27 → 2026-10-27)
Active          6 authors committed in the last 90 days of the range
Bots            3 bot commits skipped (--include-bots to count them)
Code            223 files, 75,500 lines · rs 73% · csv 7% · md 7% · (none) 5% · txt 3% · yml 1%
Truck factor    1 (Andrew Gallant)

Top hotspots
score  file                       commits  lines
────────────────────────────────────────────────
 84.1  CHANGELOG.md                   294  1,870
 65.0  crates/ignore/src/dir.rs        50  1,700
 51.3  Cargo.toml                     244    120
 50.0  crates/core/flags/defs.rs       32  7,779
 45.4  crates/ignore/src/walk.rs       84  2,775

Riskiest directories
directory   files  truck factor  key people
───────────────────────────────────────────────
crates        139             1  Andrew Gallant
benchsuite     30             1  Andrew Gallant
...
```

Self-contained mini-project: everything lives in this folder.

## What it answers

| command | question |
|---|---|
| `gitsight` / `summary` | What does this repository look like: history, size, languages, truck factor, top risks? |
| `hotspots` | Where does the code change most? These are the best places to refactor, review harder or add tests. |
| `authors` | Who contributed what, and which files does each person actually know? |
| `bus-factor` | How many people leaving would orphan most of the code? Which directories and files depend on one person? What if Alice and Bob leave? |
| `coupling` | Which files keep changing together, even though nothing in the code links them? |

Shared options:
- `--since` / `--until`;
- `--path DIR` (applied after following renames);
- `--exclude GLOB`;
- `--rev`;
- `-C PATH`;
- `--format table|json|csv`;
- `-n` (row limit).

Lock files, minified assets, `vendor/`, `node_modules/` and `dist/` are skipped by default, and so are bot commits
(`dependabot[bot]` and the like). `--no-default-excludes` and `--include-bots` turn this off.

```bash
gitsight hotspots --since "1 year ago" -n 30
gitsight bus-factor --depth 2 --without alice@example.com --without bob@example.com
gitsight bus-factor --fail-below 2          # CI gate: exit code 3 if the truck factor is below 2
gitsight coupling --path src/ --format csv > coupling.csv
gitsight authors -C ../other-repo --format json | jq '.authors[0]'
```

## Install
```bash
cargo install --path .        # from this folder; needs Rust 1.88+
pip install .                 # builds a wheel containing only the binary (via maturin), puts `gitsight` on PATH
```
The package is set up for both registries: `cargo publish` for crates.io and `maturin publish` for PyPI. It has
not been published yet. See [Releasing](#releasing). The tool needs the `git` executable at runtime.

## How the numbers are computed

### Hotspots
This follows Adam Tornhill's *Your Code as a Crime Scene*: code that is both **large** and **changed often** is
where defects and delivery slow-downs concentrate.

`score = recency-weighted commits × ln(1 + lines)`. Both factors are scaled to the highest file in the
repository, so the score runs from 0 to 100.
- Each commit is weighted `0.5^(age / half-life)` (half-life 180 days by default, `--half-life`).
- Age is measured from the newest analysed commit, not from today, so an archived project still gets a meaningful
  ranking.
- Size is logarithmic, so a single 20 000-line file does not flatten everything else.
- Binary files and files deleted since are left out (`--include-deleted` lists the deleted ones).

### Authorship and truck factor
These use the *degree-of-authorship* model (Fritz et al.) and the greedy truck-factor algorithm from Avelino et al.,
**"A novel approach for estimating truck factors"** (ICPC 2016). The code implements exactly this:

```
DOA = 3.293 + 1.098·FA + 0.164·DL − 0.321·ln(1 + AC)
```
- **FA** = 1 if the person made the oldest commit to the file in the analysed range;
- **DL** = their commits to the file;
- **AC** = everybody else's commits to the file.

A person is an **author** of a file when their DOA is at least 3.293 and at least 75 % of the file's highest DOA.

The **truck factor** is computed like this:
1. Remove the author of the most files.
2. Repeat until fewer than half of the files still have an author.
3. The number of people removed is the truck factor.

The same calculation runs per directory (`--depth`). `--without` runs the "what if these people leave" scenario
directly.

### Temporal coupling
For every pair of files changed in the same commit, `degree = shared commits / average commits of the two files`.
Commits touching more than 30 files (`--max-files`) are ignored, because mass reformatting couples everything.
Pairs need at least 5 shared commits by default: fewer than that is mostly noise from files added together once.

### Data collection
- History comes from one streamed `git log -z --numstat -M --no-merges`. Every record is NUL-separated, so file
  names with spaces, tabs, quotes, newlines or non-Latin characters parse correctly.
- Renames are followed from newest to oldest, so statistics land on each file's **current** name. A path reused
  later by a new file stays a separate file.
- Current line counts come from a single `git cat-file --batch` process reading the blobs at `--rev`. This works
  in bare repositories too.
- Authors are identified by email (case-insensitive), after `.mailmap`, so people with several addresses can be
  merged by the project itself.

## Performance
I measured this on a 4-vCPU cloud sandbox with release builds and the repository already cached by the OS.

| repository | commits analysed | files | `gitsight summary` | raw `git log --numstat -M` alone | peak memory |
|---|---|---|---|---|---|
| [ripgrep](https://github.com/BurntSushi/ripgrep) | 2,086 | 223 | 0.7–0.8 s | 0.7 s | 75 MB |
| [cargo](https://github.com/rust-lang/cargo) | 15,687 | 2,926 | 9.1 s | 8.3 s | 213 MB |

Almost all the time goes to git computing diffs with rename detection. gitsight's own parsing and analysis add
about 0.8 s on cargo; on ripgrep the difference is within measurement noise. The memory figure is the peak of the largest process (gitsight or git), measured with
`getrusage`. The first run on a cold disk cache is slower.

## Tests
```bash
cargo test           # 22 unit + 9 end-to-end tests
cargo clippy --all-targets
```
- **Unit tests** cover:
  - the parser, using real `git log` output that includes renames, binary files, empty commits and file names with
    newlines, plus malformed input;
  - rename chains and path reuse;
  - filters;
  - the DOA formula and truck-factor cases;
  - recency decay;
  - coupling;
  - date conversion;
  - table and CSV rendering.
- **End-to-end tests** build throw-away repositories with scripted authors and dates and run the real binary. They
  cover:
  - following renames;
  - default excludes and `--path`;
  - bot handling;
  - `--since`;
  - `.mailmap` merging;
  - the bus-factor what-if and the CI exit code;
  - coupling and CSV output;
  - binary and Unicode file names;
  - error exit codes.

The test suite also passes on Rust 1.88, the declared `rust-version`.

## Releasing
```bash
cargo publish --dry-run                       # verifies the crate builds from the packaged sources
cargo publish                                 # crates.io (needs `cargo login`)
pipx run maturin build --release              # wheel for the current platform -> target/wheels/
pipx run maturin publish                      # PyPI (needs a token); build per platform in CI for portable wheels
```
A wheel built locally is tagged for the build machine's glibc (here `manylinux_2_39`), so it does not run on
older distributions. For portable PyPI wheels, build manylinux, macOS and Windows wheels in CI (for example with `maturin-action`).
There is no CI workflow in this repository yet: workflows live in the repository-wide `.github/` directory, which
these isolated mini-projects do not touch.

## Limitations
- **Bus factor is an estimate.** It sees commits, not knowledge from reviews, pairing or documentation, and it
  assumes each commit's author wrote it (squash merges credit one person).
- **`--since` / `--until` use committer dates.** Displayed dates are author dates, so a rebased or cherry-picked
  commit can show an older date than the `--since` boundary.
- **Authorship covers only the analysed range.** With `--since`, "created the file" means "made the oldest commit
  in the range".
- **Renames are followed along the order `git log` emits commits.** On heavily branched histories where a file was
  renamed differently on two branches, attribution is approximate.
- **Merge commits are skipped,** so changes made only while resolving merge conflicts are not counted.
