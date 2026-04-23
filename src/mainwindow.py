# This Python file uses the following encoding: utf-8
import sys, os, zipfile, json, tempfile, importlib, shutil

from PySide6.QtWidgets import (QApplication, QMainWindow, QFileDialog,
    QListWidgetItem, QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QMessageBox, QGraphicsScene, QGraphicsTextItem,
    QGroupBox, QCheckBox, QLineEdit, QFormLayout,
    QFrame, QComboBox, QSizePolicy, QScrollArea, QTabWidget,
    QDialogButtonBox, QWidget, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView)
from PySide6.QtCore import Qt, QThread, Signal, QTimer, QElapsedTimer, QUrl, QSettings
from PySide6.QtGui import QFont, QColor, QAction, QKeySequence
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget

from ui_form import Ui_MainWindow

# For submodules, add project root to sys.path (scripts/ lives one level up from src/)
_project_root = os.path.normpath(os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, _project_root)
# Also add scripts/ itself for internal imports (e.g. from DemoTools.extractDemoVox)
sys.path.insert(0, os.path.join(_project_root, "scripts"))

# MGS Script modules
from scripts.radioModule import radioDataEditor as RDE
from scripts import demoClasses as voxCtl
from scripts import demoManager as DM
import scripts.audioTools.vagAudioTools as VAG

# Initialize Radio Data Editor
radioManager = RDE()
voxManager: dict[str, voxCtl.demo] = {}
voxOriginalData: bytes = b''   # Original bytes kept for patch-in-place VOX saving
voxFilePath: str = ""          # Path of the loaded VOX.DAT for save-as default

# Track the currently selected subtitle index
currentSubIndex: int = -1
# Track the current vox offset string so we can look up timings
currentVoxOffset: str = ""

# Demo mode state
demoManager: dict[str, voxCtl.demo] = {}
demoOriginalData: bytes = b''
demoFilePath: str = ""
currentDemoKey: str = ""       # sequential name, e.g. "demo-01"
currentVoxKey:  str = ""       # sequential name, e.g. "vox-0001"
demoOriginalJson: dict = {}    # {"demo-01": {...}} — read-only reference from extraction
demoAlteredJson:  dict = {}    # {"demo-01": {...}} — only user-modified entries (sparse)
demoOffsetsJson:  dict = {}    # {"01": "00003800"} — offset map for STAGE.DIR adjustment
demoSeqToOffset:  dict = {}    # {"demo-01": "12345"} — maps name → raw offset string

# VOX mode state (mirrors demo mode)
voxOriginalJson: dict = {}     # {"vox-0001": {...}} — read-only reference from extraction
voxAlteredJson:  dict = {}     # {"vox-0001": {...}} — only user-modified entries (sparse)
voxOffsetsJson:  dict = {}     # {"0001": "00003800"} — offset map for STAGE.DIR adjustment
voxSeqToOffset:  dict = {}     # {"vox-0001": "12345"} — maps name → raw offset string

# ZMovie mode state
zmovieOriginalJson: dict  = {}   # {"zmovie-00": {...}} — read-only reference from extraction
zmovieAlteredJson:  dict  = {}   # {"zmovie-00": {...}} — only user-modified entries (sparse)
zmovieOriginalData: bytes = b''  # original ZMOVIE.STR bytes for patch-in-place compile
zmovieFilePath:     str   = ""
currentZmovieKey:   str   = ""   # e.g. "zmovie-00"

# Graphics dictionary state (per-entry custom 12x12 tiles)
radioGraphicsJson: dict = {}   # {"call-offset": "hex-of-36-byte-tiles", ...}
demoGraphicsJson:  dict = {}   # {"demo-01": "hex-of-36-byte-tiles", ...}
voxGraphicsJson:   dict = {}   # {"vox-0001": "hex-of-36-byte-tiles", ...}

def _mergedZmovieJson() -> dict:
    """Return zmovieOriginalJson with zmovieAlteredJson overlaid (altered takes priority)."""
    merged = dict(zmovieOriginalJson)
    merged.update(zmovieAlteredJson)
    return merged

# Radio iseeva JSON state (calls-only for now)
# Structure: {callOffset: {voxOffset: {subtitleOffset: "text", ...}, ...}, ...}
radioOriginalJson: dict = {}   # read-only
radioAlteredJson:  dict = {}   # sparse — only user-modified subtitle entries

def _mergedRadioCallsJson() -> dict:
    """Return radioOriginalJson with radioAlteredJson overlaid (3-level: call->vox->sub).
    Skips underscore-prefixed keys (static field storage like _prompts, _saves, _freqAdd)."""
    merged = {}
    allKeys = set(radioOriginalJson) | set(radioAlteredJson)
    for callOff in allKeys:
        if callOff.startswith("_"):
            continue
        origCall = radioOriginalJson.get(callOff, {})
        altCall  = radioAlteredJson.get(callOff, {})
        mergedCall = {}
        for voxOff in set(origCall) | set(altCall):
            origVox = origCall.get(voxOff, {})
            altVox  = altCall.get(voxOff, {})
            mergedCall[voxOff] = {**origVox, **altVox}
        merged[callOff] = mergedCall
    return merged

def _getRadioVoxSubs(callOffset: str, voxOffset: str) -> dict:
    """Get merged subtitles for a specific call + vox combination."""
    merged = _mergedRadioCallsJson()
    return merged.get(callOffset, {}).get(voxOffset, {})

def _extractIseevaCallsFromXml(xmlRoot) -> dict:
    """Extract calls-only iseeva JSON from the in-memory XML tree.
    Structure: {callOffset: {voxOffset: {subtitleOffset: text}}}
    For calls with no VOX_CUES, the voxOffset key is the call offset itself."""
    calls = {}
    for call in xmlRoot.findall(".//Call"):
        callOffset = call.get("offset")
        callData = {}
        for vox in call.findall(".//VOX_CUES"):
            voxOffset = vox.get("offset")
            voxText = {}
            for sub in vox.findall("SUBTITLE"):
                voxText[sub.get("offset")] = sub.get("text", "")
            if voxText:
                callData[voxOffset] = voxText
        # Calls with direct SUBTITLEs (no VOX_CUES) — use call offset as vox key
        directSubs = {}
        for sub in call.findall("SUBTITLE"):
            directSubs[sub.get("offset")] = sub.get("text", "")
        if directSubs:
            callData[callOffset] = directSubs
        if callData:
            calls[callOffset] = callData
    return calls

def _extractStaticFieldsFromXml(xmlRoot) -> dict:
    """Extract static radio fields (prompts, save locations, contact names) from XML.
    Returns {"prompts": {idx: text}, "saves": {idx: contentB}, "freqAdd": {offset: name}}."""
    prompts = {}
    saves = {}
    freqAdd = {}

    # Prompts: first ASK_USER → iterate USR_OPTN children
    firstAsk = xmlRoot.find(".//ASK_USER")
    if firstAsk is not None:
        for i, option in enumerate(firstAsk):
            prompts[str(i)] = option.get("text", "")

    # Save locations: first MEM_SAVE → iterate SAVE_OPT children
    firstSave = xmlRoot.find(".//MEM_SAVE")
    if firstSave is not None:
        for i, option in enumerate(firstSave):
            saves[str(i)] = option.get("contentB", "")

    # Contact names: all ADD_FREQ elements
    for elem in xmlRoot.findall(".//ADD_FREQ"):
        offset = elem.get("offset")
        name = elem.get("name", "")
        if offset:
            freqAdd[offset] = name

    return {"prompts": prompts, "saves": saves, "freqAdd": freqAdd}


def _ensureJsonV2(data: dict) -> dict:
    """Auto-convert v1 demo/vox JSON to v2 if needed.
    v1: {"demo-01": [{"01": "text"}, {"01": "timing,duration"}]}
    v2: {"demo-01": {"startFrame": {"duration": "...", "text": "..."}}}"""
    if not data:
        return data
    first = next(iter(data.values()))
    if not isinstance(first, list):
        return data  # already v2
    from scripts.DemoTools.demoJsonConverter import convertToNew
    return convertToNew(data)

def _mergedDemoJson() -> dict:
    """Return demoOriginalJson with demoAlteredJson overlaid (altered takes priority)."""
    merged = dict(demoOriginalJson)
    merged.update(demoAlteredJson)
    return merged

def _mergedVoxJson() -> dict:
    """Return voxOriginalJson with voxAlteredJson overlaid (altered takes priority)."""
    merged = dict(voxOriginalJson)
    merged.update(voxAlteredJson)
    return merged

# Radio/VOX cross-reference index (built when RADIO XML loads)
_radioDisc2Offsets:    set = set()  # call offsets where any VOX_CUES has a zero block address
_radioClaimedVoxAddrs: set = set()  # VOX byte addresses claimed by any RADIO call (non-zero only)

# Project state
projectSettings: dict = {}     # {"radio_dat_path": ..., "demo_dat_path": ..., "vox_dat_path": ..., "brf_dat_path": ..., "face_dat_path": ..., "stage_dir_path": ...}
projectFilePath: str  = ""     # path to the currently-open .mtp file (empty if unsaved)

# Font table state
activeTblMapping: dict = {}    # hex code -> character mapping from .tbl
activeTblRaw:     str  = ""    # raw .tbl file content for project save

# ── Subtitle preview tuning ───────────────────────────────────────────────────
# SUBTITLE_FPS: the frame rate the game uses for subtitle timing values.
#   Increase → subtitles advance slower (if they're running too fast)
#   Decrease → subtitles advance faster (if they're running too slow)
# SUBTITLE_OFFSET_MS: additional delay (ms) before subtitle clock starts,
#   to compensate for ffplay startup latency.
#   Increase → subtitles appear later (if they start too early)
SUBTITLE_FPS       = 23.69   # measured from demo-25; adjust if still off
SUBTITLE_OFFSET_MS = 150      # ms to wait before subtitle clock starts; tune if start is off


class VoxConversionThread(QThread):
    """
    Runs the ffmpeg VAG→WAV conversion off the main thread.
    Emits conversionDone(wavPath) when the WAV is ready, or
    errorOccurred(message) on failure.
    Playback is handled by FfplayThread after conversion completes.

    Uses subprocess.Popen directly so the ffmpeg process can be forcibly
    killed via killSubprocess() — preventing race conditions when the user
    clicks Play while a previous conversion is still running.
    """
    conversionDone = Signal(str)   # path to the finished WAV
    errorOccurred  = Signal(str)

    def __init__(self, vagFile: str, parent=None):
        super().__init__(parent)
        self.vagFile = vagFile
        self._proc = None   # holds the active Popen so killSubprocess() can reach it

    def killSubprocess(self):
        """Forcibly kill the ffmpeg subprocess if it is still running."""
        proc = self._proc
        if proc is not None and proc.poll() is None:
            proc.kill()

    def run(self):
        import subprocess, tempfile, ffmpeg as _ffmpeg
        try:
            tempDir = tempfile.gettempdir()
            outWav  = os.path.join(tempDir, "mgs_vox_temp.wav")
            tempL   = os.path.join(tempDir, "mgs_vox_temp_L.vag")
            tempR   = os.path.join(tempDir, "mgs_vox_temp_R.vag")

            with open(self.vagFile, 'rb') as f:
                magic = f.read(4)

            if magic == b'VAGp':
                cmd = (_ffmpeg.input(self.vagFile, f='vag')
                               .output(outWav)
                               .overwrite_output()
                               .compile())
                self._proc = subprocess.Popen(cmd,
                                              stdout=subprocess.DEVNULL,
                                              stderr=subprocess.DEVNULL)
                ret = self._proc.wait()
                self._proc = None
                if ret not in (0, -9):   # -9 = SIGKILL (user stopped)
                    self.errorOccurred.emit(f"ffmpeg exited with code {ret}")
                    return

            elif magic == b'VAGi':
                VAG.splitVagFile(self.vagFile, tempL, tempR)
                left  = _ffmpeg.input(tempL, f='vag')
                right = _ffmpeg.input(tempR, f='vag')
                cmd = (_ffmpeg.filter([left, right], 'join', inputs=2, channel_layout='stereo')
                               .output(outWav, acodec='pcm_s16le')
                               .overwrite_output()
                               .compile())
                self._proc = subprocess.Popen(cmd,
                                              stdout=subprocess.DEVNULL,
                                              stderr=subprocess.DEVNULL)
                ret = self._proc.wait()
                self._proc = None
                if ret not in (0, -9):
                    self.errorOccurred.emit(f"ffmpeg exited with code {ret}")
                    return

            else:
                self.errorOccurred.emit(f"Not a valid VAG file (magic: {magic.hex()})")
                return

            if not self.isInterruptionRequested():
                self.conversionDone.emit(outWav)

        except Exception as e:
            self.errorOccurred.emit(str(e))


class FfplayThread(QThread):
    """
    Plays a WAV file via ffplay in a subprocess.
    Emits playbackFinished when the file ends naturally,
    or errorOccurred if ffplay fails or is not found.
    Kill with killSubprocess() to stop mid-playback.
    """
    playbackFinished = Signal()
    errorOccurred    = Signal(str)

    def __init__(self, wavPath: str, parent=None):
        super().__init__(parent)
        self.wavPath = wavPath
        self._proc = None

    def killSubprocess(self):
        proc = self._proc
        if proc is not None and proc.poll() is None:
            proc.kill()

    def run(self):
        import subprocess
        try:
            cmd = ['ffplay', '-nodisp', '-autoexit', self.wavPath]
            self._proc = subprocess.Popen(cmd,
                                          stdout=subprocess.DEVNULL,
                                          stderr=subprocess.DEVNULL)
            ret = self._proc.wait()
            self._proc = None
            if self.isInterruptionRequested():
                return
            # 0 = clean exit, negative = killed by signal (user stopped)
            if ret <= 0:
                self.playbackFinished.emit()
            else:
                self.errorOccurred.emit(f"ffplay exited with code {ret}")
        except FileNotFoundError:
            self.errorOccurred.emit("ffplay not found — is FFmpeg installed and on PATH?")
        except Exception as e:
            self.errorOccurred.emit(str(e))


class ZmovieConversionThread(QThread):
    """
    Extracts a zmovie entry from ZMOVIE.STR as a raw PSX STR file, then
    converts it to MP4 via ffmpeg for playback in QMediaPlayer.
    Emits conversionDone(mp4Path) when ready.
    """
    conversionDone = Signal(str)
    errorOccurred  = Signal(str)

    def __init__(self, zmovieData: bytes, entryIndex: int, parent=None):
        super().__init__(parent)
        self._zmovieData = zmovieData
        self._entryIndex = entryIndex
        self._proc = None

    def killSubprocess(self):
        proc = self._proc
        if proc is not None and proc.poll() is None:
            proc.kill()

    def run(self):
        import subprocess, tempfile
        from scripts.zmovieTools import extractZmovie as ZM

        try:
            strPath = os.path.join(tempfile.gettempdir(), f"mgs_zmovie_{self._entryIndex}.str")
            mp4Path = os.path.join(tempfile.gettempdir(), f"mgs_zmovie_{self._entryIndex}.mp4")

            ZM.extractEntryVideo(self._zmovieData, self._entryIndex, strPath)

            cmd = ['ffmpeg', '-y', '-i', strPath, mp4Path]
            self._proc = subprocess.Popen(cmd,
                                          stdout=subprocess.DEVNULL,
                                          stderr=subprocess.DEVNULL)
            ret = self._proc.wait()
            self._proc = None

            if self.isInterruptionRequested():
                return
            if ret == 0:
                self.conversionDone.emit(mp4Path)
            else:
                self.errorOccurred.emit(f"ffmpeg exited with code {ret}")
        except FileNotFoundError:
            self.errorOccurred.emit("ffmpeg not found — is FFmpeg installed and on PATH?")
        except Exception as e:
            self.errorOccurred.emit(str(e))


class AutoTranslateThread(QThread):
    """Translates a list of subtitle texts off the main thread.
    Emits lineTranslated(index, translatedText) for each completed line,
    and finished() when all lines are done."""
    lineTranslated = Signal(int, str)  # (subtitle index, translated text)
    errorOccurred  = Signal(str)

    def __init__(self, texts: list, srcLang: str, tgtLang: str, parent=None):
        super().__init__(parent)
        self._texts = texts
        self._srcLang = srcLang
        self._tgtLang = tgtLang

    def run(self):
        try:
            from deep_translator import GoogleTranslator
        except ImportError:
            self.errorOccurred.emit(
                "Install deep_translator to use translation:\n\n"
                "  pip install deep-translator")
            return
        translator = GoogleTranslator(source=self._srcLang, target=self._tgtLang)
        for i, text in enumerate(self._texts):
            if self.isInterruptionRequested():
                return
            if not text.strip():
                self.lineTranslated.emit(i, "")
                continue
            # Strip line-break markers that confuse the translator
            clean = text.replace("\\r\\n", " ").replace("｜", " ").replace("\n", " ")
            clean = " ".join(clean.split())
            try:
                result = translator.translate(clean)
                self.lineTranslated.emit(i, result or "")
            except Exception as e:
                self.errorOccurred.emit(f"Translation failed on subtitle {i}: {e}")
                return


from PySide6.QtWidgets import QListWidget as _QListWidget

class OffsetListWidget(_QListWidget):
    """QListWidget with QComboBox-compatible API for drop-in replacement."""
    currentIndexChanged = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._userData = []  # parallel list of userData per row
        self.currentRowChanged.connect(self.currentIndexChanged)

    def addItem(self, text, userData=None):
        super().addItem(text)
        self._userData.append(userData)

    def clear(self):
        super().clear()
        self._userData.clear()

    def currentData(self):
        idx = self.currentRow()
        if 0 <= idx < len(self._userData):
            return self._userData[idx]
        return None

    def findData(self, data):
        try:
            return self._userData.index(data)
        except ValueError:
            return -1

    def currentIndex(self):
        return self.currentRow()

    def setCurrentIndex(self, idx):
        self.setCurrentRow(idx)


