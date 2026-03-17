# This Python file uses the following encoding: utf-8
import sys, os, zipfile, json, tempfile, importlib, shutil

from PySide6.QtWidgets import (QApplication, QMainWindow, QFileDialog,
    QListWidgetItem, QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QMessageBox, QGraphicsScene, QGraphicsTextItem,
    QGroupBox, QCheckBox, QLineEdit, QFormLayout,
    QFrame)
from PySide6.QtCore import Qt, QThread, Signal, QTimer, QElapsedTimer, QUrl, QSettings
from PySide6.QtGui import QFont, QColor, QAction, QKeySequence
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget

from ui_form import Ui_MainWindow

# For submodules, add submodule to sys.path
submodule_path = os.path.join(os.path.dirname(__file__), "scripts")
sys.path.insert(0, submodule_path)

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
demoDialogueJson: dict = {}    # {"demo-01": {"1234": {"duration": "...", "text": "..."}}}
demoSeqToOffset: dict = {}     # {"demo-01": "12345"} — maps name → raw offset string

# VOX mode state (mirrors demo mode)
voxDialogueJson: dict = {}     # {"vox-0001": {"1234": {"duration": "...", "text": "..."}}}
voxSeqToOffset:  dict = {}     # {"vox-0001": "12345"} — maps name → raw offset string

# ZMovie mode state
zmovieDialogueJson: dict  = {}   # {"zmovie-00": {"1234": {"duration": "...", "text": "..."}}}
zmovieOriginalData: bytes = b''  # original ZMOVIE.STR bytes for patch-in-place compile
zmovieFilePath:     str   = ""
currentZmovieKey:   str   = ""   # e.g. "zmovie-00"

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
        super().accept()


