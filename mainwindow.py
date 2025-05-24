# This Python file uses the following encoding: utf-8
import sys, os

from PySide6.QtWidgets import QApplication, QMainWindow, QFileDialog, QListWidgetItem

# Important:
# You need to run the following command to generate the ui_form.py file
#     pyside6-uic form.ui -o ui_form.py, or
#     pyside2-uic form.ui -o ui_form.py
from ui_form import Ui_MainWindow

# For submodules, add submodule to sys.path
submodule_path = os.path.join(os.path.dirname(__file__), "scripts")  # Adjust path if needed
sys.path.insert(0, submodule_path)  # Insert at the beginning to prioritize

# MGS Script modules
from scripts.radioModule import radioDataEditor as RDE
from scripts import demoClasses as voxCtl
from scripts import demoManager as DM
# Hold off on vox/demo
# from scripts import demoManager 

# Initialkize Radio Data Editor
radioManager = RDE()
voxManager: dict [str, voxCtl.demo] = {}
import scripts.audioTools.vagAudioTools as VAG

class XmlFileDialog(QFileDialog):
    """
    Custom QFileDialog class to handle file dialog operations.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFileMode(QFileDialog.ExistingFiles)
        self.setNameFilter("XML files (*.xml)")
        self.setViewMode(QFileDialog.List)
        self.setAcceptMode(QFileDialog.AcceptOpen)
        self.setModal(True)
        self.setWindowTitle("Select a Radio.xml File")
        self.setDirectory(os.getcwd())

class MainWindow(QMainWindow):
    """
    MainWindow class that inherits from QMainWindow.
    This class is responsible for setting up the main window of the application.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.ui = Ui_MainWindow()
        self.ui.setupUi(self)
        # After this point is my setup


        # File Menu
        # Loading DAT file
        self.ui.actionLoad_RADIO_DAT.triggered.connect(self.loadRadioDatFile)
        self.ui.actionLoad_RADIO_DAT.setStatusTip("Load a RADIO.DAT file")
        # Loading XML File
        self.ui.actionLoad_Radio_XML.triggered.connect(self.loadRadioXMLFile)
        self.ui.actionLoad_Radio_XML.setStatusTip("Load a RADIO.XML file")
        # Loading the VOX data
        self.ui.actionLoad_VOX_DAT.triggered.connect(self.loadVoxData)
        # Saving 
        self.ui.actionSave_RADIO_DAT.triggered.connect(self.saveRadioDatFile)
        self.ui.actionSave_RADIO_DAT.setStatusTip("Save a RADIO.DAT file to XML format")

        # Offset Selector:
        self.ui.offsetListBox.currentIndexChanged.connect(self.selectCallOffset)
        self.ui.offsetListBox.setStatusTip("Select a call offset to edit")
        self.ui.audioCueListView.currentItemChanged.connect(self.selectAudioCue)
        self.ui.subsPreviewList.currentItemChanged.connect(self.subtitleSelect)

        # Setting up Buttons
        self.ui.playVoxButton.clicked.connect(self.playVoxFile)
    
    def openFileDialog(self, fileTypes: str) -> str:
        # open dialog box
        dialog = XmlFileDialog(self)
        dialog.setNameFilter(fileTypes)
        if dialog.exec_() == QFileDialog.Accepted:
            selected_files = dialog.selectedFiles()
            if selected_files:
                filename = selected_files[0]
                print(f"Selected file: {filename}")
                return filename 
                # Load the radio file
                # self.loadRadioFile(filename)
            else:
                print("No file selected.")
        else:
            print("Dialog canceled.")

    def loadRadioDatFile(self): # Loads the radio file from DAT
        """
        This will probably parse the DAT file to an XML and load that instead. 
        Intermediate step is save the XML to file.
        """
        print("Not implemented yet")
        pass
    
    def loadRadioXMLFile(self): # Loads the radio file from DAT
        # open dialog box
        dialog = XmlFileDialog(self)
        if dialog.exec_() == QFileDialog.Accepted:
            selected_files = dialog.selectedFiles()
            if selected_files:
                filename = selected_files[0]
                print(f"Selected file: {filename}")
                # Load the radio file
                # self.loadRadioFile(filename)
            else:
                print("No file selected.")
        else:
            print("Dialog canceled.")
            return
        # Clear existing entries, then load offsets from file
        self.ui.offsetListBox.clear()

        radioManager.loadRadioXmlFile(filename)
        for offset in radioManager.getCallOffsets():
            self.ui.offsetListBox.addItem(offset, userData=offset)
        print("Not implemented yet")
        pass
    
    def loadVoxData(self):
        global voxManager
        voxFile = self.openFileDialog(fileTypes="DAT Files (*.DAT)")
        voxData = open(voxFile, 'rb').read()
        voxManager = DM.parseDemoFile(voxData)
        self.ui.playVoxButton.setEnabled(True)

    def saveRadioDatFile(self): # Saves the radio file to XML
        print("Not implemented yet")
        pass

    def playVoxFile(self):
        global voxManager
        voxOffset = self.ui.VoxAddressDisplay.text()
        if len(voxOffset) == 0:
            pass
        else:
            voxData = voxManager.get(str(voxOffset))
            vagFile = voxCtl.outputVagFile(voxData, "tempVag", "/tmp")
            VAG.playVagFile(vagFile)
            
        

    def selectCallOffset(self, index):
        """
        This function is called when the user selects an offset from the list.
        It sets the working call in the radio manager to the selected offset.
        """
        if index == -1:
            return
        offset = self.ui.offsetListBox.currentData()
        if offset is None:
            print("No item selected.")
            return
        print(f"Selected offset: {offset}")
        radioManager.setWorkingCall(offset)

        # Update the frequency display for the current call
        self.ui.FreqDisplay.display(radioManager.workingCall.get("freq"))
        # Reset toolbar
        self.ui.VoxAddressDisplay.setText("") 
        self.ui.VoxBlockAddressDisplay.setText("")

        # Not sure yet how to reset the list.
        self.ui.audioCueListView.clear()
        # Add the audio cues to the list view
        for audio in radioManager.getVoxOffsets():
            QListWidgetItem(audio, self.ui.audioCueListView)

    def selectAudioCue(self, item):
        """
        This function is called when the user selects an audio cue from the list.
        It sets the working audio cue in the radio manager to the selected offset.
        """
        offset = self.ui.audioCueListView.currentItem().text()
        print(f"Selected audio cue: {offset}")
        if offset is None:
            print("No item selected.")
            return
        else:
            radioManager.setWorkingVox(offset)
            self.ui.subsPreviewList.clear()
            # Get and display the offset of the VOX file
            voxOffset = radioManager.workingVox.get("content")[8:16]
            offsetBlock = bytes.fromhex(voxOffset)
            self.ui.VoxAddressDisplay.setText(str(int.from_bytes(offsetBlock, byteorder="big") * 0x800)) # Currently no vox offset value, grab it from raw hex
            self.ui.VoxBlockAddressDisplay.setText("0x" + voxOffset) # Currently no vox offset value, grab it from raw hex
            # Update subtitles in the display
            for text in radioManager.getSubs():
                QListWidgetItem(text, self.ui.subsPreviewList)
            for i, sub in enumerate(radioManager.getSubs()):
                item = QListWidgetItem(sub, self.ui.subsColumnList)
                item.setData(i, sub)
                
    def subtitleSelect(self, item):
        """
        This function is called when the user selects a subtitle from the list.
        It sets the working subtitle in the radio manager to the selected offset.
        """
        if item is None:
            print("No item selected.")
            return
        item = self.ui.subsPreviewList.currentRow()
        print(f"Selected subtitle: {item}")
        self.ui.DialogueEditorBox.setText(radioManager.getSubs()[item].replace("\\r\\n", "\n"))
        

if __name__ == "__main__":
    app = QApplication(sys.argv)
    widget = MainWindow()
    widget.show()
    sys.exit(app.exec())

