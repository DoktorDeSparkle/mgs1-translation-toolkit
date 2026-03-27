"""
Round-trip integration tests for MGS Dialogue Editor.

For each game version (usa-d1, jpn-d1, integral-d1):
  1. Load the game data folder into the GUI
  2. Save to a .mtp project file
  3. Reload the .mtp project
  4. Finalize (compile) with --hex to re-inject original hex
  5. Compare compiled RADIO.DAT against the original — must be byte-identical

Prerequisites:
  - Place game data under build-src/{version}/MGS/ with at minimum RADIO.DAT
    and STAGE.DIR.  DEMO.DAT, VOX.DAT, ZMOVIE.STR are optional.
  - pip install pytest pytest-qt PySide6

Run:
  cd mgs-undubbed-gui
  pytest tests/ -v
"""
import os, shutil, tempfile, zipfile, json
from argparse import Namespace

import pytest
from PySide6.QtWidgets import QApplication

# ── Import app internals ───────────────────────────────────────────────────
import mainwindow as mw


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_file(folder: str, name: str) -> str:
    """Case-insensitive file lookup (mirrors MainWindow._findFileInFolder)."""
    target = name.lower()
    for f in os.listdir(folder):
        if f.lower() == target:
            return os.path.join(folder, f)
    return ""


def _reset_global_state():
    """Reset mainwindow module-level globals so each test starts clean."""
    mw.radioManager = mw.RDE()
    mw.voxManager = {}
    mw.voxOriginalData = b''
    mw.voxFilePath = ""
    mw.demoManager = {}
    mw.demoOriginalData = b''
    mw.demoFilePath = ""
    mw.demoOriginalJson = {}
    mw.demoAlteredJson = {}
    mw.demoOffsetsJson = {}
    mw.demoSeqToOffset = {}
    mw.voxOriginalJson = {}
    mw.voxAlteredJson = {}
    mw.voxOffsetsJson = {}
    mw.voxSeqToOffset = {}
    mw.zmovieOriginalJson = {}
    mw.zmovieAlteredJson = {}
    mw.zmovieOriginalData = b''
    mw.radioOriginalJson = {}
    mw.radioAlteredJson = {}
    mw.projectFilePath = ""
    mw.projectSettings = {}


