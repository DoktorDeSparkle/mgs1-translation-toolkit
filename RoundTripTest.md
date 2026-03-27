# Round-Trip Integration Tests

Automated tests that verify game data can be loaded, saved to a project file, reloaded, and recompiled without any data loss or corruption.

## Prerequisites

- Python 3.8+ with PySide6 installed (the project venv)
- `pytest` and `pytest-qt` installed in the venv
- Game data files placed under `build-src/`

### Install test dependencies

```bash
.venv/bin/pip install pytest pytest-qt
```

### Game data setup

Create the following directory structure and copy the original game files into each:

```
build-src/
  usa-d1/MGS/
    RADIO.DAT
    STAGE.DIR
    DEMO.DAT      (optional)
    VOX.DAT       (optional)
    ZMOVIE.STR    (optional)
    BRF.DAT       (optional)
    FACE.DAT      (optional)
  jpn-d1/MGS/
    (same files)
  integral-d1/MGS/
    (same files)
```

Tests skip automatically for any version folder that is missing, so you can start with just one.

At minimum, each version needs `RADIO.DAT` and `STAGE.DIR` for the core round-trip test.

## Running

```bash
cd mgs-undubbed-gui
.venv/bin/python -m pytest tests/ -v
```

Stop on first failure:

```bash
.venv/bin/python -m pytest tests/ -v -x
```

Run a single version:

```bash
.venv/bin/python -m pytest tests/ -v -k "usa-d1"
```

## Test Matrix

9 tests total: 3 versions x 3 test cases.

| Test Class | Test | What it verifies |
|------------|------|-----------------|
| `TestRoundTrip` | `test_load_save_finalize_radio` | Load folder -> save .mtp -> reload .mtp -> finalize with `--hex` -> compiled RADIO.DAT is byte-identical to original |
| `TestMtpProjectIntegrity` | `test_mtp_contains_all_loaded_data` | .mtp ZIP contains radio.xml, all *-original.json, offsets JSONs, and settings.json with correct paths |
| `TestStageDirRoundTrip` | `test_stagedir_preserved` | STAGE.DIR is byte-identical after round-trip compilation with no edits |

Each test runs against all three versions: `usa-d1`, `jpn-d1`, `integral-d1`.

## Version-specific flags

| Version | `--hex` | `--integral` | `--long` |
|---------|---------|-------------|----------|
| usa-d1 | Yes | No | No |
| jpn-d1 | Yes | No | No |
| integral-d1 | Yes | Yes | No |

The `--hex` flag tells the recompiler to re-inject the original hex bytes for subtitle text rather than re-encoding from decoded text. This is what makes the byte-identical round-trip possible.

The `--integral` flag enables Integral disc mode (0x800-aligned blocks, 2-byte block index).

## How the tests work

### 1. Load folder

Calls `MainWindow._loadAllFromFolder()` directly, bypassing the `QFileDialog`. This parses RADIO.DAT via `RadioDatTools` into XML, and extracts DEMO/VOX/ZMOVIE into JSON.

### 2. Save to .mtp

Calls `MainWindow._writeProjectFile()` to create a ZIP archive containing:
- `settings.json` (DAT file paths)
- `radio.xml` (parsed radio data)
- `radio-original.json` / `radio-altered.json`
- `demo-original.json` / `demo-altered.json` / `demo-offsets.json`
- `vox-original.json` / `vox-altered.json` / `vox-offsets.json`
- `zmovie-original.json` / `zmovie-altered.json`
- `font.tbl`

### 3. Reload .mtp

Resets all module-level globals, creates a fresh `MainWindow`, then deserializes the .mtp ZIP contents back into the radio XML tree and JSON dicts (same logic as `MainWindow.openProject()`).

### 4. Finalize with --hex

Calls `RadioDatRecompiler.main()` with a `Namespace` matching the finalize dialog settings. The `--hex` flag re-injects original hex bytes, so unchanged data should recompile identically.

### 5. Binary comparison

Reads the compiled RADIO.DAT and compares byte-for-byte against the original. On mismatch, the test outputs:
- Original and compiled file sizes
- Size delta
- First 20 byte-level differences with hex offsets

## File structure

```
mgs-undubbed-gui/
  pytest.ini              # pytest config (testpaths, qt_api)
  tests/
    conftest.py           # shared fixtures, version configs, skip logic
    test_roundtrip.py     # all test classes
  build-src/              # game data (not checked in)
    usa-d1/MGS/
    jpn-d1/MGS/
    integral-d1/MGS/
```

## DuckStation smoke testing

DuckStation accepts CLI arguments (`duckstation-qt -batch /path/to.bin`) so a launch test is technically possible: start the emulator, wait, kill, check exit code. However, there is no reliable way to assert that the game loaded correctly without screenshot comparison or memory inspection, making it fragile and host-dependent. This is better kept as a manual verification step after the automated tests pass.

## Adding new versions

To add a new game version, add an entry to `VERSION_CONFIGS` in `tests/conftest.py`:

```python
VERSION_CONFIGS = [
    ("usa-d1",      "MGS", {"hex": True, "integral": False, "long": False}),
    ("jpn-d1",      "MGS", {"hex": True, "integral": False, "long": False}),
    ("integral-d1", "MGS", {"hex": True, "integral": True,  "long": False}),
    # Add new versions here:
    ("eur-d1",      "MGS", {"hex": True, "integral": False, "long": False}),
]
```

Then place the game data under `build-src/eur-d1/MGS/`.
