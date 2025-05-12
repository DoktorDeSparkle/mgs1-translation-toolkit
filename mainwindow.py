# This Python file uses the following encoding: utf-8
import sys, os

from PySide6.QtWidgets import QApplication, QMainWindow

# Important:
# You need to run the following command to generate the ui_form.py file
#     pyside6-uic form.ui -o ui_form.py, or
#     pyside2-uic form.ui -o ui_form.py
from ui_form import Ui_MainWindow

# For submodules, add submodule to sys.path
submodule_path = os.path.join(os.path.dirname(__file__), "scripts")  # Adjust path if needed
sys.path.insert(0, submodule_path)  # Insert at the beginning to prioritize

# MGS Script modules
from scripts import radioModule as radioManager



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
        # Loading
        self.ui.actionLoad_RADIO_DAT.triggered.connect(self.loadRadioDatFile)
        self.ui.actionLoad_RADIO_DAT.setShortcut("Ctrl+O")
        self.ui.actionLoad_RADIO_DAT.setStatusTip("Load a RADIO.DAT file")
        # Saving 
        self.ui.actionSave_RADIO_DAT.triggered.connect(self.loadRadioDatFile)
        self.ui.actionSave_RADIO_DAT.setShortcut("Ctrl+S")
        self.ui.actionSave_RADIO_DAT.setStatusTip("Save a RADIO.DAT file to XML format")


        # File Saving
        self.ui.quitButton.clicked.connect(self.populateOffsets)

    def loadRadioDatFile(self): # Loads the radio file from DAT
        print("Not implemented yet")
        pass

    def saveRadioDatFile(self): # Saves the radio file to XML
        print("Not implemented yet")
        pass


    # Testing!
    def populateOffsets(self):
        self.ui.offsetListBox.clear()
        stuff = ["apple", "shoe", "bicycle"]
        for i in range(len(stuff)):
            self.ui.offsetListBox.addItem(str(i), userData=stuff[i])
        print("populated")
        pass

if __name__ == "__main__":
    app = QApplication(sys.argv)
    widget = MainWindow()
    widget.show()
    sys.exit(app.exec())