def _binary_diff_summary(orig: bytes, compiled: bytes, max_diffs: int = 20) -> str:
    """Return a human-readable summary of byte differences."""
    diffs = []
    length = max(len(orig), len(compiled))
    for i in range(length):
        a = orig[i] if i < len(orig) else None
        b = compiled[i] if i < len(compiled) else None
        if a != b:
            diffs.append(
                f"  offset 0x{i:08X}: "
                f"orig={f'0x{a:02X}' if a is not None else 'EOF'} "
                f"compiled={f'0x{b:02X}' if b is not None else 'EOF'}")
            if len(diffs) >= max_diffs:
                diffs.append(f"  ... (truncated, showing first {max_diffs})")
                break
    return "\n".join(diffs)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestRoundTrip:
    """Full round-trip: load folder → save .mtp → reload → finalize → compare."""

    def test_load_save_finalize_radio(self, qtbot, version_config, tmp_path):
        """Load game data, save/reload .mtp, finalize with --hex, compare RADIO.DAT."""
        version_name, data_folder, flags = version_config

        # ── 0. Reset state and create MainWindow ──────────────────────────
        _reset_global_state()
        window = mw.MainWindow()
        qtbot.addWidget(window)

        # ── 1. Locate files in the data folder ───────────────────────────
        radio_path    = _find_file(data_folder, "RADIO.DAT")
        demo_path     = _find_file(data_folder, "DEMO.DAT")
        vox_path      = _find_file(data_folder, "VOX.DAT")
        zmovie_path   = _find_file(data_folder, "ZMOVIE.STR")
        brf_path      = _find_file(data_folder, "BRF.DAT")
        face_path     = _find_file(data_folder, "FACE.DAT")
        stagedir_path = _find_file(data_folder, "STAGE.DIR")

        assert radio_path, f"RADIO.DAT not found in {data_folder}"
        assert stagedir_path, f"STAGE.DIR not found in {data_folder}"

        # Read original RADIO.DAT bytes for comparison later
        original_radio = open(radio_path, 'rb').read()

        # ── 2. Load folder (bypass QFileDialog) ─────────────────────────
        window._loadAllFromFolder(
            radio_path, demo_path, vox_path, zmovie_path,
            brfPath=brf_path, facePath=face_path,
            stageDirPath=stagedir_path)

        assert mw.radioManager.radioXMLData is not None, \
            f"[{version_name}] Radio XML failed to load"
        assert mw.projectSettings.get("radio_dat_path") == radio_path

        # ── 3. Save to .mtp ──────────────────────────────────────────────
        mtp_path = str(tmp_path / f"{version_name}.mtp")
        window._writeProjectFile(mtp_path)
        assert os.path.isfile(mtp_path), f".mtp not created at {mtp_path}"

        # Verify .mtp is a valid ZIP containing expected files
        with zipfile.ZipFile(mtp_path, 'r') as zf:
            names = zf.namelist()
            assert 'settings.json' in names
            assert 'radio.xml' in names
            assert 'radio-original.json' in names

        # ── 4. Reset and reload from .mtp ────────────────────────────────
        _reset_global_state()
        window2 = mw.MainWindow()
        qtbot.addWidget(window2)

        # Simulate openProject() without the file dialog
        with zipfile.ZipFile(mtp_path, 'r') as zf:
            names = zf.namelist()
            settings = json.loads(zf.read('settings.json'))
            radio_xml = zf.read('radio.xml').decode('utf-8') if 'radio.xml' in names else None
            radio_orig_json = json.loads(zf.read('radio-original.json')) if 'radio-original.json' in names else {}
            radio_alt_json = json.loads(zf.read('radio-altered.json')) if 'radio-altered.json' in names else {}

        # Restore radio XML via temp file (same as openProject)
        if radio_xml:
            import tempfile as _tf
            with _tf.NamedTemporaryFile(suffix='.xml', delete=False,
                                        mode='w', encoding='utf-8') as tmp:
                tmp.write(radio_xml)
                tmp_xml = tmp.name
            mw.radioManager.loadRadioXmlFile(tmp_xml)
            os.unlink(tmp_xml)

        mw.projectSettings = settings
        mw.radioOriginalJson = radio_orig_json
        mw.radioAlteredJson = radio_alt_json
        mw.projectFilePath = mtp_path

        assert mw.radioManager.radioXMLData is not None, \
            f"[{version_name}] Radio XML failed to reload from .mtp"

        # ── 5. Finalize (compile RADIO with --hex) ───────────────────────
        import scripts.RadioDatRecompiler as RDR

        # Reset recompiler state
        RDR.stageBytes = b''
        RDR.debug = False
        RDR.subUseOriginalHex = False
        RDR.useDWidSaveB = False
        RDR.newOffsets = {}

        # Write current XML to temp for the recompiler
        import xml.etree.ElementTree as ET
        from xml.dom.minidom import parseString
        import copy

        xml_copy = copy.deepcopy(mw.radioManager.radioXMLData)
        xml_str = parseString(ET.tostring(xml_copy)).toprettyxml(indent="  ")
        compile_xml = str(tmp_path / f"{version_name}-compile.xml")
        with open(compile_xml, 'w', encoding='utf-8') as f:
            f.write(xml_str)

        radio_out = str(tmp_path / f"{version_name}-RADIO.DAT")
        stage_out = str(tmp_path / f"{version_name}-STAGE.DIR")

        args = Namespace(
            input=compile_xml,
            output=radio_out,
            stage=stagedir_path,
            stageOut=stage_out,
            prepare=False,
            hex=True,              # --hex: re-inject original hex
            debug=False,
            double=False,
            integral=flags["integral"],
            long=flags["long"],
            pad=False,
            roundtrip=False,
        )
        RDR.main(args)

        assert os.path.isfile(radio_out), \
            f"[{version_name}] Compiled RADIO.DAT not created"

        # ── 6. Compare compiled RADIO.DAT with original ─────────────────
        compiled_radio = open(radio_out, 'rb').read()

        if original_radio != compiled_radio:
            size_diff = len(compiled_radio) - len(original_radio)
            diff_summary = _binary_diff_summary(original_radio, compiled_radio)
            pytest.fail(
                f"[{version_name}] RADIO.DAT round-trip mismatch!\n"
                f"  Original size:  {len(original_radio)} bytes\n"
                f"  Compiled size:  {len(compiled_radio)} bytes\n"
                f"  Size delta:     {size_diff:+d} bytes\n"
                f"  First differences:\n{diff_summary}")


