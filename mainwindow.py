# This Python file uses the following encoding: utf-8
import sys

from PySide6.QtWidgets import QApplication, QMainWindow

# Important:
# You need to run the following command to generate the ui_form.py file
#     pyside6-uic form.ui -o ui_form.py, or
#     pyside2-uic form.ui -o ui_form.py
from ui_form import Ui_MainWindow

class MainWindow(QMainWindow):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.ui = Ui_MainWindow()
        self.ui.setupUi(self)
        self.ui.quitButton.clicked.connect(self.populateOffsets)


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