class XmlFileDialog(QFileDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFileMode(QFileDialog.ExistingFiles)
        self.setNameFilter("XML files (*.xml)")
        self.setViewMode(QFileDialog.List)
        self.setAcceptMode(QFileDialog.AcceptOpen)
        self.setModal(True)
        self.setWindowTitle("Select a Radio.xml File")
        self.setDirectory(os.getcwd())


class RadioStaticFieldsDialog(QDialog):
    """Dialog for editing radio static fields: prompts, save locations, contact names."""

    def __init__(self, xmlRoot, existingEdits, initialTab=0, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit Radio Static Fields")
        self.setMinimumSize(500, 400)
        self.result_data = {}

        originals = _extractStaticFieldsFromXml(xmlRoot)

        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        layout.addWidget(tabs)

        # ── Tab 0: Prompts ──────────────────────────────────────────────
        self._promptEdits = {}
        promptWidget = QWidget()
        promptLayout = QFormLayout(promptWidget)
        editPrompts = existingEdits.get("_prompts", {})
        for idx in sorted(originals["prompts"].keys(), key=int):
            origText = originals["prompts"][idx]
            edit = QLineEdit()
            edit.setPlaceholderText(origText)
            if idx in editPrompts:
                edit.setText(editPrompts[idx])
            self._promptEdits[idx] = edit
            promptLayout.addRow(QLabel(origText), edit)
        promptScroll = QScrollArea()
        promptScroll.setWidgetResizable(True)
        promptScroll.setWidget(promptWidget)
        tabs.addTab(promptScroll, "Prompts")

        # ── Tab 1: Save Locations ───────────────────────────────────────
        self._saveEdits = {}
        saveWidget = QWidget()
        saveLayout = QFormLayout(saveWidget)
        editSaves = existingEdits.get("_saves", {})
        for idx in sorted(originals["saves"].keys(), key=int):
            origText = originals["saves"][idx]
            edit = QLineEdit()
            edit.setMinimumWidth(300)
            edit.setPlaceholderText(origText)
            if idx in editSaves:
                edit.setText(editSaves[idx])
            self._saveEdits[idx] = edit
            saveLayout.addRow(QLabel(origText), edit)
        saveScroll = QScrollArea()
        saveScroll.setWidgetResizable(True)
        saveScroll.setWidget(saveWidget)
        tabs.addTab(saveScroll, "Save Locations")

        # ── Tab 2: Contact Names ────────────────────────────────────────
        # Group by unique name so user edits one row per unique contact
        self._freqEdits = {}       # {uniqueName: QLineEdit}
        self._freqNameOffsets = {}  # {uniqueName: [offset, ...]}
        editFreq = existingEdits.get("_freqAdd", {})
        nameToOffsets = {}
        for offset, name in originals["freqAdd"].items():
            nameToOffsets.setdefault(name, []).append(offset)

        freqWidget = QWidget()
        freqLayout = QFormLayout(freqWidget)
        for name in sorted(nameToOffsets.keys()):
            offsets = nameToOffsets[name]
            self._freqNameOffsets[name] = offsets
            edit = QLineEdit()
            edit.setPlaceholderText(name)
            # Pre-fill from existing edits (check first offset for this name)
            existing = editFreq.get(offsets[0])
            if existing:
                edit.setText(existing)
            self._freqEdits[name] = edit
            countLabel = f" ({len(offsets)}x)" if len(offsets) > 1 else ""
            freqLayout.addRow(QLabel(f"{name}{countLabel}"), edit)
        freqScroll = QScrollArea()
        freqScroll.setWidgetResizable(True)
        freqScroll.setWidget(freqWidget)
        tabs.addTab(freqScroll, "Contact Names")

        tabs.setCurrentIndex(initialTab)

        # ── OK / Cancel ─────────────────────────────────────────────────
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._collectAndAccept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _collectAndAccept(self):
        """Collect edited values into self.result_data and accept."""
        # Prompts: only include if text differs from placeholder (original)
        prompts = {}
        for idx, edit in self._promptEdits.items():
            text = edit.text().strip()
            if text and text != edit.placeholderText():
                prompts[idx] = text
        # Save locations
        saves = {}
        for idx, edit in self._saveEdits.items():
            text = edit.text().strip()
            if text and text != edit.placeholderText():
                saves[idx] = text
        # Contact names: expand back to per-offset dict
        freqAdd = {}
        for name, edit in self._freqEdits.items():
            text = edit.text().strip()
            if text and text != edit.placeholderText():
                for offset in self._freqNameOffsets[name]:
                    freqAdd[offset] = text

        self.result_data = {
            "_prompts": prompts,
            "_saves": saves,
            "_freqAdd": freqAdd,
        }
        self.accept()


class NotificationDialog(QDialog):
    def __init__(self, title="Notification", message="", parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        layout = QVBoxLayout()
        layout.addWidget(QLabel(message))
        ok_button = QPushButton("OK")
        ok_button.clicked.connect(self.accept)
        layout.addWidget(ok_button)
        self.setLayout(layout)


class PreferencesDialog(QDialog):
    """Application preferences dialog."""

    # (display name, deep_translator language code)
    LANGUAGES = [
        ("English", "en"),
        ("Japanese", "ja"),
        ("Spanish", "es"),
        ("French", "fr"),
        ("German", "de"),
        ("Italian", "it"),
        ("Portuguese", "pt"),
        ("Russian", "ru"),
        ("Korean", "ko"),
        ("Chinese (Simplified)", "zh-CN"),
        ("Chinese (Traditional)", "zh-TW"),
    ]

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Preferences")
        self.setMinimumWidth(350)
        self._settings = settings

        layout = QVBoxLayout()

        # ── Translation section ───────────────────────────────────────────
        transGroup = QGroupBox("Translation")
        transLayout = QFormLayout()

        from PySide6.QtWidgets import QComboBox
        self.comboTargetLang = QComboBox()
        currentCode = settings.value("translate/target_lang", "en")
        for i, (name, code) in enumerate(self.LANGUAGES):
            self.comboTargetLang.addItem(name, code)
            if code == currentCode:
                self.comboTargetLang.setCurrentIndex(i)
        transLayout.addRow("Target language:", self.comboTargetLang)

        self.comboSourceLang = QComboBox()
        self.comboSourceLang.addItem("Auto-detect", "auto")
        currentSrc = settings.value("translate/source_lang", "ja")
        idx = 0
        for i, (name, code) in enumerate(self.LANGUAGES):
            self.comboSourceLang.addItem(name, code)
            if code == currentSrc:
                idx = i + 1  # +1 for auto-detect entry
        self.comboSourceLang.setCurrentIndex(idx)
        transLayout.addRow("Source language:", self.comboSourceLang)

        transGroup.setLayout(transLayout)
        layout.addWidget(transGroup)

        # ── Editor section ───────────────────────────────────────────────
        editorGroup = QGroupBox("Editor")
        editorLayout = QVBoxLayout()

        self.chkRevertWarn = QCheckBox("Warn before reverting to original")
        self.chkRevertWarn.setChecked(
            settings.value("editor/warn_on_revert", True, type=bool))
        editorLayout.addWidget(self.chkRevertWarn)

        editorGroup.setLayout(editorLayout)
        layout.addWidget(editorGroup)

        # ── Build section ────────────────────────────────────────────────
        buildGroup = QGroupBox("Build")
        buildLayout = QVBoxLayout()

        outDirRow = QHBoxLayout()
        outDirRow.addWidget(QLabel("Default output folder:"))
        self.txtDefaultOutDir = QLineEdit(
            settings.value("build/output_dir", ""))
        self.txtDefaultOutDir.setPlaceholderText("(none — writes alongside originals)")
        outDirRow.addWidget(self.txtDefaultOutDir)
        btnBrowseOutDir = QPushButton("Browse...")
        btnBrowseOutDir.clicked.connect(self._browseDefaultOutDir)
        outDirRow.addWidget(btnBrowseOutDir)
        buildLayout.addLayout(outDirRow)

        buildGroup.setLayout(buildLayout)
        layout.addWidget(buildGroup)

        # ── Subtitle Preview section ─────────────────────────────────────
        from PySide6.QtWidgets import QSlider, QDoubleSpinBox
        subGroup = QGroupBox("Subtitle Preview")
        subGroup.setToolTip(
            "Adjusts the frame rate used to sync subtitle highlighting\n"
            "with audio playback. Tune this if subtitles run ahead of or\n"
            "behind the audio. Unfortunately it must be manually tuned\n"
            "because audio does not consistently follow a single rate.")
        subLayout = QHBoxLayout()

        subLayout.addWidget(QLabel("FPS:"))

        self.spinSubFps = QDoubleSpinBox()
        self.spinSubFps.setRange(20.0, 40.0)
        self.spinSubFps.setDecimals(2)
        self.spinSubFps.setSingleStep(0.1)
        savedFps = float(settings.value("preview/subtitle_fps", SUBTITLE_FPS))
        self.spinSubFps.setValue(savedFps)
        subLayout.addWidget(self.spinSubFps)

        self.sliderSubFps = QSlider(Qt.Horizontal)
        self.sliderSubFps.setRange(2000, 4000)  # slider in centiFPS (20.00–40.00)
        self.sliderSubFps.setValue(int(savedFps * 100))
        subLayout.addWidget(self.sliderSubFps, 1)

        btnDefaultFps = QPushButton("Default")
        btnDefaultFps.setToolTip(f"Reset to {SUBTITLE_FPS}")
        btnDefaultFps.clicked.connect(
            lambda: (self.spinSubFps.setValue(SUBTITLE_FPS),
                     self.sliderSubFps.setValue(int(SUBTITLE_FPS * 100))))
        subLayout.addWidget(btnDefaultFps)

        # Keep spinbox and slider in sync
        self.spinSubFps.valueChanged.connect(
            lambda v: self.sliderSubFps.setValue(int(v * 100)))
        self.sliderSubFps.valueChanged.connect(
            lambda v: self.spinSubFps.setValue(v / 100.0))

        subGroup.setLayout(subLayout)
        layout.addWidget(subGroup)

        # ── Buttons ───────────────────────────────────────────────────────
        btnRow = QHBoxLayout()
        btnOk = QPushButton("OK")
        btnOk.setDefault(True)
        btnOk.clicked.connect(self.accept)
        btnRow.addWidget(btnOk)
        btnCancel = QPushButton("Cancel")
        btnCancel.clicked.connect(self.reject)
        btnRow.addWidget(btnCancel)
        layout.addLayout(btnRow)

        self.setLayout(layout)

    def accept(self):
        self._settings.setValue("translate/target_lang",
                                self.comboTargetLang.currentData())
        self._settings.setValue("translate/source_lang",
                                self.comboSourceLang.currentData())
        self._settings.setValue("editor/warn_on_revert",
                                self.chkRevertWarn.isChecked())
        self._settings.setValue("build/output_dir",
                                self.txtDefaultOutDir.text().strip())
        self._settings.setValue("preview/subtitle_fps",
                                self.spinSubFps.value())
        super().accept()

    def _browseDefaultOutDir(self):
        path = QFileDialog.getExistingDirectory(
            self, "Select Default Output Folder", self.txtDefaultOutDir.text())
        if path:
            self.txtDefaultOutDir.setText(path)


class _LogCapture:
    """Redirect stdout/stderr to a callback while preserving original output."""
    def __init__(self, callback, original):
        self._callback = callback
        self._original = original
    def write(self, text):
        if text.strip():
            self._callback(text.rstrip())
        self._original.write(text)
    def flush(self):
        self._original.flush()


class FinalizeProgressDialog(QDialog):
    """Modal progress dialog shown during Finalize Project.
    Captures stdout/stderr and displays build output in real time."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Finalizing Project...")
        self.setMinimumSize(520, 360)
        self.setModal(True)
        layout = QVBoxLayout()

        self.statusLabel = QLabel("Starting...")
        self.statusLabel.setStyleSheet("font-weight: bold;")
        layout.addWidget(self.statusLabel)

        from PySide6.QtWidgets import QPlainTextEdit
        self.logBox = QPlainTextEdit()
        self.logBox.setReadOnly(True)
        self.logBox.setFont(QFont("Menlo, Courier New, Courier, monospace", 11))
        layout.addWidget(self.logBox)

        self.btnClose = QPushButton("Close")
        self.btnClose.setEnabled(False)
        self.btnClose.clicked.connect(self.accept)
        layout.addWidget(self.btnClose)

        self.setLayout(layout)

    def setStep(self, label: str):
        self.statusLabel.setText(label)
        QApplication.processEvents()

    def log(self, text: str):
        self.logBox.appendPlainText(text)
        QApplication.processEvents()

    def finish(self, summary: str, hasErrors: bool):
        self.statusLabel.setText("Complete (with errors)" if hasErrors else "Complete!")
        self.log("\n" + summary)
        self.btnClose.setEnabled(True)


class FinalizeProjectDialog(QDialog):
    """Dialog for batch-compiling all (or selected) game data files."""
    def __init__(self, stageDirPath="", defaultOutputDir="", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Finalize Project")
        self.setMinimumWidth(480)
        layout = QVBoxLayout()

        # ── RADIO section ─────────────────────────────────────────────────
        self.radioGroup = QGroupBox("RADIO.DAT")
        self.radioGroup.setCheckable(True)
        self.radioGroup.setChecked(True)
        radioLayout = QFormLayout()

        self.chkOrigHex = QCheckBox("Use original hex (-x)")
        radioLayout.addRow(self.chkOrigHex)

        self.chkDoubleWidth = QCheckBox("Double-width save blocks (-D)")
        radioLayout.addRow(self.chkDoubleWidth)

        self.chkPad = QCheckBox("Pad calls to 0x800 (-P)")
        radioLayout.addRow(self.chkPad)

        self.chkIntegral = QCheckBox("Integral disc (--integral)")
        self.chkIntegral.toggled.connect(self._onIntegralToggled)
        radioLayout.addRow(self.chkIntegral)

        self.chkLong = QCheckBox("Long call headers (--long)")
        radioLayout.addRow(self.chkLong)

        self.chkDebug = QCheckBox("Debug output (-v)")
        radioLayout.addRow(self.chkDebug)

        self.radioGroup.setLayout(radioLayout)
        layout.addWidget(self.radioGroup)

        # ── STAGE.DIR section ─────────────────────────────────────────────
        self.stageGroup = QGroupBox("STAGE.DIR")
        self.stageGroup.setCheckable(True)
        self.stageGroup.setChecked(bool(stageDirPath))
        stageLayout = QFormLayout()

        stageDirRow = QHBoxLayout()
        self.txtStageDir = QLineEdit(stageDirPath)
        self.txtStageDir.setPlaceholderText("Path to STAGE.DIR")
        stageDirRow.addWidget(self.txtStageDir)
        self.btnBrowseStage = QPushButton("Browse...")
        self.btnBrowseStage.clicked.connect(self._browseStageDir)
        stageDirRow.addWidget(self.btnBrowseStage)
        stageLayout.addRow("Input STAGE.DIR:", stageDirRow)

        self.stageGroup.setLayout(stageLayout)
        layout.addWidget(self.stageGroup)

        # ── DEMO section ──────────────────────────────────────────────────
        self.demoGroup = QGroupBox("DEMO.DAT")
        self.demoGroup.setCheckable(True)
        self.demoGroup.setChecked(True)
        demoLayout = QVBoxLayout()
        demoLayout.addWidget(QLabel("Compile JSON edits into DEMO.DAT binary."))
        self.demoGroup.setLayout(demoLayout)
        layout.addWidget(self.demoGroup)
        # Grey out STAGE.DIR unless Radio or Demo is checked
        self.radioGroup.toggled.connect(self._updateStageEnabled)
        self.demoGroup.toggled.connect(self._updateStageEnabled)
        self.stageGroup.toggled.connect(self._warnStageUnchecked)
        self._updateStageEnabled()

        # ── VOX section ───────────────────────────────────────────────────
        self.voxGroup = QGroupBox("VOX.DAT")
        self.voxGroup.setCheckable(True)
        self.voxGroup.setChecked(True)
        voxLayout = QVBoxLayout()
        voxLayout.addWidget(QLabel("Compile JSON edits into VOX.DAT binary."))
        self.voxGroup.setLayout(voxLayout)
        layout.addWidget(self.voxGroup)

        # ── ZMOVIE section ────────────────────────────────────────────────
        self.zmovieGroup = QGroupBox("ZMOVIE.STR")
        self.zmovieGroup.setCheckable(True)
        self.zmovieGroup.setChecked(True)
        zmovieLayout = QVBoxLayout()
        zmovieLayout.addWidget(QLabel("Compile JSON edits into ZMOVIE.STR binary."))
        self.zmovieGroup.setLayout(zmovieLayout)
        layout.addWidget(self.zmovieGroup)

        # ── Output folder ────────────────────────────────────────────────
        outGroup = QGroupBox("Output")
        outLayout = QVBoxLayout()

        outDirRow = QHBoxLayout()
        self.txtOutputDir = QLineEdit(defaultOutputDir)
        self.txtOutputDir.setPlaceholderText("(leave blank to write alongside originals)")
        outDirRow.addWidget(self.txtOutputDir)
        btnBrowseOut = QPushButton("Browse...")
        btnBrowseOut.clicked.connect(self._browseOutputDir)
        outDirRow.addWidget(btnBrowseOut)
        outLayout.addLayout(outDirRow)

        self.chkReplace = QCheckBox("Overwrite files in output folder on each build")
        self.chkReplace.setChecked(True)
        outLayout.addWidget(self.chkReplace)

        outGroup.setLayout(outLayout)
        layout.addWidget(outGroup)

        btnRow = QHBoxLayout()
        btnFinalize = QPushButton("Finalize")
        btnFinalize.setDefault(True)
        btnFinalize.clicked.connect(self.accept)
        btnRow.addWidget(btnFinalize)
        btnCancel = QPushButton("Cancel")
        btnCancel.clicked.connect(self.reject)
        btnRow.addWidget(btnCancel)
        layout.addLayout(btnRow)

        self.setLayout(layout)

    def _onIntegralToggled(self, checked):
        """Integral implies padding — force pad on when integral is checked."""
        if checked:
            self.chkPad.setChecked(True)
            self.chkPad.setEnabled(False)
        else:
            self.chkPad.setEnabled(True)

    def _updateStageEnabled(self):
        """Grey out STAGE.DIR section unless Radio or Demo is checked."""
        radioOn = self.radioGroup.isChecked()
        demoOn = self.demoGroup is not None and self.demoGroup.isChecked()
        self.stageGroup.setEnabled(radioOn or demoOn)

    def _warnStageUnchecked(self, checked):
        if not checked:
            QMessageBox.warning(self, "STAGE.DIR Disabled",
                "Disabling STAGE.DIR modifications will likely break the game "
                "if any VOX or RADIO offsets have changed.\n\n"
                "Only uncheck this if you know offsets are unchanged.")

    def _browseOutputDir(self):
        path = QFileDialog.getExistingDirectory(
            self, "Select Output Folder", self.txtOutputDir.text())
        if path:
            self.txtOutputDir.setText(path)

    def _browseStageDir(self):
        path = QFileDialog.getOpenFileName(
            self, "Select STAGE.DIR", self.txtStageDir.text(),
            "DIR Files (*.DIR *.dir);;All Files (*)"
        )[0]
        if path:
            self.txtStageDir.setText(path)

    # Convenience properties for reading results after exec()
    @property
    def radioEnabled(self): return self.radioGroup.isChecked()
    @property
    def demoEnabled(self): return self.demoGroup.isChecked()
    @property
    def stageEnabled(self): return self.stageGroup.isChecked() and self.stageGroup.isEnabled()
    @property
    def voxEnabled(self): return self.voxGroup.isChecked()
    @property
    def zmovieEnabled(self): return self.zmovieGroup.isChecked()
    @property
    def useOrigHex(self): return self.chkOrigHex.isChecked()
    @property
    def doubleWidth(self): return self.chkDoubleWidth.isChecked()
    @property
    def debugOutput(self): return self.chkDebug.isChecked()
    @property
    def pad(self): return self.chkPad.isChecked()
    @property
    def integral(self): return self.chkIntegral.isChecked()
    @property
    def longHeaders(self): return self.chkLong.isChecked()
    @property
    def stageDirPath(self): return self.txtStageDir.text().strip()
    @property
    def replaceOriginals(self): return self.chkReplace.isChecked()
    @property
    def outputDir(self): return self.txtOutputDir.text().strip()


class FontEditorDialog(QDialog):
    """Dialog for editing both variable-width ASCII and fixed 12x12 kana/kanji glyphs."""

    KANA_COLS = 22   # grid columns for kana tab
    ASCII_COLS = 16   # grid columns for ASCII tab
    SCALE = 4         # display scale (12px -> 48px)

    def __init__(self, tblMapping=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Font Editor")
        self.setMinimumSize(900, 600)

        from scripts.fontTools import mgsFontTools as MFT
        from scripts.fontTools import tblTools
        self._MFT = MFT
        self._tblTools = tblTools

        self._fontBlock = None                 # MFT.FontBlock when loaded
        self._tblMapping: dict[str, str] = tblMapping or tblTools.generateDefaultTbl()
        self._modifiedKanaSlots: set[int] = set()
        self._modifiedAsciiSlots: set[int] = set()
        self._selectedSection: str = "kana"    # "ascii" or "kana"
        self._selectedSlot: int = -1
        self._stageDirPath: str = ""
        self._kanaButtons: list[QPushButton] = []
        self._asciiButtons: list[QPushButton] = []

        self._buildUI()

    # ── property aliases for backward compat with MainWindow integration ──

    @property
    def tblMapping(self) -> dict[str, str]:
        return self._tblMapping

    @property
    def tblRaw(self) -> str:
        return self._tblTools.tblToString(self._tblMapping)

    # ── UI construction ──────────────────────────────────────────────────

    def _buildUI(self):
        from PySide6.QtWidgets import (QScrollArea, QWidget, QGridLayout,
            QSplitter, QTabWidget, QSpinBox, QSizePolicy)

        mainLayout = QVBoxLayout(self)

        # ── Top bar ──────────────────────────────────────────────────────
        topRow = QHBoxLayout()
        self.btnLoadStageDir = QPushButton("Load STAGE.DIR...")
        self.btnLoadStageDir.clicked.connect(self._loadStageDir)
        topRow.addWidget(self.btnLoadStageDir)
        self.btnLoadTbl = QPushButton("Load .tbl...")
        self.btnLoadTbl.clicked.connect(self._loadTbl)
        topRow.addWidget(self.btnLoadTbl)
        self.btnSaveTbl = QPushButton("Save .tbl...")
        self.btnSaveTbl.clicked.connect(self._saveTbl)
        topRow.addWidget(self.btnSaveTbl)
        topRow.addStretch()
        self._fontInfoLabel = QLabel("")
        topRow.addWidget(self._fontInfoLabel)
        mainLayout.addLayout(topRow)

        # ── Splitter: tabs on left, detail panel on right ────────────────
        splitter = QSplitter(Qt.Horizontal)

        # Tab widget for ASCII / Kana grids
        self._tabWidget = QTabWidget()

        # -- ASCII tab --
        asciiScroll = QScrollArea()
        asciiScroll.setWidgetResizable(True)
        asciiGridWidget = QWidget()
        self._asciiGridLayout = QGridLayout(asciiGridWidget)
        self._asciiGridLayout.setSpacing(2)
        asciiScroll.setWidget(asciiGridWidget)
        self._tabWidget.addTab(asciiScroll, "ASCII (96)")

        # -- Kana/Kanji tab --
        kanaScroll = QScrollArea()
        kanaScroll.setWidgetResizable(True)
        kanaGridWidget = QWidget()
        self._kanaGridLayout = QGridLayout(kanaGridWidget)
        self._kanaGridLayout.setSpacing(2)
        kanaScroll.setWidget(kanaGridWidget)
        self._tabWidget.addTab(kanaScroll, "Kana/Kanji")

        splitter.addWidget(self._tabWidget)

        # Detail panel
        detailWidget = QWidget()
        detailWidget.setFixedWidth(240)
        detailLayout = QVBoxLayout(detailWidget)

        self._previewLabel = QLabel()
        self._previewLabel.setFixedSize(120, 120)
        self._previewLabel.setAlignment(Qt.AlignCenter)
        self._previewLabel.setStyleSheet("border: 1px solid #555; background: black;")
        detailLayout.addWidget(self._previewLabel, alignment=Qt.AlignCenter)

        self._slotLabel = QLabel("Slot: —")
        detailLayout.addWidget(self._slotLabel)
        self._hexLabel = QLabel("Hex: —")
        detailLayout.addWidget(self._hexLabel)

        charRow = QHBoxLayout()
        charRow.addWidget(QLabel("Char:"))
        self._charEdit = QLineEdit()
        self._charEdit.setMaxLength(2)
        self._charEdit.setFixedWidth(60)
        self._charEdit.editingFinished.connect(self._onCharEdited)
        charRow.addWidget(self._charEdit)
        charRow.addStretch()
        detailLayout.addLayout(charRow)

        # Width spinner (ASCII only, hidden for kana)
        from PySide6.QtWidgets import QSpinBox
        self._widthRow = QHBoxLayout()
        self._widthLabel = QLabel("Width:")
        self._widthRow.addWidget(self._widthLabel)
        self._widthSpin = QSpinBox()
        self._widthSpin.setRange(1, 12)
        self._widthSpin.setFixedWidth(60)
        self._widthSpin.valueChanged.connect(self._onWidthChanged)
        self._widthRow.addWidget(self._widthSpin)
        self._widthRow.addStretch()
        detailLayout.addLayout(self._widthRow)
        self._widthLabel.setVisible(False)
        self._widthSpin.setVisible(False)

        self.btnImportPng = QPushButton("Import PNG...")
        self.btnImportPng.clicked.connect(self._importSinglePng)
        detailLayout.addWidget(self.btnImportPng)
        self.btnExportPng = QPushButton("Export PNG...")
        self.btnExportPng.clicked.connect(self._exportSinglePng)
        detailLayout.addWidget(self.btnExportPng)

        detailLayout.addStretch()
        splitter.addWidget(detailWidget)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        mainLayout.addWidget(splitter)

        # ── Bottom bar ───────────────────────────────────────────────────
        bottomRow = QHBoxLayout()
        btnExportAll = QPushButton("Export All Glyphs...")
        btnExportAll.clicked.connect(self._exportAll)
        bottomRow.addWidget(btnExportAll)
        btnImportFolder = QPushButton("Import Glyphs from Folder...")
        btnImportFolder.clicked.connect(self._importFolder)
        bottomRow.addWidget(btnImportFolder)
        bottomRow.addStretch()
        self.btnApply = QPushButton("Apply to STAGE.DIR...")
        self.btnApply.clicked.connect(self._applyToStageDir)
        self.btnApply.setEnabled(False)
        bottomRow.addWidget(self.btnApply)
        mainLayout.addLayout(bottomRow)

    # ── Grid building / refresh ──────────────────────────────────────────

    def _buildGrids(self):
        """Build glyph button grids sized to the loaded FontBlock."""
        from PySide6.QtGui import QIcon
        from PySide6.QtCore import QSize

        fb = self._fontBlock
        if not fb:
            return

        cellSize = 12 * self.SCALE + 8  # 56px

        # Clear old buttons
        for btn in self._asciiButtons:
            btn.deleteLater()
        self._asciiButtons.clear()
        for btn in self._kanaButtons:
            btn.deleteLater()
        self._kanaButtons.clear()

        # ASCII grid
        for i in range(fb.asciiCount):
            btn = QPushButton()
            btn.setFixedSize(cellSize, cellSize)
            btn.setToolTip(f"ASCII {i}")
            btn.clicked.connect(lambda checked=False, idx=i: self._selectAsciiSlot(idx))
            row, col = divmod(i, self.ASCII_COLS)
            self._asciiGridLayout.addWidget(btn, row, col)
            self._asciiButtons.append(btn)

        # Kana grid
        for i in range(fb.kanaCount):
            btn = QPushButton()
            btn.setFixedSize(cellSize, cellSize)
            btn.setToolTip(f"Slot {i}")
            btn.clicked.connect(lambda checked=False, idx=i: self._selectKanaSlot(idx))
            row, col = divmod(i, self.KANA_COLS)
            self._kanaGridLayout.addWidget(btn, row, col)
            self._kanaButtons.append(btn)

        # Update tab labels with actual counts
        self._tabWidget.setTabText(0, f"ASCII ({fb.asciiCount})")
        self._tabWidget.setTabText(1, f"Kana/Kanji ({fb.kanaCount})")

    def _refreshAsciiGrid(self):
        from PySide6.QtGui import QPixmap, QIcon
        from PySide6.QtCore import QSize

        fb = self._fontBlock
        if not fb:
            return

        iconSize = 12 * self.SCALE
        for i, btn in enumerate(self._asciiButtons):
            if i < len(fb.asciiGlyphs):
                w = fb.asciiPixelWidth(i)
                img = self._glyphToQImage(fb.asciiGlyphs[i], w)
                scaled = img.scaled(iconSize, iconSize, Qt.KeepAspectRatio, Qt.FastTransformation)
                btn.setIcon(QIcon(QPixmap.fromImage(scaled)))
                btn.setIconSize(QSize(iconSize, iconSize))

            char = chr(0x20 + i) if i < 95 else chr(0x7F)
            btn.setToolTip(f"ASCII {i} [{char!r}] w={fb.asciiPixelWidth(i)}")

            if i in self._modifiedAsciiSlots:
                btn.setStyleSheet("border: 2px solid #44aaff;")
            else:
                btn.setStyleSheet("")

    def _refreshKanaGrid(self):
        from PySide6.QtGui import QPixmap, QIcon
        from PySide6.QtCore import QSize

        fb = self._fontBlock
        if not fb:
            return

        iconSize = 12 * self.SCALE
        for i, btn in enumerate(self._kanaButtons):
            if i < len(fb.kanaGlyphs):
                img = self._glyphToQImage(fb.kanaGlyphs[i], self._MFT.KANA_GLYPH_WIDTH)
                scaled = img.scaled(iconSize, iconSize, Qt.KeepAspectRatio, Qt.FastTransformation)
                btn.setIcon(QIcon(QPixmap.fromImage(scaled)))
                btn.setIconSize(QSize(iconSize, iconSize))

            hexCode = self._tblTools.slotToHexCode(i)
            char = self._tblMapping.get(hexCode, "")
            btn.setToolTip(f"Slot {i} [{hexCode}] {char}")

            if i in self._modifiedKanaSlots:
                btn.setStyleSheet("border: 2px solid #44aaff;")
            else:
                btn.setStyleSheet("")

    def _refreshAllGrids(self):
        self._refreshAsciiGrid()
        self._refreshKanaGrid()

    def _glyphToQImage(self, data: bytes, width: int = 12):
        from PySide6.QtGui import QImage
        height = self._MFT.GLYPH_HEIGHT
        pixels = self._MFT.glyphToPixels(data, width, height)
        img = QImage(width, height, QImage.Format_Grayscale8)
        for y, row in enumerate(pixels):
            for x, val in enumerate(row):
                gray = self._MFT.PALETTE[val]
                img.setPixelColor(x, y, QColor(gray, gray, gray))
        return img

    # ── Slot selection ───────────────────────────────────────────────────

    def _selectAsciiSlot(self, idx):
        from PySide6.QtGui import QPixmap
        self._selectedSection = "ascii"
        self._selectedSlot = idx

        fb = self._fontBlock
        char = chr(0x20 + idx) if idx < 95 else chr(0x7F)
        self._slotLabel.setText(f"ASCII Slot: {idx}")
        self._hexLabel.setText(f"Char: {char!r}  (0x{0x20 + idx:02X})")
        self._charEdit.setText(char)
        self._charEdit.setEnabled(False)  # ASCII chars are fixed

        # Show width spinner
        self._widthLabel.setVisible(True)
        self._widthSpin.setVisible(True)
        if fb and idx < len(fb.asciiGlyphs):
            self._widthSpin.blockSignals(True)
            self._widthSpin.setValue(fb.asciiPixelWidth(idx))
            self._widthSpin.blockSignals(False)

        if fb and idx < len(fb.asciiGlyphs):
            w = fb.asciiPixelWidth(idx)
            img = self._glyphToQImage(fb.asciiGlyphs[idx], w)
            scaled = img.scaled(120, 120, Qt.KeepAspectRatio, Qt.FastTransformation)
            self._previewLabel.setPixmap(QPixmap.fromImage(scaled))

        # Highlight in ASCII grid
        for i, btn in enumerate(self._asciiButtons):
            if i == idx:
                btn.setStyleSheet("border: 2px solid #ffaa00;")
            elif i in self._modifiedAsciiSlots:
                btn.setStyleSheet("border: 2px solid #44aaff;")
            else:
                btn.setStyleSheet("")

    def _selectKanaSlot(self, idx):
        from PySide6.QtGui import QPixmap
        self._selectedSection = "kana"
        self._selectedSlot = idx

        hexCode = self._tblTools.slotToHexCode(idx)
        char = self._tblMapping.get(hexCode, "")
        self._slotLabel.setText(f"Kana Slot: {idx}")
        self._hexLabel.setText(f"Hex: {hexCode}")
        self._charEdit.setText(char)
        self._charEdit.setEnabled(True)

        # Hide width spinner (kana are fixed 12x12)
        self._widthLabel.setVisible(False)
        self._widthSpin.setVisible(False)

        fb = self._fontBlock
        if fb and idx < len(fb.kanaGlyphs):
            img = self._glyphToQImage(fb.kanaGlyphs[idx], self._MFT.KANA_GLYPH_WIDTH)
            scaled = img.scaled(120, 120, Qt.KeepAspectRatio, Qt.FastTransformation)
            self._previewLabel.setPixmap(QPixmap.fromImage(scaled))

        # Highlight in kana grid
        for i, btn in enumerate(self._kanaButtons):
            if i == idx:
                btn.setStyleSheet("border: 2px solid #ffaa00;")
            elif i in self._modifiedKanaSlots:
                btn.setStyleSheet("border: 2px solid #44aaff;")
            else:
                btn.setStyleSheet("")

    def _onCharEdited(self):
        if self._selectedSection != "kana" or self._selectedSlot < 0:
            return
        hexCode = self._tblTools.slotToHexCode(self._selectedSlot)
        newChar = self._charEdit.text()
        if newChar:
            self._tblMapping[hexCode] = newChar
        elif hexCode in self._tblMapping:
            del self._tblMapping[hexCode]

    def _onWidthChanged(self, newWidth):
        if self._selectedSection != "ascii" or self._selectedSlot < 0:
            return
        fb = self._fontBlock
        if not fb or self._selectedSlot >= len(fb.asciiGlyphs):
            return
        oldWidth = fb.asciiPixelWidth(self._selectedSlot)
        if newWidth == oldWidth:
            return
        # Recompute glyph data at new width (re-render from old pixel data)
        oldGlyph = fb.asciiGlyphs[self._selectedSlot]
        oldPixels = self._MFT.glyphToPixels(oldGlyph, oldWidth)
        # Crop or pad each row to new width
        newPixels = []
        for row in oldPixels:
            if newWidth <= len(row):
                newPixels.append(row[:newWidth])
            else:
                newPixels.append(row + [0] * (newWidth - len(row)))
        fb.asciiGlyphs[self._selectedSlot] = self._MFT.pixelsToGlyph(newPixels)
        # Update VFW byte to new pixel width (clears any flag bits)
        fb.asciiVfwBytes[self._selectedSlot] = newWidth
        self._modifiedAsciiSlots.add(self._selectedSlot)
        self._refreshAsciiGrid()
        self._selectAsciiSlot(self._selectedSlot)

    # ── File operations ──────────────────────────────────────────────────

    def _loadStageDir(self):
        path = QFileDialog.getOpenFileName(
            self, "Select STAGE.DIR", self._stageDirPath,
            "DIR Files (*.DIR *.dir);;All Files (*)"
        )[0]
        if not path:
            return
        try:
            self._fontBlock = self._MFT.loadFont(path)
            self._stageDirPath = path
            self._modifiedKanaSlots.clear()
            self._modifiedAsciiSlots.clear()
            self._selectedSlot = -1
            self._buildGrids()
            self._refreshAllGrids()
            self.btnApply.setEnabled(True)
            fb = self._fontBlock
            self._fontInfoLabel.setText(
                f"Font at 0x{fb.fileOffset:X}  |  "
                f"{fb.asciiCount} ASCII  |  {fb.kanaCount} kana/kanji  |  "
                f"{fb.totalSize} bytes")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to load font from STAGE.DIR:\n{e}")

    def _loadTbl(self):
        path = QFileDialog.getOpenFileName(
            self, "Load Table File", "", "Table Files (*.tbl);;All Files (*)"
        )[0]
        if not path:
            return
        try:
            self._tblMapping = self._tblTools.loadTbl(path)
            self._refreshKanaGrid()
            if self._selectedSection == "kana" and self._selectedSlot >= 0:
                self._selectKanaSlot(self._selectedSlot)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to load .tbl file:\n{e}")

    def _saveTbl(self):
        path = QFileDialog.getSaveFileName(
            self, "Save Table File", "font.tbl", "Table Files (*.tbl);;All Files (*)"
        )[0]
        if not path:
            return
        try:
            self._tblTools.saveTbl(path, self._tblMapping)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to save .tbl file:\n{e}")

    def _importSinglePng(self):
        if self._selectedSlot < 0 or not self._fontBlock:
            QMessageBox.information(self, "No Selection", "Load a STAGE.DIR and select a glyph slot first.")
            return
        path = QFileDialog.getOpenFileName(
            self, "Import PNG", "", "PNG Files (*.png);;All Files (*)"
        )[0]
        if not path:
            return
        try:
            from PIL import Image
            fb = self._fontBlock
            if self._selectedSection == "ascii":
                img = Image.open(path)
                w = img.width  # use image width as new character width
                fb.asciiGlyphs[self._selectedSlot] = self._MFT.imageToGlyph(img, w)
                fb.asciiVfwBytes[self._selectedSlot] = w  # update VFW to match
                self._modifiedAsciiSlots.add(self._selectedSlot)
                self._refreshAsciiGrid()
                self._selectAsciiSlot(self._selectedSlot)
            else:
                fb.kanaGlyphs[self._selectedSlot] = self._MFT.pngToGlyph(path)
                self._modifiedKanaSlots.add(self._selectedSlot)
                self._refreshKanaGrid()
                self._selectKanaSlot(self._selectedSlot)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to import PNG:\n{e}")

    def _exportSinglePng(self):
        if self._selectedSlot < 0 or not self._fontBlock:
            QMessageBox.information(self, "No Selection", "Load a STAGE.DIR and select a glyph slot first.")
            return
        fb = self._fontBlock
        if self._selectedSection == "ascii":
            defaultName = f"ascii-{self._selectedSlot:02d}.png"
        else:
            defaultName = f"glyph-{self._selectedSlot:03d}.png"
        path = QFileDialog.getSaveFileName(
            self, "Export PNG", defaultName, "PNG Files (*.png)"
        )[0]
        if not path:
            return
        try:
            if self._selectedSection == "ascii":
                w = fb.asciiPixelWidth(self._selectedSlot)
                self._MFT.glyphToPng(fb.asciiGlyphs[self._selectedSlot], path, w)
            else:
                self._MFT.glyphToPng(fb.kanaGlyphs[self._selectedSlot], path)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to export PNG:\n{e}")

    def _exportAll(self):
        if not self._fontBlock:
            QMessageBox.information(self, "No Data", "Load a STAGE.DIR first.")
            return
        folder = QFileDialog.getExistingDirectory(self, "Export All Glyphs To")
        if not folder:
            return
        try:
            fb = self._fontBlock
            for i, glyph in enumerate(fb.asciiGlyphs):
                w = fb.asciiPixelWidth(i)
                self._MFT.glyphToPng(glyph, os.path.join(folder, f"ascii-{i:02d}.png"), w)
            for i, glyph in enumerate(fb.kanaGlyphs):
                self._MFT.glyphToPng(glyph, os.path.join(folder, f"glyph-{i:03d}.png"))
            total = fb.asciiCount + fb.kanaCount
            QMessageBox.information(self, "Done",
                f"Exported {total} glyphs ({fb.asciiCount} ASCII + {fb.kanaCount} kana) to:\n{folder}")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to export glyphs:\n{e}")

    def _importFolder(self):
        if not self._fontBlock:
            QMessageBox.information(self, "No Data", "Load a STAGE.DIR first to establish baseline glyphs.")
            return
        folder = QFileDialog.getExistingDirectory(self, "Import Glyphs From Folder")
        if not folder:
            return
        try:
            fb = self._fontBlock
            # Import kana glyphs (glyph-NNN.png)
            kanaImported = self._MFT.importKanaFromFolder(folder, fb.kanaCount)
            for idx, data in kanaImported.items():
                fb.kanaGlyphs[idx] = data
                self._modifiedKanaSlots.add(idx)
            # Import ASCII glyphs (ascii-NN.png)
            asciiImported = self._MFT.importAsciiFromFolder(folder)
            for idx, data in asciiImported.items():
                fb.asciiGlyphs[idx] = data
                fb.asciiVfwBytes[idx] = fb.asciiPixelWidth(idx)  # update VFW to match
                self._modifiedAsciiSlots.add(idx)
            self._refreshAllGrids()
            total = len(kanaImported) + len(asciiImported)
            QMessageBox.information(self, "Done", f"Imported {total} glyphs.")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to import glyphs:\n{e}")

    def _applyToStageDir(self):
        if not self._fontBlock:
            return
        path = QFileDialog.getSaveFileName(
            self, "Save Modified STAGE.DIR",
            self._stageDirPath or "STAGE.DIR",
            "DIR Files (*.DIR *.dir);;All Files (*)"
        )[0]
        if not path:
            return
        try:
            self._MFT.injectFont(self._stageDirPath, path, self._fontBlock)
            self._modifiedKanaSlots.clear()
            self._modifiedAsciiSlots.clear()
            self._refreshAllGrids()
            QMessageBox.information(self, "Done", f"Font injected into:\n{path}")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to inject font:\n{e}")


class CallDictEditorDialog(QDialog):
    """Dialog for viewing/editing the per-entry custom graphics dictionary.

    Each entry (radio call, demo, vox, zmovie) can contain an array of
    36-byte tiles (12x12 pixels, 2bpp) that define custom characters for
    Japanese text rendering.  This dialog presents those tiles in a grid,
    lets the user import/export PNGs, and edit the character mapping.
    """

    SCALE = 4     # display scale (12px -> 48px)

    def __init__(self, entryKey: str, graphicsHex: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Call Dictionary Editor \u2014 {entryKey}")
        self.setMinimumSize(700, 450)

        from scripts.fontTools import mgsFontTools as MFT
        from scripts.translation import characters
        self._MFT = MFT
        self._characters = characters

        self._entryKey = entryKey
        self._graphicsHex = graphicsHex or ""
        self._tiles: list[bytes] = []       # list of 36-byte tile blobs
        self._tileButtons: list[QPushButton] = []
        self._modifiedSlots: set[int] = set()
        self._selectedSlot: int = -1
        self._currentCols: int = 0  # recalculated on resize

        self._parseTiles()
        self._buildUI()
        if self._tiles:
            self._buildGrid()
            self._refreshGrid()

    # ── Properties ───────────────────────────────────────────────────────

    @property
    def graphicsHex(self) -> str:
        """Return the (possibly modified) hex string of all tiles."""
        return "".join(t.hex() for t in self._tiles)

    @property
    def modified(self) -> bool:
        return len(self._modifiedSlots) > 0

    # ── Tile parsing ─────────────────────────────────────────────────────

    def _parseTiles(self):
        """Split the hex string into 36-byte (72 hex char) tile blobs."""
        raw = self._graphicsHex
        self._tiles = []
        if not raw:
            return
        # Each tile is 36 bytes = 72 hex characters
        for i in range(0, len(raw), 72):
            chunk = raw[i:i + 72]
            if len(chunk) < 72:
                break
            tile = bytes.fromhex(chunk)
            if tile == bytes(36):
                break  # null tile = end of dictionary
            self._tiles.append(tile)

    # ── UI construction ──────────────────────────────────────────────────

    def _buildUI(self):
        from PySide6.QtWidgets import QScrollArea, QWidget, QGridLayout, QSplitter

        mainLayout = QVBoxLayout(self)

        # ── Splitter: grid on left, detail panel on right ────────────────
        splitter = QSplitter(Qt.Horizontal)

        self._gridScroll = QScrollArea()
        self._gridScroll.setWidgetResizable(True)
        gridWidget = QWidget()
        self._gridLayout = QGridLayout(gridWidget)
        self._gridLayout.setHorizontalSpacing(5)
        self._gridLayout.setVerticalSpacing(8)
        self._gridScroll.setWidget(gridWidget)
        splitter.addWidget(self._gridScroll)

        # Detail panel
        detailWidget = QWidget()
        detailWidget.setFixedWidth(220)
        detailLayout = QVBoxLayout(detailWidget)

        detailLayout.addWidget(QLabel(f"<b>{self._entryKey}</b>"))
        self._tileCountLabel = QLabel(f"{len(self._tiles)} tiles")
        detailLayout.addWidget(self._tileCountLabel)

        self._previewLabel = QLabel()
        self._previewLabel.setFixedSize(120, 120)
        self._previewLabel.setAlignment(Qt.AlignCenter)
        self._previewLabel.setStyleSheet("border: 1px solid #555; background: black;")
        detailLayout.addWidget(self._previewLabel, alignment=Qt.AlignCenter)

        self._slotLabel = QLabel("Slot: \u2014")
        detailLayout.addWidget(self._slotLabel)
        self._invokeLabel = QLabel("Invoke: \u2014")
        detailLayout.addWidget(self._invokeLabel)
        self._hexLabel = QLabel("Tile hex: \u2014")
        self._hexLabel.setWordWrap(True)
        detailLayout.addWidget(self._hexLabel)

        charRow = QHBoxLayout()
        charRow.addWidget(QLabel("Char:"))
        self._charEdit = QLineEdit()
        self._charEdit.setMaxLength(2)
        self._charEdit.setFixedWidth(60)
        self._charEdit.setReadOnly(True)  # read-only for now; mapping comes from characters.py
        charRow.addWidget(self._charEdit)
        charRow.addStretch()
        detailLayout.addLayout(charRow)

        self.btnImportPng = QPushButton("Import PNG...")
        self.btnImportPng.clicked.connect(self._importSinglePng)
        detailLayout.addWidget(self.btnImportPng)
        self.btnExportPng = QPushButton("Export PNG...")
        self.btnExportPng.clicked.connect(self._exportSinglePng)
        detailLayout.addWidget(self.btnExportPng)

        detailLayout.addStretch()
        splitter.addWidget(detailWidget)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        mainLayout.addWidget(splitter)

        # ── Bottom bar ───────────────────────────────────────────────────
        bottomRow = QHBoxLayout()
        btnExportAll = QPushButton("Export All Tiles...")
        btnExportAll.clicked.connect(self._exportAll)
        bottomRow.addWidget(btnExportAll)
        btnImportFolder = QPushButton("Import Tiles from Folder...")
        btnImportFolder.clicked.connect(self._importFolder)
        bottomRow.addWidget(btnImportFolder)
        bottomRow.addStretch()
        btnBox = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btnBox.accepted.connect(self.accept)
        btnBox.rejected.connect(self.reject)
        bottomRow.addWidget(btnBox)
        mainLayout.addLayout(bottomRow)

    # ── Grid building / refresh ──────────────────────────────────────────

    _CELL_SIZE = 12 * 4 + 8  # 56px per button

    def _calcCols(self) -> int:
        """Calculate how many tile columns fit in the grid scroll area viewport."""
        vw = self._gridScroll.viewport().width()
        hSpacing = self._gridLayout.horizontalSpacing()
        cols = max(1, (vw + hSpacing) // (self._CELL_SIZE + hSpacing))
        return cols

    def _buildGrid(self):
        cellSize = self._CELL_SIZE

        for btn in self._tileButtons:
            btn.deleteLater()
        self._tileButtons.clear()

        cols = self._calcCols()
        self._currentCols = cols

        for i in range(len(self._tiles)):
            btn = QPushButton()
            btn.setFixedSize(cellSize, cellSize)
            btn.setToolTip(f"Tile {i}")
            btn.clicked.connect(lambda checked=False, idx=i: self._selectSlot(idx))
            row, col = divmod(i, cols)
            self._gridLayout.addWidget(btn, row, col)
            self._tileButtons.append(btn)

        self._tileCountLabel.setText(f"{len(self._tiles)} tiles")

    def _relayoutGrid(self):
        """Re-position existing buttons when column count changes on resize."""
        cols = self._calcCols()
        if cols == self._currentCols:
            return
        self._currentCols = cols
        for i, btn in enumerate(self._tileButtons):
            row, col = divmod(i, cols)
            self._gridLayout.addWidget(btn, row, col)

    def showEvent(self, event):
        super().showEvent(event)
        if self._tileButtons:
            # Force a relayout now that the viewport has its real geometry.
            self._currentCols = 0
            self._relayoutGrid()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._tileButtons:
            self._relayoutGrid()

    def _refreshGrid(self):
        from PySide6.QtGui import QPixmap, QIcon
        from PySide6.QtCore import QSize

        iconSize = 12 * self.SCALE
        for i, btn in enumerate(self._tileButtons):
            if i < len(self._tiles):
                img = self._tileToQImage(self._tiles[i])
                scaled = img.scaled(iconSize, iconSize, Qt.KeepAspectRatio, Qt.FastTransformation)
                btn.setIcon(QIcon(QPixmap.fromImage(scaled)))
                btn.setIconSize(QSize(iconSize, iconSize))

            char = self._lookupChar(i)
            invoke = self._invokeHex(i)
            btn.setToolTip(f"Tile {i} ({invoke}): {char}" if char else f"Tile {i} ({invoke})")

            if i in self._modifiedSlots:
                btn.setStyleSheet("border: 2px solid #44aaff;")
            elif i == self._selectedSlot:
                btn.setStyleSheet("border: 2px solid #ffaa00;")
            else:
                btn.setStyleSheet("")

    def _tileToQImage(self, data: bytes, width: int = 12):
        from PySide6.QtGui import QImage
        height = self._MFT.GLYPH_HEIGHT
        pixels = self._MFT.glyphToPixels(data, width, height)
        img = QImage(width, height, QImage.Format_Grayscale8)
        for y, row in enumerate(pixels):
            for x, val in enumerate(row):
                gray = self._MFT.PALETTE[val]
                img.setPixelColor(x, y, QColor(gray, gray, gray))
        return img

    def _lookupChar(self, slot: int) -> str:
        """Look up the Unicode character for a tile via characters.graphicsData."""
        if slot < 0 or slot >= len(self._tiles):
            return ""
        hexStr = self._tiles[slot].hex()
        return self._characters.graphicsData.get(hexStr, "")

    @staticmethod
    def _invokeHex(slot: int) -> str:
        """Return the 2-byte hex code used to reference this tile in subtitle text.
        Slot 0 → index 1.  0x96 XX for 1-254, 0x97 XX for 255-508, 0x98 XX for 509+."""
        index = slot + 1
        if index > 508:
            return f"0x98{index - 508:02X}"
        elif index > 254:
            return f"0x97{index - 254:02X}"
        else:
            return f"0x96{index:02X}"

    # ── Slot selection ───────────────────────────────────────────────────

    def _selectSlot(self, idx: int):
        from PySide6.QtGui import QPixmap
        self._selectedSlot = idx

        char = self._lookupChar(idx)
        self._slotLabel.setText(f"Slot: {idx}")
        self._invokeLabel.setText(f"Invoke: {self._invokeHex(idx)}")
        tileHex = self._tiles[idx].hex() if idx < len(self._tiles) else ""
        # Show truncated hex to avoid overflowing the panel
        displayHex = tileHex[:36] + "\u2026" if len(tileHex) > 36 else tileHex
        self._hexLabel.setText(f"Tile hex: {displayHex}")
        self._charEdit.setText(char)

        if idx < len(self._tiles):
            img = self._tileToQImage(self._tiles[idx])
            scaled = img.scaled(120, 120, Qt.KeepAspectRatio, Qt.FastTransformation)
            self._previewLabel.setPixmap(QPixmap.fromImage(scaled))

        # Update grid highlights
        for i, btn in enumerate(self._tileButtons):
            if i == idx:
                btn.setStyleSheet("border: 2px solid #ffaa00;")
            elif i in self._modifiedSlots:
                btn.setStyleSheet("border: 2px solid #44aaff;")
            else:
                btn.setStyleSheet("")

    # ── Import / Export ──────────────────────────────────────────────────

    def _importSinglePng(self):
        if self._selectedSlot < 0 or self._selectedSlot >= len(self._tiles):
            QMessageBox.information(self, "No Selection", "Select a tile slot first.")
            return
        path = QFileDialog.getOpenFileName(
            self, "Import PNG", "", "PNG Files (*.png);;All Files (*)"
        )[0]
        if not path:
            return
        try:
            newGlyph = self._MFT.pngToGlyph(path)
            self._tiles[self._selectedSlot] = newGlyph
            self._modifiedSlots.add(self._selectedSlot)
            self._refreshGrid()
            self._selectSlot(self._selectedSlot)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to import PNG:\n{e}")

    def _exportSinglePng(self):
        if self._selectedSlot < 0 or self._selectedSlot >= len(self._tiles):
            QMessageBox.information(self, "No Selection", "Select a tile slot first.")
            return
        defaultName = f"{self._entryKey}-tile-{self._selectedSlot:02d}.png"
        path = QFileDialog.getSaveFileName(
            self, "Export PNG", defaultName, "PNG Files (*.png)"
        )[0]
        if not path:
            return
        try:
            self._MFT.glyphToPng(self._tiles[self._selectedSlot], path)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to export PNG:\n{e}")

    def _exportAll(self):
        if not self._tiles:
            QMessageBox.information(self, "No Data", "No tiles to export.")
            return
        folder = QFileDialog.getExistingDirectory(self, "Export All Tiles To")
        if not folder:
            return
        try:
            for i, tile in enumerate(self._tiles):
                self._MFT.glyphToPng(tile, os.path.join(folder, f"{self._entryKey}-tile-{i:02d}.png"))
            QMessageBox.information(self, "Done", f"Exported {len(self._tiles)} tiles to:\n{folder}")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to export tiles:\n{e}")

    def _importFolder(self):
        if not self._tiles:
            QMessageBox.information(self, "No Data", "No tiles loaded to replace.")
            return
        folder = QFileDialog.getExistingDirectory(self, "Import Tiles From Folder")
        if not folder:
            return
        try:
            imported = 0
            for i in range(len(self._tiles)):
                pngPath = os.path.join(folder, f"{self._entryKey}-tile-{i:02d}.png")
                if os.path.isfile(pngPath):
                    self._tiles[i] = self._MFT.pngToGlyph(pngPath)
                    self._modifiedSlots.add(i)
                    imported += 1
            self._refreshGrid()
            QMessageBox.information(self, "Done", f"Imported {imported} tiles.")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to import tiles:\n{e}")


class SubtitleTableWidget(QTableWidget):
    """Drop-in replacement for subsPreviewList.
    Displays subtitles as a table with Original and Edited columns.

    Columns:
      0 – #          (index, fixed narrow)
      1 – Original   (read-only original text)
      2 – Edited     (current / edited text)
      3 – ✓          (bullet marker for modified entries, narrow)

    Compatible with the QListWidget API subset used by MainWindow:
      clear(), count(), currentRow(), setCurrentRow(),
      currentItemChanged signal (emulated via currentCellChanged).
    """

    COL_IDX  = 0
    COL_SUB  = 1
    COL_EDIT = 2

    currentItemChanged = Signal(object, object)

    def __init__(self, parent=None):
        super().__init__(0, 3, parent)

        self.setHorizontalHeaderLabels(["#", "Subtitle", "Edited"])
        hdr = self.horizontalHeader()
        hdr.setSectionResizeMode(self.COL_IDX,  QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(self.COL_SUB,  QHeaderView.Stretch)
        hdr.setSectionResizeMode(self.COL_EDIT, QHeaderView.Stretch)
        hdr.setHighlightSections(False)
        hdr.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        self.verticalHeader().setVisible(False)

        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setAlternatingRowColors(True)
        self.setShowGrid(True)
        self.setGridStyle(Qt.SolidLine)
        self.setWordWrap(True)

        self.setStyleSheet("""
            QTableWidget::item {
                padding: 3px 6px;
            }
        """)

        self.currentCellChanged.connect(self._onCellChanged)

    # ── Compat API ────────────────────────────────────────────────────────

    def clear(self):
        self.setRowCount(0)

    def count(self) -> int:
        return self.rowCount()

    def currentRow(self) -> int:
        return super().currentRow()

    def setCurrentRow(self, row: int):
        if 0 <= row < self.rowCount():
            self.selectRow(row)
        elif self.rowCount() == 0:
            self.clearSelection()

    # ── Population ────────────────────────────────────────────────────────

    def addSubtitleRow(self, index: int, text: str, editedText: str = ""):
        """Append a subtitle row.

        Args:
            index:      display index (0-based)
            text:       the subtitle text (always shown)
            editedText: edited version (shown only when entry has been modified)
        """
        row = self.rowCount()
        self.insertRow(row)

        idxItem  = QTableWidgetItem(str(index + 1))
        subItem  = QTableWidgetItem(text.replace("｜", "\n"))
        editItem = QTableWidgetItem(editedText.replace("｜", "\n") if editedText else "")

        idxItem.setTextAlignment(Qt.AlignCenter)
        subItem.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        editItem.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        for item in (idxItem, subItem, editItem):
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)

        self.setItem(row, self.COL_IDX,  idxItem)
        self.setItem(row, self.COL_SUB,  subItem)
        self.setItem(row, self.COL_EDIT, editItem)

        self.resizeRowToContents(row)

    def updateRowEditText(self, row: int, text: str):
        if 0 <= row < self.rowCount():
            it = self.item(row, self.COL_EDIT)
            if it:
                it.setText(text.replace("｜", "\n"))
            self.resizeRowToContents(row)

    # ── Internal ──────────────────────────────────────────────────────────

    def _onCellChanged(self, curRow, _curCol, prevRow, _prevCol):
        cur  = self.item(curRow,  0) if curRow  >= 0 else None
        prev = self.item(prevRow, 0) if prevRow >= 0 else None
        self.currentItemChanged.emit(cur, prev)


class MainWindow(QMainWindow):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.ui = Ui_MainWindow()
        self.ui.setupUi(self)

        # App icon
        iconPath = os.path.join(os.path.dirname(__file__), "icon.png")
        if os.path.exists(iconPath):
            from PySide6.QtGui import QIcon
            self.setWindowIcon(QIcon(iconPath))

        # Editor mode: "radio" or "demo"
        self._editorMode = "radio"

        # ── File Menu (clear and rebuild) ────────────────────────────────────
        self.ui.menuFile.clear()

        # ── Edit Menu (clear and rebuild — .ui has placeholder items) ────────
        self.ui.menuEdit.clear()
        self._appSettings = QSettings("MGS-Undubbed", "DialogueEditor")
        actionPreferences = QAction("Preferences...", self)
        actionPreferences.triggered.connect(self._openPreferences)
        self.ui.menuEdit.addAction(actionPreferences)

        # ── Replace offset combo box with persistent list widget ─────────────
        oldCombo = self.ui.offsetListBox
        parentLayout = oldCombo.parentWidget().layout()
        comboIdx = parentLayout.indexOf(oldCombo)
        oldCombo.setVisible(False)

        offsetRow = QHBoxLayout()
        self.ui.offsetListBox = OffsetListWidget(self)
        self.ui.offsetListBox.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.ui.offsetListBox.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        offsetRow.addWidget(self.ui.offsetListBox, stretch=1)

        # Right side: freq filter, prev, next — stacked, vertically centered
        rightCol = QVBoxLayout()
        rightCol.addStretch()
        freqRow = QHBoxLayout()
        self.freqFilterLabel = QLabel("Freq:")
        self.freqFilterLabel.setVisible(True)
        self.freqFilterCombo = QComboBox()
        self.freqFilterCombo.setVisible(True)
        freqRow.addWidget(self.freqFilterLabel)
        freqRow.addWidget(self.freqFilterCombo)
        rightCol.addLayout(freqRow)
        self.btnPrevEntry = QPushButton("▲ Prev")
        self.btnPrevEntry.setToolTip("Previous entry (Cmd+Up)")
        self.btnPrevEntry.setShortcut(QKeySequence(Qt.CTRL | Qt.Key_Up))
        self.btnPrevEntry.clicked.connect(lambda: self._navigateEntry(-1))
        self.btnNextEntry = QPushButton("▼ Next")
        self.btnNextEntry.setToolTip("Next entry (Cmd+Down)")
        self.btnNextEntry.setShortcut(QKeySequence(Qt.CTRL | Qt.Key_Down))
        self.btnNextEntry.clicked.connect(lambda: self._navigateEntry(1))
        rightCol.addWidget(self.btnPrevEntry)
        rightCol.addWidget(self.btnNextEntry)
        self.callDictButton = QPushButton("Edit Dictionary")
        self.callDictButton.setToolTip("Edit this entry's custom character tiles (Cmd+D)")
        self.callDictButton.setEnabled(False)
        self.callDictButton.clicked.connect(self._openCallDictEditor)
        rightCol.addWidget(self.callDictButton)
        self.autoTranslateButton = QPushButton("Auto-translate")
        self.autoTranslateButton.setToolTip(
            "Translate, auto-format, and apply all subtitles\n"
            "in the current entry using Preferences language settings")
        self.autoTranslateButton.setEnabled(False)
        self.autoTranslateButton.clicked.connect(self._autoTranslateAll)
        rightCol.addWidget(self.autoTranslateButton)
        rightCol.addStretch()
        offsetRow.addLayout(rightCol, stretch=1)

        parentLayout.insertLayout(comboIdx + 1, offsetRow)

        # ── Replace subsPreviewList with SubtitleTableWidget ─────────────��──
        oldSubsList = self.ui.subsPreviewList
        subsLayout = oldSubsList.parentWidget().layout()
        oldSubsList.setVisible(False)
        oldSubsList.setParent(None)
        self.ui.subsPreviewList = SubtitleTableWidget(self)

        # ── Lock offset area size, put vox + subtitle lists in a splitter ──
        # Remove audioCueListView from the vertical layout
        from PySide6.QtWidgets import QSplitter
        audioCueIdx = subsLayout.indexOf(self.ui.audioCueListView)
        subsLayout.removeWidget(self.ui.audioCueListView)

        # Fix the call offset area so it doesn't expand with the window
        self.ui.offsetListBox.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        self.ui.offsetListBox.setMaximumHeight(180)

        # Vertical splitter: audioCueListView (top) + subsPreviewList (bottom)
        self._listSplitter = QSplitter(Qt.Vertical)
        self._listSplitter.addWidget(self.ui.audioCueListView)
        self._listSplitter.addWidget(self.ui.subsPreviewList)
        self._listSplitter.setStretchFactor(0, 1)
        self._listSplitter.setStretchFactor(1, 1)
        subsLayout.insertWidget(audioCueIdx, self._listSplitter)

        # Give all extra vertical space to the splitter, not the offset area
        for i in range(subsLayout.count()):
            item = subsLayout.itemAt(i)
            if item and item.widget() is self._listSplitter:
                subsLayout.setStretch(i, 1)
            else:
                subsLayout.setStretch(i, 0)

        # ── Empty-state hint (shown until data is loaded) ─────────────────────
        self._emptyHint = QLabel(
            "Open a folder or project to begin\n\n"
            "File \u2192 Open Folder... (Cmd+O)\n"
            "File \u2192 Open Project... (Cmd+P)")
        self._emptyHint.setAlignment(Qt.AlignCenter)
        self._emptyHint.setStyleSheet("color: #888; padding: 20px;")
        self._emptyHint.setWordWrap(True)
        subsLayout.insertWidget(0, self._emptyHint)

        # ── Navigation ───────────────────────────────────────────────────────
        self.ui.offsetListBox.currentIndexChanged.connect(self.selectCallOffset)
        self.ui.audioCueListView.currentItemChanged.connect(self.selectAudioCue)
        self.ui.subsPreviewList.currentItemChanged.connect(self.subtitleSelect)

        # ── Audio ────────────────────────────────────────────────────────────
        self.ui.playVoxButton.clicked.connect(self.playVoxFile)

        # ── Edit buttons (added programmatically) ────────────────────────────
        self._addEditButtons()

        # ── Offset list filter checkboxes (inserted above offsetListBox) ──────
        labelIdx = self.ui.verticalLayout.indexOf(self.ui.labelCallOffset)

        self.chkDisc1Only = QCheckBox("This disc only (hide missing audio)")
        self.chkDisc1Only.setChecked(False)
        self.chkDisc1Only.setVisible(False)
        self.chkDisc1Only.toggled.connect(self._populateRadioOffsets)
        self.ui.verticalLayout.insertWidget(labelIdx + 2, self.chkDisc1Only)

        self.chkUnclaimedVox = QCheckBox("Show unclaimed clips only")
        self.chkUnclaimedVox.setChecked(False)
        self.chkUnclaimedVox.setVisible(False)
        self.chkUnclaimedVox.toggled.connect(self._populateVoxOffsets)
        self.ui.verticalLayout.insertWidget(labelIdx + 3, self.chkUnclaimedVox)

        self.chkSkipVoxSort = QCheckBox("Skip VOX sorting")
        self.chkSkipVoxSort.setToolTip(
            "Show all subtitles from the selected call at once,\n"
            "instead of grouping them by VOX cue.")
        self.chkSkipVoxSort.setChecked(False)
        self.chkSkipVoxSort.setVisible(False)
        self.chkSkipVoxSort.toggled.connect(self._onSkipVoxSortToggled)
        self.ui.verticalLayout.insertWidget(labelIdx + 4, self.chkSkipVoxSort)

        # (frequency filter is now in the nav column beside the offset list)
        self.freqFilterCombo.currentIndexChanged.connect(self._populateRadioOffsets)

        # (nav buttons are now beside the offset list widget above)

        # ── Build File menu from scratch ──────────────────────────────────────
        self.actionOpenFolder = QAction("Open Folder...", self)
        self.actionOpenFolder.setStatusTip("Find and load RADIO.DAT, DEMO.DAT, VOX.DAT, ZMOVIE.STR from a folder")
        self.actionOpenFolder.setShortcut("Ctrl+O")
        self.actionOpenFolder.triggered.connect(self.openFolder)

        self.actionOpenProject = QAction("Open Project (.mtp)...", self)
        self.actionOpenProject.setStatusTip("Open a saved MTP project file")
        self.actionOpenProject.setShortcut("Ctrl+P")
        self.actionOpenProject.triggered.connect(self.openProject)

        self.actionSaveProject = QAction("Save Project", self)
        self.actionSaveProject.setStatusTip("Save the current project to its .mtp file")
        self.actionSaveProject.setShortcut("Ctrl+S")
        self.actionSaveProject.setEnabled(True)
        self.actionSaveProject.triggered.connect(self.saveProject)

        self.actionSaveProjectAs = QAction("Save Project As...", self)
        self.actionSaveProjectAs.setStatusTip("Save the current project to a new .mtp file")
        self.actionSaveProjectAs.setShortcut(QKeySequence(Qt.CTRL | Qt.SHIFT | Qt.Key_S))
        self.actionSaveProjectAs.triggered.connect(self.saveProjectAs)

        self.actionCloseProject = QAction("Close Project", self)
        self.actionCloseProject.setStatusTip("Close the current project and reset to initial state")
        self.actionCloseProject.setShortcut(QKeySequence(Qt.CTRL | Qt.Key_W))
        self.actionCloseProject.triggered.connect(self.closeProject)

        self.actionFinalizeProject = QAction("Finalize Project...", self)
        self.actionFinalizeProject.setStatusTip("Batch-compile all game data files")
        self.actionFinalizeProject.triggered.connect(self.finalizeProject)

        actionQuit = QAction("Quit", self)
        actionQuit.setShortcut("Ctrl+Q")
        actionQuit.triggered.connect(self.close)

        self.ui.menuFile.addAction(self.actionOpenFolder)
        self.ui.menuFile.addAction(self.actionOpenProject)
        self.ui.menuFile.addSeparator()
        self.ui.menuFile.addAction(self.actionSaveProject)
        self.ui.menuFile.addAction(self.actionSaveProjectAs)
        self.ui.menuFile.addAction(self.actionCloseProject)
        self.ui.menuFile.addSeparator()
        self.ui.menuFile.addAction(self.actionFinalizeProject)
        self.ui.menuFile.addSeparator()
        self.ui.menuFile.addAction(actionQuit)

        # ── Keyboard shortcuts ────────────────────────────────────────────────
        self.ui.playVoxButton.setShortcut(QKeySequence(Qt.CTRL | Qt.Key_Space))

        # ── Bottom-right GUI buttons (remove redundant Quit, add actions) ────
        from PySide6.QtWidgets import QFrame
        self.ui.quitButton.setVisible(False)
        quitIdx = self.ui.verticalLayout_4.indexOf(self.ui.quitButton)

        btnOpenFolder = QPushButton("Open Folder...")
        btnOpenFolder.clicked.connect(self.openFolder)
        self.ui.verticalLayout_4.insertWidget(quitIdx, btnOpenFolder)

        btnOpenProject = QPushButton("Open Project (.mtp)...")
        btnOpenProject.clicked.connect(self.openProject)
        self.ui.verticalLayout_4.insertWidget(quitIdx + 1, btnOpenProject)

        btnFinalize = QPushButton("Finalize Project...")
        btnFinalize.clicked.connect(self.finalizeProject)
        self.ui.verticalLayout_4.insertWidget(quitIdx + 2, btnFinalize)

        separatorLine = QFrame()
        separatorLine.setFrameShape(QFrame.HLine)
        separatorLine.setFrameShadow(QFrame.Sunken)
        self.ui.verticalLayout_4.insertWidget(quitIdx + 3, separatorLine)

        # ── Radio static fields buttons (radio-mode only) ────────────────────
        self.btnEditPrompts = QPushButton("Edit Prompts...")
        self.btnEditPrompts.setToolTip("Edit save prompts (ASK_USER)")
        self.btnEditPrompts.setEnabled(False)
        self.btnEditPrompts.clicked.connect(lambda: self._openStaticFieldsDialog(0))
        self.ui.verticalLayout_4.insertWidget(quitIdx + 4, self.btnEditPrompts)

        self.btnEditSaveLocations = QPushButton("Edit Save Locations...")
        self.btnEditSaveLocations.setToolTip("Edit save location names (MEM_SAVE)")
        self.btnEditSaveLocations.setEnabled(False)
        self.btnEditSaveLocations.clicked.connect(lambda: self._openStaticFieldsDialog(1))
        self.ui.verticalLayout_4.insertWidget(quitIdx + 5, self.btnEditSaveLocations)

        self.btnEditContactNames = QPushButton("Edit Contact Names...")
        self.btnEditContactNames.setToolTip("Edit codec contact names (ADD_FREQ)")
        self.btnEditContactNames.setEnabled(False)
        self.btnEditContactNames.clicked.connect(lambda: self._openStaticFieldsDialog(2))
        self.ui.verticalLayout_4.insertWidget(quitIdx + 6, self.btnEditContactNames)

        # ── View menu (4 mutually exclusive mode actions) ─────────────────────
        from PySide6.QtGui import QActionGroup
        viewMenu = self.menuBar().addMenu("View")
        self._modeGroup = QActionGroup(self)
        self._modeGroup.setExclusive(True)

        self.actionRadioMode = QAction("Radio Mode", self)
        self.actionRadioMode.setCheckable(True)
        self.actionRadioMode.setChecked(True)
        self.actionRadioMode.setStatusTip("Switch to Radio codec call editor")
        self._modeGroup.addAction(self.actionRadioMode)
        viewMenu.addAction(self.actionRadioMode)

        self.actionDemoMode = QAction("Demo Mode", self)
        self.actionDemoMode.setCheckable(True)
        self.actionDemoMode.setStatusTip("Switch to Demo/cutscene subtitle editor")
        self._modeGroup.addAction(self.actionDemoMode)
        viewMenu.addAction(self.actionDemoMode)

        self.actionVoxMode = QAction("VOX Mode", self)
        self.actionVoxMode.setCheckable(True)
        self.actionVoxMode.setStatusTip("Switch to VOX audio subtitle editor")
        self._modeGroup.addAction(self.actionVoxMode)
        viewMenu.addAction(self.actionVoxMode)

        self.actionZmovieMode = QAction("ZMovie Mode", self)
        self.actionZmovieMode.setCheckable(True)
        self.actionZmovieMode.setStatusTip("Switch to ZMovie FMV subtitle editor")
        self._modeGroup.addAction(self.actionZmovieMode)
        viewMenu.addAction(self.actionZmovieMode)

        self._modeGroup.triggered.connect(self._onModeChanged)

        # ── Tools menu ──────────────────────────────────────────────────────
        toolsMenu = self.menuBar().addMenu("Tools")
        self.actionFontEditor = QAction("Font Editor...", self)
        self.actionFontEditor.setStatusTip("Extract, edit, and inject game font glyphs")
        self.actionFontEditor.triggered.connect(self._openFontEditor)
        toolsMenu.addAction(self.actionFontEditor)

        self.actionCallDictEditor = QAction("Call Dictionary Editor...", self)
        self.actionCallDictEditor.setStatusTip("View and edit per-entry custom character tiles")
        self.actionCallDictEditor.setShortcut(QKeySequence("Ctrl+D"))
        self.actionCallDictEditor.triggered.connect(self._openCallDictEditor)
        toolsMenu.addAction(self.actionCallDictEditor)

        # ── Mode tab bar ───────────────────────────────────────────────────────
        from PySide6.QtWidgets import QToolBar, QTabBar
        self._modeToolBar = QToolBar("Editor Mode", self)
        self._modeToolBar.setMovable(False)
        self._modeToolBar.setFloatable(False)
        spacerL = QWidget()
        spacerL.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._modeToolBar.addWidget(spacerL)
        self._modeTabBar = QTabBar()
        self._modeTabBar.setExpanding(False)
        self._modeTabBar.addTab("RADIO")
        self._modeTabBar.addTab("DEMO")
        self._modeTabBar.addTab("VOX")
        self._modeTabBar.addTab("ZMOVIE")
        self._modeToolBar.addWidget(self._modeTabBar)
        spacerR = QWidget()
        spacerR.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._modeToolBar.addWidget(spacerR)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, self._modeToolBar)
        self._modeTabBar.currentChanged.connect(self._onTabChanged)

        # Internal flag to suppress spinbox signals while loading data
        self._loadingSubtitle = False
        # True whenever there are unsaved edits (either mode)
        self._modified = False

        # Playback threads (None when idle)
        self._convThread: VoxConversionThread = None
        self._playThread: FfplayThread = None

        # Elapsed timer for subtitle sync — started when ffplay launches
        self._elapsed = QElapsedTimer()

        # ── Subtitle preview (graphicsView) ──────────────────────────────────
        self._setupSubtitlePreview()

        # Frame timer — ticks at ~30 fps to drive subtitle sync
        self._frameTimer = QTimer(self)
        self._frameTimer.setInterval(33)
        self._frameTimer.timeout.connect(self._tickPreview)

    # ── UI additions ─────────────────────────────────────────────────────────

    def _addEditButtons(self):
        """Adds editing buttons to the right of the timing fields."""
        # ── Reparent timing widgets into a horizontal layout ──────────────
        # Remove timing labels + spinboxes from the vertical layout so we
        # can place them side-by-side with a button column.
        for w in (self.ui.labelStartFrame, self.ui.startFrameBox,
                  self.ui.labelDuration, self.ui.durationBox):
            self.ui.verticalLayout_2.removeWidget(w)

        timingCol = QVBoxLayout()
        timingCol.addWidget(self.ui.labelStartFrame)
        timingCol.addWidget(self.ui.startFrameBox)
        timingCol.addWidget(self.ui.labelDuration)
        timingCol.addWidget(self.ui.durationBox)
        timingCol.addStretch()

        # ── Button column (right side) ────────────────────────────────────
        btnCol = QVBoxLayout()

        self.applyEditButton = QPushButton("Apply Edit")
        self.applyEditButton.setToolTip("Save text and timing changes (Cmd+Enter)")
        self.applyEditButton.setEnabled(False)
        self.applyEditButton.setShortcut(QKeySequence(Qt.CTRL | Qt.Key_Return))
        self.applyEditButton.clicked.connect(self.applyEdit)
        btnCol.addWidget(self.applyEditButton)

        self.splitSubButton = QPushButton("Split Subtitle")
        self.splitSubButton.setToolTip("Split this subtitle in two, halving the display duration")
        self.splitSubButton.setEnabled(False)
        self.splitSubButton.clicked.connect(self.splitSubtitle)
        btnCol.addWidget(self.splitSubButton)

        self.deleteSubButton = QPushButton("Delete Subtitle")
        self.deleteSubButton.setToolTip("Remove this subtitle from the call")
        self.deleteSubButton.setEnabled(False)
        self.deleteSubButton.clicked.connect(self.deleteSubtitle)
        btnCol.addWidget(self.deleteSubButton)

        self.revertVoxButton = QPushButton("Revert to Original")
        self.revertVoxButton.setToolTip("Discard changes and restore the original VOX entry")
        self.revertVoxButton.setVisible(False)
        self.revertVoxButton.clicked.connect(self._revertVoxEntry)
        btnCol.addWidget(self.revertVoxButton)

        self.translateButton = QPushButton("Translate")
        self.translateButton.setToolTip("Translate the current line (Cmd+T)")
        self.translateButton.setEnabled(False)
        self.translateButton.setShortcut("Ctrl+T")
        self.translateButton.clicked.connect(self._translateLine)
        btnCol.addWidget(self.translateButton)

        self.autoFormatButton = QPushButton("Auto-format")
        self.autoFormatButton.setToolTip("Re-wrap text with pixel-accurate MGS1 line breaks (Cmd+Shift+F)")
        self.autoFormatButton.setEnabled(False)
        self.autoFormatButton.setShortcut(QKeySequence(Qt.CTRL | Qt.SHIFT | Qt.Key_F))
        self.autoFormatButton.clicked.connect(self._autoFormatLine)
        btnCol.addWidget(self.autoFormatButton)

        btnCol.addStretch()

        # ── Combine timing + buttons in a horizontal row ──────────────────
        bottomRow = QHBoxLayout()
        bottomRow.addLayout(timingCol)
        bottomRow.addLayout(btnCol)
        self.ui.verticalLayout_2.addLayout(bottomRow)

        # ── Stop button — inserted into the StatusBar row next to Play ────────
        self.stopVoxButton = QPushButton("Stop")
        self.stopVoxButton.setToolTip("Stop audio playback")
        self.stopVoxButton.setEnabled(False)
        self.stopVoxButton.clicked.connect(self.stopVoxFile)
        play_idx = self.ui.horizontalLayout_2.indexOf(self.ui.playVoxButton)
        self.ui.horizontalLayout_2.insertWidget(play_idx + 1, self.stopVoxButton)

        # Fix UI bugs in generated form: base-9 display and missing max
        self.ui.startFrameBox.setDisplayIntegerBase(10)
        self.ui.durationBox.setMaximum(99999999)
        self.ui.durationBox.setDisplayIntegerBase(10)

        # Timing change signals — mark unsaved changes
        self.ui.startFrameBox.valueChanged.connect(self._onTimingChanged)
        self.ui.durationBox.valueChanged.connect(self._onTimingChanged)

    # ── File operations ───────────────────────────────────────────────────────

    def openFileDialog(self, fileTypes: str, title: str = "Open File") -> str:
        dialog = XmlFileDialog(self)
        dialog.setNameFilter(fileTypes)
        dialog.setWindowTitle(title)
        if dialog.exec_() == QFileDialog.Accepted:
            selected = dialog.selectedFiles()
            if selected:
                return selected[0]
        return None

    def loadRadioDatFile(self):
        print("Not implemented yet — load DAT and parse to XML first")

    def loadRadioXMLFile(self):
        filename = self.openFileDialog("XML Files (*.xml)", "Load Radio.XML")
        if not filename:
            return
        radioManager.loadRadioXmlFile(filename)
        # Extract per-call graphics dictionaries from XML
        global radioGraphicsJson
        radioGraphicsJson = {}
        if radioManager.radioXMLData is not None:
            for call in radioManager.radioXMLData.findall(".//Call"):
                gfxHex = call.get("graphicsBytes", "")
                if gfxHex:
                    radioGraphicsJson[call.get("offset")] = gfxHex
        self._buildRadioVoxIndex()
        self._populateRadioOffsets()
        if projectFilePath:
            self.setWindowTitle(f"Dialogue Editor \u2014 {os.path.basename(projectFilePath)}")
        else:
            self.setWindowTitle("Dialogue Editor \u2014 Unsaved Project")

    def _buildRadioVoxIndex(self):
        """Scan all RADIO calls and build the disc-2 and claimed-VOX index sets."""
        global _radioDisc2Offsets, _radioClaimedVoxAddrs
        _radioDisc2Offsets    = set()
        _radioClaimedVoxAddrs = set()
        if not radioManager.radioXMLData:
            return
        for call in radioManager.calls:
            callOffset = call.get("offset", "")
            for vox in call.findall(".//VOX_CUES"):
                content = vox.get("content", "")
                if len(content) < 16:
                    continue
                blockHex = content[8:16]
                byteAddr = int.from_bytes(bytes.fromhex(blockHex), byteorder="big") * 0x800
                if byteAddr == 0:
                    _radioDisc2Offsets.add(callOffset)
                else:
                    _radioClaimedVoxAddrs.add(byteAddr)

    def _refreshFreqFilter(self):
        """Rebuild the frequency filter combo from the loaded radio calls."""
        self.freqFilterCombo.blockSignals(True)
        current = self.freqFilterCombo.currentData()
        self.freqFilterCombo.clear()
        self.freqFilterCombo.addItem("All Frequencies", userData=None)
        freqs = sorted(
            {c.get("freq") for c in radioManager.calls if c.get("freq")},
            key=lambda f: float(f)
        )
        for freq in freqs:
            self.freqFilterCombo.addItem(freq, userData=freq)
        # Restore previous selection if still present
        idx = self.freqFilterCombo.findData(current)
        self.freqFilterCombo.setCurrentIndex(idx if idx >= 0 else 0)
        self.freqFilterCombo.blockSignals(False)

    def _hideEmptyHint(self):
        """Hide the first-run empty-state hint once data is loaded."""
        if self._emptyHint.isVisible():
            self._emptyHint.setVisible(False)

    def _populateRadioOffsets(self):
        """Repopulate the Radio offset list, applying disc and frequency filters."""
        self._hideEmptyHint()
        filterDisc2 = self.chkDisc1Only.isChecked()
        filterFreq = self.freqFilterCombo.currentData()  # None = show all
        current = self.ui.offsetListBox.currentData()
        self.ui.offsetListBox.blockSignals(True)
        self.ui.offsetListBox.clear()
        for call in radioManager.calls:
            offset = call.get("offset")
            if filterDisc2 and offset in _radioDisc2Offsets:
                continue
            if filterFreq and call.get("freq") != filterFreq:
                continue
            label = f"\u2022 {offset}" if offset in radioAlteredJson else offset
            self.ui.offsetListBox.addItem(label, userData=offset)
        self.ui.offsetListBox.blockSignals(False)
        # Restore selection if still present, else select first
        idx = self.ui.offsetListBox.findData(current)
        self.ui.offsetListBox.setCurrentIndex(idx if idx >= 0 else 0)
        # Always force-reload the call — setCurrentIndex is a no-op when the
        # index doesn't change, so the VOX/subtitle lists would stay stale.
        self._selectRadioCall(self.ui.offsetListBox.currentIndex())

    def _updateRadioOffsetMarker(self, callOffset: str):
        """Update the bullet marker on the current offset list item without
        rebuilding the entire list (which would reset VOX/subtitle selections)."""
        idx = self.ui.offsetListBox.currentIndex()
        if idx < 0:
            return
        listItem = self.ui.offsetListBox.item(idx)
        if not listItem:
            return
        data = self.ui.offsetListBox.currentData()
        if data:
            newLabel = f"\u2022 {data}" if callOffset in radioAlteredJson else data
            listItem.setText(newLabel)

    def _populateVoxOffsets(self):
        """Repopulate the VOX offset list, optionally showing only unclaimed clips."""
        self._hideEmptyHint()
        filterUnclaimed = self.chkUnclaimedVox.isChecked()
        merged = _mergedVoxJson()
        current = self.ui.offsetListBox.currentData()
        self.ui.offsetListBox.blockSignals(True)
        self.ui.offsetListBox.clear()
        for name in sorted(merged.keys()):
            if filterUnclaimed:
                offset = voxSeqToOffset.get(name)
                if offset and int(offset) in _radioClaimedVoxAddrs:
                    continue
            # Mark altered entries with a bullet
            label = f"\u2022 {name}" if name in voxAlteredJson else name
            self.ui.offsetListBox.addItem(label, userData=name)
        self.ui.offsetListBox.blockSignals(False)
        idx = self.ui.offsetListBox.findData(current)
        self.ui.offsetListBox.setCurrentIndex(idx if idx >= 0 else 0)

    def loadVoxData(self):
        voxFile = self.openFileDialog("DAT Files (*.DAT *.dat)", "Load VOX.DAT")
        if not voxFile:
            return
        self._loadVoxFromPath(voxFile)
        self._switchToVoxMode()

    def _loadVoxFromPath(self, voxFile: str):
        global voxManager, voxOriginalData, voxFilePath
        global voxOriginalJson, voxAlteredJson, voxOffsetsJson, voxSeqToOffset
        voxOriginalData = open(voxFile, 'rb').read()
        voxFilePath = voxFile
        voxManager = DM.parseDemoFile(voxOriginalData)
        try:
            from DemoTools.extractDemoVox import extractFromFile
            voxOriginalJson = extractFromFile(voxFile, fileType='vox')
        except Exception as e:
            print(f"Warning: extractFromFile failed ({e}), extracting from parsed manager")
            voxOriginalJson = self._extractJsonFromManager(voxManager, "vox")
        voxAlteredJson = {}  # fresh load, no edits yet
        # Extract per-entry graphics dictionaries from captionChunks
        global voxGraphicsJson
        voxGraphicsJson = {}
        sortedOffsets = sorted(voxManager.keys(), key=lambda k: int(k))
        voxSeqToOffset = {f"vox-{i + 1:04}": off for i, off in enumerate(sortedOffsets)}
        try:
            for seqKey, off in voxSeqToOffset.items():
                voxObj = voxManager.get(off)
                if not voxObj:
                    continue
                for seg in voxObj.segments:
                    gfx = getattr(seg, '_graphicsData', b'')
                    if gfx:
                        voxGraphicsJson[seqKey] = gfx.hex()
                        break
        except Exception as e:
            print(f"Warning: VOX graphics extraction failed: {e}")
        # Build offsets.json for STAGE.DIR adjustment
        voxOffsetsJson = {}
        for name, off in voxSeqToOffset.items():
            num = name.replace("vox-", "")
            voxOffsetsJson[num] = f"{int(off):08x}"
        self.ui.playVoxButton.setEnabled(True)
        self.statusBar().showMessage(
            f"VOX.DAT loaded: {len(voxManager)} clips, {len(voxOriginalJson)} with dialogue", 4000
        )

    def saveRadioXMLFile(self):
        if radioManager.radioXMLData is None:
            QMessageBox.warning(self, "Nothing loaded", "No XML is currently loaded.")
            return
        filename = QFileDialog.getSaveFileName(
            self, "Save RADIO.XML", radioManager.xmlFilePath or "", "XML Files (*.xml)"
        )[0]
        if not filename:
            return
        if radioManager.saveXML(filename):
            self._modified = False
            self.statusBar().showMessage(f"Saved: {filename}", 4000)
        else:
            QMessageBox.critical(self, "Save failed", f"Could not write to {filename}")

    def saveRadioDatFile(self):
        """Recompile the current XML to RADIO.DAT via RadioDatRecompiler."""
        if radioManager.radioXMLData is None:
            QMessageBox.warning(self, "Nothing loaded", "No XML is currently loaded.")
            return
        QMessageBox.information(
            self, "Not implemented",
            "DAT recompile requires running xmlModifierTools + RadioDatRecompiler.\n"
            "Save the XML first (File → Save RADIO.XML) then run the CLI tools."
        )

    def saveVoxDatFile(self):
        """
        Patch-in-place: sync only altered entries, then serialise modified captionChunks
        back into a copy of the original VOX bytes and write to disk.
        """
        if not voxManager or not voxOriginalData:
            QMessageBox.warning(self, "Nothing loaded", "No VOX.DAT is currently loaded.")
            return
        if not voxAlteredJson:
            QMessageBox.warning(self, "No changes", "No VOX entries have been modified.")
            return

        filename = QFileDialog.getSaveFileName(
            self, "Save VOX.DAT", voxFilePath or "", "DAT Files (*.DAT *.dat)"
        )[0]
        if not filename:
            return

        try:
            self._syncJsonToManager(voxAlteredJson, voxSeqToOffset, voxManager)
            alteredOffsets = set()
            for key in voxAlteredJson:
                off = voxSeqToOffset.get(key)
                if off:
                    alteredOffsets.add(int(off))

            patchedData = bytearray(voxOriginalData)
            sortedOffsets = sorted(int(k) for k in voxManager)

            for i, byteOffset in enumerate(sortedOffsets):
                if byteOffset not in alteredOffsets:
                    continue  # skip unmodified — keep original bytes
                origLen = (
                    sortedOffsets[i + 1] - byteOffset
                    if i + 1 < len(sortedOffsets)
                    else len(voxOriginalData) - byteOffset
                )
                origSlice = bytes(voxOriginalData[byteOffset: byteOffset + origLen])
                newSlice = voxManager[str(byteOffset)].getModifiedBytes(origSlice)

                if len(newSlice) != origLen:
                    QMessageBox.critical(
                        self, "VOX Save Error",
                        f"Demo at offset {byteOffset} changed size ({origLen} → {len(newSlice)}).\n"
                        "Cannot write — block counts must match."
                    )
                    return

                patchedData[byteOffset: byteOffset + origLen] = newSlice

            with open(filename, 'wb') as f:
                f.write(bytes(patchedData))
            self.statusBar().showMessage(
                f"VOX.DAT saved ({len(alteredOffsets)} entries patched): {filename}", 5000)

        except Exception as e:
            QMessageBox.critical(self, "VOX Save Error", str(e))

    # ── Navigation handlers ───────────────────────────────────────────────────

    def _navigateEntry(self, delta: int):
        """Move to the previous (delta=-1) or next (delta=+1) entry in the offset list."""
        count = self.ui.offsetListBox.count()
        if count == 0:
            return
        current = self.ui.offsetListBox.currentIndex()
        newIdx = max(0, min(current + delta, count - 1))
        if newIdx != current:
            self.ui.offsetListBox.setCurrentIndex(newIdx)

    def selectCallOffset(self, index):
        """Route to the appropriate handler depending on the current editor mode."""
        if self._editorMode == "demo":
            self._selectDemo(index)
        elif self._editorMode == "vox":
            self._selectVox(index)
        elif self._editorMode == "zmovie":
            self._selectZmovie(index)
        else:
            self._selectRadioCall(index)

    def _selectRadioCall(self, index):
        global currentSubIndex, currentVoxOffset
        if index == -1:
            return
        offset = self.ui.offsetListBox.currentData()
        if offset is None:
            return
        radioManager.setWorkingCall(offset)
        currentSubIndex = -1
        currentVoxOffset = ""

        self.ui.FreqDisplay.display(radioManager.workingCall.get("freq"))
        self.ui.VoxAddressDisplay.setText("")
        self.ui.VoxBlockAddressDisplay.setText("")
        self.revertVoxButton.setVisible(offset in radioAlteredJson)

        self.ui.audioCueListView.clear()
        for i, audio in enumerate(radioManager.getVoxOffsets()):
            QListWidgetItem(audio, self.ui.audioCueListView)

        self._clearEditor()

        if self.chkSkipVoxSort.isChecked():
            self._populateAllCallSubtitles()
        elif self.ui.audioCueListView.count() > 0:
            # Auto-select first VOX cue — fires selectAudioCue which populates subtitles
            self.ui.audioCueListView.setCurrentRow(0)
        else:
            # No VOX_CUES (e.g. staff calls): load SUBTITLE elements directly from the call
            self.ui.subsPreviewList.clear()
            callOffset = radioManager.workingCall.get("offset")
            # For direct subs, vox key = call offset
            origSubs = radioOriginalJson.get(callOffset, {}).get(callOffset, {})
            altSubs = radioAlteredJson.get(callOffset, {}).get(callOffset, {})
            for i, sub in enumerate(radioManager.workingCall.findall("SUBTITLE")):
                subOffset = sub.get("offset")
                original = origSubs.get(subOffset, sub.get("text", ""))
                edited = altSubs.get(subOffset, "")
                self.ui.subsPreviewList.addSubtitleRow(
                    i, original, editedText=edited)
            if self.ui.subsPreviewList.count() > 0:
                self.ui.subsPreviewList.setCurrentRow(0)

    def _onSkipVoxSortToggled(self, checked: bool):
        """Toggle between per-VOX-cue and all-at-once subtitle display."""
        self.ui.audioCueListView.setVisible(not checked)
        if self._editorMode != "radio":
            return
        if checked:
            self._populateAllCallSubtitles()
        else:
            # Re-select current call to restore normal VOX-cue grouping
            idx = self.ui.offsetListBox.currentIndex()
            if idx >= 0:
                self._selectRadioCall(idx)

    def _populateAllCallSubtitles(self):
        """Show every subtitle from all VOX cues in the current call at once."""
        self.ui.subsPreviewList.clear()
        # Build a flat index mapping row → (voxOffset, subtitleElement)
        self._allCallSubs: list[tuple[str, object]] = []
        callOffset = radioManager.workingCall.get("offset")
        if not callOffset:
            return
        i = 0
        # Iterate all VOX_CUES in the call
        for vox in radioManager.workingCall.findall(".//VOX_CUES"):
            voxOffset = vox.get("offset")
            origSubs = radioOriginalJson.get(callOffset, {}).get(voxOffset, {})
            altSubs = radioAlteredJson.get(callOffset, {}).get(voxOffset, {})
            for sub in vox.findall("SUBTITLE"):
                subOffset = sub.get("offset")
                original = origSubs.get(subOffset, sub.get("text", ""))
                edited = altSubs.get(subOffset, "")
                self.ui.subsPreviewList.addSubtitleRow(
                    i, original, editedText=edited)
                self._allCallSubs.append((voxOffset, sub))
                i += 1
        # Also include direct subtitles (calls with no VOX_CUES)
        if not radioManager.workingCall.findall(".//VOX_CUES"):
            origSubs = radioOriginalJson.get(callOffset, {}).get(callOffset, {})
            altSubs = radioAlteredJson.get(callOffset, {}).get(callOffset, {})
            for sub in radioManager.workingCall.findall("SUBTITLE"):
                subOffset = sub.get("offset")
                original = origSubs.get(subOffset, sub.get("text", ""))
                edited = altSubs.get(subOffset, "")
                self.ui.subsPreviewList.addSubtitleRow(
                    i, original, editedText=edited)
                self._allCallSubs.append((callOffset, sub))
                i += 1
        if self.ui.subsPreviewList.count() > 0:
            self.ui.subsPreviewList.setCurrentRow(0)

    def _selectDemo(self, index):
        global currentDemoKey, currentSubIndex
        if index == -1:
            return
        key = self.ui.offsetListBox.currentData()  # "demo-NN"
        if key is None:
            return
        currentDemoKey = key
        currentSubIndex = -1
        self._clearEditor()
        self.ui.subsPreviewList.clear()
        origSubs = demoOriginalJson.get(key, {})
        altSubs = demoAlteredJson.get(key, {})
        modified = key in demoAlteredJson
        # Show original subtitles; show edited text only where altered
        for i, startFrame in enumerate(sorted(origSubs.keys(), key=int)):
            origSub = origSubs[startFrame]
            origText = origSub.get("text", "").strip() or f"[Frame {startFrame}]"
            altSub = altSubs.get(startFrame, {})
            editedText = altSub.get("text", "").strip() if altSub else ""
            self.ui.subsPreviewList.addSubtitleRow(
                i, origText, editedText=editedText)
        # Enable revert button only if this entry has been altered
        self.revertVoxButton.setVisible(self._editorMode == "demo" and modified)
        if self.ui.subsPreviewList.count() > 0:
            self.ui.subsPreviewList.setCurrentRow(0)

    def _selectZmovie(self, index):
        global currentZmovieKey, currentSubIndex
        if index == -1:
            return
        key = self.ui.offsetListBox.currentData()
        if key is None:
            return
        currentZmovieKey = key
        currentSubIndex = -1
        self._clearEditor()
        self.ui.subsPreviewList.clear()
        origSubs = zmovieOriginalJson.get(key, {})
        altSubs = zmovieAlteredJson.get(key, {})
        for i, startFrame in enumerate(sorted(origSubs.keys(), key=int)):
            origSub = origSubs[startFrame]
            origText = origSub.get("text", "").strip() or f"[Frame {startFrame}]"
            altSub = altSubs.get(startFrame, {})
            editedText = altSub.get("text", "").strip() if altSub else ""
            self.ui.subsPreviewList.addSubtitleRow(
                i, origText, editedText=editedText)
        self.revertVoxButton.setVisible(self._editorMode == "zmovie" and modified)
        if self.ui.subsPreviewList.count() > 0:
            self.ui.subsPreviewList.setCurrentRow(0)

    def _selectVox(self, index):
        global currentVoxKey, currentSubIndex
        if index == -1:
            return
        key = self.ui.offsetListBox.currentData()  # "vox-NNNN"
        if key is None:
            return
        currentVoxKey = key
        currentSubIndex = -1
        self._clearEditor()
        self.ui.subsPreviewList.clear()
        origSubs = voxOriginalJson.get(key, {})
        altSubs = voxAlteredJson.get(key, {})
        for i, startFrame in enumerate(sorted(origSubs.keys(), key=int)):
            origSub = origSubs[startFrame]
            origText = origSub.get("text", "").strip() or f"[Frame {startFrame}]"
            altSub = altSubs.get(startFrame, {})
            editedText = altSub.get("text", "").strip() if altSub else ""
            self.ui.subsPreviewList.addSubtitleRow(
                i, origText, editedText=editedText)
        # Enable revert button only if this entry has been altered
        self.revertVoxButton.setVisible(self._editorMode == "vox" and key in voxAlteredJson)
        if self.ui.subsPreviewList.count() > 0:
            self.ui.subsPreviewList.setCurrentRow(0)

    def selectAudioCue(self, item):
        global currentSubIndex, currentVoxOffset
        if item is None:
            return
        offset = self.ui.audioCueListView.currentItem().text().split("  ", 1)[-1]
        radioManager.setWorkingVox(offset)
        currentSubIndex = -1

        # VOX byte address
        voxOffsetHex = radioManager.workingVox.get("content")[8:16]
        offsetBlock = bytes.fromhex(voxOffsetHex)
        byteAddr = int.from_bytes(offsetBlock, byteorder="big") * 0x800
        currentVoxOffset = str(byteAddr)

        self.ui.VoxAddressDisplay.setText(currentVoxOffset)
        self.ui.VoxBlockAddressDisplay.setText("0x" + voxOffsetHex.upper())

        self.ui.subsPreviewList.clear()
        callOffset = radioManager.workingCall.get("offset")
        voxOffset = radioManager.workingVox.get("offset")
        origSubs = radioOriginalJson.get(callOffset, {}).get(voxOffset, {})
        altSubs = radioAlteredJson.get(callOffset, {}).get(voxOffset, {})
        for i, sub in enumerate(radioManager.workingVox.findall("SUBTITLE")):
            subOffset = sub.get("offset")
            original = origSubs.get(subOffset, sub.get("text", ""))
            edited = altSubs.get(subOffset, "")
            self.ui.subsPreviewList.addSubtitleRow(
                i, original, editedText=edited)

        self._clearEditor()
        if self.ui.subsPreviewList.count() > 0:
            self.ui.subsPreviewList.setCurrentRow(0)

    def subtitleSelect(self, item):
        global currentSubIndex
        if item is None:
            return
        idx = self.ui.subsPreviewList.currentRow()
        if idx < 0:
            return
        currentSubIndex = idx

        self._loadingSubtitle = True

        if self._editorMode in ("demo", "vox", "zmovie"):
            key, djson = self._modeData()
            subtitles = djson.get(key, {})
            sortedFrames = sorted(subtitles.keys(), key=int)
            if idx < len(sortedFrames):
                frame = sortedFrames[idx]
                sub = subtitles[frame]
                self.ui.DialogueEditorBox.setText(sub.get("text", "").replace("｜", "\n"))
                self.ui.startFrameBox.setValue(int(frame))
                self.ui.durationBox.setValue(int(sub.get("duration", "0")))
            else:
                self.ui.DialogueEditorBox.clear()
                self.ui.startFrameBox.setValue(0)
                self.ui.durationBox.setValue(0)
        elif self.chkSkipVoxSort.isChecked() and hasattr(self, '_allCallSubs'):
            # Skip-vox-sort mode: use the flat index across all VOX cues
            if idx >= len(self._allCallSubs):
                return
            voxOffset, subElem = self._allCallSubs[idx]
            subOffset = subElem.get("offset")
            callOffset = radioManager.workingCall.get("offset")
            voxSubs = _getRadioVoxSubs(callOffset, voxOffset)
            text = voxSubs.get(subOffset, subElem.get("text", ""))
            text = text.replace("\\r\\n", "\n")
            self.ui.DialogueEditorBox.setText(text)
            self.ui.startFrameBox.setValue(0)
            self.ui.durationBox.setValue(0)
        else:
            # Populate text editor from iseeva JSON
            subElems = self._radioSubtitleElems()
            if idx >= len(subElems):
                return
            subOffset = subElems[idx].get("offset")
            callOffset = radioManager.workingCall.get("offset")
            voxOffset = self._radioVoxKey()
            voxSubs = _getRadioVoxSubs(callOffset, voxOffset)
            text = voxSubs.get(subOffset, subElems[idx].get("text", ""))
            text = text.replace("\\r\\n", "\n")
            self.ui.DialogueEditorBox.setText(text)

            # Populate timing from VOX if loaded
            timing_loaded = self._loadTimingFromVox(idx)
            if not timing_loaded:
                self.ui.startFrameBox.setValue(0)
                self.ui.durationBox.setValue(0)

        self._loadingSubtitle = False

        self.applyEditButton.setEnabled(True)
        self.translateButton.setEnabled(True)
        self.autoFormatButton.setEnabled(True)
        self.splitSubButton.setEnabled(True)
        self.deleteSubButton.setEnabled(self._editorMode == "radio")
        self.callDictButton.setEnabled(self._editorMode in ("radio", "demo", "vox"))
        self.autoTranslateButton.setEnabled(True)

    # ── VOX timing helpers ────────────────────────────────────────────────────

    def _getVoxSubtitleLines(self) -> list:
        """
        Returns a flat list of dialogueLine objects from the demo matching the
        current VOX_CUES, or [] if VOX is not loaded / not found.
        """
        if not voxManager or not currentVoxOffset:
            return []
        demo = voxManager.get(currentVoxOffset)
        if demo is None:
            return []
        lines = []
        for seg in demo.segments:
            if hasattr(seg, 'subtitles'):
                lines.extend(seg.subtitles)
        return lines

    def _loadTimingFromVox(self, idx: int) -> bool:
        """Fills startFrameBox / durationBox from the VOX demo. Returns True on success."""
        lines = self._getVoxSubtitleLines()
        if not lines or idx >= len(lines):
            return False
        line = lines[idx]
        self.ui.startFrameBox.setValue(line.startFrame)
        self.ui.durationBox.setValue(line.displayFrames)
        return True

    def _onTimingChanged(self, _value):
        """Mark the apply button as having pending changes (cosmetic only for now)."""
        if not self._loadingSubtitle:
            self.applyEditButton.setStyleSheet("color: orange;")

    # ── Edit actions ──────────────────────────────────────────────────────────

    def applyEdit(self):
        """Write text editor content + spinbox timings back to the in-memory data."""
        global currentSubIndex
        if currentSubIndex < 0:
            return

        savedIdx = currentSubIndex  # snapshot before any signal can mutate the global

        if self._editorMode in ("demo", "vox", "zmovie"):
            if self._editorMode == "vox":
                key = currentVoxKey
                if key not in voxAlteredJson:
                    orig = voxOriginalJson.get(key, {})
                    voxAlteredJson[key] = json.loads(json.dumps(orig))
                subtitles = voxAlteredJson[key]
            elif self._editorMode == "demo":
                key = currentDemoKey
                if key not in demoAlteredJson:
                    orig = demoOriginalJson.get(key, {})
                    demoAlteredJson[key] = json.loads(json.dumps(orig))
                subtitles = demoAlteredJson[key]
            else:  # zmovie
                key = currentZmovieKey
                if key not in zmovieAlteredJson:
                    orig = zmovieOriginalJson.get(key, {})
                    zmovieAlteredJson[key] = json.loads(json.dumps(orig))
                subtitles = zmovieAlteredJson[key]
            sortedFrames = sorted(subtitles.keys(), key=int)
            newStart = str(self.ui.startFrameBox.value())
            if savedIdx < len(sortedFrames):
                oldFrame = sortedFrames[savedIdx]
                newText = self.ui.DialogueEditorBox.toPlainText().replace("\n", "｜")
                newDur = str(self.ui.durationBox.value())
                del subtitles[oldFrame]
                subtitles[newStart] = {"duration": newDur, "text": newText}
            self._modified = True
            self._refreshSubsList()
            self.applyEditButton.setStyleSheet("")
            label = {"demo": "DEMO", "vox": "VOX", "zmovie": "ZMovie"}.get(self._editorMode, "")
            self.statusBar().showMessage(
                f"Changes applied (unsaved \u2014 use File \u2192 Export {label} JSON)", 5000
            )
            # Update revert button visibility and offset list markers
            if self._editorMode == "vox":
                self.revertVoxButton.setVisible(key in voxAlteredJson)
                self._populateVoxOffsets()
            elif self._editorMode == "demo":
                self.revertVoxButton.setVisible(key in demoAlteredJson)
                self._populateDemoOffsets()
            elif self._editorMode == "zmovie":
                self.revertVoxButton.setVisible(key in zmovieAlteredJson)
                self._populateZmovieOffsets()
            nextRow = min(savedIdx + 1, self.ui.subsPreviewList.count() - 1)
            self.ui.subsPreviewList.setCurrentRow(nextRow)
            return

        # --- Text → iseeva JSON (copy-on-write) --------------------------------
        newText = self.ui.DialogueEditorBox.toPlainText().replace("\n", "\\r\\n")
        callOffset = radioManager.workingCall.get("offset")

        if self.chkSkipVoxSort.isChecked() and hasattr(self, '_allCallSubs'):
            # Skip-vox-sort: look up voxOffset and subtitle element from the flat index
            if savedIdx < len(self._allCallSubs):
                voxOffset, subElem = self._allCallSubs[savedIdx]
                subOffset = subElem.get("offset")
                if callOffset not in radioAlteredJson:
                    radioAlteredJson[callOffset] = {}
                if voxOffset not in radioAlteredJson[callOffset]:
                    radioAlteredJson[callOffset][voxOffset] = {}
                radioAlteredJson[callOffset][voxOffset][subOffset] = newText
        else:
            voxOffset = self._radioVoxKey()
            subElems = self._radioSubtitleElems()
            if savedIdx < len(subElems):
                subOffset = subElems[savedIdx].get("offset")
                if callOffset not in radioAlteredJson:
                    radioAlteredJson[callOffset] = {}
                if voxOffset not in radioAlteredJson[callOffset]:
                    radioAlteredJson[callOffset][voxOffset] = {}
                radioAlteredJson[callOffset][voxOffset][subOffset] = newText

            # --- Sync text to VOX altered JSON --------------------------------------
            self._syncRadioEditToVox(savedIdx, newText)

            # --- Timing + text → VOX demo -----------------------------------------
            lines = self._getVoxSubtitleLines()
            if lines and savedIdx < len(lines):
                lines[savedIdx].startFrame = self.ui.startFrameBox.value()
                lines[savedIdx].displayFrames = self.ui.durationBox.value()
                lines[savedIdx].text = self.ui.DialogueEditorBox.toPlainText().replace("\n", "｜")

        # Refresh subtitle list to show new text (preserve VOX cue selection)
        self._modified = True
        if self.chkSkipVoxSort.isChecked():
            self._populateAllCallSubtitles()
        else:
            self._refreshSubsList()
        # Update the bullet marker on the current offset list item
        self._updateRadioOffsetMarker(callOffset)
        self.revertVoxButton.setVisible(callOffset in radioAlteredJson)
        self.applyEditButton.setStyleSheet("")
        if projectFilePath:
            self.statusBar().showMessage("Changes applied (unsaved — use File → Save Project)", 5000)
        else:
            self.statusBar().showMessage("Changes applied (unsaved — use File → Save RADIO.XML or Save Project As)", 5000)
        # Advance to next subtitle, or stay on last
        lastRow = self.ui.subsPreviewList.count() - 1
        nextRow = min(savedIdx + 1, lastRow)
        self.ui.subsPreviewList.setCurrentRow(nextRow)

    # ── Translate / Auto-format helpers ─────────────────────────────────────

    def _openPreferences(self):
        """Show the Preferences dialog."""
        dlg = PreferencesDialog(self._appSettings, parent=self)
        dlg.exec()

    def _translateLine(self):
        """Translate the current editor text using deep_translator."""
        text = self.ui.DialogueEditorBox.toPlainText().strip()
        if not text:
            return
        try:
            from deep_translator import GoogleTranslator
        except ImportError:
            QMessageBox.warning(self, "Missing Dependency",
                "Install deep_translator to use translation:\n\n"
                "  pip install deep-translator")
            return

        # Strip line-break markers that confuse the translator
        clean = text.replace("\\r\\n", " ").replace("｜", " ")
        clean = " ".join(clean.split())

        srcLang = self._appSettings.value("translate/source_lang", "ja")
        tgtLang = self._appSettings.value("translate/target_lang", "en")
        self.statusBar().showMessage(f"Translating ({srcLang} → {tgtLang})...")
        QApplication.processEvents()
        try:
            result = GoogleTranslator(source=srcLang, target=tgtLang).translate(clean)
            if result:
                self.ui.DialogueEditorBox.setPlainText(result)
                self.applyEditButton.setStyleSheet("color: orange;")
                self.statusBar().showMessage("Translation complete", 3000)
        except Exception as e:
            QMessageBox.warning(self, "Translation Error", str(e))
            self.statusBar().clearMessage()

    # Default MGS1 ASCII character widths (pixels), from original_widths.txt
    # Index 0 = space (0x20), through to index 94 = '~' (0x7E)
    _MGS_WIDTHS = {
        ' ': 4, '!': 5, '"': 5, '#': 12, '$': 7, '%': 10, '&': 8, "'": 3,
        '(': 3, ')': 3, '*': 5, '+': 8, ',': 3, '-': 5, '.': 3, '/': 6,
        '0': 7, '1': 7, '2': 7, '3': 7, '4': 7, '5': 7, '6': 7, '7': 7,
        '8': 7, '9': 7, ':': 3, ';': 3, '<': 4, '=': 8, '>': 4, '?': 8,
        '@': 5, 'A': 8, 'B': 9, 'C': 9, 'D': 9, 'E': 8, 'F': 8, 'G': 10,
        'H': 9, 'I': 4, 'J': 7, 'K': 9, 'L': 8, 'M': 11, 'N': 9, 'O': 10,
        'P': 8, 'Q': 10, 'R': 9, 'S': 8, 'T': 8, 'U': 9, 'V': 8, 'W': 12,
        'X': 8, 'Y': 8, 'Z': 8, '[': 3, '\\': 8, ']': 3, '^': 4, '_': 6,
        '`': 3, 'a': 7, 'b': 8, 'c': 7, 'd': 8, 'e': 8, 'f': 4, 'g': 8,
        'h': 7, 'i': 3, 'j': 3, 'k': 7, 'l': 3, 'm': 10, 'n': 7, 'o': 7,
        'p': 8, 'q': 8, 'r': 4, 's': 6, 't': 4, 'u': 7, 'v': 6, 'w': 9,
        'x': 6, 'y': 6, 'z': 6, '{': 2, '|': 2, '}': 2, '~': 5,
    }

    def _autoFormatLine(self):
        """Re-wrap the current editor text using MGS1 pixel-width-aware line breaks."""
        from scripts.translation.mgs_font_text import wrap_text

        text = self.ui.DialogueEditorBox.toPlainText()
        if not text.strip():
            return

        # Flatten existing line breaks to plain text
        flat = text.replace("\n", " ").replace("｜", " ")
        # Collapse multiple spaces
        flat = " ".join(flat.split())

        lines = wrap_text(flat, self._MGS_WIDTHS)
        self.ui.DialogueEditorBox.setPlainText("\n".join(lines))
        self.applyEditButton.setStyleSheet("color: orange;")
        self.statusBar().showMessage(
            f"Auto-formatted: {len(lines)} line(s), "
            f"max {max(sum(self._MGS_WIDTHS.get(c, 0) for c in ln) for ln in lines)}px",
            5000)

    # ── Auto-translate all subtitles ────────────────────────────────────────

    def _autoTranslateAll(self):
        """Translate, auto-format, and apply every subtitle in the current entry."""
        from PySide6.QtWidgets import QProgressDialog

        # Gather source texts for all subtitles in the current entry
        texts = []
        if self._editorMode in ("demo", "vox", "zmovie"):
            key, djson = self._modeData()
            subtitles = djson.get(key, {})
            for frame in sorted(subtitles.keys(), key=int):
                texts.append(subtitles[frame].get("text", ""))
        elif self.chkSkipVoxSort.isChecked() and hasattr(self, '_allCallSubs'):
            # Radio skip-vox-sort: iterate the flat index
            callOffset = radioManager.workingCall.get("offset")
            for voxOffset, subElem in self._allCallSubs:
                subOffset = subElem.get("offset")
                voxSubs = _getRadioVoxSubs(callOffset, voxOffset)
                texts.append(voxSubs.get(subOffset, subElem.get("text", "")))
        else:
            # Radio mode (normal VOX-cue grouping)
            subElems = self._radioSubtitleElems()
            callOffset = radioManager.workingCall.get("offset")
            voxOffset = self._radioVoxKey()
            voxSubs = _getRadioVoxSubs(callOffset, voxOffset)
            for sub in subElems:
                subOffset = sub.get("offset")
                texts.append(voxSubs.get(subOffset, sub.get("text", "")))

        if not texts:
            return

        srcLang = self._appSettings.value("translate/source_lang", "ja")
        tgtLang = self._appSettings.value("translate/target_lang", "en")

        # Progress dialog
        self._atProgress = QProgressDialog(
            f"Translating subtitles ({srcLang} \u2192 {tgtLang})...",
            "Cancel", 0, len(texts), self)
        self._atProgress.setWindowTitle("Auto-translate")
        self._atProgress.setMinimumDuration(0)
        self._atProgress.setValue(0)
        self._atProgress.setWindowModality(Qt.WindowModal)

        # Store results as they arrive; apply all when thread finishes
        self._atResults: list[str] = [""] * len(texts)
        self._atTotal = len(texts)

        self._atThread = AutoTranslateThread(texts, srcLang, tgtLang, parent=self)
        self._atThread.lineTranslated.connect(self._onAutoTranslateLine)
        self._atThread.errorOccurred.connect(self._onAutoTranslateError)
        self._atThread.finished.connect(self._onAutoTranslateDone)
        self._atProgress.canceled.connect(self._onAutoTranslateCancel)
        self._atThread.start()

    def _onAutoTranslateLine(self, idx: int, text: str):
        """Receive one translated line from the worker thread."""
        self._atResults[idx] = text
        self._atProgress.setValue(idx + 1)
        self._atProgress.setLabelText(
            f"Translating subtitle {idx + 1} of {self._atTotal}...")

    def _onAutoTranslateError(self, msg: str):
        self._atProgress.close()
        QMessageBox.warning(self, "Auto-translate Error", msg)

    def _onAutoTranslateCancel(self):
        if hasattr(self, '_atThread') and self._atThread.isRunning():
            self._atThread.requestInterruption()
            self._atThread.wait()

    def _onAutoTranslateDone(self):
        """All translations received — auto-format and apply each one."""
        if not hasattr(self, '_atProgress') or not self._atProgress.isVisible():
            return  # was cancelled
        self._atProgress.close()

        from scripts.translation.mgs_font_text import wrap_text

        count = self.ui.subsPreviewList.count()
        for i, translated in enumerate(self._atResults):
            if i >= count:
                break
            if not translated.strip():
                continue

            # Select the subtitle row
            self.ui.subsPreviewList.setCurrentRow(i)
            QApplication.processEvents()

            # Set translated text in editor
            self.ui.DialogueEditorBox.setPlainText(translated)

            # Auto-format: wrap to MGS1 pixel widths
            raw = translated.replace("\n", " ").replace("｜", " ")
            raw = " ".join(raw.split())
            lines = wrap_text(raw, self._MGS_WIDTHS)
            self.ui.DialogueEditorBox.setPlainText("\n".join(lines))

            # Apply edit
            self.applyEdit()

        self.statusBar().showMessage(
            f"Auto-translated {len(self._atResults)} subtitle(s)", 5000)

    _SPLIT_GAP_FRAMES = 2  # frames between the two halves

    def splitSubtitle(self):
        """
        Duplicate the selected subtitle and split the timing:
        - First keeps same text, duration ≈ half the original
        - Second starts a few frames after the first ends, same text,
          duration adjusted so it ends on the original end frame
        """
        global currentSubIndex
        if currentSubIndex < 0:
            return

        gap = self._SPLIT_GAP_FRAMES

        if self._editorMode in ("demo", "vox", "zmovie"):
            if self._editorMode == "vox":
                key = currentVoxKey
                if key not in voxAlteredJson:
                    orig = voxOriginalJson.get(key, {})
                    voxAlteredJson[key] = json.loads(json.dumps(orig))
                subtitles = voxAlteredJson[key]
            elif self._editorMode == "demo":
                key = currentDemoKey
                if key not in demoAlteredJson:
                    orig = demoOriginalJson.get(key, {})
                    demoAlteredJson[key] = json.loads(json.dumps(orig))
                subtitles = demoAlteredJson[key]
            else:  # zmovie
                key = currentZmovieKey
                if key not in zmovieAlteredJson:
                    orig = zmovieOriginalJson.get(key, {})
                    zmovieAlteredJson[key] = json.loads(json.dumps(orig))
                subtitles = zmovieAlteredJson[key]
            sortedFrames = sorted(subtitles.keys(), key=int)
            if currentSubIndex >= len(sortedFrames):
                return
            frame = sortedFrames[currentSubIndex]
            sub = subtitles[frame]
            text = sub.get("text", "")
            origStart = int(frame)
            origDur = int(sub.get("duration", "0"))
            origEnd = origStart + origDur

            halfDur = origDur // 2
            secondStart = origStart + halfDur + gap
            secondDur = max(1, origEnd - secondStart)

            # Update first entry's duration
            subtitles[frame] = {"duration": str(halfDur), "text": text}
            # Insert second entry
            subtitles[str(secondStart)] = {"duration": str(secondDur), "text": text}

            self._modified = True
            self._refreshSubsList()
            if self._editorMode == "vox":
                self.revertVoxButton.setVisible(key in voxAlteredJson)
                self._populateVoxOffsets()
            elif self._editorMode == "demo":
                self.revertVoxButton.setVisible(key in demoAlteredJson)
                self._populateDemoOffsets()
            elif self._editorMode == "zmovie":
                self.revertVoxButton.setVisible(key in zmovieAlteredJson)
                self._populateZmovieOffsets()
            self.ui.subsPreviewList.setCurrentRow(currentSubIndex)
            self.statusBar().showMessage("Subtitle split", 3000)
            return

        # ── Radio mode — split via iseeva JSON + XML ─────────────────────
        subElems = self._radioSubtitleElems()
        if currentSubIndex >= len(subElems):
            return
        origElem = subElems[currentSubIndex]
        origSubOffset = origElem.get("offset")
        callOffset = radioManager.workingCall.get("offset")
        voxOffset = self._radioVoxKey()

        # Get text from merged JSON
        voxSubs = _getRadioVoxSubs(callOffset, voxOffset)
        text = voxSubs.get(origSubOffset, origElem.get("text", ""))

        # Add new subtitle to XML with offset = original + 1
        newSub = radioManager.addSubtitle(currentSubIndex, text, after=True)
        newSubOffset = str(int(origSubOffset) + 1)
        newSub.set("offset", newSubOffset)

        # Write both entries to altered JSON
        if callOffset not in radioAlteredJson:
            radioAlteredJson[callOffset] = {}
        if voxOffset not in radioAlteredJson[callOffset]:
            radioAlteredJson[callOffset][voxOffset] = {}
        radioAlteredJson[callOffset][voxOffset][origSubOffset] = text
        radioAlteredJson[callOffset][voxOffset][newSubOffset] = text

        # Sync split to VOX altered JSON
        self._syncRadioSplitToVox(currentSubIndex, text)

        # Split VOX timing if loaded (in-memory demo object for audio sync)
        lines = self._getVoxSubtitleLines()
        if lines and currentSubIndex < len(lines):
            orig_line = lines[currentSubIndex]
            origStart = orig_line.startFrame
            origDur = orig_line.displayFrames
            origEnd = origStart + origDur

            halfDur = origDur // 2
            secondStart = origStart + halfDur + gap
            secondDur = max(1, origEnd - secondStart)

            orig_line.displayFrames = halfDur
            self._insertVoxLine(currentSubIndex, secondStart, secondDur, text)

        self._modified = True
        self._refreshSubsList()
        self._populateRadioOffsets()
        self.revertVoxButton.setVisible(callOffset in radioAlteredJson)
        self.ui.subsPreviewList.setCurrentRow(currentSubIndex)
        self.statusBar().showMessage("Subtitle split", 3000)

    def _revertVoxEntry(self):
        """Revert the current entry to its original state, discarding alterations."""
        if self._editorMode == "vox":
            key, altDict, refreshFn, selectFn = (
                currentVoxKey, voxAlteredJson,
                self._populateVoxOffsets, self._selectVox)
        elif self._editorMode == "demo":
            key, altDict, refreshFn, selectFn = (
                currentDemoKey, demoAlteredJson,
                self._populateDemoOffsets, self._selectDemo)
        elif self._editorMode == "zmovie":
            key, altDict, refreshFn, selectFn = (
                currentZmovieKey, zmovieAlteredJson,
                self._populateZmovieOffsets, self._selectZmovie)
        elif self._editorMode == "radio":
            callOff = radioManager.workingCall.get("offset") if radioManager.workingCall is not None else None
            if not callOff:
                return
            key, altDict, refreshFn, selectFn = (
                callOff, radioAlteredJson,
                self._populateRadioOffsets, self._selectRadioCall)
        else:
            return
        if not key or key not in altDict:
            return
        if self._appSettings.value("editor/warn_on_revert", True, type=bool):
            reply = QMessageBox.warning(
                self, "Revert to Original",
                f"Revert {key} to its original state?\n\n"
                "All changes to this entry will be lost.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No
            )
            if reply != QMessageBox.Yes:
                return
        del altDict[key]
        self.revertVoxButton.setVisible(False)
        refreshFn()
        selectFn(self.ui.offsetListBox.currentIndex())
        self._clearEditor()
        self.statusBar().showMessage(f"{key} reverted to original", 3000)

    def deleteSubtitle(self):
        """Remove the currently selected subtitle from the XML."""
        global currentSubIndex
        if currentSubIndex < 0:
            return
        reply = QMessageBox.question(
            self, "Delete subtitle",
            "Remove this subtitle from the call?",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return
        radioManager.removeSubtitle(currentSubIndex)
        self._refreshSubsList()
        currentSubIndex = -1
        self._clearEditor()

    # ── VOX demo mutation ─────────────────────────────────────────────────────

    def _insertVoxLine(self, afterIndex: int, startFrame: int, displayFrames: int, text: str):
        """
        Inserts a new dialogueLine into the captionChunk(s) of the current demo
        at the position corresponding to afterIndex + 1.
        """
        if not voxManager or not currentVoxOffset:
            return
        demo = voxManager.get(currentVoxOffset)
        if demo is None:
            return

        # Flatten to find which chunk/position contains afterIndex
        flat_idx = 0
        for seg in demo.segments:
            if isinstance(seg, voxCtl.captionChunk):
                for pos, line in enumerate(seg.subtitles):
                    if flat_idx == afterIndex:
                        # Build a new dialogueLine-like object
                        new_line = voxCtl.dialogueLine.__new__(voxCtl.dialogueLine)
                        new_line.startFrame = startFrame
                        new_line.displayFrames = displayFrames
                        new_line.length = 0
                        new_line.buffer = b'\x00\x00\x00\x00'
                        new_line.final = False
                        new_line.kanjiDict = {}
                        new_line.text = text
                        seg.subtitles.insert(pos + 1, new_line)
                        return
                    flat_idx += 1

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _radioSubtitleElems(self) -> list:
        """Return the SUBTITLE XML elements for the current radio selection.
        If a VOX cue is selected, returns its SUBTITLEs; otherwise the call's direct SUBTITLEs."""
        if radioManager.workingVox is not None:
            return radioManager.workingVox.findall("SUBTITLE")
        elif radioManager.workingCall is not None:
            return radioManager.workingCall.findall("SUBTITLE")
        return []

    def _radioVoxKey(self) -> str:
        """Return the vox offset key for the current radio selection.
        For VOX_CUES, returns the vox offset; for direct subs, returns the call offset."""
        if radioManager.workingVox is not None:
            return radioManager.workingVox.get("offset")
        elif radioManager.workingCall is not None:
            return radioManager.workingCall.get("offset")
        return ""

    def _voxKeyForCurrentRadioCue(self) -> str:
        """Return the vox sequential key (e.g. 'vox-0035') that corresponds to
        the current radio VOX_CUES byte address, or '' if not found."""
        if not currentVoxOffset or not voxSeqToOffset:
            return ""
        for name, off in voxSeqToOffset.items():
            if off == currentVoxOffset:
                return name
        return ""

    def _syncRadioEditToVox(self, subIdx: int, newText: str):
        """Propagate a radio subtitle text edit to the matching voxAlteredJson entry."""
        voxKey = self._voxKeyForCurrentRadioCue()
        if not voxKey:
            return
        # Copy-on-write: deep-copy original into altered if not yet present
        if voxKey not in voxAlteredJson:
            orig = voxOriginalJson.get(voxKey, {})
            if not orig:
                return
            voxAlteredJson[voxKey] = json.loads(json.dumps(orig))
        subtitles = voxAlteredJson[voxKey]
        sortedFrames = sorted(subtitles.keys(), key=int)
        if subIdx < len(sortedFrames):
            frame = sortedFrames[subIdx]
            subtitles[frame]["text"] = newText.replace("\\r\\n", "｜")

    def _syncRadioSplitToVox(self, subIdx: int, text: str):
        """Propagate a radio subtitle split to the matching voxAlteredJson entry.
        Splits the VOX subtitle at subIdx into two with halved durations."""
        voxKey = self._voxKeyForCurrentRadioCue()
        if not voxKey:
            return
        if voxKey not in voxAlteredJson:
            orig = voxOriginalJson.get(voxKey, {})
            if not orig:
                return
            voxAlteredJson[voxKey] = json.loads(json.dumps(orig))
        subtitles = voxAlteredJson[voxKey]
        sortedFrames = sorted(subtitles.keys(), key=int)
        if subIdx >= len(sortedFrames):
            return
        frame = sortedFrames[subIdx]
        sub = subtitles[frame]
        origStart = int(frame)
        origDur = int(sub.get("duration", "0"))
        origEnd = origStart + origDur
        gap = self._SPLIT_GAP_FRAMES

        halfDur = origDur // 2
        secondStart = origStart + halfDur + gap
        secondDur = max(1, origEnd - secondStart)

        subtitles[frame] = {"duration": str(halfDur), "text": text.replace("\\r\\n", "｜")}
        subtitles[str(secondStart)] = {"duration": str(secondDur), "text": text.replace("\\r\\n", "｜")}

    def _refreshSubsList(self):
        """Rebuild the subtitle list widget from the current data source."""
        self.ui.subsPreviewList.clear()
        if self._editorMode in ("demo", "vox", "zmovie"):
            if self._editorMode == "demo":
                key = currentDemoKey
                origSubs = demoOriginalJson.get(key, {})
                altSubs = demoAlteredJson.get(key, {})
            elif self._editorMode == "vox":
                key = currentVoxKey
                origSubs = voxOriginalJson.get(key, {})
                altSubs = voxAlteredJson.get(key, {})
            else:
                key = currentZmovieKey
                origSubs = zmovieOriginalJson.get(key, {})
                altSubs = zmovieAlteredJson.get(key, {})
            for i, startFrame in enumerate(sorted(origSubs.keys(), key=int)):
                origSub = origSubs[startFrame]
                origText = origSub.get("text", "").strip() or f"[Frame {startFrame}]"
                altSub = altSubs.get(startFrame, {})
                editedText = altSub.get("text", "").strip() if altSub else ""
                self.ui.subsPreviewList.addSubtitleRow(
                    i, origText, editedText=editedText)
        else:
            callOffset = radioManager.workingCall.get("offset") if radioManager.workingCall is not None else None
            voxOffset = self._radioVoxKey() if callOffset else None
            origSubs = radioOriginalJson.get(callOffset, {}).get(voxOffset, {}) if callOffset and voxOffset else {}
            altSubs = radioAlteredJson.get(callOffset, {}).get(voxOffset, {}) if callOffset and voxOffset else {}
            for i, sub in enumerate(self._radioSubtitleElems()):
                subOffset = sub.get("offset")
                original = origSubs.get(subOffset, sub.get("text", ""))
                edited = altSubs.get(subOffset, "")
                self.ui.subsPreviewList.addSubtitleRow(
                    i, original, editedText=edited)

    def _clearEditor(self):
        """Reset the editor panel."""
        self.ui.DialogueEditorBox.clear()
        self._loadingSubtitle = True
        self.ui.startFrameBox.setValue(0)
        self.ui.durationBox.setValue(0)
        self._loadingSubtitle = False
        self.applyEditButton.setEnabled(False)
        self.applyEditButton.setStyleSheet("")
        self.splitSubButton.setEnabled(False)
        self.deleteSubButton.setEnabled(False)
        self.translateButton.setEnabled(False)
        self.autoFormatButton.setEnabled(False)
        self.callDictButton.setEnabled(False)
        self.autoTranslateButton.setEnabled(False)

    # ── Audio ─────────────────────────────────────────────────────────────────

    def playVoxFile(self):
        if self._editorMode == "zmovie":
            return self._playZmovieVideo()
        elif self._editorMode == "demo":
            offset = demoSeqToOffset.get(currentDemoKey)
            if not offset or not demoManager:
                self.statusBar().showMessage("No demo entry selected.", 3000)
                return
            demo = demoManager.get(offset)
            if demo is None:
                self.statusBar().showMessage(f"Demo audio not found for: {currentDemoKey}", 3000)
                return
        elif self._editorMode == "vox":
            offset = voxSeqToOffset.get(currentVoxKey)
            if not offset or not voxManager:
                self.statusBar().showMessage("No VOX entry selected.", 3000)
                return
            demo = voxManager.get(offset)
            if demo is None:
                self.statusBar().showMessage(f"VOX audio not found for: {currentVoxKey}", 3000)
                return
        else:
            voxOffset = self.ui.VoxAddressDisplay.text()
            if not voxOffset:
                return
            if voxOffset == "0":
                NotificationDialog(
                    "Warning!", "No Vox Offset — this call may be on the other disc.", self
                ).exec()
                return
            demo = voxManager.get(str(voxOffset))
            if demo is None:
                self.statusBar().showMessage(f"VOX clip not found at offset {voxOffset}", 3000)
                return

        # Stop any in-progress conversion or playback
        self.stopVoxFile()

        import tempfile
        # Check audio exists before attempting extraction
        if demo.getAudioHeader() is None:
            self.statusBar().showMessage("This entry has no audio data.", 3000)
            self._resetPlaybackButtons()
            return

        try:
            vagFile = voxCtl.outputVagFile(demo, "mgs_vox_temp", tempfile.gettempdir())
            # Patch VAG header if sample rate is 0 (unknown byte code in SAMPLE_RATES)
            with open(vagFile, 'r+b') as vf:
                vf.seek(16)
                sr = int.from_bytes(vf.read(4), 'big')
                if sr == 0:
                    raw_code = audioHdr.content[6] if len(audioHdr.content) > 6 else 0xFF
                    print(f"Unknown VAG sample rate code 0x{raw_code:02X} — add to SAMPLE_RATES; defaulting to 22050 Hz")
                    vf.seek(16)
                    vf.write((22050).to_bytes(4, 'big'))
        except Exception as e:
            self.statusBar().showMessage(f"Audio extract failed: {e}", 6000)
            self._resetPlaybackButtons()
            return

        self._convThread = VoxConversionThread(vagFile, parent=self)
        self._convThread.conversionDone.connect(self._onConversionDone)
        self._convThread.errorOccurred.connect(self._onPlaybackError)
        self._convThread.start()

        self.ui.playVoxButton.setEnabled(False)
        self.stopVoxButton.setEnabled(True)
        self.statusBar().showMessage("Converting…")

    def stopVoxFile(self):
        """Kill any running conversion or playback subprocess."""
        if self._convThread and self._convThread.isRunning():
            self._convThread.killSubprocess()
            self._convThread.requestInterruption()
            self._convThread.wait(2000)
        if self._playThread and self._playThread.isRunning():
            self._playThread.killSubprocess()
            self._playThread.requestInterruption()
            self._playThread.wait(1000)
        if self._zmovieConvThread and self._zmovieConvThread.isRunning():
            self._zmovieConvThread.killSubprocess()
            self._zmovieConvThread.requestInterruption()
            self._zmovieConvThread.wait(2000)
        # Stop QMediaPlayer and release file handles so temp files can be reused
        if hasattr(self, '_mediaPlayer'):
            self._mediaPlayer.stop()
            self._mediaPlayer.setSource(QUrl())
        self._stopPreview()
        self._showGraphicsHideVideo()
        self._resetPlaybackButtons()

    def closeEvent(self, event):
        """Warn about unsaved edits, then kill audio before closing."""
        if self._modified:
            reply = QMessageBox.question(
                self, "Unsaved Changes",
                "You have unsaved edits. Quit anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.No:
                event.ignore()
                return
        self.stopVoxFile()
        event.accept()

    def _onConversionDone(self, wavPath: str):
        """Launch ffplay on the converted WAV and start the subtitle timer."""
        self._playThread = FfplayThread(wavPath, parent=self)
        self._playThread.playbackFinished.connect(self._onPlaybackFinished)
        self._playThread.errorOccurred.connect(self._onPlaybackError)
        self._playThread.start()
        self._elapsed.restart()
        self._frameTimer.start()
        self.statusBar().showMessage("Playing…")

    def _onPlaybackFinished(self):
        self._stopPreview()
        self._resetPlaybackButtons()
        self.statusBar().showMessage("Playback finished.", 2000)

    def _onPlaybackError(self, msg: str):
        self._stopPreview()
        self._showGraphicsHideVideo()
        self._resetPlaybackButtons()
        self.statusBar().showMessage(f"Audio error: {msg}", 5000)

    # ── ZMovie video playback ─────────────────────────────────────────────────

    def _playZmovieVideo(self):
        """Extract and play the current zmovie entry as MP4 video."""
        global currentZmovieKey
        if not zmovieOriginalData or not currentZmovieKey:
            self.statusBar().showMessage("No zmovie entry selected.", 3000)
            return

        # Parse entry index from key like "zmovie-00"
        try:
            entryIndex = int(currentZmovieKey.split("-")[1])
        except (IndexError, ValueError):
            self.statusBar().showMessage(f"Invalid zmovie key: {currentZmovieKey}", 3000)
            return

        self.stopVoxFile()

        self._zmovieConvThread = ZmovieConversionThread(
            zmovieOriginalData, entryIndex, parent=self
        )
        self._zmovieConvThread.conversionDone.connect(self._onZmovieConversionDone)
        self._zmovieConvThread.errorOccurred.connect(self._onPlaybackError)
        self._zmovieConvThread.start()

        self.ui.playVoxButton.setEnabled(False)
        self.stopVoxButton.setEnabled(True)
        self.statusBar().showMessage("Converting zmovie to MP4…")

    def _onZmovieConversionDone(self, mp4Path: str):
        """Load the converted MP4 into QMediaPlayer and start video playback."""
        self.ui.graphicsView.setVisible(False)
        self._videoWidget.setVisible(True)
        self._mediaPlayer.setSource(QUrl.fromLocalFile(mp4Path))
        self._mediaPlayer.play()
        self._frameTimer.start()
        self.statusBar().showMessage("Playing zmovie…")

    def _onVideoStateChanged(self, state):
        """Handle QMediaPlayer state changes — detect when video finishes."""
        if state == QMediaPlayer.PlaybackState.StoppedState:
            self._stopPreview()
            self._showGraphicsHideVideo()
            self._resetPlaybackButtons()
            self.statusBar().showMessage("Video playback finished.", 2000)

    def _showGraphicsHideVideo(self):
        """Restore graphicsView and hide the video widget."""
        self._videoWidget.setVisible(False)
        self.ui.graphicsView.setVisible(True)

    def _resetPlaybackButtons(self):
        if self._editorMode == "zmovie":
            can_play = bool(zmovieOriginalData)
        else:
            can_play = bool(voxManager) or bool(demoManager)
        self.ui.playVoxButton.setEnabled(can_play)
        self.stopVoxButton.setEnabled(False)

    # ── Subtitle preview ──────────────────────────────────────────────────────

    def _setupSubtitlePreview(self):
        """Initialise the QGraphicsScene used for the subtitle preview."""
        self._previewScene = QGraphicsScene(self)
        self._previewScene.setBackgroundBrush(QColor(0, 0, 0))
        self.ui.graphicsView.setScene(self._previewScene)
        self.ui.graphicsView.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.ui.graphicsView.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._previewTextItem = QGraphicsTextItem()
        font = QFont("Arial", 24, QFont.Weight.Bold)
        self._previewTextItem.setFont(font)
        self._previewTextItem.setDefaultTextColor(QColor(255, 255, 255))
        self._previewTextItem.setTextWidth(-1)  # no word-wrap; honour explicit newlines only
        self._previewScene.addItem(self._previewTextItem)

        # ── ZMovie video player (hidden by default) ─────────────────────────
        self._videoWidget = QVideoWidget()
        self._videoWidget.setVisible(False)
        # Insert video widget into the same layout as graphicsView
        parentLayout = self.ui.graphicsView.parentWidget().layout()
        if parentLayout:
            idx = parentLayout.indexOf(self.ui.graphicsView)
            parentLayout.insertWidget(idx + 1, self._videoWidget)

        self._mediaPlayer = QMediaPlayer(self)
        self._audioOutput = QAudioOutput(self)
        self._mediaPlayer.setAudioOutput(self._audioOutput)
        self._mediaPlayer.setVideoOutput(self._videoWidget)
        self._mediaPlayer.playbackStateChanged.connect(self._onVideoStateChanged)

        # Subtitle overlay label on top of video widget
        self._videoSubLabel = QLabel(self._videoWidget)
        self._videoSubLabel.setAlignment(Qt.AlignHCenter | Qt.AlignBottom)
        self._videoSubLabel.setStyleSheet(
            "color: white; background: transparent; font: bold 14pt Arial;"
            "padding: 6px;"
        )
        self._videoSubLabel.setWordWrap(True)

        # Track zmovie conversion thread
        self._zmovieConvThread: ZmovieConversionThread = None

    def _tickPreview(self):
        """Called ~30× per second while audio is playing. Updates the subtitle overlay."""
        # For zmovie video playback, use QMediaPlayer position instead of elapsed timer
        fps = float(self._appSettings.value("preview/subtitle_fps", SUBTITLE_FPS))
        if self._editorMode == "zmovie" and self._mediaPlayer.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            elapsed_ms = max(0, self._mediaPlayer.position())
            currentFrame = int(elapsed_ms * fps / 1000)
        else:
            elapsed_ms = max(0, self._elapsed.elapsed() - SUBTITLE_OFFSET_MS)
            currentFrame = int(elapsed_ms * fps / 1000)
        text = ""

        if self._editorMode in ("demo", "vox", "zmovie"):
            key, djson = self._modeData()
            for startFrame, sub in djson.get(key, {}).items():
                sf = int(startFrame)
                dur = int(sub.get("duration", "0"))
                if sf <= currentFrame < sf + dur:
                    text = sub.get("text", "").replace("｜", "\n")
                    break
        else:
            for line in self._getVoxSubtitleLines():
                if line.startFrame <= currentFrame < line.startFrame + line.displayFrames:
                    text = line.text.replace('\x00', '').replace("｜", "\n")
                    break

        # Route subtitle text to the appropriate display
        if self._editorMode == "zmovie" and self._videoWidget.isVisible():
            self._videoSubLabel.setText(text)
            self._videoSubLabel.setGeometry(0, self._videoWidget.height() - 60,
                                            self._videoWidget.width(), 60)
        else:
            self._previewTextItem.setPlainText(text)
            self._positionPreviewText()

    def _positionPreviewText(self):
        """Centre the text item horizontally and pin it near the bottom of the view."""
        vw = self.ui.graphicsView.viewport().width()
        vh = self.ui.graphicsView.viewport().height()
        self._previewScene.setSceneRect(0, 0, vw, vh)
        br = self._previewTextItem.boundingRect()
        x = max(0.0, (vw - br.width()) / 2)
        y = max(0.0, vh - br.height() - 12)
        self._previewTextItem.setPos(x, y)

    def _stopPreview(self):
        """Stop the frame timer and clear the subtitle overlay."""
        self._frameTimer.stop()
        self._previewTextItem.setPlainText("")
        self._videoSubLabel.setText("")
        if self._mediaPlayer.playbackState() != QMediaPlayer.PlaybackState.StoppedState:
            self._mediaPlayer.stop()

    # ── Demo mode ─────────────────────────────────────────────────────────────

    def loadDemoData(self):
        demoFile = self.openFileDialog("DAT Files (*.DAT *.dat)", "Load DEMO.DAT")
        if not demoFile:
            return
        self._loadDemoFromPath(demoFile)
        self._switchToDemoMode()
        self.statusBar().showMessage(
            f"DEMO.DAT loaded: {len(demoOriginalJson)} entries with dialogue", 4000
        )

    def _loadDemoFromPath(self, demoFile: str):
        global demoManager, demoOriginalData, demoFilePath
        global demoOriginalJson, demoAlteredJson, demoOffsetsJson, demoSeqToOffset
        demoOriginalData = open(demoFile, 'rb').read()
        demoFilePath = demoFile
        demoManager = DM.parseDemoFile(demoOriginalData)
        try:
            from DemoTools.extractDemoVox import extractFromFile
            demoOriginalJson = extractFromFile(demoFile, fileType="demo")
        except Exception as e:
            print(f"Warning: extractFromFile failed ({e}), extracting from parsed manager")
            demoOriginalJson = self._extractJsonFromManager(demoManager, "demo")
        demoAlteredJson = {}  # fresh load, no edits yet
        # Extract per-entry graphics dictionaries from captionChunks
        global demoGraphicsJson
        demoGraphicsJson = {}
        sortedOffsets = sorted(demoManager.keys(), key=lambda k: int(k))
        demoSeqToOffset = {f"demo-{i + 1:02}": off for i, off in enumerate(sortedOffsets)}
        try:
            for seqKey, off in demoSeqToOffset.items():
                demoObj = demoManager.get(off)
                if not demoObj:
                    continue
                for seg in demoObj.segments:
                    gfx = getattr(seg, '_graphicsData', b'')
                    if gfx:
                        demoGraphicsJson[seqKey] = gfx.hex()
                        break
        except Exception as e:
            print(f"Warning: demo graphics extraction failed: {e}")
        # Build offsets.json for STAGE.DIR adjustment
        demoOffsetsJson = {}
        for name, off in demoSeqToOffset.items():
            num = name.replace("demo-", "")
            demoOffsetsJson[num] = f"{int(off):08x}"

    def loadZmovieData(self):
        zmovieFile = self.openFileDialog("STR Files (*.STR *.str);;All Files (*)", "Load ZMOVIE.STR")
        if not zmovieFile:
            return
        try:
            self._loadZmovieFromPath(zmovieFile)
            self._switchToZmovieMode()
            self.statusBar().showMessage(
                f"ZMOVIE.STR loaded: {len(zmovieOriginalJson)} entries with subtitles", 4000
            )
        except Exception as e:
            QMessageBox.critical(self, "Load Error", str(e))

    def _loadZmovieFromPath(self, zmovieFile: str):
        global zmovieOriginalJson, zmovieAlteredJson, zmovieOriginalData, zmovieFilePath
        from zmovieTools.extractZmovie import extractFromFile as zmExtract
        zmovieOriginalData = open(zmovieFile, 'rb').read()
        zmovieFilePath = zmovieFile
        try:
            zmovieOriginalJson = zmExtract(zmovieFile)
        except Exception as e:
            print(f"Warning: ZMovie subtitle extraction failed: {e}")
            zmovieOriginalJson = {}
        zmovieAlteredJson = {}  # fresh load, no edits yet

    def exportZmovieJson(self):
        """Save zmovieAlteredJson (only changed entries) to a JSON file."""
        if not zmovieAlteredJson:
            QMessageBox.warning(self, "No changes", "No ZMOVIE entries have been modified.")
            return
        stem = os.path.splitext(os.path.basename(zmovieFilePath))[0].lower() if zmovieFilePath else "zmovie"
        default = os.path.join(os.path.dirname(zmovieFilePath) if zmovieFilePath else "",
                               f"{stem}-dialogue.json")
        filename = QFileDialog.getSaveFileName(
            self, "Export ZMovie JSON (altered only)", default, "JSON Files (*.json)"
        )[0]
        if not filename:
            return
        try:
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(zmovieAlteredJson, f, ensure_ascii=False, indent=2)
            self._modified = False
            self.statusBar().showMessage(
                f"ZMovie JSON exported ({len(zmovieAlteredJson)} altered entries): {filename}", 5000)
        except Exception as e:
            QMessageBox.critical(self, "Export Error", str(e))

    def compileZmovieFile(self):
        """Compile zmovieAlteredJson into a new ZMOVIE.STR using extractZmovie.compileToFile.
        Only altered entries are patched; unchanged entries keep original bytes."""
        if not zmovieOriginalData:
            QMessageBox.warning(self, "Nothing loaded", "No ZMOVIE.STR is currently loaded.")
            return
        if not zmovieAlteredJson:
            QMessageBox.warning(self, "No changes", "No ZMOVIE entries have been modified.")
            return
        filename = QFileDialog.getSaveFileName(
            self, "Compile ZMOVIE.STR", zmovieFilePath or "", "STR Files (*.STR *.str)"
        )[0]
        if not filename:
            return
        try:
            from zmovieTools.extractZmovie import compileToFile as zmCompile
            zmCompile(filename, zmovieOriginalData, zmovieAlteredJson)
            self._modified = False
            self.statusBar().showMessage(
                f"ZMOVIE.STR compiled ({len(zmovieAlteredJson)} entries patched): {filename}", 5000)
        except ValueError as e:
            QMessageBox.critical(self, "Compile Error — Subtitle Too Long", str(e))
        except Exception as e:
            QMessageBox.critical(self, "Compile Error", str(e))

    def exportDemoJson(self):
        """Save demoAlteredJson (only changed entries) to a JSON file."""
        if not demoAlteredJson:
            QMessageBox.warning(self, "No changes", "No DEMO entries have been modified.")
            return
        stem = os.path.splitext(os.path.basename(demoFilePath))[0].lower() if demoFilePath else "demo"
        default = os.path.join(os.path.dirname(demoFilePath) if demoFilePath else "", f"{stem}-dialogue.json")
        filename = QFileDialog.getSaveFileName(
            self, "Export DEMO JSON (altered only)", default, "JSON Files (*.json)"
        )[0]
        if not filename:
            return
        try:
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(demoAlteredJson, f, ensure_ascii=False, indent=2)
            self._modified = False
            self.statusBar().showMessage(f"DEMO JSON exported ({len(demoAlteredJson)} altered entries): {filename}", 5000)
        except Exception as e:
            QMessageBox.critical(self, "Export Error", str(e))

    def compileDemoDatFile(self):
        """Sync only altered DEMO entries into demoManager, then patch-in-place and write a new DEMO.DAT."""
        if not demoManager or not demoOriginalData:
            QMessageBox.warning(self, "Nothing loaded", "No DEMO.DAT is currently loaded.")
            return
        if not demoAlteredJson:
            QMessageBox.warning(self, "No changes", "No DEMO entries have been modified.")
            return
        filename = QFileDialog.getSaveFileName(
            self, "Compile DEMO.DAT", demoFilePath or "", "DAT Files (*.DAT *.dat)"
        )[0]
        if not filename:
            return
        try:
            self._syncJsonToDemoManager()
            alteredOffsets = set()
            for key in demoAlteredJson:
                off = demoSeqToOffset.get(key)
                if off:
                    alteredOffsets.add(int(off))
            patchedData = bytearray(demoOriginalData)
            sortedOffsets = sorted(int(k) for k in demoManager)
            for i, byteOffset in enumerate(sortedOffsets):
                if byteOffset not in alteredOffsets:
                    continue  # skip unmodified — keep original bytes
                demoObj = demoManager[str(byteOffset)]
                origLen = (
                    sortedOffsets[i + 1] - byteOffset
                    if i + 1 < len(sortedOffsets)
                    else len(demoOriginalData) - byteOffset
                )
                origSlice = bytes(demoOriginalData[byteOffset: byteOffset + origLen])
                newSlice = demoObj.getModifiedBytes(origSlice)
                if len(newSlice) != origLen:
                    QMessageBox.critical(
                        self, "Compile Error",
                        f"Demo at offset {byteOffset} changed size ({origLen} → {len(newSlice)}).\n"
                        "Cannot write — block counts must match."
                    )
                    return
                patchedData[byteOffset: byteOffset + origLen] = newSlice
            with open(filename, 'wb') as f:
                f.write(bytes(patchedData))
            self._modified = False
            self.statusBar().showMessage(
                f"DEMO.DAT compiled ({len(alteredOffsets)} entries patched): {filename}", 5000)
        except Exception as e:
            QMessageBox.critical(self, "Compile Error", str(e))

    def exportVoxJson(self):
        """Save voxAlteredJson (only changed entries) to a JSON file."""
        if not voxAlteredJson:
            QMessageBox.warning(self, "No changes", "No VOX entries have been modified.")
            return
        stem = os.path.splitext(os.path.basename(voxFilePath))[0].lower() if voxFilePath else "vox"
        default = os.path.join(os.path.dirname(voxFilePath) if voxFilePath else "",
                               f"{stem}-dialogue.json")
        filename = QFileDialog.getSaveFileName(
            self, "Export VOX JSON (altered only)", default, "JSON Files (*.json)"
        )[0]
        if not filename:
            return
        try:
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(voxAlteredJson, f, ensure_ascii=False, indent=2)
            self._modified = False
            self.statusBar().showMessage(f"VOX JSON exported ({len(voxAlteredJson)} altered entries): {filename}", 5000)
        except Exception as e:
            QMessageBox.critical(self, "Export Error", str(e))

    def compileVoxDatFile(self):
        """Sync only altered VOX entries into voxManager, then patch-in-place and write a new VOX.DAT.
        Unchanged entries are left pristine — their original bytes are kept as-is."""
        if not voxManager or not voxOriginalData:
            QMessageBox.warning(self, "Nothing loaded", "No VOX.DAT is currently loaded.")
            return
        if not voxAlteredJson:
            QMessageBox.warning(self, "No changes", "No VOX entries have been modified.")
            return
        filename = QFileDialog.getSaveFileName(
            self, "Compile VOX.DAT", voxFilePath or "", "DAT Files (*.DAT *.dat)"
        )[0]
        if not filename:
            return
        try:
            # Only sync altered entries — unchanged entries stay pristine
            self._syncJsonToManager(voxAlteredJson, voxSeqToOffset, voxManager)
            # Patch graphics data from voxGraphicsJson into captionChunks
            for key, gfxHex in voxGraphicsJson.items():
                offset = voxSeqToOffset.get(key)
                if not offset:
                    continue
                vox = voxManager.get(offset)
                if vox is None:
                    continue
                for seg in vox.segments:
                    if hasattr(seg, '_graphicsData'):
                        seg._graphicsData = bytes.fromhex(gfxHex)
                        break
            # Build set of altered byte offsets so we only patch those
            alteredOffsets = set()
            for key in voxAlteredJson:
                off = voxSeqToOffset.get(key)
                if off:
                    alteredOffsets.add(int(off))
            patchedData = bytearray(voxOriginalData)
            sortedOffsets = sorted(int(k) for k in voxManager)
            for i, byteOffset in enumerate(sortedOffsets):
                if byteOffset not in alteredOffsets:
                    continue  # skip unmodified — keep original bytes
                voxObj = voxManager[str(byteOffset)]
                origLen = (
                    sortedOffsets[i + 1] - byteOffset
                    if i + 1 < len(sortedOffsets)
                    else len(voxOriginalData) - byteOffset
                )
                origSlice = bytes(voxOriginalData[byteOffset: byteOffset + origLen])
                newSlice = voxObj.getModifiedBytes(origSlice)
                if len(newSlice) != origLen:
                    QMessageBox.critical(
                        self, "Compile Error",
                        f"VOX at offset {byteOffset} changed size ({origLen} \u2192 {len(newSlice)}).\n"
                        "Cannot write \u2014 block counts must match."
                    )
                    return
                patchedData[byteOffset: byteOffset + origLen] = newSlice
            with open(filename, 'wb') as f:
                f.write(bytes(patchedData))
            self._modified = False
            self.statusBar().showMessage(
                f"VOX.DAT compiled ({len(alteredOffsets)} entries patched): {filename}", 5000)
        except Exception as e:
            QMessageBox.critical(self, "Compile Error", str(e))

    @staticmethod
    def _extractJsonFromManager(manager: dict, prefix: str) -> dict:
        """Fallback: extract dialogue JSON from an already-parsed demo/vox manager.
        Produces the same format as DemoTools.extractDemoVox.extractFromFile."""
        sortedOffsets = sorted(manager.keys(), key=lambda k: int(k))
        result = {}
        for i, off in enumerate(sortedOffsets):
            key = f"demo-{i + 1:02}" if prefix == "demo" else f"vox-{i + 1:04}"
            demo = manager[off]
            subs = {}
            for seg in demo.segments:
                if hasattr(seg, 'subtitles'):
                    for sub in seg.subtitles:
                        text = sub.text.replace('\x00', '')
                        subs[str(sub.startFrame)] = {
                            "duration": str(sub.displayFrames),
                            "text": text,
                        }
            if subs:
                result[key] = subs
        return result

    def _syncJsonToManager(self, dialogueJson: dict, seqToOffset: dict, manager: dict):
        """Sync dialogue JSON edits into binary demo objects before patch-in-place compile."""
        import struct
        from scripts.demoClasses import dialogueLine
        for key, subtitles in dialogueJson.items():
            offset = seqToOffset.get(key)
            if not offset:
                continue
            demo = manager.get(offset)
            if demo is None:
                continue
            lines = []
            captionSeg = None
            for seg in demo.segments:
                if hasattr(seg, 'subtitles'):
                    lines.extend(seg.subtitles)
                    captionSeg = seg
            for idx, startFrame in enumerate(sorted(subtitles.keys(), key=int)):
                sub = subtitles[startFrame]
                if idx < len(lines):
                    lines[idx].startFrame    = int(startFrame)
                    lines[idx].displayFrames = int(sub.get("duration", "0"))
                    lines[idx].text          = sub.get("text", "")
                elif captionSeg is not None:
                    # Create new dialogueLine for added subtitles (e.g. from splits)
                    stubData = struct.pack("<III", 0, int(startFrame), int(sub.get("duration", "0")))
                    stubData += bytes(4)  # buffer
                    stubData += b'\x00'   # minimal text terminator
                    newLine = dialogueLine(stubData, captionSeg.kanjiDict)
                    newLine.text = sub.get("text", "")
                    captionSeg.subtitles.append(newLine)
                    print(f"[sync] Created new subtitle line in {key}: frame={startFrame}")

    def _syncJsonToDemoManager(self):
        self._syncJsonToManager(demoAlteredJson, demoSeqToOffset, demoManager)
        # Patch graphics data from demoGraphicsJson into captionChunks
        for key, gfxHex in demoGraphicsJson.items():
            offset = demoSeqToOffset.get(key)
            if not offset:
                continue
            demo = demoManager.get(offset)
            if demo is None:
                continue
            for seg in demo.segments:
                if hasattr(seg, '_graphicsData'):
                    seg._graphicsData = bytes.fromhex(gfxHex)
                    break

    def _openFontEditor(self):
        """Open the Font Editor dialog."""
        global activeTblMapping, activeTblRaw
        import scripts.translation.radioDict as RD

        dlg = FontEditorDialog(tblMapping=activeTblMapping or None, parent=self)
        dlg.exec()

        # Update the active .tbl mapping from the dialog
        activeTblMapping = dlg.tblMapping
        activeTblRaw = dlg.tblRaw

        # Push overrides to the encoder
        from scripts.fontTools import tblTools
        overrides = tblTools.tblToEncoderOverrides(activeTblMapping)
        RD.tblEncoderOverrides = overrides

    def _openCallDictEditor(self):
        """Open the Call Dictionary Editor for the current entry."""
        global radioGraphicsJson, demoGraphicsJson, voxGraphicsJson
        mode = self._editorMode

        if mode == "radio":
            callOffset = self.ui.offsetListBox.currentData() or ""
            if not callOffset:
                QMessageBox.information(self, "No Selection", "Select a radio call first.")
                return
            gfxHex = radioGraphicsJson.get(callOffset, "")
            dlg = CallDictEditorDialog(f"Call {callOffset}", gfxHex, parent=self)
            if dlg.exec() == QDialog.Accepted and dlg.modified:
                radioGraphicsJson[callOffset] = dlg.graphicsHex
                for call in radioManager.radioXMLData.findall(".//Call"):
                    if call.get("offset") == callOffset:
                        call.set("graphicsBytes", dlg.graphicsHex)
                        break
                self._modified = True

        elif mode == "demo":
            if not currentDemoKey:
                QMessageBox.information(self, "No Selection", "Select a demo entry first.")
                return
            gfxHex = demoGraphicsJson.get(currentDemoKey, "")
            dlg = CallDictEditorDialog(currentDemoKey, gfxHex, parent=self)
            if dlg.exec() == QDialog.Accepted and dlg.modified:
                demoGraphicsJson[currentDemoKey] = dlg.graphicsHex
                self._modified = True

        elif mode == "vox":
            if not currentVoxKey:
                QMessageBox.information(self, "No Selection", "Select a VOX entry first.")
                return
            gfxHex = voxGraphicsJson.get(currentVoxKey, "")
            dlg = CallDictEditorDialog(currentVoxKey, gfxHex, parent=self)
            if dlg.exec() == QDialog.Accepted and dlg.modified:
                voxGraphicsJson[currentVoxKey] = dlg.graphicsHex
                self._modified = True

        else:
            QMessageBox.information(
                self, "Not Available",
                "Call Dictionary Editor is currently available for Radio, Demo, and VOX modes."
            )

    def finalizeProject(self):
        """Batch-compile all (or selected) game data files."""
        from argparse import Namespace

        # ── Determine project folder & auto-detect STAGE.DIR ─────────────
        projectFolder = ""
        for path in [projectSettings.get("radio_dat_path", ""),
                     projectSettings.get("demo_dat_path", ""),
                     projectSettings.get("vox_dat_path", ""),
                     projectSettings.get("zmovie_str_path", ""),
                     demoFilePath, voxFilePath, zmovieFilePath]:
            if path and os.path.isfile(path):
                projectFolder = os.path.dirname(path)
                break

        stageDirAutoPath = projectSettings.get("stage_dir_path", "")
        if not stageDirAutoPath and projectFolder:
            for name in os.listdir(projectFolder):
                if name.upper() == "STAGE.DIR":
                    stageDirAutoPath = os.path.join(projectFolder, name)
                    break

        # ── Show dialog ──────────────────────────────────────────────────
        defaultOutDir = self._appSettings.value("build/output_dir", "")
        dlg = FinalizeProjectDialog(stageDirPath=stageDirAutoPath,
                                    defaultOutputDir=defaultOutDir, parent=self)
        if dlg.exec() != QDialog.Accepted:
            return

        # ── Validate prerequisites ───────────────────────────────────────
        missing = []
        if dlg.radioEnabled:
            if radioManager.radioXMLData is None:
                missing.append("RADIO: No XML data loaded.")
        if dlg.demoEnabled:
            if not demoManager or not demoOriginalData:
                missing.append("DEMO: No DEMO.DAT loaded.")
        if dlg.voxEnabled:
            if not voxManager or not voxOriginalData:
                missing.append("VOX: No VOX.DAT loaded.")
        if dlg.zmovieEnabled:
            if not zmovieOriginalData:
                missing.append("ZMOVIE: No ZMOVIE.STR loaded.")
        if missing:
            QMessageBox.critical(self, "Missing Data",
                "Cannot finalize — the following are not loaded:\n\n" +
                "\n".join(missing))
            return

        # ── Output folder setup ──────────────────────────────────────────
        outDir = dlg.outputDir
        if outDir:
            os.makedirs(outDir, exist_ok=True)
        elif dlg.replaceOriginals:
            ans = QMessageBox.warning(self, "Replace Original Files",
                "No output folder set — this will overwrite the original files. "
                "This cannot be undone. Continue?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if ans != QMessageBox.Yes:
                return

        def _outPath(origPath: str, defaultName: str) -> str:
            """Determine output path: custom folder with original name, or alongside original."""
            if outDir:
                return os.path.join(outDir, os.path.basename(origPath) if origPath else defaultName)
            elif dlg.replaceOriginals and origPath:
                return origPath
            elif origPath:
                return os.path.join(os.path.dirname(origPath), defaultName)
            else:
                return os.path.join(projectFolder or tmpDir, defaultName)

        results = []   # list of (label, success, detail)
        tmpDir = tempfile.mkdtemp(prefix="mgs-finalize-")
        stagePath = (dlg.stageDirPath or None) if dlg.stageEnabled else None
        # Working STAGE.DIR path — starts as original, updated if VOX offsets patch it
        stageWorkingPath = stagePath

        # ── Show progress dialog and capture stdout ───────────────────
        progress = FinalizeProgressDialog(parent=self)
        progress.show()
        QApplication.processEvents()
        _oldStdout, _oldStderr = sys.stdout, sys.stderr
        sys.stdout = _LogCapture(progress.log, _oldStdout)
        sys.stderr = _LogCapture(progress.log, _oldStderr)

        try:
            # ══════════════════════════════════════════════════════════════
            # Order matters! VOX must compile first so its new offsets can
            # be patched into STAGE.DIR and RADIO XML before RADIO compiles.
            # Build script order: VOX → VOX offsets → DEMO → RADIO → ZMOVIE
            # ══════════════════════════════════════════════════════════════

            # ── 1. VOX compile ───────────────────────────────────────────
            doVox = dlg.voxEnabled and bool(voxAlteredJson)
            if dlg.voxEnabled and not doVox:
                results.append(("VOX", True, "No changes — skipped"))
            if doVox:
                progress.setStep("Compiling VOX.DAT...")
                try:
                    self._syncJsonToManager(voxAlteredJson, voxSeqToOffset, voxManager)
                    alteredOffsets = set()
                    for key in voxAlteredJson:
                        off = voxSeqToOffset.get(key)
                        if off:
                            alteredOffsets.add(int(off))
                    # Rebuild entry by entry, padding each to 0x800 alignment
                    # Track new offsets for the offset adjuster
                    output = bytearray()
                    _newVoxEntryOffsets = []  # [(entryIndex, newByteOffset), ...]
                    sortedOffsets = sorted(int(k) for k in voxManager)
                    for i, byteOffset in enumerate(sortedOffsets):
                        _newVoxEntryOffsets.append((i, len(output)))
                        origLen = (sortedOffsets[i + 1] - byteOffset
                                   if i + 1 < len(sortedOffsets)
                                   else len(voxOriginalData) - byteOffset)
                        origSlice = bytes(voxOriginalData[byteOffset:byteOffset + origLen])
                        if byteOffset in alteredOffsets:
                            entry = voxManager[str(byteOffset)].getModifiedBytes(origSlice)
                        else:
                            entry = origSlice
                        output.extend(entry)
                        # Pad to 0x800 boundary
                        remainder = len(entry) % 0x800
                        if remainder != 0:
                            output.extend(b'\x00' * (0x800 - remainder))
                    outPath = _outPath(voxFilePath, "VOX-NEW.DAT")
                    with open(outPath, 'wb') as f:
                        f.write(bytes(output))
                    results.append(("VOX", True, f"VOX.DAT → {outPath}"))
                except Exception as e:
                    results.append(("VOX", False, str(e)))

            # ── 2. VOX offset adjust (STAGE.DIR + RADIO XML) ────────────
            #    Compute new offsets from the compiled VOX.DAT and patch
            #    STAGE.DIR Pv tags + RADIO XML voxCode attributes.
            if doVox and voxOffsetsJson:
                progress.setStep("Adjusting VOX offsets in STAGE.DIR + RADIO XML...")
                try:
                    from scripts.StageDirTools.voxOffsetAdjuster import (
                        buildBlockMap, adjustVoxOffsets, adjustRadioXml)
                    # Compute new offsets from the rebuilt VOX.DAT
                    newVoxOffsets = {}
                    for i, newOff in _newVoxEntryOffsets:
                        num = f"{i + 1:04}"
                        newVoxOffsets[num] = f"{newOff:08x}"
                    blockMap = buildBlockMap(voxOffsetsJson, newVoxOffsets)
                    changedBlocks = sum(1 for k, v in blockMap.items() if k != v)

                    voxAdjDetail = []
                    if changedBlocks > 0 and stagePath:
                        stageData = bytearray(open(stagePath, 'rb').read())
                        stageReps = adjustVoxOffsets(stageData, blockMap)
                        # Write to temp so RADIO compile can chain from it
                        stageTmp = os.path.join(tmpDir, "STAGE-voxadj.DIR")
                        with open(stageTmp, 'wb') as f:
                            f.write(stageData)
                        stageWorkingPath = stageTmp
                        voxAdjDetail.append(
                            f"STAGE.DIR: {stageReps} Pv refs patched")

                    if changedBlocks > 0 and dlg.radioEnabled and radioManager.radioXMLData is not None:
                        # Save XML to temp, patch it, reload
                        tmpXmlForVox = os.path.join(tmpDir, "radio-voxadj.xml")
                        radioManager.saveXML(tmpXmlForVox)
                        radioReps = adjustRadioXml(tmpXmlForVox, blockMap)
                        # Reload patched XML so RADIO compile uses updated voxCodes
                        radioManager.loadRadioXmlFile(tmpXmlForVox)
                        voxAdjDetail.append(
                            f"RADIO XML: {radioReps} voxCode refs patched")

                    if changedBlocks == 0:
                        voxAdjDetail.append("No VOX offsets changed — nothing to adjust")
                    results.append(("VOX Offsets", True, "; ".join(voxAdjDetail)))
                except Exception as e:
                    results.append(("VOX Offsets", False, str(e)))

            # ── 3. DEMO compile ──────────────────────────────────────────
            doDemo = dlg.demoEnabled and bool(demoAlteredJson)
            if dlg.demoEnabled and not doDemo:
                results.append(("DEMO", True, "No changes — skipped"))
            if doDemo:
                progress.setStep("Compiling DEMO.DAT...")
                try:
                    self._syncJsonToDemoManager()
                    alteredOffsets = set()
                    for key in demoAlteredJson:
                        off = demoSeqToOffset.get(key)
                        if off:
                            alteredOffsets.add(int(off))
                    # Rebuild entry by entry, padding each to 0x800 alignment
                    output = bytearray()
                    _newDemoEntryOffsets = []  # [(entryIndex, newByteOffset), ...]
                    sortedOffsets = sorted(int(k) for k in demoManager)
                    for i, byteOffset in enumerate(sortedOffsets):
                        _newDemoEntryOffsets.append((i, len(output)))
                        origLen = (sortedOffsets[i + 1] - byteOffset
                                   if i + 1 < len(sortedOffsets)
                                   else len(demoOriginalData) - byteOffset)
                        origSlice = bytes(demoOriginalData[byteOffset:byteOffset + origLen])
                        if byteOffset in alteredOffsets:
                            entry = demoManager[str(byteOffset)].getModifiedBytes(origSlice)
                        else:
                            entry = origSlice
                        output.extend(entry)
                        # Pad to 0x800 boundary
                        remainder = len(entry) % 0x800
                        if remainder != 0:
                            output.extend(b'\x00' * (0x800 - remainder))
                    outPath = _outPath(demoFilePath, "DEMO-NEW.DAT")
                    with open(outPath, 'wb') as f:
                        f.write(bytes(output))
                    results.append(("DEMO", True, f"DEMO.DAT → {outPath}"))
                except Exception as e:
                    results.append(("DEMO", False, str(e)))

            # ── 3b. DEMO offset adjust (STAGE.DIR) ──────────────────────
            #    Same pattern as VOX: compute new offsets, patch Ps/Pp tags
            if doDemo and demoOffsetsJson and stageWorkingPath:
                progress.setStep("Adjusting DEMO offsets in STAGE.DIR...")
                try:
                    import struct as _struct
                    # Compute new demo offsets from rebuilt DEMO.DAT
                    newDemoOffsets = {}
                    for i, newOff in _newDemoEntryOffsets:
                        num = f"{i + 1:02}"
                        newDemoOffsets[num] = f"{newOff:08x}"
                    # Build {old_hex_offset: new_hex_offset} map
                    offsetsToChange = {}
                    for key in demoOffsetsJson:
                        if key in newDemoOffsets:
                            offsetsToChange[demoOffsetsJson[key]] = newDemoOffsets[key]
                    changedDemo = sum(1 for k, v in offsetsToChange.items() if k != v)

                    if changedDemo > 0:
                        stageData = bytearray(open(stageWorkingPath, 'rb').read())
                        patternA = bytes.fromhex("5073060a")
                        patternB = bytes.fromhex("50700408")
                        offset = 0
                        reps = 0
                        while offset < len(stageData):
                            if (stageData[offset:offset + 4] == patternA and
                                    stageData[offset + 8:offset + 12] == patternB):
                                foundHex = stageData[offset + 4:offset + 8].hex()
                                if foundHex in offsetsToChange:
                                    stageData[offset + 4:offset + 8] = bytes.fromhex(
                                        offsetsToChange[foundHex])
                                    reps += 1
                                offset += 12
                            else:
                                offset += 1
                        stageTmp = os.path.join(tmpDir, "STAGE-demoadj.DIR")
                        with open(stageTmp, 'wb') as f:
                            f.write(stageData)
                        stageWorkingPath = stageTmp
                        results.append(("DEMO Offsets", True,
                            f"STAGE.DIR: {reps} demo refs patched"))
                    else:
                        results.append(("DEMO Offsets", True,
                            "No DEMO offsets changed — nothing to adjust"))
                except Exception as e:
                    results.append(("DEMO Offsets", False, str(e)))

            # ── 4. RADIO compile (uses patched XML from step 2,
            #       and stageWorkingPath which may have VOX/DEMO offset patches)
            if dlg.radioEnabled:
                progress.setStep("Compiling RADIO.DAT...")
                try:
                    import scripts.RadioDatRecompiler as RDR
                    # Reset module globals
                    RDR.stageBytes = b''
                    RDR.debug = False
                    RDR.subUseOriginalHex = False
                    RDR.useDWidSaveB = False
                    RDR.newOffsets = {}

                    # Inject altered iseeva JSON into XML before compilation
                    import copy
                    import xml.etree.ElementTree as _ET
                    xmlCopy = copy.deepcopy(radioManager.radioXMLData)
                    if radioAlteredJson:
                        for call in xmlCopy.findall(".//Call"):
                            callOff = call.get("offset")
                            altCall = radioAlteredJson.get(callOff)
                            if not altCall:
                                continue
                            call.set("modified", "True")
                            # 3-level: altCall is {voxOffset: {subOffset: text}}
                            def _injectIntoContainer(container, altSubs):
                                """Inject altered subs into a VOX_CUES or Call element.
                                Updates existing SUBTITLEs and creates missing ones."""
                                existingOffsets = set()
                                lastSub = None
                                for sub in container.findall("SUBTITLE"):
                                    existingOffsets.add(sub.get("offset"))
                                    newText = altSubs.get(sub.get("offset"))
                                    if newText is not None:
                                        sub.set("text", newText)
                                    lastSub = sub
                                # Create missing subtitles (e.g. from splits)
                                for subOff, text in altSubs.items():
                                    if subOff not in existingOffsets:
                                        attrs = {
                                            "offset": subOff, "length": "0",
                                            "face": lastSub.get("face", "95f2") if lastSub is not None else "95f2",
                                            "anim": lastSub.get("anim", "39c3") if lastSub is not None else "39c3",
                                            "unk3": lastSub.get("unk3", "0000") if lastSub is not None else "0000",
                                            "text": text, "textHex": "", "lengthLost": "0",
                                        }
                                        newElem = _ET.SubElement(container, "SUBTITLE", attrs)
                                        print(f"[finalize] Created missing SUBTITLE offset={subOff} in container offset={container.get('offset')}")

                            for vox in call.findall(".//VOX_CUES"):
                                altVox = altCall.get(vox.get("offset"), {})
                                if altVox:
                                    _injectIntoContainer(vox, altVox)
                            # Direct subs (no VOX_CUES) — keyed by call offset
                            altDirect = altCall.get(callOff, {})
                            if altDirect:
                                _injectIntoContainer(call, altDirect)
                    # Inject static fields (prompts, saves, contact names)
                    import scripts.xmlModifierTools as XMT
                    XMT.root = xmlCopy
                    if radioAlteredJson.get("_prompts"):
                        XMT.injectUserPrompts({"prompts": {"0": radioAlteredJson["_prompts"]}})
                    if radioAlteredJson.get("_saves"):
                        XMT.injectSaveBlocks({"saves": {"0": radioAlteredJson["_saves"]}})
                    if radioAlteredJson.get("_freqAdd"):
                        XMT.injectCallNames({"freqAdd": radioAlteredJson["_freqAdd"]})

                    tmpXmlPath = os.path.join(tmpDir, "radio-finalize.xml")
                    from xml.dom.minidom import parseString as _parseString
                    xmlStr = _parseString(_ET.tostring(xmlCopy)).toprettyxml(indent="  ")
                    with open(tmpXmlPath, 'w', encoding='utf-8') as _xf:
                        _xf.write(xmlStr)

                    radioDatPath = projectSettings.get("radio_dat_path", "")
                    radioOut = _outPath(radioDatPath, "RADIO-NEW.DAT")

                    # STAGE.DIR: RDR reads from stageWorkingPath (may be VOX/DEMO-patched temp)
                    # and writes its own radio offset patches to the final output
                    if stageWorkingPath:
                        stageOut = _outPath(stagePath, os.path.basename(stagePath) if stagePath else "STAGE.DIR")
                    else:
                        stageOut = None

                    args = Namespace(
                        input=tmpXmlPath, output=radioOut,
                        stage=stageWorkingPath, stageOut=stageOut,
                        prepare=False, hex=dlg.useOrigHex,
                        debug=dlg.debugOutput, double=dlg.doubleWidth,
                        integral=dlg.integral,
                        long=dlg.longHeaders, pad=dlg.pad, roundtrip=False,
                    )
                    RDR.main(args)
                    detail = f"RADIO.DAT → {radioOut}"
                    if stageOut:
                        detail += f"\nSTAGE.DIR → {stageOut}"
                    results.append(("RADIO", True, detail))
                except Exception as e:
                    results.append(("RADIO", False, str(e)))

            # ── 5. ZMOVIE compile ────────────────────────────────────────
            if dlg.zmovieEnabled and not zmovieAlteredJson:
                results.append(("ZMOVIE", True, "No changes — skipped"))
            if dlg.zmovieEnabled and zmovieAlteredJson:
                progress.setStep("Compiling ZMOVIE.STR...")
                try:
                    from zmovieTools.extractZmovie import compileToFile as zmCompile
                    outPath = _outPath(zmovieFilePath, "ZMOVIE-NEW.STR")
                    zmCompile(outPath, zmovieOriginalData, zmovieAlteredJson)
                    results.append(("ZMOVIE", True, f"ZMOVIE.STR → {outPath}"))
                except Exception as e:
                    results.append(("ZMOVIE", False, str(e)))

        finally:
            shutil.rmtree(tmpDir, ignore_errors=True)
            sys.stdout = _oldStdout
            sys.stderr = _oldStderr

        # ── Summary ──────────────────────────────────────────────────────
        lines = []
        for label, ok, detail in results:
            status = "OK" if ok else "FAILED"
            lines.append(f"[{status}] {label}: {detail}")
        summary = "\n\n".join(lines)
        hasErrors = not all(ok for _, ok, _ in results)
        progress.finish(summary, hasErrors)
        progress.exec()  # block until user clicks Close

    def _onModeChanged(self, action: QAction):
        if action == self.actionDemoMode:
            self._switchToDemoMode()
        elif action == self.actionVoxMode:
            self._switchToVoxMode()
        elif action == self.actionZmovieMode:
            self._switchToZmovieMode()
        else:
            self._switchToRadioMode()

    _TAB_INDEX = {"radio": 0, "demo": 1, "vox": 2, "zmovie": 3}

    def _onTabChanged(self, index: int):
        """Called when the user clicks a tab; delegates to the appropriate switch method."""
        modes = ["radio", "demo", "vox", "zmovie"]
        if 0 <= index < len(modes):
            mode = modes[index]
            if mode != self._editorMode:
                {"radio": self._switchToRadioMode,
                 "demo":  self._switchToDemoMode,
                 "vox":   self._switchToVoxMode,
                 "zmovie": self._switchToZmovieMode}[mode]()

    def _syncTab(self):
        """Keep the tab bar in sync with the current editor mode."""
        idx = self._TAB_INDEX.get(self._editorMode, 0)
        self._modeTabBar.blockSignals(True)
        self._modeTabBar.setCurrentIndex(idx)
        self._modeTabBar.blockSignals(False)

    def _modeData(self) -> tuple:
        """Return (currentKey, dialogueJson) for the active sequence-based mode.
        For demo/vox modes, returns the merged view (altered preferred, original fallback)."""
        if self._editorMode == "demo":
            return currentDemoKey, _mergedDemoJson()
        elif self._editorMode == "vox":
            return currentVoxKey, _mergedVoxJson()
        else:  # zmovie
            return currentZmovieKey, _mergedZmovieJson()

    def _hideRadioWidgets(self):
        self.ui.audioCueListView.setVisible(False)
        self.ui.audioCueListView.setEnabled(True)
        self.ui.FreqLabel.setVisible(False)
        self.ui.FreqDisplay.setVisible(False)
        self.ui.VoxBlockAddressLabel.setVisible(False)
        self.ui.VoxBlockAddressDisplay.setVisible(False)
        self.ui.VoxAddressLabel.setVisible(False)
        self.ui.VoxAddressDisplay.setVisible(False)
        self.chkDisc1Only.setVisible(False)
        self.chkUnclaimedVox.setVisible(False)
        self.chkSkipVoxSort.setVisible(False)
        self.freqFilterLabel.setVisible(False)
        self.freqFilterCombo.setVisible(False)
        self.revertVoxButton.setVisible(False)
        self.ui.startFrameBox.setEnabled(True)
        self.ui.durationBox.setEnabled(True)
        self.btnEditPrompts.setVisible(False)
        self.btnEditSaveLocations.setVisible(False)
        self.btnEditContactNames.setVisible(False)

    def _switchToDemoMode(self):
        self._editorMode = "demo"
        self.actionDemoMode.setChecked(True)
        self._syncTab()
        self._hideRadioWidgets()
        self.ui.playVoxButton.setEnabled(bool(demoOriginalJson) or bool(demoAlteredJson))
        self._populateDemoOffsets()
        self._clearEditor()
        self.ui.subsPreviewList.clear()
        if self.ui.offsetListBox.count() > 0:
            self.ui.offsetListBox.setCurrentIndex(0)
            self._selectDemo(0)

    def _populateDemoOffsets(self):
        """Repopulate the DEMO offset list with bullet markers on altered entries."""
        self._hideEmptyHint()
        merged = _mergedDemoJson()
        current = self.ui.offsetListBox.currentData()
        self.ui.offsetListBox.blockSignals(True)
        self.ui.offsetListBox.clear()
        for name in sorted(merged.keys()):
            label = f"\u2022 {name}" if name in demoAlteredJson else name
            self.ui.offsetListBox.addItem(label, userData=name)
        self.ui.offsetListBox.blockSignals(False)
        idx = self.ui.offsetListBox.findData(current)
        self.ui.offsetListBox.setCurrentIndex(idx if idx >= 0 else 0)

    def _switchToZmovieMode(self):
        self._editorMode = "zmovie"
        self.actionZmovieMode.setChecked(True)
        self._syncTab()
        self._hideRadioWidgets()
        self.ui.playVoxButton.setEnabled(bool(zmovieOriginalData))
        self._populateZmovieOffsets()
        self._clearEditor()
        self.ui.subsPreviewList.clear()
        if self.ui.offsetListBox.count() > 0:
            self.ui.offsetListBox.setCurrentIndex(0)
            self._selectZmovie(0)

    def _populateZmovieOffsets(self):
        """Repopulate the ZMOVIE offset list with bullet markers on altered entries."""
        self._hideEmptyHint()
        merged = _mergedZmovieJson()
        current = self.ui.offsetListBox.currentData()
        self.ui.offsetListBox.blockSignals(True)
        self.ui.offsetListBox.clear()
        for name in sorted(merged.keys()):
            label = f"\u2022 {name}" if name in zmovieAlteredJson else name
            self.ui.offsetListBox.addItem(label, userData=name)
        self.ui.offsetListBox.blockSignals(False)
        idx = self.ui.offsetListBox.findData(current)
        self.ui.offsetListBox.setCurrentIndex(idx if idx >= 0 else 0)

    def _switchToVoxMode(self):
        self._editorMode = "vox"
        self.actionVoxMode.setChecked(True)
        self._syncTab()
        self._hideRadioWidgets()
        self.ui.playVoxButton.setEnabled(bool(voxOriginalJson) or bool(voxAlteredJson))
        self.chkUnclaimedVox.setVisible(bool(_radioClaimedVoxAddrs))
        self.revertVoxButton.setVisible(False)
        self._populateVoxOffsets()
        self._clearEditor()
        self.ui.subsPreviewList.clear()
        if self.ui.offsetListBox.count() > 0:
            self.ui.offsetListBox.setCurrentIndex(0)
            self._selectVox(0)

    def _switchToRadioMode(self):
        self._editorMode = "radio"
        self.actionRadioMode.setChecked(True)
        self._syncTab()
        # Restore radio-specific widgets
        self.ui.audioCueListView.setVisible(not self.chkSkipVoxSort.isChecked())
        self.ui.FreqLabel.setVisible(True)
        self.ui.FreqDisplay.setVisible(True)
        self.ui.VoxBlockAddressLabel.setVisible(True)
        self.ui.VoxBlockAddressDisplay.setVisible(True)
        self.ui.VoxAddressLabel.setVisible(True)
        self.ui.VoxAddressDisplay.setVisible(True)
        self.ui.playVoxButton.setEnabled(bool(voxManager))
        self.chkUnclaimedVox.setVisible(False)
        self.chkSkipVoxSort.setVisible(True)
        self.chkDisc1Only.setVisible(bool(_radioDisc2Offsets))
        self.revertVoxButton.setVisible(False)
        self.ui.startFrameBox.setEnabled(False)
        self.ui.durationBox.setEnabled(False)
        self.freqFilterLabel.setVisible(True)
        self.freqFilterCombo.setVisible(True)
        hasXml = radioManager.radioXMLData is not None
        self.btnEditPrompts.setVisible(True)
        self.btnEditPrompts.setEnabled(hasXml)
        self.btnEditSaveLocations.setVisible(True)
        self.btnEditSaveLocations.setEnabled(hasXml)
        self.btnEditContactNames.setVisible(True)
        self.btnEditContactNames.setEnabled(hasXml)
        self._refreshFreqFilter()
        self._clearEditor()
        self.ui.subsPreviewList.clear()
        self.ui.audioCueListView.clear()
        self._populateRadioOffsets()
        # Explicitly populate the call — setCurrentIndex is a no-op if index didn't change,
        # so currentIndexChanged won't fire and audioCueListView would stay blank.
        self._selectRadioCall(self.ui.offsetListBox.currentIndex())
        # Auto-select first audio cue and first subtitle so editor is ready immediately
        if self.ui.audioCueListView.count() > 0:
            self.ui.audioCueListView.setCurrentRow(0)
        if self.ui.subsPreviewList.count() > 0:
            self.ui.subsPreviewList.setCurrentRow(0)

    def _openStaticFieldsDialog(self, initialTab=0):
        """Open the radio static fields editor dialog."""
        existingEdits = {
            "_prompts": radioAlteredJson.get("_prompts", {}),
            "_saves":   radioAlteredJson.get("_saves", {}),
            "_freqAdd": radioAlteredJson.get("_freqAdd", {}),
        }
        dlg = RadioStaticFieldsDialog(
            radioManager.radioXMLData, existingEdits,
            initialTab=initialTab, parent=self)
        if dlg.exec() == QDialog.Accepted:
            for key in ("_prompts", "_saves", "_freqAdd"):
                if dlg.result_data.get(key):
                    radioAlteredJson[key] = dlg.result_data[key]
                elif key in radioAlteredJson:
                    del radioAlteredJson[key]
            self._modified = True

    def _getDemoSubtitleLines(self) -> list:
        """Return a flat list of dialogueLine objects from the currently selected demo entry.
        Uses the demoSeqToOffset mapping to resolve the raw offset from the sequential name."""
        offset = demoSeqToOffset.get(currentDemoKey)
        if not offset or not demoManager:
            return []
        demo = demoManager.get(offset)
        if demo is None:
            return []
        lines = []
        for seg in demo.segments:
            if hasattr(seg, 'subtitles'):   # avoids module-identity isinstance issue
                lines.extend(seg.subtitles)
        return lines

    def _updateDemoSegmentSubtitle(self, idx: int, startFrame: int, displayFrames: int, text: str):
        """Mirror a JSON edit back into the demoManager dialogueLine for patch-in-place save."""
        lines = self._getDemoSubtitleLines()
        if idx < len(lines):
            lines[idx].startFrame = startFrame
            lines[idx].displayFrames = displayFrames
            lines[idx].text = text


    # ── Open Folder ───────────────────────────────────────────────────────────

    def openFolder(self):
        """Scan a folder for RADIO.DAT, DEMO.DAT, VOX.DAT, ZMOVIE.STR and load them all."""
        folder = QFileDialog.getExistingDirectory(self, "Open Game Data Folder")
        if not folder:
            return

        radioPath    = self._findFileInFolder(folder, "RADIO.DAT")
        demoPath     = self._findFileInFolder(folder, "DEMO.DAT")
        voxPath      = self._findFileInFolder(folder, "VOX.DAT")
        zmoviePath   = self._findFileInFolder(folder, "ZMOVIE.STR")
        brfPath      = self._findFileInFolder(folder, "BRF.DAT")
        facePath     = self._findFileInFolder(folder, "FACE.DAT")
        stageDirPath = self._findFileInFolder(folder, "STAGE.DIR")

        missing = [n for n, p in [
            ("RADIO.DAT",  radioPath),
            ("DEMO.DAT",   demoPath),
            ("VOX.DAT",    voxPath),
            ("ZMOVIE.STR", zmoviePath),
        ] if not p]
        if missing:
            QMessageBox.warning(
                self, "Files Missing",
                "Could not find the following files in the selected folder:\n"
                + "\n".join(f"  \u2022 {m}" for m in missing)
                + "\n\nContinuing with what\u2019s available."
            )
        if not radioPath and not demoPath and not voxPath and not zmoviePath:
            return
        self._loadAllFromFolder(radioPath, demoPath, voxPath, zmoviePath,
                                brfPath=brfPath, facePath=facePath,
                                stageDirPath=stageDirPath)

    def _findFileInFolder(self, folder: str, name: str) -> str:
        """Case-insensitive file search in a folder. Returns full path or empty string."""
        target = name.lower()
        try:
            for f in os.listdir(folder):
                if f.lower() == target:
                    return os.path.join(folder, f)
        except OSError:
            pass
        return ""

    def _loadAllFromFolder(self, radioPath: str, demoPath: str, voxPath: str, zmoviePath: str = "",
                           brfPath: str = "", facePath: str = "", stageDirPath: str = ""):
        global projectSettings
        errors = []

        if radioPath:
            try:
                self.statusBar().showMessage("Loading RADIO.DAT\u2026")
                QApplication.processEvents()
                self._loadRadioFromPath(radioPath)
            except Exception as e:
                errors.append(f"RADIO.DAT: {e}")

        if demoPath:
            try:
                self.statusBar().showMessage("Loading DEMO.DAT\u2026")
                QApplication.processEvents()
                self._loadDemoFromPath(demoPath)
            except Exception as e:
                errors.append(f"DEMO.DAT: {e}")

        if voxPath:
            try:
                self.statusBar().showMessage("Loading VOX.DAT\u2026")
                QApplication.processEvents()
                self._loadVoxFromPath(voxPath)
            except Exception as e:
                errors.append(f"VOX.DAT: {e}")

        if zmoviePath:
            try:
                self.statusBar().showMessage("Loading ZMOVIE.STR\u2026")
                QApplication.processEvents()
                self._loadZmovieFromPath(zmoviePath)
            except Exception as e:
                errors.append(f"ZMOVIE.STR: {e}")

        projectSettings = {
            "radio_dat_path":   radioPath   or "",
            "demo_dat_path":    demoPath    or "",
            "vox_dat_path":     voxPath     or "",
            "zmovie_str_path":  zmoviePath  or "",
            "brf_dat_path":     brfPath      or "",
            "face_dat_path":    facePath     or "",
            "stage_dir_path":   stageDirPath or "",
        }

        if errors:
            QMessageBox.warning(self, "Load Errors",
                                "Some files failed to load:\n\n" + "\n".join(errors))

        # Switch to radio mode to show the loaded data
        if radioManager.radioXMLData is not None:
            self.actionDemoMode.setChecked(False)
            self._switchToRadioMode()

        self.statusBar().showMessage(
            f"Folder loaded — Radio: {'OK' if radioPath else 'missing'}  "
            f"Demo: {'OK' if demoPath else 'missing'}  "
            f"VOX: {'OK' if voxPath else 'missing'}  "
            f"ZMovie: {'OK' if zmoviePath else 'missing'}",
            8000
        )

    def _loadRadioFromPath(self, radioPath: str):
        """Parse RADIO.DAT via RadioDatTools and load the resulting XML."""
        import RadioDatTools as RDT
        import xml.etree.ElementTree as ET
        from xml.dom.minidom import parseString

        # Reset module-level globals so we always get a fresh parse tree
        RDT.root = ET.Element("RadioData")
        RDT.elementStack = [(RDT.root, -1)]
        RDT.radioData = b''
        RDT.fileSize = 0
        RDT.offset = 0
        RDT.callDict = {}
        RDT.callOffsetToNext = {}
        RDT.customGraphicsData = []

        tmpDir = tempfile.mkdtemp()
        tmpBase = os.path.join(tmpDir, "radio_parse")
        try:
            RDT.setRadioData(radioPath)
            RDT.radioDict.openRadioFile(radioPath)
            RDT.root.set('length', str(RDT.fileSize))
            RDT.analyzeRadioFile(tmpBase)  # builds RDT.root, writes log to tmpDir

            xmlStr = parseString(ET.tostring(RDT.root)).toprettyxml(indent="  ")
            xmlPath = tmpBase + '.xml'
            with open(xmlPath, 'w', encoding='utf-8') as xf:
                xf.write(xmlStr)

            radioManager.loadRadioXmlFile(xmlPath)
            global radioOriginalJson, radioAlteredJson, radioGraphicsJson
            radioOriginalJson = _extractIseevaCallsFromXml(radioManager.radioXMLData)
            radioAlteredJson = {}
            # Extract per-call graphics dictionaries from XML
            radioGraphicsJson = {}
            for call in radioManager.radioXMLData.findall(".//Call"):
                gfxHex = call.get("graphicsBytes", "")
                if gfxHex:
                    radioGraphicsJson[call.get("offset")] = gfxHex
            self._buildRadioVoxIndex()
            self._populateRadioOffsets()
            if projectFilePath:
                self.setWindowTitle(f"Dialogue Editor \u2014 {os.path.basename(projectFilePath)}")
            else:
                self.setWindowTitle("Dialogue Editor \u2014 Unsaved Project")
        finally:
            shutil.rmtree(tmpDir, ignore_errors=True)

    # ── Project Save ──────────────────────────────────────────────────────────

    def saveProjectAs(self):
        global projectFilePath
        filename = QFileDialog.getSaveFileName(
            self, "Save MTP Project", projectFilePath or "", "MTP Files (*.mtp)"
        )[0]
        if not filename:
            return
        if not filename.endswith('.mtp'):
            filename += '.mtp'
        projectFilePath = filename
        try:
            self._writeProjectFile(filename)
            self.actionSaveProject.setEnabled(True)
            self._modified = False
            self.statusBar().showMessage(f"Project saved: {filename}", 5000)
        except Exception as e:
            QMessageBox.critical(self, "Save Error", str(e))

    def saveProject(self):
        global projectFilePath
        if not projectFilePath:
            self.saveProjectAs()
        else:
            try:
                self._writeProjectFile(projectFilePath)
                self._modified = False
                self.statusBar().showMessage(f"Project saved: {projectFilePath}", 5000)
            except Exception as e:
                QMessageBox.critical(self, "Save Error", str(e))

    def closeProject(self):
        """Close the current project and reset all state to initial values."""
        global radioManager, projectFilePath, projectSettings
        global voxManager, voxOriginalData, voxFilePath
        global demoManager, demoOriginalData, demoFilePath, currentDemoKey, currentVoxKey
        global demoOriginalJson, demoAlteredJson, demoOffsetsJson, demoSeqToOffset
        global voxOriginalJson, voxAlteredJson, voxOffsetsJson, voxSeqToOffset
        global zmovieOriginalJson, zmovieAlteredJson, zmovieOriginalData, zmovieFilePath, currentZmovieKey
        global radioOriginalJson, radioAlteredJson
        global currentSubIndex, currentVoxOffset
        global activeTblMapping, activeTblRaw

        if self._modified:
            reply = QMessageBox.question(
                self, "Unsaved Changes",
                "You have unsaved changes. Close anyway?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No
            )
            if reply != QMessageBox.Yes:
                return

        # Reset radio state
        radioManager = RDE()
        radioOriginalJson = {}
        radioAlteredJson  = {}

        # Reset VOX state
        voxManager = {}
        voxOriginalData = b''
        voxFilePath = ""
        voxOriginalJson = {}
        voxAlteredJson  = {}
        voxOffsetsJson  = {}
        voxSeqToOffset  = {}

        # Reset Demo state
        demoManager = {}
        demoOriginalData = b''
        demoFilePath = ""
        currentDemoKey = ""
        currentVoxKey  = ""
        demoOriginalJson = {}
        demoAlteredJson  = {}
        demoOffsetsJson  = {}
        demoSeqToOffset  = {}

        # Reset ZMovie state
        zmovieOriginalJson = {}
        zmovieAlteredJson  = {}
        zmovieOriginalData = b''
        zmovieFilePath = ""
        currentZmovieKey = ""

        # Reset cursor state
        currentSubIndex = -1
        currentVoxOffset = ""

        # Reset font table overrides
        activeTblMapping = {}
        activeTblRaw = ""
        try:
            import scripts.translation.radioDict as RD
            RD.tblEncoderOverrides = {}
        except Exception:
            pass

        # Reset project identity
        projectFilePath = ""
        projectSettings = {}
        self._modified = False

        # Clear UI widgets
        self.ui.offsetListBox.clear()
        self.ui.audioCueListView.clear()
        self.ui.subsPreviewList.clear()
        self.ui.FreqDisplay.display(0)
        self.ui.VoxAddressDisplay.setText("")
        self.ui.VoxBlockAddressDisplay.setText("")
        self._clearEditor()
        self.revertVoxButton.setVisible(False)

        # Disable radio-specific edit buttons (no XML loaded)
        self.btnEditPrompts.setEnabled(False)
        self.btnEditSaveLocations.setEnabled(False)
        self.btnEditContactNames.setEnabled(False)

        # Disable Save Project until a new project is opened/created
        self.actionSaveProject.setEnabled(False)

        # Return to radio mode (default)
        self._switchToRadioMode()

        # Show first-run hint again
        self._emptyHint.setVisible(True)

        self.setWindowTitle("Dialogue Editor")
        self.statusBar().showMessage("Project closed.", 3000)

    def _writeProjectFile(self, path: str):
        import xml.etree.ElementTree as ET
        from xml.dom.minidom import parseString
        with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.writestr('settings.json', json.dumps(projectSettings, indent=2))
            if radioManager.radioXMLData is not None:
                xmlStr = parseString(ET.tostring(radioManager.radioXMLData)).toprettyxml(indent="  ")
                zf.writestr('radio.xml', xmlStr)
            if radioOriginalJson:
                zf.writestr('radio-original.json',
                            json.dumps(radioOriginalJson, ensure_ascii=False, indent=2))
            if radioAlteredJson:
                zf.writestr('radio-altered.json',
                            json.dumps(radioAlteredJson, ensure_ascii=False, indent=2))
            if demoOriginalJson:
                zf.writestr('demo-original.json',
                            json.dumps(demoOriginalJson, ensure_ascii=False, indent=2))
            if demoAlteredJson:
                zf.writestr('demo-altered.json',
                            json.dumps(demoAlteredJson, ensure_ascii=False, indent=2))
            if demoOffsetsJson:
                zf.writestr('demo-offsets.json',
                            json.dumps(demoOffsetsJson, indent=2))
            if voxOriginalJson:
                zf.writestr('vox-original.json',
                            json.dumps(voxOriginalJson, ensure_ascii=False, indent=2))
            if voxAlteredJson:
                zf.writestr('vox-altered.json',
                            json.dumps(voxAlteredJson, ensure_ascii=False, indent=2))
            if voxOffsetsJson:
                zf.writestr('vox-offsets.json',
                            json.dumps(voxOffsetsJson, indent=2))
            if zmovieOriginalJson:
                zf.writestr('zmovie-original.json',
                            json.dumps(zmovieOriginalJson, ensure_ascii=False, indent=2))
            if zmovieAlteredJson:
                zf.writestr('zmovie-altered.json',
                            json.dumps(zmovieAlteredJson, ensure_ascii=False, indent=2))
            if radioGraphicsJson:
                zf.writestr('radio-graphics.json',
                            json.dumps(radioGraphicsJson, indent=2))
            if demoGraphicsJson:
                zf.writestr('demo-graphics.json',
                            json.dumps(demoGraphicsJson, indent=2))
            if voxGraphicsJson:
                zf.writestr('vox-graphics.json',
                            json.dumps(voxGraphicsJson, indent=2))
            if activeTblRaw:
                zf.writestr('font.tbl', activeTblRaw)

    # ── Project Open ──────────────────────────────────────────────────────────

    def openProject(self):
        global projectFilePath, projectSettings
        global demoOriginalJson, demoAlteredJson, demoOffsetsJson, demoSeqToOffset
        global voxOriginalJson, voxAlteredJson, voxOffsetsJson, voxSeqToOffset
        global zmovieOriginalJson, zmovieAlteredJson
        global radioOriginalJson, radioAlteredJson
        global radioGraphicsJson, demoGraphicsJson, voxGraphicsJson
        global activeTblMapping, activeTblRaw

        filename = QFileDialog.getOpenFileName(
            self, "Open MTP Project", projectFilePath or "", "MTP Files (*.mtp)"
        )[0]
        if not filename:
            return

        try:
            with zipfile.ZipFile(filename, 'r') as zf:
                names = zf.namelist()
                settings = json.loads(zf.read('settings.json'))
                radioXml = zf.read('radio.xml').decode('utf-8') if 'radio.xml' in names else None
                # New split DEMO format
                demoOrigJson = json.loads(zf.read('demo-original.json'))  if 'demo-original.json'    in names else {}
                demoAltJson  = json.loads(zf.read('demo-altered.json'))   if 'demo-altered.json'     in names else {}
                demoOffJson  = json.loads(zf.read('demo-offsets.json'))   if 'demo-offsets.json'     in names else {}
                # Backward compat
                demoLegacyJson = json.loads(zf.read('demo-dialogue.json')) if 'demo-dialogue.json'   in names else {}
                # New split VOX format
                voxOrigJson = json.loads(zf.read('vox-original.json'))    if 'vox-original.json'     in names else {}
                voxAltJson  = json.loads(zf.read('vox-altered.json'))     if 'vox-altered.json'      in names else {}
                voxOffJson  = json.loads(zf.read('vox-offsets.json'))     if 'vox-offsets.json'      in names else {}
                # Backward compat: old projects only have vox-dialogue.json
                voxLegacyJson = json.loads(zf.read('vox-dialogue.json')) if 'vox-dialogue.json'     in names else {}
                # New split ZMOVIE format
                zmOrigJson    = json.loads(zf.read('zmovie-original.json')) if 'zmovie-original.json' in names else {}
                zmAltJson     = json.loads(zf.read('zmovie-altered.json'))  if 'zmovie-altered.json'  in names else {}
                # Backward compat
                zmLegacyJson  = json.loads(zf.read('zmovie-dialogue.json')) if 'zmovie-dialogue.json' in names else {}
                # Radio iseeva JSON
                radioOrigJson = json.loads(zf.read('radio-original.json')) if 'radio-original.json' in names else {}
                radioAltJson  = json.loads(zf.read('radio-altered.json'))  if 'radio-altered.json'  in names else {}
                tblRaw      = zf.read('font.tbl').decode('utf-8')          if 'font.tbl'              in names else ""
                # Graphics dictionaries
                radioGfxJson = json.loads(zf.read('radio-graphics.json')) if 'radio-graphics.json' in names else {}
                demoGfxJson  = json.loads(zf.read('demo-graphics.json'))  if 'demo-graphics.json'  in names else {}
                voxGfxJson   = json.loads(zf.read('vox-graphics.json'))   if 'vox-graphics.json'   in names else {}
        except Exception as e:
            QMessageBox.critical(self, "Open Failed", f"Could not read project file:\n{e}")
            return

        # Restore radio XML
        if radioXml:
            try:
                with tempfile.NamedTemporaryFile(
                    suffix='.xml', delete=False, mode='w', encoding='utf-8'
                ) as tmp:
                    tmp.write(radioXml)
                    tmpPath = tmp.name
                radioManager.loadRadioXmlFile(tmpPath)
                os.unlink(tmpPath)
                self._buildRadioVoxIndex()
                self._populateRadioOffsets()
            except Exception as e:
                QMessageBox.warning(self, "Radio Load Error", f"Failed to restore radio XML:\n{e}")

        # Restore radio iseeva JSON
        if radioOrigJson:
            radioOriginalJson = radioOrigJson
            radioAlteredJson  = radioAltJson
        elif radioXml and radioManager.radioXMLData is not None:
            # Migration: old .mtp with radio.xml but no iseeva JSONs
            radioOriginalJson = _extractIseevaCallsFromXml(radioManager.radioXMLData)
            radioAlteredJson = radioAltJson  # preserve any loaded alterations

        # Restore demo/vox subtitle JSON (auto-convert v1 → v2 if needed)
        if demoOrigJson:
            demoOriginalJson = _ensureJsonV2(demoOrigJson)
            demoAlteredJson  = _ensureJsonV2(demoAltJson)
            demoOffsetsJson  = demoOffJson
            demoSeqToOffset  = {k: "" for k in set(demoOriginalJson) | set(demoAlteredJson)}
        elif demoLegacyJson:
            demoOriginalJson = _ensureJsonV2(demoLegacyJson)
            demoAlteredJson  = {}
            demoOffsetsJson  = {}
            demoSeqToOffset  = {k: "" for k in demoOriginalJson}
        if voxOrigJson:
            voxOriginalJson = _ensureJsonV2(voxOrigJson)
            voxAlteredJson  = _ensureJsonV2(voxAltJson)
            voxOffsetsJson  = voxOffJson
            voxSeqToOffset  = {k: "" for k in set(voxOriginalJson) | set(voxAlteredJson)}
        elif voxLegacyJson:
            voxOriginalJson = _ensureJsonV2(voxLegacyJson)
            voxAlteredJson  = {}
            voxOffsetsJson  = {}
            voxSeqToOffset  = {k: "" for k in voxOriginalJson}
        if zmOrigJson:
            zmovieOriginalJson = _ensureJsonV2(zmOrigJson)
            zmovieAlteredJson  = _ensureJsonV2(zmAltJson)
        elif zmLegacyJson:
            zmovieOriginalJson = _ensureJsonV2(zmLegacyJson)
            zmovieAlteredJson  = {}

        # Restore graphics dictionaries
        radioGraphicsJson = radioGfxJson
        demoGraphicsJson  = demoGfxJson
        voxGraphicsJson   = voxGfxJson
        # Migration: extract from XML/managers if no graphics JSON was stored
        if not radioGraphicsJson and radioManager.radioXMLData is not None:
            for call in radioManager.radioXMLData.findall(".//Call"):
                gfxHex = call.get("graphicsBytes", "")
                if gfxHex:
                    radioGraphicsJson[call.get("offset")] = gfxHex

        # Restore font table overrides
        if tblRaw:
            from scripts.fontTools import tblTools
            import scripts.translation.radioDict as RD
            activeTblRaw = tblRaw
            activeTblMapping = tblTools.loadTblFromString(tblRaw)
            RD.tblEncoderOverrides = tblTools.tblToEncoderOverrides(activeTblMapping)

        # Attempt to load audio capability from original DAT paths
        self._tryLoadAudioManagers(settings)

        projectFilePath = filename
        projectSettings = settings
        self.actionSaveProject.setEnabled(True)
        self._modified = False
        self.setWindowTitle(f"Dialogue Editor \u2014 {os.path.basename(filename)}")
        self.statusBar().showMessage(f"Project opened: {filename}", 5000)

        if radioManager.radioXMLData is not None:
            self.actionDemoMode.setChecked(False)
            self._switchToRadioMode()

    def _tryLoadAudioManagers(self, settings: dict):
        """Load audio managers from DAT paths stored in project settings.
        Prompts the user to relocate a DAT if its stored path no longer exists."""
        dats = [
            ("DEMO.DAT",    "demo_dat_path",    self._loadDemoAudioOnly),
            ("VOX.DAT",     "vox_dat_path",     self._loadVoxAudioOnly),
            ("ZMOVIE.STR",  "zmovie_str_path",  self._loadZmovieDataOnly),
        ]
        for label, key, loader in dats:
            stored = settings.get(key, "")
            if not stored:
                continue
            if os.path.exists(stored):
                try:
                    loader(stored)
                except Exception as e:
                    print(f"Warning: could not load {label} audio: {e}")
            else:
                reply = QMessageBox.question(
                    self, f"{label} Not Found",
                    f"{label} was not found at:\n{stored}\n\nBrowse for it?",
                    QMessageBox.Yes | QMessageBox.No
                )
                if reply == QMessageBox.Yes:
                    fileFilter = "STR Files (*.STR *.str)" if "STR" in label else "DAT Files (*.DAT *.dat)"
                    found = self.openFileDialog(fileFilter, f"Locate {label}")
                    if found:
                        try:
                            loader(found)
                            settings[key] = found
                        except Exception as e:
                            QMessageBox.warning(self, f"{label} Load Error", str(e))

    def _loadDemoAudioOnly(self, path: str):
        """Load demoManager for audio playback without overwriting demoOriginalJson/demoAlteredJson."""
        global demoManager, demoOriginalData, demoFilePath, demoSeqToOffset, demoOffsetsJson
        demoOriginalData = open(path, 'rb').read()
        demoFilePath = path
        demoManager = DM.parseDemoFile(demoOriginalData)
        sortedOffsets = sorted(demoManager.keys(), key=lambda k: int(k))
        newSeqMap = {f"demo-{i + 1:02}": off for i, off in enumerate(sortedOffsets)}
        for k in list(demoSeqToOffset.keys()):
            if k in newSeqMap:
                demoSeqToOffset[k] = newSeqMap[k]
        # Build offsets if not already loaded from project
        if not demoOffsetsJson:
            demoOffsetsJson = {}
            for name, off in newSeqMap.items():
                num = name.replace("demo-", "")
                demoOffsetsJson[num] = f"{int(off):08x}"

    def _loadVoxAudioOnly(self, path: str):
        """Load voxManager for audio playback without overwriting voxOriginalJson/voxAlteredJson."""
        global voxManager, voxOriginalData, voxFilePath, voxSeqToOffset, voxOffsetsJson
        voxOriginalData = open(path, 'rb').read()
        voxFilePath = path
        voxManager = DM.parseDemoFile(voxOriginalData)
        sortedOffsets = sorted(voxManager.keys(), key=lambda k: int(k))
        newSeqMap = {f"vox-{i + 1:04}": off for i, off in enumerate(sortedOffsets)}
        for k in list(voxSeqToOffset.keys()):
            if k in newSeqMap:
                voxSeqToOffset[k] = newSeqMap[k]
        # Build offsets if not already loaded from project
        if not voxOffsetsJson:
            voxOffsetsJson = {}
            for name, off in newSeqMap.items():
                num = name.replace("vox-", "")
                voxOffsetsJson[num] = f"{int(off):08x}"

    def _loadZmovieDataOnly(self, path: str):
        """Load zmovieOriginalData for compile without overwriting zmovieOriginalJson/zmovieAlteredJson."""
        global zmovieOriginalData, zmovieFilePath
        zmovieOriginalData = open(path, 'rb').read()
        zmovieFilePath = path


if __name__ == "__main__":
    # Set macOS menu bar app name (must be before QApplication init)
    if sys.platform == "darwin":
        try:
            from Foundation import NSBundle
            info = NSBundle.mainBundle().localizedInfoDictionary() or NSBundle.mainBundle().infoDictionary()
            info["CFBundleName"] = "MGS Dialogue Editor"
        except ImportError:
            pass
    app = QApplication(sys.argv)
    app.setApplicationName("MGS Dialogue Editor")
    # Set app icon (used by macOS dock/task switcher)
    iconPath = os.path.join(os.path.dirname(__file__), "icon.png")
    if os.path.exists(iconPath):
        from PySide6.QtGui import QIcon
        app.setWindowIcon(QIcon(iconPath))
    widget = MainWindow()
    widget.show()
    sys.exit(app.exec())