class FinalizeProjectDialog(QDialog):
    """Dialog for batch-compiling all (or selected) game data files."""
    def __init__(self, stageDirPath="", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Finalize Project")
        self.setMinimumWidth(480)
        layout = QVBoxLayout()

        # ── RADIO section ─────────────────────────────────────────────────
        self.radioGroup = QGroupBox("RADIO.DAT")
        self.radioGroup.setCheckable(True)
        self.radioGroup.setChecked(True)
        radioLayout = QFormLayout()

        self.chkPrepare = QCheckBox("Prepare lengths (-p)")
        self.chkPrepare.setChecked(True)
        radioLayout.addRow(self.chkPrepare)

        self.chkOrigHex = QCheckBox("Use original hex (-x)")
        radioLayout.addRow(self.chkOrigHex)

        self.chkDoubleWidth = QCheckBox("Double-width save blocks (-D)")
        radioLayout.addRow(self.chkDoubleWidth)

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
        stageLayout.addRow("STAGE.DIR path:", stageDirRow)

        self.txtStageOut = QLineEdit()
        self.txtStageOut.setPlaceholderText("Output name for STAGE.DIR (optional, -S)")
        stageLayout.addRow("STAGE.DIR output:", self.txtStageOut)

        self.stageGroup.setLayout(stageLayout)
        layout.addWidget(self.stageGroup)

        # Grey out STAGE.DIR unless Radio or Demo is checked
        self._updateStageEnabled()
        self.radioGroup.toggled.connect(self._updateStageEnabled)
        self.demoGroup = None  # forward ref; connected after demoGroup is created

        # ── DEMO section ──────────────────────────────────────────────────
        self.demoGroup = QGroupBox("DEMO.DAT")
        self.demoGroup.setCheckable(True)
        self.demoGroup.setChecked(True)
        demoLayout = QVBoxLayout()
        demoLayout.addWidget(QLabel("Compile JSON edits into DEMO.DAT binary."))
        self.demoGroup.setLayout(demoLayout)
        layout.addWidget(self.demoGroup)
        self.demoGroup.toggled.connect(self._updateStageEnabled)

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

        # ── Bottom area ───────────────────────────────────────────────────
        self.chkReplace = QCheckBox("Replace original files")
        layout.addWidget(self.chkReplace)

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

    def _updateStageEnabled(self):
        """Grey out STAGE.DIR section unless Radio or Demo is checked."""
        radioOn = self.radioGroup.isChecked()
        demoOn = self.demoGroup is not None and self.demoGroup.isChecked()
        self.stageGroup.setEnabled(radioOn or demoOn)

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
    def prepare(self): return self.chkPrepare.isChecked()
    @property
    def useOrigHex(self): return self.chkOrigHex.isChecked()
    @property
    def doubleWidth(self): return self.chkDoubleWidth.isChecked()
    @property
    def debugOutput(self): return self.chkDebug.isChecked()
    @property
    def stageDirPath(self): return self.txtStageDir.text().strip()
    @property
    def stageOutName(self): return self.txtStageOut.text().strip()
    @property
    def replaceOriginals(self): return self.chkReplace.isChecked()


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


class MainWindow(QMainWindow):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.ui = Ui_MainWindow()
        self.ui.setupUi(self)

        # Editor mode: "radio" or "demo"
        self._editorMode = "radio"

        # ── File Menu ────────────────────────────────────────────────────────
        self.ui.actionLoad_RADIO_DAT.triggered.connect(self.loadRadioDatFile)
        self.ui.actionLoad_RADIO_DAT.setStatusTip("Load a RADIO.DAT file")
        self.ui.actionLoad_Radio_XML.triggered.connect(self.loadRadioXMLFile)
        self.ui.actionLoad_Radio_XML.setStatusTip("Load a RADIO.XML file")
        self.ui.actionLoad_VOX_DAT.triggered.connect(self.loadVoxData)
        self.ui.menuFile.removeAction(self.ui.actionSave_RADIO_DAT)
        self.ui.actionSave_RADIO_XML.triggered.connect(self.saveRadioXMLFile)
        self.ui.actionSave_RADIO_XML.setStatusTip("Save current edits to RADIO.XML")

        # ── Edit Menu ────────────────────────────────────────────────────────
        self._appSettings = QSettings("MGS-Undubbed", "DialogueEditor")
        self.ui.actionPreferences.triggered.connect(self._openPreferences)

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

        # ── Project actions (top of File menu) ───────────────────────────────
        self.actionOpenFolder = QAction("Open Folder...", self)
        self.actionOpenFolder.setStatusTip("Find and load RADIO.DAT, DEMO.DAT, and VOX.DAT from a folder")
        self.actionOpenFolder.triggered.connect(self.openFolder)
        self.ui.menuFile.insertAction(self.ui.actionLoad_RADIO_DAT, self.actionOpenFolder)

        self.actionOpenProject = QAction("Open Project (.mtp)...", self)
        self.actionOpenProject.setStatusTip("Open a saved MTP project file")
        self.actionOpenProject.triggered.connect(self.openProject)
        self.ui.menuFile.insertAction(self.ui.actionLoad_RADIO_DAT, self.actionOpenProject)

        self.actionSaveProject = QAction("Save Project", self)
        self.actionSaveProject.setStatusTip("Save the current project to its .mtp file")
        self.actionSaveProject.setEnabled(False)
        self.actionSaveProject.triggered.connect(self.saveProject)
        self.ui.menuFile.insertAction(self.ui.actionLoad_RADIO_DAT, self.actionSaveProject)

        self.actionSaveProjectAs = QAction("Save Project As...", self)
        self.actionSaveProjectAs.setStatusTip("Save the current project to a new .mtp file")
        self.actionSaveProjectAs.triggered.connect(self.saveProjectAs)
        self.ui.menuFile.insertAction(self.ui.actionLoad_RADIO_DAT, self.actionSaveProjectAs)

        self.ui.menuFile.insertSeparator(self.ui.actionLoad_RADIO_DAT)

        # ── Keyboard shortcuts ────────────────────────────────────────────────
        # Override defaults from the .ui file to match project-centric workflow
        self.ui.actionLoad_RADIO_DAT.setShortcut("")       # was Ctrl+O
        self.ui.playVoxButton.setShortcut("")               # was Ctrl+P
        self.actionOpenFolder.setShortcut("Ctrl+O")
        self.actionOpenProject.setShortcut("Ctrl+P")
        self.actionSaveProject.setShortcut("Ctrl+S")
        self.ui.playVoxButton.setShortcut(QKeySequence(Qt.CTRL | Qt.Key_Space))

        # ── Add Save/Load VOX and DEMO actions (not in generated form) ────────
        self.actionSave_VOX_DAT = QAction("Save VOX.DAT", self)
        self.actionSave_VOX_DAT.setStatusTip("Write timing edits back to VOX.DAT")
        self.actionSave_VOX_DAT.triggered.connect(self.saveVoxDatFile)
        self.ui.menuFile.insertAction(self.ui.actionSave_RADIO_XML, self.actionSave_VOX_DAT)

        self.actionLoad_DEMO_DAT = QAction("Load DEMO.DAT...", self)
        self.actionLoad_DEMO_DAT.setStatusTip("Load a DEMO.DAT file for demo editing")
        self.actionLoad_DEMO_DAT.triggered.connect(self.loadDemoData)
        self.ui.menuFile.insertAction(self.ui.actionLoad_VOX_DAT, self.actionLoad_DEMO_DAT)

        self.actionExport_DEMO_JSON = QAction("Export DEMO JSON...", self)
        self.actionExport_DEMO_JSON.setStatusTip("Save subtitle edits to a JSON file")
        self.actionExport_DEMO_JSON.triggered.connect(self.exportDemoJson)
        self.ui.menuFile.insertAction(self.actionSave_VOX_DAT, self.actionExport_DEMO_JSON)

        self.actionExport_VOX_JSON = QAction("Export VOX JSON...", self)
        self.actionExport_VOX_JSON.setStatusTip("Save VOX subtitle edits to a JSON file")
        self.actionExport_VOX_JSON.triggered.connect(self.exportVoxJson)
        self.ui.menuFile.insertAction(self.actionSave_VOX_DAT, self.actionExport_VOX_JSON)

        self.actionLoad_ZMOVIE = QAction("Load ZMOVIE.STR...", self)
        self.actionLoad_ZMOVIE.setStatusTip("Load a ZMOVIE.STR file for subtitle editing")
        self.actionLoad_ZMOVIE.triggered.connect(self.loadZmovieData)
        self.ui.menuFile.insertAction(self.actionSave_VOX_DAT, self.actionLoad_ZMOVIE)

        self.actionExport_ZMOVIE_JSON = QAction("Export ZMovie JSON...", self)
        self.actionExport_ZMOVIE_JSON.setStatusTip("Save ZMovie subtitle edits to a JSON file")
        self.actionExport_ZMOVIE_JSON.triggered.connect(self.exportZmovieJson)
        self.ui.menuFile.insertAction(self.actionSave_VOX_DAT, self.actionExport_ZMOVIE_JSON)

        # ── Bottom of File menu: Open shortcuts, Finalize, then Quit ─────
        self.ui.menuFile.insertSeparator(self.ui.actionQuit)

        actionOpenFolderBottom = QAction("Open Folder...", self)
        actionOpenFolderBottom.setStatusTip("Find and load RADIO.DAT, DEMO.DAT, and VOX.DAT from a folder")
        actionOpenFolderBottom.triggered.connect(self.openFolder)
        self.ui.menuFile.insertAction(self.ui.actionQuit, actionOpenFolderBottom)

        actionOpenProjectBottom = QAction("Open Project (.mtp)...", self)
        actionOpenProjectBottom.setStatusTip("Open a saved MTP project file")
        actionOpenProjectBottom.triggered.connect(self.openProject)
        self.ui.menuFile.insertAction(self.ui.actionQuit, actionOpenProjectBottom)

        self.actionFinalizeProject = QAction("Finalize Project...", self)
        self.actionFinalizeProject.setStatusTip("Batch-compile all game data files")
        self.actionFinalizeProject.triggered.connect(self.finalizeProject)
        self.ui.menuFile.insertAction(self.ui.actionQuit, self.actionFinalizeProject)

        self.ui.menuFile.insertSeparator(self.ui.actionQuit)

        # ── Bottom-right GUI buttons (above Quit) ────────────────────────────
        from PySide6.QtWidgets import QFrame
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

        # ── Mode tab bar ───────────────────────────────────────────────────────
        from PySide6.QtWidgets import QToolBar, QTabBar
        self._modeToolBar = QToolBar("Editor Mode", self)
        self._modeToolBar.setMovable(False)
        self._modeToolBar.setFloatable(False)
        self._modeTabBar = QTabBar()
        self._modeTabBar.setExpanding(False)
        self._modeTabBar.addTab("Radio")
        self._modeTabBar.addTab("Demo")
        self._modeTabBar.addTab("VOX")
        self._modeTabBar.addTab("ZMovie")
        self._modeToolBar.addWidget(self._modeTabBar)
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
        # Tracks which demo keys have already had fps estimated (so we only print once each)
        self._fpsEstimated: set = set()

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

        self.translateButton = QPushButton("Translate")
        self.translateButton.setToolTip("Translate the current line (Cmd+T)")
        self.translateButton.setEnabled(False)
        self.translateButton.setShortcut("Ctrl+T")
        self.translateButton.clicked.connect(self._translateLine)
        btnCol.addWidget(self.translateButton)

        self.autoFormatButton = QPushButton("Auto-format")
        self.autoFormatButton.setToolTip("Re-wrap text with pixel-accurate MGS1 line breaks (Cmd+F)")
        self.autoFormatButton.setEnabled(False)
        self.autoFormatButton.setShortcut("Ctrl+F")
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
        self._buildRadioVoxIndex()
        self._populateRadioOffsets()
        self.setWindowTitle(f"Dialogue Editor — {os.path.basename(filename)}")

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

    def _populateRadioOffsets(self):
        """Repopulate the Radio offset list, optionally hiding disc-2 calls."""
        filterDisc2 = self.chkDisc1Only.isChecked()
        current = self.ui.offsetListBox.currentData()
        self.ui.offsetListBox.blockSignals(True)
        self.ui.offsetListBox.clear()
        for offset in radioManager.getCallOffsets():
            if filterDisc2 and offset in _radioDisc2Offsets:
                continue
            self.ui.offsetListBox.addItem(offset, userData=offset)
        self.ui.offsetListBox.blockSignals(False)
        # Restore selection if still present, else select first
        idx = self.ui.offsetListBox.findData(current)
        self.ui.offsetListBox.setCurrentIndex(idx if idx >= 0 else 0)

    def _populateVoxOffsets(self):
        """Repopulate the VOX offset list, optionally showing only unclaimed clips."""
        filterUnclaimed = self.chkUnclaimedVox.isChecked()
        current = self.ui.offsetListBox.currentData()
        self.ui.offsetListBox.blockSignals(True)
        self.ui.offsetListBox.clear()
        for name in sorted(voxDialogueJson.keys()):
            if filterUnclaimed:
                offset = voxSeqToOffset.get(name)
                if offset and int(offset) in _radioClaimedVoxAddrs:
                    continue
            self.ui.offsetListBox.addItem(name, userData=name)
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
        global voxManager, voxOriginalData, voxFilePath, voxDialogueJson, voxSeqToOffset
        voxOriginalData = open(voxFile, 'rb').read()
        voxFilePath = voxFile
        voxManager = DM.parseDemoFile(voxOriginalData)
        try:
            from DemoTools.extractDemoVox import extractFromFile
            voxDialogueJson = extractFromFile(voxFile, fileType='vox')
        except Exception as e:
            print(f"Warning: VOX dialogue extraction failed: {e}")
            voxDialogueJson = {}
        sortedOffsets = sorted(voxManager.keys(), key=lambda k: int(k))
        voxSeqToOffset = {f"vox-{i + 1:04}": off for i, off in enumerate(sortedOffsets)}
        self.ui.playVoxButton.setEnabled(True)
        self.statusBar().showMessage(
            f"VOX.DAT loaded: {len(voxManager)} clips, {len(voxDialogueJson)} with dialogue", 4000
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
        Patch-in-place: serialise any modified captionChunks back into a copy of the
        original VOX bytes, then write the result to disk.
        """
        if not voxManager or not voxOriginalData:
            QMessageBox.warning(self, "Nothing loaded", "No VOX.DAT is currently loaded.")
            return

        filename = QFileDialog.getSaveFileName(
            self, "Save VOX.DAT", voxFilePath or "", "DAT Files (*.DAT *.dat)"
        )[0]
        if not filename:
            return

        try:
            patchedData = bytearray(voxOriginalData)

            # Sort demos by byte offset so we process in file order
            for offsetStr, demoObj in sorted(voxManager.items(), key=lambda x: int(x[0])):
                byteOffset = int(offsetStr)
                # Find the original length of this demo in the file
                # (distance to the next demo, or end-of-file)
                sortedOffsets = sorted(int(k) for k in voxManager)
                idx = sortedOffsets.index(byteOffset)
                if idx + 1 < len(sortedOffsets):
                    origLen = sortedOffsets[idx + 1] - byteOffset
                else:
                    origLen = len(voxOriginalData) - byteOffset

                origSlice = bytes(voxOriginalData[byteOffset: byteOffset + origLen])
                newSlice = demoObj.getModifiedBytes(origSlice)

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
            self.statusBar().showMessage(f"VOX.DAT saved: {filename}", 5000)

        except Exception as e:
            QMessageBox.critical(self, "VOX Save Error", str(e))

    # ── Navigation handlers ───────────────────────────────────────────────────

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

        self.ui.audioCueListView.clear()
        for audio in radioManager.getVoxOffsets():
            QListWidgetItem(audio, self.ui.audioCueListView)

        self._clearEditor()

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
        subtitles = demoDialogueJson.get(key, {})
        for startFrame in sorted(subtitles.keys(), key=int):
            sub = subtitles[startFrame]
            text = sub.get("text", "").strip() or f"[Frame {startFrame}]"
            QListWidgetItem(text, self.ui.subsPreviewList)

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
        subtitles = zmovieDialogueJson.get(key, {})
        for startFrame in sorted(subtitles.keys(), key=int):
            sub = subtitles[startFrame]
            text = sub.get("text", "").strip() or f"[Frame {startFrame}]"
            QListWidgetItem(text, self.ui.subsPreviewList)

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
        subtitles = voxDialogueJson.get(key, {})
        for startFrame in sorted(subtitles.keys(), key=int):
            sub = subtitles[startFrame]
            text = sub.get("text", "").strip() or f"[Frame {startFrame}]"
            QListWidgetItem(text, self.ui.subsPreviewList)

    def selectAudioCue(self, item):
        global currentSubIndex, currentVoxOffset
        if item is None:
            return
        offset = self.ui.audioCueListView.currentItem().text()
        radioManager.setWorkingVox(offset)
        currentSubIndex = -1

        # VOX byte address
        voxOffsetHex = radioManager.workingVox.get("content")[8:16]
        offsetBlock = bytes.fromhex(voxOffsetHex)
        byteAddr = int.from_bytes(offsetBlock, byteorder="big") * 0x800
        currentVoxOffset = str(byteAddr)

        self.ui.VoxAddressDisplay.setText(currentVoxOffset)
        self.ui.VoxBlockAddressDisplay.setText("0x" + voxOffsetHex)

        self.ui.subsPreviewList.clear()
        for text in radioManager.getSubs():
            QListWidgetItem(text, self.ui.subsPreviewList)

        self._clearEditor()

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
        else:
            # Populate text editor
            subs = radioManager.getSubs()
            text = subs[idx].replace("\\r\\n", "\n")
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

        if self._editorMode in ("demo", "vox", "zmovie"):
            key, djson = self._modeData()
            subtitles = djson.get(key, {})
            sortedFrames = sorted(subtitles.keys(), key=int)
            if currentSubIndex < len(sortedFrames):
                oldFrame = sortedFrames[currentSubIndex]
                newText = self.ui.DialogueEditorBox.toPlainText().replace("\n", "｜")
                newStart = str(self.ui.startFrameBox.value())
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
            return

        # --- Text → XML -------------------------------------------------------
        newText = self.ui.DialogueEditorBox.toPlainText().replace("\n", "\\r\\n")
        radioManager.updateSubText(currentSubIndex, newText)

        # --- Timing + text → VOX demo -----------------------------------------
        lines = self._getVoxSubtitleLines()
        if lines and currentSubIndex < len(lines):
            lines[currentSubIndex].startFrame = self.ui.startFrameBox.value()
            lines[currentSubIndex].displayFrames = self.ui.durationBox.value()
            lines[currentSubIndex].text = self.ui.DialogueEditorBox.toPlainText().replace("\n", "｜")

        # Refresh subtitle list to show new text
        self._modified = True
        self._refreshSubsList()
        self.applyEditButton.setStyleSheet("")
        if projectFilePath:
            self.statusBar().showMessage("Changes applied (unsaved — use File → Save Project)", 5000)
        else:
            self.statusBar().showMessage("Changes applied (unsaved — use File → Save RADIO.XML or Save Project As)", 5000)

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
            key, djson = self._modeData()
            subtitles = djson.get(key, {})
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
            self.ui.subsPreviewList.setCurrentRow(currentSubIndex)
            self.statusBar().showMessage("Subtitle split", 3000)
            return

        # ── Radio mode ────────────────────────────────────────────────────
        text = radioManager.getSubs()[currentSubIndex]

        # Duplicate text into both entries
        radioManager.addSubtitle(currentSubIndex, text, after=True)

        # Split VOX timing if loaded
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
        self.ui.subsPreviewList.setCurrentRow(currentSubIndex)
        self.statusBar().showMessage("Subtitle split", 3000)

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

    def _refreshSubsList(self):
        """Rebuild the subtitle list widget from the current data source."""
        self.ui.subsPreviewList.clear()
        if self._editorMode in ("demo", "vox", "zmovie"):
            key, djson = self._modeData()
            subtitles = djson.get(key, {})
            for startFrame in sorted(subtitles.keys(), key=int):
                sub = subtitles[startFrame]
                text = sub.get("text", "").strip() or f"[Frame {startFrame}]"
                QListWidgetItem(text, self.ui.subsPreviewList)
        else:
            for text in radioManager.getSubs():
                QListWidgetItem(text, self.ui.subsPreviewList)

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
        self._estimateSubtitleFps(wavPath)
        self._playThread = FfplayThread(wavPath, parent=self)
        self._playThread.playbackFinished.connect(self._onPlaybackFinished)
        self._playThread.errorOccurred.connect(self._onPlaybackError)
        self._playThread.start()
        self._elapsed.restart()
        self._frameTimer.start()
        self.statusBar().showMessage("Playing…")

    def _estimateSubtitleFps(self, wavPath: str):
        """
        Estimate the subtitle frame rate by comparing audio duration to the
        last subtitle's end frame.  Prints to console for tuning SUBTITLE_FPS.
        Only runs once per unique demo entry, in demo mode only.
        """
        if self._editorMode not in ("demo", "vox", "zmovie"):
            return
        key, djson = self._modeData()
        if key in self._fpsEstimated:
            return
        import wave
        subtitles = djson.get(key, {})
        if not subtitles:
            return

        last_end = max(
            int(sf) + int(sub.get("duration", "0"))
            for sf, sub in subtitles.items()
        )
        if last_end == 0:
            return

        try:
            with wave.open(wavPath, 'rb') as w:
                audio_duration = w.getnframes() / w.getframerate()
        except Exception as e:
            print(f"[subtitle fps] Could not read WAV duration: {e}")
            return

        estimated_fps = last_end / audio_duration
        self._fpsEstimated.add(key)
        print(
            f"[subtitle fps] {key}: "
            f"audio={audio_duration:.3f}s  "
            f"last_end_frame={last_end}  "
            f"\u2192 estimated fps={estimated_fps:.4f}"
        )

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
        if self._editorMode == "zmovie" and self._mediaPlayer.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            elapsed_ms = max(0, self._mediaPlayer.position())
            currentFrame = int(elapsed_ms * SUBTITLE_FPS / 1000)
        else:
            elapsed_ms = max(0, self._elapsed.elapsed() - SUBTITLE_OFFSET_MS)
            currentFrame = int(elapsed_ms * SUBTITLE_FPS / 1000)
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
            f"DEMO.DAT loaded: {len(demoDialogueJson)} entries with dialogue", 4000
        )

    def _loadDemoFromPath(self, demoFile: str):
        global demoManager, demoOriginalData, demoFilePath, demoDialogueJson, demoSeqToOffset
        demoOriginalData = open(demoFile, 'rb').read()
        demoFilePath = demoFile
        demoManager = DM.parseDemoFile(demoOriginalData)
        try:
            from DemoTools.extractDemoVox import extractFromFile
            demoDialogueJson = extractFromFile(demoFile, fileType="demo")
        except Exception as e:
            print(f"Warning: dialogue extraction failed: {e}")
            demoDialogueJson = {}
        sortedOffsets = sorted(demoManager.keys(), key=lambda k: int(k))
        demoSeqToOffset = {f"demo-{i + 1:02}": off for i, off in enumerate(sortedOffsets)}

    def loadZmovieData(self):
        zmovieFile = self.openFileDialog("STR Files (*.STR *.str);;All Files (*)", "Load ZMOVIE.STR")
        if not zmovieFile:
            return
        try:
            self._loadZmovieFromPath(zmovieFile)
            self._switchToZmovieMode()
            self.statusBar().showMessage(
                f"ZMOVIE.STR loaded: {len(zmovieDialogueJson)} entries with subtitles", 4000
            )
        except Exception as e:
            QMessageBox.critical(self, "Load Error", str(e))

    def _loadZmovieFromPath(self, zmovieFile: str):
        global zmovieDialogueJson, zmovieOriginalData, zmovieFilePath
        from zmovieTools.extractZmovie import extractFromFile as zmExtract
        zmovieOriginalData = open(zmovieFile, 'rb').read()
        zmovieFilePath = zmovieFile
        try:
            zmovieDialogueJson = zmExtract(zmovieFile)
        except Exception as e:
            print(f"Warning: ZMovie subtitle extraction failed: {e}")
            zmovieDialogueJson = {}

    def exportZmovieJson(self):
        """Save zmovieDialogueJson to a JSON file."""
        if not zmovieDialogueJson:
            QMessageBox.warning(self, "Nothing loaded", "No ZMOVIE.STR is currently loaded.")
            return
        stem = os.path.splitext(os.path.basename(zmovieFilePath))[0].lower() if zmovieFilePath else "zmovie"
        default = os.path.join(os.path.dirname(zmovieFilePath) if zmovieFilePath else "",
                               f"{stem}-dialogue.json")
        filename = QFileDialog.getSaveFileName(
            self, "Export ZMovie JSON", default, "JSON Files (*.json)"
        )[0]
        if not filename:
            return
        try:
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(zmovieDialogueJson, f, ensure_ascii=False, indent=2)
            self._modified = False
            self.statusBar().showMessage(f"ZMovie JSON exported: {filename}", 5000)
        except Exception as e:
            QMessageBox.critical(self, "Export Error", str(e))

    def compileZmovieFile(self):
        """Compile zmovieDialogueJson into a new ZMOVIE.STR using extractZmovie.compileToFile."""
        if not zmovieOriginalData:
            QMessageBox.warning(self, "Nothing loaded", "No ZMOVIE.STR is currently loaded.")
            return
        filename = QFileDialog.getSaveFileName(
            self, "Compile ZMOVIE.STR", zmovieFilePath or "", "STR Files (*.STR *.str)"
        )[0]
        if not filename:
            return
        try:
            from zmovieTools.extractZmovie import compileToFile as zmCompile
            zmCompile(filename, zmovieOriginalData, zmovieDialogueJson)
            self._modified = False
            self.statusBar().showMessage(f"ZMOVIE.STR compiled: {filename}", 5000)
        except ValueError as e:
            QMessageBox.critical(self, "Compile Error — Subtitle Too Long", str(e))
        except Exception as e:
            QMessageBox.critical(self, "Compile Error", str(e))

    def exportDemoJson(self):
        """Save demoDialogueJson to a JSON file — the intermediate format for scripts."""
        import json
        if not demoDialogueJson:
            QMessageBox.warning(self, "Nothing loaded", "No DEMO.DAT is currently loaded.")
            return
        stem = os.path.splitext(os.path.basename(demoFilePath))[0].lower() if demoFilePath else "demo"
        default = os.path.join(os.path.dirname(demoFilePath) if demoFilePath else "", f"{stem}-dialogue.json")
        filename = QFileDialog.getSaveFileName(
            self, "Export DEMO JSON", default, "JSON Files (*.json)"
        )[0]
        if not filename:
            return
        try:
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(demoDialogueJson, f, ensure_ascii=False, indent=2)
            self._modified = False
            self.statusBar().showMessage(f"DEMO JSON exported: {filename}", 5000)
        except Exception as e:
            QMessageBox.critical(self, "Export Error", str(e))

    def compileDemoDatFile(self):
        """Sync JSON edits into demoManager, then patch-in-place and write a new DEMO.DAT."""
        if not demoManager or not demoOriginalData:
            QMessageBox.warning(self, "Nothing loaded", "No DEMO.DAT is currently loaded.")
            return
        filename = QFileDialog.getSaveFileName(
            self, "Compile DEMO.DAT", demoFilePath or "", "DAT Files (*.DAT *.dat)"
        )[0]
        if not filename:
            return
        try:
            self._syncJsonToDemoManager()
            patchedData = bytearray(demoOriginalData)
            sortedOffsets = sorted(int(k) for k in demoManager)
            for i, byteOffset in enumerate(sortedOffsets):
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
            self.statusBar().showMessage(f"DEMO.DAT compiled: {filename}", 5000)
        except Exception as e:
            QMessageBox.critical(self, "Compile Error", str(e))

    def exportVoxJson(self):
        """Save voxDialogueJson to a JSON file."""
        if not voxDialogueJson:
            QMessageBox.warning(self, "Nothing loaded", "No VOX.DAT is currently loaded.")
            return
        stem = os.path.splitext(os.path.basename(voxFilePath))[0].lower() if voxFilePath else "vox"
        default = os.path.join(os.path.dirname(voxFilePath) if voxFilePath else "",
                               f"{stem}-dialogue.json")
        filename = QFileDialog.getSaveFileName(
            self, "Export VOX JSON", default, "JSON Files (*.json)"
        )[0]
        if not filename:
            return
        try:
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(voxDialogueJson, f, ensure_ascii=False, indent=2)
            self._modified = False
            self.statusBar().showMessage(f"VOX JSON exported: {filename}", 5000)
        except Exception as e:
            QMessageBox.critical(self, "Export Error", str(e))

    def compileVoxDatFile(self):
        """Sync voxDialogueJson into voxManager, then patch-in-place and write a new VOX.DAT."""
        if not voxManager or not voxOriginalData:
            QMessageBox.warning(self, "Nothing loaded", "No VOX.DAT is currently loaded.")
            return
        filename = QFileDialog.getSaveFileName(
            self, "Compile VOX.DAT", voxFilePath or "", "DAT Files (*.DAT *.dat)"
        )[0]
        if not filename:
            return
        try:
            self._syncJsonToManager(voxDialogueJson, voxSeqToOffset, voxManager)
            patchedData = bytearray(voxOriginalData)
            sortedOffsets = sorted(int(k) for k in voxManager)
            for i, byteOffset in enumerate(sortedOffsets):
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
            self.statusBar().showMessage(f"VOX.DAT compiled: {filename}", 5000)
        except Exception as e:
            QMessageBox.critical(self, "Compile Error", str(e))

    def _syncJsonToManager(self, dialogueJson: dict, seqToOffset: dict, manager: dict):
        """Sync dialogue JSON edits into binary demo objects before patch-in-place compile."""
        for key, subtitles in dialogueJson.items():
            offset = seqToOffset.get(key)
            if not offset:
                continue
            demo = manager.get(offset)
            if demo is None:
                continue
            lines = []
            for seg in demo.segments:
                if hasattr(seg, 'subtitles'):
                    lines.extend(seg.subtitles)
            for idx, startFrame in enumerate(sorted(subtitles.keys(), key=int)):
                if idx >= len(lines):
                    break
                sub = subtitles[startFrame]
                lines[idx].startFrame    = int(startFrame)
                lines[idx].displayFrames = int(sub.get("duration", "0"))
                lines[idx].text          = sub.get("text", "")

    def _syncJsonToDemoManager(self):
        self._syncJsonToManager(demoDialogueJson, demoSeqToOffset, demoManager)

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
        dlg = FinalizeProjectDialog(stageDirPath=stageDirAutoPath, parent=self)
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

        # ── Confirm overwrite ────────────────────────────────────────────
        if dlg.replaceOriginals:
            ans = QMessageBox.warning(self, "Replace Original Files",
                "This will overwrite the original files. "
                "This cannot be undone. Continue?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if ans != QMessageBox.Yes:
                return

        results = []   # list of (label, success, detail)
        tmpDir = tempfile.mkdtemp(prefix="mgs-finalize-")

        try:
            # ── RADIO ────────────────────────────────────────────────────
            if dlg.radioEnabled:
                try:
                    import scripts.RadioDatRecompiler as RDR
                    # Reset module globals
                    RDR.stageBytes = b''
                    RDR.debug = False
                    RDR.subUseOriginalHex = False
                    RDR.useDWidSaveB = False
                    RDR.newOffsets = {}

                    tmpXmlPath = os.path.join(tmpDir, "radio-finalize.xml")
                    radioManager.saveXML(tmpXmlPath)

                    radioDatPath = projectSettings.get("radio_dat_path", "")
                    if dlg.replaceOriginals and radioDatPath:
                        radioOut = radioDatPath
                    elif radioDatPath:
                        radioOut = os.path.join(os.path.dirname(radioDatPath), "RADIO-NEW.DAT")
                    else:
                        radioOut = os.path.join(projectFolder or tmpDir, "RADIO-NEW.DAT")

                    stagePath = (dlg.stageDirPath or None) if dlg.stageEnabled else None
                    if dlg.replaceOriginals and stagePath:
                        stageOut = dlg.stageOutName or stagePath
                    elif stagePath:
                        stageOut = dlg.stageOutName or os.path.join(
                            os.path.dirname(stagePath), "STAGE-NEW.DIR")
                    else:
                        stageOut = None

                    args = Namespace(
                        input=tmpXmlPath, output=radioOut,
                        stage=stagePath, stageOut=stageOut,
                        prepare=dlg.prepare, hex=dlg.useOrigHex,
                        debug=dlg.debugOutput, double=dlg.doubleWidth,
                    )
                    RDR.main(args)
                    detail = f"RADIO.DAT → {radioOut}"
                    if stagePath:
                        detail += f"\nSTAGE.DIR → {stageOut}"
                    results.append(("RADIO", True, detail))
                except Exception as e:
                    results.append(("RADIO", False, str(e)))

            # ── DEMO ─────────────────────────────────────────────────────
            if dlg.demoEnabled:
                try:
                    self._syncJsonToDemoManager()
                    patchedData = bytearray(demoOriginalData)
                    sortedOffsets = sorted(int(k) for k in demoManager)
                    for i, byteOffset in enumerate(sortedOffsets):
                        demoObj = demoManager[str(byteOffset)]
                        origLen = (sortedOffsets[i + 1] - byteOffset
                                   if i + 1 < len(sortedOffsets)
                                   else len(demoOriginalData) - byteOffset)
                        origSlice = bytes(demoOriginalData[byteOffset:byteOffset + origLen])
                        newSlice = demoObj.getModifiedBytes(origSlice)
                        if len(newSlice) != origLen:
                            raise ValueError(
                                f"Demo at offset {byteOffset} changed size "
                                f"({origLen} → {len(newSlice)})")
                        patchedData[byteOffset:byteOffset + origLen] = newSlice
                    if dlg.replaceOriginals and demoFilePath:
                        outPath = demoFilePath
                    else:
                        outPath = os.path.join(
                            os.path.dirname(demoFilePath) if demoFilePath else projectFolder or tmpDir,
                            "DEMO-NEW.DAT")
                    with open(outPath, 'wb') as f:
                        f.write(bytes(patchedData))
                    results.append(("DEMO", True, f"DEMO.DAT → {outPath}"))
                except Exception as e:
                    results.append(("DEMO", False, str(e)))

            # ── VOX ──────────────────────────────────────────────────────
            if dlg.voxEnabled:
                try:
                    self._syncJsonToManager(voxDialogueJson, voxSeqToOffset, voxManager)
                    patchedData = bytearray(voxOriginalData)
                    sortedOffsets = sorted(int(k) for k in voxManager)
                    for i, byteOffset in enumerate(sortedOffsets):
                        voxObj = voxManager[str(byteOffset)]
                        origLen = (sortedOffsets[i + 1] - byteOffset
                                   if i + 1 < len(sortedOffsets)
                                   else len(voxOriginalData) - byteOffset)
                        origSlice = bytes(voxOriginalData[byteOffset:byteOffset + origLen])
                        newSlice = voxObj.getModifiedBytes(origSlice)
                        if len(newSlice) != origLen:
                            raise ValueError(
                                f"VOX at offset {byteOffset} changed size "
                                f"({origLen} → {len(newSlice)})")
                        patchedData[byteOffset:byteOffset + origLen] = newSlice
                    if dlg.replaceOriginals and voxFilePath:
                        outPath = voxFilePath
                    else:
                        outPath = os.path.join(
                            os.path.dirname(voxFilePath) if voxFilePath else projectFolder or tmpDir,
                            "VOX-NEW.DAT")
                    with open(outPath, 'wb') as f:
                        f.write(bytes(patchedData))
                    results.append(("VOX", True, f"VOX.DAT → {outPath}"))
                except Exception as e:
                    results.append(("VOX", False, str(e)))

            # ── ZMOVIE ───────────────────────────────────────────────────
            if dlg.zmovieEnabled:
                try:
                    from zmovieTools.extractZmovie import compileToFile as zmCompile
                    if dlg.replaceOriginals and zmovieFilePath:
                        outPath = zmovieFilePath
                    else:
                        outPath = os.path.join(
                            os.path.dirname(zmovieFilePath) if zmovieFilePath else projectFolder or tmpDir,
                            "ZMOVIE-NEW.STR")
                    zmCompile(outPath, zmovieOriginalData, zmovieDialogueJson)
                    results.append(("ZMOVIE", True, f"ZMOVIE.STR → {outPath}"))
                except Exception as e:
                    results.append(("ZMOVIE", False, str(e)))

        finally:
            shutil.rmtree(tmpDir, ignore_errors=True)

        # ── Summary ──────────────────────────────────────────────────────
        lines = []
        for label, ok, detail in results:
            status = "OK" if ok else "FAILED"
            lines.append(f"[{status}] {label}: {detail}")
        summary = "\n\n".join(lines)
        if all(ok for _, ok, _ in results):
            QMessageBox.information(self, "Finalize Complete", summary)
        else:
            QMessageBox.warning(self, "Finalize Complete (with errors)", summary)

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
        """Return (currentKey, dialogueJson) for the active sequence-based mode."""
        if self._editorMode == "demo":
            return currentDemoKey, demoDialogueJson
        elif self._editorMode == "vox":
            return currentVoxKey, voxDialogueJson
        else:  # zmovie
            return currentZmovieKey, zmovieDialogueJson

    def _hideRadioWidgets(self):
        self.ui.audioCueListView.setVisible(False)
        self.ui.FreqLabel.setVisible(False)
        self.ui.FreqDisplay.setVisible(False)
        self.ui.VoxBlockAddressLabel.setVisible(False)
        self.ui.VoxBlockAddressDisplay.setVisible(False)
        self.ui.VoxAddressLabel.setVisible(False)
        self.ui.VoxAddressDisplay.setVisible(False)
        self.chkDisc1Only.setVisible(False)
        self.chkUnclaimedVox.setVisible(False)

    def _switchToDemoMode(self):
        self._editorMode = "demo"
        self.actionDemoMode.setChecked(True)
        self._syncTab()
        self._hideRadioWidgets()
        self.ui.playVoxButton.setEnabled(bool(demoDialogueJson))
        self.ui.offsetListBox.clear()
        for name in sorted(demoDialogueJson.keys()):
            self.ui.offsetListBox.addItem(name, userData=name)
        self._clearEditor()
        self.ui.subsPreviewList.clear()
        if self.ui.offsetListBox.count() > 0:
            self.ui.offsetListBox.setCurrentIndex(0)
            self._selectDemo(0)

    def _switchToZmovieMode(self):
        self._editorMode = "zmovie"
        self.actionZmovieMode.setChecked(True)
        self._syncTab()
        self._hideRadioWidgets()
        self.ui.playVoxButton.setEnabled(bool(zmovieOriginalData))
        self.ui.offsetListBox.clear()
        for name in sorted(zmovieDialogueJson.keys()):
            self.ui.offsetListBox.addItem(name, userData=name)
        self._clearEditor()
        self.ui.subsPreviewList.clear()
        if self.ui.offsetListBox.count() > 0:
            self.ui.offsetListBox.setCurrentIndex(0)
            self._selectZmovie(0)

    def _switchToVoxMode(self):
        self._editorMode = "vox"
        self.actionVoxMode.setChecked(True)
        self._syncTab()
        self._hideRadioWidgets()
        self.ui.playVoxButton.setEnabled(bool(voxDialogueJson))
        self.chkUnclaimedVox.setVisible(bool(_radioClaimedVoxAddrs))
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
        self.ui.audioCueListView.setVisible(True)
        self.ui.FreqLabel.setVisible(True)
        self.ui.FreqDisplay.setVisible(True)
        self.ui.VoxBlockAddressLabel.setVisible(True)
        self.ui.VoxBlockAddressDisplay.setVisible(True)
        self.ui.VoxAddressLabel.setVisible(True)
        self.ui.VoxAddressDisplay.setVisible(True)
        self.ui.playVoxButton.setEnabled(bool(voxManager))
        self.chkUnclaimedVox.setVisible(False)
        self.chkDisc1Only.setVisible(bool(_radioDisc2Offsets))
        self._populateRadioOffsets()
        self._clearEditor()
        self.ui.subsPreviewList.clear()
        self.ui.audioCueListView.clear()

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
            self._buildRadioVoxIndex()
            self._populateRadioOffsets()
            self.setWindowTitle(f"Dialogue Editor \u2014 {os.path.basename(radioPath)}")
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

    def _writeProjectFile(self, path: str):
        import xml.etree.ElementTree as ET
        from xml.dom.minidom import parseString
        with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.writestr('settings.json', json.dumps(projectSettings, indent=2))
            if radioManager.radioXMLData is not None:
                xmlStr = parseString(ET.tostring(radioManager.radioXMLData)).toprettyxml(indent="  ")
                zf.writestr('radio.xml', xmlStr)
            if demoDialogueJson:
                zf.writestr('demo-dialogue.json',
                            json.dumps(demoDialogueJson, ensure_ascii=False, indent=2))
            if voxDialogueJson:
                zf.writestr('vox-dialogue.json',
                            json.dumps(voxDialogueJson, ensure_ascii=False, indent=2))
            if zmovieDialogueJson:
                zf.writestr('zmovie-dialogue.json',
                            json.dumps(zmovieDialogueJson, ensure_ascii=False, indent=2))
            if activeTblRaw:
                zf.writestr('font.tbl', activeTblRaw)

    # ── Project Open ──────────────────────────────────────────────────────────

    def openProject(self):
        global projectFilePath, projectSettings
        global demoDialogueJson, demoSeqToOffset
        global voxDialogueJson, voxSeqToOffset
        global zmovieDialogueJson
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
                demoJson    = json.loads(zf.read('demo-dialogue.json'))    if 'demo-dialogue.json'    in names else {}
                voxJson     = json.loads(zf.read('vox-dialogue.json'))     if 'vox-dialogue.json'     in names else {}
                zmovieJson  = json.loads(zf.read('zmovie-dialogue.json'))  if 'zmovie-dialogue.json'  in names else {}
                tblRaw      = zf.read('font.tbl').decode('utf-8')          if 'font.tbl'              in names else ""
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

        # Restore demo/vox subtitle JSON
        if demoJson:
            demoDialogueJson = demoJson
            demoSeqToOffset  = {k: "" for k in demoJson}  # real offsets filled in by _loadDemoAudioOnly
        if voxJson:
            voxDialogueJson = voxJson
            voxSeqToOffset  = {k: "" for k in voxJson}
        if zmovieJson:
            zmovieDialogueJson = zmovieJson

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
            ("DEMO.DAT", "demo_dat_path", self._loadDemoAudioOnly),
            ("VOX.DAT",  "vox_dat_path",  self._loadVoxAudioOnly),
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
                    found = self.openFileDialog("DAT Files (*.DAT *.dat)", f"Locate {label}")
                    if found:
                        try:
                            loader(found)
                            settings[key] = found
                        except Exception as e:
                            QMessageBox.warning(self, f"{label} Load Error", str(e))

    def _loadDemoAudioOnly(self, path: str):
        """Load demoManager for audio playback without overwriting demoDialogueJson."""
        global demoManager, demoOriginalData, demoFilePath, demoSeqToOffset
        demoOriginalData = open(path, 'rb').read()
        demoFilePath = path
        demoManager = DM.parseDemoFile(demoOriginalData)
        sortedOffsets = sorted(demoManager.keys(), key=lambda k: int(k))
        newSeqMap = {f"demo-{i + 1:02}": off for i, off in enumerate(sortedOffsets)}
        for k in list(demoSeqToOffset.keys()):
            if k in newSeqMap:
                demoSeqToOffset[k] = newSeqMap[k]

    def _loadVoxAudioOnly(self, path: str):
        """Load voxManager for audio playback without overwriting voxDialogueJson."""
        global voxManager, voxOriginalData, voxFilePath, voxSeqToOffset
        voxOriginalData = open(path, 'rb').read()
        voxFilePath = path
        voxManager = DM.parseDemoFile(voxOriginalData)
        sortedOffsets = sorted(voxManager.keys(), key=lambda k: int(k))
        newSeqMap = {f"vox-{i + 1:04}": off for i, off in enumerate(sortedOffsets)}
        for k in list(voxSeqToOffset.keys()):
            if k in newSeqMap:
                voxSeqToOffset[k] = newSeqMap[k]


if __name__ == "__main__":
    app = QApplication(sys.argv)
    widget = MainWindow()
    widget.show()
    sys.exit(app.exec())
