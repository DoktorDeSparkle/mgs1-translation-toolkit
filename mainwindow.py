# This Python file uses the following encoding: utf-8
import sys, os

from PySide6.QtWidgets import (QApplication, QMainWindow, QFileDialog,
    QListWidgetItem, QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QMessageBox)
from PySide6.QtCore import Qt, QThread, Signal, QUrl
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput

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


class VoxConversionThread(QThread):
    """
    Runs the ffmpeg VAG→WAV conversion off the main thread.
    Emits conversionDone(wavPath) when the WAV is ready, or
    errorOccurred(message) on failure.
    Playback itself is handled by QMediaPlayer in the main thread —
    no ffplay required, works on Windows / macOS / Linux.
    """
    conversionDone = Signal(str)   # path to the finished WAV
    errorOccurred  = Signal(str)

    def __init__(self, vagFile: str, parent=None):
        super().__init__(parent)
        self.vagFile = vagFile

    def run(self):
        try:
            result = VAG.playVagFile(self.vagFile, convertOnly=True)
            if result == -1:
                self.errorOccurred.emit("Not a valid VAG file.")
                return
            self.conversionDone.emit(VAG.getTempWavPath())
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


class MainWindow(QMainWindow):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.ui = Ui_MainWindow()
        self.ui.setupUi(self)

        # ── File Menu ────────────────────────────────────────────────────────
        self.ui.actionLoad_RADIO_DAT.triggered.connect(self.loadRadioDatFile)
        self.ui.actionLoad_RADIO_DAT.setStatusTip("Load a RADIO.DAT file")
        self.ui.actionLoad_Radio_XML.triggered.connect(self.loadRadioXMLFile)
        self.ui.actionLoad_Radio_XML.setStatusTip("Load a RADIO.XML file")
        self.ui.actionLoad_VOX_DAT.triggered.connect(self.loadVoxData)
        self.ui.actionSave_RADIO_DAT.triggered.connect(self.saveRadioDatFile)
        self.ui.actionSave_RADIO_DAT.setStatusTip("Recompile RADIO.DAT from current XML")
        self.ui.actionSave_RADIO_XML.triggered.connect(self.saveRadioXMLFile)
        self.ui.actionSave_RADIO_XML.setStatusTip("Save current edits to RADIO.XML")

        # ── Navigation ───────────────────────────────────────────────────────
        self.ui.offsetListBox.currentIndexChanged.connect(self.selectCallOffset)
        self.ui.audioCueListView.currentItemChanged.connect(self.selectAudioCue)
        self.ui.subsPreviewList.currentItemChanged.connect(self.subtitleSelect)

        # ── Audio ────────────────────────────────────────────────────────────
        self.ui.playVoxButton.clicked.connect(self.playVoxFile)

        # ── Edit buttons (added programmatically) ────────────────────────────
        self._addEditButtons()

        # ── Add Save VOX.DAT to File menu (not in generated form) ─────────────
        from PySide6.QtGui import QAction
        self.actionSave_VOX_DAT = QAction("Save VOX.DAT", self)
        self.actionSave_VOX_DAT.setStatusTip("Write timing edits back to VOX.DAT")
        self.actionSave_VOX_DAT.triggered.connect(self.saveVoxDatFile)
        self.ui.menuFile.insertAction(self.ui.actionSave_RADIO_XML, self.actionSave_VOX_DAT)

        # Internal flag to suppress spinbox signals while loading data
        self._loadingSubtitle = False

        # ── Audio player (QMediaPlayer — no ffplay needed, cross-platform) ───
        self._audioOutput = QAudioOutput(self)
        self._player = QMediaPlayer(self)
        self._player.setAudioOutput(self._audioOutput)
        self._player.playbackStateChanged.connect(self._onPlaybackStateChanged)
        self._player.errorOccurred.connect(
            lambda err, msg: self._onPlaybackError(msg)
        )

        # Conversion thread (None when idle)
        self._convThread: VoxConversionThread = None

    # ── UI additions ─────────────────────────────────────────────────────────

    def _addEditButtons(self):
        """Adds Apply Edit, Split Subtitle, and Delete Subtitle buttons below the dialogue editor."""
        btn_layout = QHBoxLayout()

        self.applyEditButton = QPushButton("Apply Edit")
        self.applyEditButton.setToolTip("Save text and timing changes to the loaded XML")
        self.applyEditButton.setEnabled(False)
        self.applyEditButton.clicked.connect(self.applyEdit)
        btn_layout.addWidget(self.applyEditButton)

        self.splitSubButton = QPushButton("Split Subtitle")
        self.splitSubButton.setToolTip("Split this subtitle in two, halving the display duration")
        self.splitSubButton.setEnabled(False)
        self.splitSubButton.clicked.connect(self.splitSubtitle)
        btn_layout.addWidget(self.splitSubButton)

        self.deleteSubButton = QPushButton("Delete Subtitle")
        self.deleteSubButton.setToolTip("Remove this subtitle from the call")
        self.deleteSubButton.setEnabled(False)
        self.deleteSubButton.clicked.connect(self.deleteSubtitle)
        btn_layout.addWidget(self.deleteSubButton)

        # Insert below the durationBox inside groupBox's layout
        self.ui.verticalLayout_2.addLayout(btn_layout)

        # ── Stop button — inserted into the StatusBar row next to Play ────────
        self.stopVoxButton = QPushButton("Stop")
        self.stopVoxButton.setToolTip("Stop audio playback")
        self.stopVoxButton.setEnabled(False)
        self.stopVoxButton.clicked.connect(self.stopVoxFile)
        # Insert immediately after playVoxButton in horizontalLayout_2
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
        self.ui.offsetListBox.clear()
        for offset in radioManager.getCallOffsets():
            self.ui.offsetListBox.addItem(offset, userData=offset)
        self.setWindowTitle(f"Dialogue Editor — {os.path.basename(filename)}")

    def loadVoxData(self):
        global voxManager, voxOriginalData, voxFilePath
        voxFile = self.openFileDialog("DAT Files (*.DAT *.dat)", "Load VOX.DAT")
        if not voxFile:
            return
        voxOriginalData = open(voxFile, 'rb').read()
        voxFilePath = voxFile
        voxManager = DM.parseDemoFile(voxOriginalData)
        self.ui.playVoxButton.setEnabled(True)
        self.statusBar().showMessage(f"VOX.DAT loaded: {len(voxManager)} clips", 4000)

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
        self.splitSubButton.setEnabled(True)
        self.deleteSubButton.setEnabled(True)

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
            if isinstance(seg, voxCtl.captionChunk):
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

        # --- Text → XML -------------------------------------------------------
        newText = self.ui.DialogueEditorBox.toPlainText().replace("\n", "\\r\\n")
        radioManager.updateSubText(currentSubIndex, newText)

        # --- Timing → VOX demo -----------------------------------------------
        lines = self._getVoxSubtitleLines()
        if lines and currentSubIndex < len(lines):
            lines[currentSubIndex].startFrame = self.ui.startFrameBox.value()
            lines[currentSubIndex].displayFrames = self.ui.durationBox.value()

        # Refresh subtitle list to show new text
        self._refreshSubsList()
        self.applyEditButton.setStyleSheet("")
        self.statusBar().showMessage("Changes applied (unsaved — use File → Save RADIO.XML)", 5000)

    def splitSubtitle(self):
        """
        Splits the selected subtitle in two:
        - Radio XML: first half of text becomes this entry, second half is inserted after
        - VOX timings: duration is split 50/50 if a VOX demo is loaded
        """
        global currentSubIndex
        if currentSubIndex < 0:
            return

        subs = radioManager.getSubs()
        text = subs[currentSubIndex]
        # Attempt to split on \r\n first, then at midpoint
        if "\\r\\n" in text:
            parts = text.split("\\r\\n", 1)
        else:
            mid = len(text) // 2
            parts = [text[:mid], text[mid:]]

        # Update first subtitle
        radioManager.updateSubText(currentSubIndex, parts[0])

        # Insert second subtitle after current
        radioManager.addSubtitle(currentSubIndex, parts[1], after=True)

        # Split VOX timing if loaded
        lines = self._getVoxSubtitleLines()
        if lines and currentSubIndex < len(lines):
            orig_line = lines[currentSubIndex]
            orig_start = orig_line.startFrame
            orig_dur = orig_line.displayFrames
            half_dur = orig_dur // 2

            orig_line.displayFrames = half_dur

            # Insert a new dialogueLine after in the captionChunk
            self._insertVoxLine(currentSubIndex, orig_start + half_dur, half_dur, parts[1])

        self._refreshSubsList()
        # Re-select the first of the two new entries
        self.ui.subsPreviewList.setCurrentRow(currentSubIndex)
        self.statusBar().showMessage("Subtitle split — remember to save XML and VOX", 5000)

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
        """Rebuild the subtitle list widget from the current working VOX_CUES."""
        self.ui.subsPreviewList.clear()
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

    # ── Audio ─────────────────────────────────────────────────────────────────

    def playVoxFile(self):
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
        vagFile = voxCtl.outputVagFile(demo, "mgs_vox_temp", tempfile.gettempdir())

        self._convThread = VoxConversionThread(vagFile, parent=self)
        self._convThread.conversionDone.connect(self._onConversionDone)
        self._convThread.errorOccurred.connect(self._onPlaybackError)
        self._convThread.start()

        self.ui.playVoxButton.setEnabled(False)
        self.stopVoxButton.setEnabled(True)
        self.statusBar().showMessage("Converting…")

    def stopVoxFile(self):
        """Stop conversion thread (if running) and stop QMediaPlayer."""
        if self._convThread and self._convThread.isRunning():
            self._convThread.requestInterruption()
            self._convThread.wait(500)
        self._player.stop()
        self._resetPlaybackButtons()

    def _onConversionDone(self, wavPath: str):
        """Called from the conversion thread when the WAV is ready."""
        self._player.setSource(QUrl.fromLocalFile(wavPath))
        self._player.play()
        self.statusBar().showMessage("Playing…")

    def _onPlaybackStateChanged(self, state):
        if state == QMediaPlayer.PlaybackState.StoppedState:
            self._resetPlaybackButtons()
            self.statusBar().showMessage("Playback finished.", 2000)

    def _onPlaybackError(self, msg: str):
        self._resetPlaybackButtons()
        self.statusBar().showMessage(f"Audio error: {msg}", 5000)

    def _resetPlaybackButtons(self):
        self.ui.playVoxButton.setEnabled(bool(voxManager))
        self.stopVoxButton.setEnabled(False)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    widget = MainWindow()
    widget.show()
    sys.exit(app.exec())