class TestMtpProjectIntegrity:
    """Verify .mtp project file contents after save and reload."""

    def test_mtp_contains_all_loaded_data(self, qtbot, version_config, tmp_path):
        """Saved .mtp should contain JSON for every mode that was loaded."""
        version_name, data_folder, flags = version_config

        _reset_global_state()
        window = mw.MainWindow()
        qtbot.addWidget(window)

        radio_path    = _find_file(data_folder, "RADIO.DAT")
        demo_path     = _find_file(data_folder, "DEMO.DAT")
        vox_path      = _find_file(data_folder, "VOX.DAT")
        zmovie_path   = _find_file(data_folder, "ZMOVIE.STR")
        stagedir_path = _find_file(data_folder, "STAGE.DIR")

        window._loadAllFromFolder(
            radio_path, demo_path, vox_path, zmovie_path,
            stageDirPath=stagedir_path)

        mtp_path = str(tmp_path / f"{version_name}-integrity.mtp")
        window._writeProjectFile(mtp_path)

        with zipfile.ZipFile(mtp_path, 'r') as zf:
            names = zf.namelist()

            if radio_path:
                assert 'radio.xml' in names, "Missing radio.xml"
                assert 'radio-original.json' in names, "Missing radio-original.json"

            if demo_path:
                assert 'demo-original.json' in names, "Missing demo-original.json"
                assert 'demo-offsets.json' in names, "Missing demo-offsets.json"

            if vox_path:
                assert 'vox-original.json' in names, "Missing vox-original.json"
                assert 'vox-offsets.json' in names, "Missing vox-offsets.json"

            if zmovie_path:
                assert 'zmovie-original.json' in names, "Missing zmovie-original.json"

            # Verify settings.json round-trips
            settings = json.loads(zf.read('settings.json'))
            assert settings.get("radio_dat_path") == (radio_path or "")


class TestStageDirRoundTrip:
    """Verify STAGE.DIR is preserved through finalize when no edits are made."""

    def test_stagedir_preserved(self, qtbot, version_config, tmp_path):
        """With no edits and --hex, STAGE.DIR should be identical to original."""
        version_name, data_folder, flags = version_config

        stagedir_path = _find_file(data_folder, "STAGE.DIR")
        if not stagedir_path:
            pytest.skip(f"No STAGE.DIR in {data_folder}")

        radio_path = _find_file(data_folder, "RADIO.DAT")
        if not radio_path:
            pytest.skip(f"No RADIO.DAT in {data_folder}")

        original_stage = open(stagedir_path, 'rb').read()

        _reset_global_state()
        window = mw.MainWindow()
        qtbot.addWidget(window)

        window._loadAllFromFolder(
            radio_path,
            _find_file(data_folder, "DEMO.DAT"),
            _find_file(data_folder, "VOX.DAT"),
            _find_file(data_folder, "ZMOVIE.STR"),
            stageDirPath=stagedir_path)

        # Compile RADIO only
        import scripts.RadioDatRecompiler as RDR
        import xml.etree.ElementTree as ET
        from xml.dom.minidom import parseString
        import copy

        RDR.stageBytes = b''
        RDR.debug = False
        RDR.subUseOriginalHex = False
        RDR.useDWidSaveB = False
        RDR.newOffsets = {}

        xml_copy = copy.deepcopy(mw.radioManager.radioXMLData)
        xml_str = parseString(ET.tostring(xml_copy)).toprettyxml(indent="  ")
        compile_xml = str(tmp_path / f"{version_name}-stage-compile.xml")
        with open(compile_xml, 'w', encoding='utf-8') as f:
            f.write(xml_str)

        radio_out = str(tmp_path / f"{version_name}-stage-RADIO.DAT")
        stage_out = str(tmp_path / f"{version_name}-STAGE.DIR")

        args = Namespace(
            input=compile_xml, output=radio_out,
            stage=stagedir_path, stageOut=stage_out,
            prepare=False, hex=True,
            debug=False, double=False,
            integral=flags["integral"],
            long=flags["long"],
            pad=False, roundtrip=False,
        )
        RDR.main(args)

        compiled_stage = open(stage_out, 'rb').read()

        if original_stage != compiled_stage:
            diff_summary = _binary_diff_summary(original_stage, compiled_stage)
            pytest.fail(
                f"[{version_name}] STAGE.DIR round-trip mismatch!\n"
                f"  Original size:  {len(original_stage)} bytes\n"
                f"  Compiled size:  {len(compiled_stage)} bytes\n"
                f"  First differences:\n{diff_summary}")
