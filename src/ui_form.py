# -*- coding: utf-8 -*-

################################################################################
## Form generated from reading UI file 'form.ui'
##
## Created by: Qt User Interface Compiler version 6.10.2
##
## WARNING! All changes made in this file will be lost when recompiling UI file!
################################################################################

from PySide6.QtCore import (QCoreApplication, QDate, QDateTime, QLocale,
    QMetaObject, QObject, QPoint, QRect,
    QSize, QTime, QUrl, Qt)
from PySide6.QtGui import (QAction, QBrush, QColor, QConicalGradient,
    QCursor, QFont, QFontDatabase, QGradient,
    QIcon, QImage, QKeySequence, QLinearGradient,
    QPainter, QPalette, QPixmap, QRadialGradient,
    QTransform)
from PySide6.QtWidgets import (QApplication, QComboBox, QFrame, QGraphicsView,
    QGroupBox, QHBoxLayout, QLCDNumber, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMainWindow,
    QMenu, QMenuBar, QPushButton, QSizePolicy,
    QSpinBox, QStatusBar, QTextEdit, QVBoxLayout,
    QWidget)

class Ui_MainWindow(object):
    def setupUi(self, MainWindow):
        if not MainWindow.objectName():
            MainWindow.setObjectName(u"MainWindow")
        MainWindow.resize(1189, 821)
        self.actionLoad_RADIO_DAT = QAction(MainWindow)
        self.actionLoad_RADIO_DAT.setObjectName(u"actionLoad_RADIO_DAT")
        self.actionLoad_RADIO_DAT.setCheckable(False)
        self.actionLoad_VOX_DAT = QAction(MainWindow)
        self.actionLoad_VOX_DAT.setObjectName(u"actionLoad_VOX_DAT")
        self.actionSave_RADIO_DAT = QAction(MainWindow)
        self.actionSave_RADIO_DAT.setObjectName(u"actionSave_RADIO_DAT")
        self.actionSave_RADIO_XML = QAction(MainWindow)
        self.actionSave_RADIO_XML.setObjectName(u"actionSave_RADIO_XML")
        self.actionQuit = QAction(MainWindow)
        self.actionQuit.setObjectName(u"actionQuit")
        self.actionPreferences = QAction(MainWindow)
        self.actionPreferences.setObjectName(u"actionPreferences")
        self.actionAbout_Dialogue_Editor = QAction(MainWindow)
        self.actionAbout_Dialogue_Editor.setObjectName(u"actionAbout_Dialogue_Editor")
        self.actionLoad_Radio_XML = QAction(MainWindow)
        self.actionLoad_Radio_XML.setObjectName(u"actionLoad_Radio_XML")
        self.actionAnother_option = QAction(MainWindow)
        self.actionAnother_option.setObjectName(u"actionAnother_option")
        self.actionCopy = QAction(MainWindow)
        self.actionCopy.setObjectName(u"actionCopy")
        self.actionPasta = QAction(MainWindow)
        self.actionPasta.setObjectName(u"actionPasta")
        self.actionHow_to_use = QAction(MainWindow)
        self.actionHow_to_use.setObjectName(u"actionHow_to_use")
        self.centralwidget = QWidget(MainWindow)
        self.centralwidget.setObjectName(u"centralwidget")
        self.horizontalLayout = QHBoxLayout(self.centralwidget)
        self.horizontalLayout.setObjectName(u"horizontalLayout")
        self.frame = QFrame(self.centralwidget)
        self.frame.setObjectName(u"frame")
        self.frame.setFrameShape(QFrame.Shape.StyledPanel)
        self.frame.setFrameShadow(QFrame.Shadow.Raised)
        self.verticalLayout = QVBoxLayout(self.frame)
        self.verticalLayout.setObjectName(u"verticalLayout")
        self.labelCallOffset = QLabel(self.frame)
        self.labelCallOffset.setObjectName(u"labelCallOffset")

        self.verticalLayout.addWidget(self.labelCallOffset)

        self.offsetListBox = QComboBox(self.frame)
        self.offsetListBox.setObjectName(u"offsetListBox")

        self.verticalLayout.addWidget(self.offsetListBox)

        self.StatusBar = QFrame(self.frame)
        self.StatusBar.setObjectName(u"StatusBar")
        self.StatusBar.setFrameShape(QFrame.Shape.StyledPanel)
        self.StatusBar.setFrameShadow(QFrame.Shadow.Raised)
        self.horizontalLayout_2 = QHBoxLayout(self.StatusBar)
        self.horizontalLayout_2.setObjectName(u"horizontalLayout_2")
        self.FreqLabel = QLabel(self.StatusBar)
        self.FreqLabel.setObjectName(u"FreqLabel")
        self.FreqLabel.setEnabled(True)

        self.horizontalLayout_2.addWidget(self.FreqLabel)

        self.FreqDisplay = QLCDNumber(self.StatusBar)
        self.FreqDisplay.setObjectName(u"FreqDisplay")
        self.FreqDisplay.setEnabled(True)
        palette = QPalette()
        brush = QBrush(QColor(0, 255, 58, 217))
        brush.setStyle(Qt.BrushStyle.SolidPattern)
        palette.setBrush(QPalette.ColorGroup.Active, QPalette.ColorRole.WindowText, brush)
        palette.setBrush(QPalette.ColorGroup.Inactive, QPalette.ColorRole.WindowText, brush)
        self.FreqDisplay.setPalette(palette)
        self.FreqDisplay.setAutoFillBackground(True)
        self.FreqDisplay.setFrameShape(QFrame.Shape.NoFrame)
        self.FreqDisplay.setFrameShadow(QFrame.Shadow.Plain)
        self.FreqDisplay.setLineWidth(1)
        self.FreqDisplay.setMidLineWidth(0)
        self.FreqDisplay.setSmallDecimalPoint(True)
        self.FreqDisplay.setDigitCount(6)
        self.FreqDisplay.setMode(QLCDNumber.Mode.Dec)
        self.FreqDisplay.setSegmentStyle(QLCDNumber.SegmentStyle.Flat)
        self.FreqDisplay.setProperty(u"value", 140.849999999999994)

        self.horizontalLayout_2.addWidget(self.FreqDisplay)

        self.playVoxButton = QPushButton(self.StatusBar)
        self.playVoxButton.setObjectName(u"playVoxButton")
        self.playVoxButton.setEnabled(False)

        self.horizontalLayout_2.addWidget(self.playVoxButton)

        self.VoxBlockAddressLabel = QLabel(self.StatusBar)
        self.VoxBlockAddressLabel.setObjectName(u"VoxBlockAddressLabel")

        self.horizontalLayout_2.addWidget(self.VoxBlockAddressLabel)

        self.VoxBlockAddressDisplay = QLineEdit(self.StatusBar)
        self.VoxBlockAddressDisplay.setObjectName(u"VoxBlockAddressDisplay")

        self.horizontalLayout_2.addWidget(self.VoxBlockAddressDisplay)

        self.VoxAddressLabel = QLabel(self.StatusBar)
        self.VoxAddressLabel.setObjectName(u"VoxAddressLabel")

        self.horizontalLayout_2.addWidget(self.VoxAddressLabel)

        self.VoxAddressDisplay = QLineEdit(self.StatusBar)
        self.VoxAddressDisplay.setObjectName(u"VoxAddressDisplay")
        self.VoxAddressDisplay.setReadOnly(True)

        self.horizontalLayout_2.addWidget(self.VoxAddressDisplay)


        self.verticalLayout.addWidget(self.StatusBar)

        self.audioCueListView = QListWidget(self.frame)
        self.audioCueListView.setObjectName(u"audioCueListView")

        self.verticalLayout.addWidget(self.audioCueListView)

        self.subsPreviewList = QListWidget(self.frame)
        self.subsPreviewList.setObjectName(u"subsPreviewList")

        self.verticalLayout.addWidget(self.subsPreviewList)


        self.horizontalLayout.addWidget(self.frame)

        self.frame_2 = QFrame(self.centralwidget)
        self.frame_2.setObjectName(u"frame_2")
        self.frame_2.setFrameShape(QFrame.Shape.StyledPanel)
        self.frame_2.setFrameShadow(QFrame.Shadow.Raised)
        self.verticalLayout_4 = QVBoxLayout(self.frame_2)
        self.verticalLayout_4.setObjectName(u"verticalLayout_4")
        self.previewFrame = QFrame(self.frame_2)
        self.previewFrame.setObjectName(u"previewFrame")
        self.previewFrame.setFrameShape(QFrame.Shape.StyledPanel)
        self.previewFrame.setFrameShadow(QFrame.Shadow.Raised)
        self.verticalLayout_3 = QVBoxLayout(self.previewFrame)
        self.verticalLayout_3.setObjectName(u"verticalLayout_3")
        self.previewLabel = QLabel(self.previewFrame)
        self.previewLabel.setObjectName(u"previewLabel")

        self.verticalLayout_3.addWidget(self.previewLabel)

        self.graphicsView = QGraphicsView(self.previewFrame)
        self.graphicsView.setObjectName(u"graphicsView")
        self.graphicsView.setInteractive(False)

        self.verticalLayout_3.addWidget(self.graphicsView)


        self.verticalLayout_4.addWidget(self.previewFrame)

        self.groupBox = QGroupBox(self.frame_2)
        self.groupBox.setObjectName(u"groupBox")
        self.verticalLayout_2 = QVBoxLayout(self.groupBox)
        self.verticalLayout_2.setObjectName(u"verticalLayout_2")
        self.labelDialogue = QLabel(self.groupBox)
        self.labelDialogue.setObjectName(u"labelDialogue")

        self.verticalLayout_2.addWidget(self.labelDialogue)

        self.DialogueEditorBox = QTextEdit(self.groupBox)
        self.DialogueEditorBox.setObjectName(u"DialogueEditorBox")

        self.verticalLayout_2.addWidget(self.DialogueEditorBox)

        self.labelStartFrame = QLabel(self.groupBox)
        self.labelStartFrame.setObjectName(u"labelStartFrame")

        self.verticalLayout_2.addWidget(self.labelStartFrame)

        self.startFrameBox = QSpinBox(self.groupBox)
        self.startFrameBox.setObjectName(u"startFrameBox")
        self.startFrameBox.setMaximum(99999999)
        self.startFrameBox.setDisplayIntegerBase(9)

        self.verticalLayout_2.addWidget(self.startFrameBox)

        self.labelDuration = QLabel(self.groupBox)
        self.labelDuration.setObjectName(u"labelDuration")

        self.verticalLayout_2.addWidget(self.labelDuration)

        self.durationBox = QSpinBox(self.groupBox)
        self.durationBox.setObjectName(u"durationBox")

        self.verticalLayout_2.addWidget(self.durationBox)


        self.verticalLayout_4.addWidget(self.groupBox)

        self.quitButton = QPushButton(self.frame_2)
        self.quitButton.setObjectName(u"quitButton")

        self.verticalLayout_4.addWidget(self.quitButton)


        self.horizontalLayout.addWidget(self.frame_2)

        MainWindow.setCentralWidget(self.centralwidget)
        self.menubar = QMenuBar(MainWindow)
        self.menubar.setObjectName(u"menubar")
        self.menubar.setGeometry(QRect(0, 0, 1189, 24))
        self.menubar.setDefaultUp(True)
        self.menuFile = QMenu(self.menubar)
        self.menuFile.setObjectName(u"menuFile")
        self.menuEdit = QMenu(self.menubar)
        self.menuEdit.setObjectName(u"menuEdit")
        self.menuHelp = QMenu(self.menubar)
        self.menuHelp.setObjectName(u"menuHelp")
        MainWindow.setMenuBar(self.menubar)
        self.statusbar = QStatusBar(MainWindow)
        self.statusbar.setObjectName(u"statusbar")
        MainWindow.setStatusBar(self.statusbar)
#if QT_CONFIG(shortcut)
        self.labelCallOffset.setBuddy(self.offsetListBox)
        self.previewLabel.setBuddy(self.graphicsView)
        self.labelDialogue.setBuddy(self.DialogueEditorBox)
        self.labelStartFrame.setBuddy(self.startFrameBox)
        self.labelDuration.setBuddy(self.durationBox)
#endif // QT_CONFIG(shortcut)

        self.menubar.addAction(self.menuFile.menuAction())
        self.menubar.addAction(self.menuEdit.menuAction())
        self.menubar.addAction(self.menuHelp.menuAction())
        self.menuFile.addAction(self.actionLoad_RADIO_DAT)
        self.menuFile.addAction(self.actionLoad_Radio_XML)
        self.menuFile.addAction(self.actionLoad_VOX_DAT)
        self.menuFile.addSeparator()
        self.menuFile.addAction(self.actionSave_RADIO_DAT)
        self.menuFile.addAction(self.actionSave_RADIO_XML)
        self.menuFile.addSeparator()
        self.menuFile.addAction(self.actionQuit)
        self.menuEdit.addAction(self.actionAnother_option)
        self.menuEdit.addAction(self.actionPreferences)
        self.menuEdit.addSeparator()
        self.menuEdit.addAction(self.actionCopy)
        self.menuEdit.addAction(self.actionPasta)
        self.menuHelp.addAction(self.actionHow_to_use)
        self.menuHelp.addAction(self.actionAbout_Dialogue_Editor)

        self.retranslateUi(MainWindow)
        self.quitButton.clicked.connect(MainWindow.close)
        self.actionQuit.triggered.connect(MainWindow.close)

        QMetaObject.connectSlotsByName(MainWindow)
    # setupUi

    def retranslateUi(self, MainWindow):
        MainWindow.setWindowTitle(QCoreApplication.translate("MainWindow", u"Dialogue Editor v0.2.1", None))
        self.actionLoad_RADIO_DAT.setText(QCoreApplication.translate("MainWindow", u"Load RADIO.DAT...", None))
#if QT_CONFIG(shortcut)
        self.actionLoad_RADIO_DAT.setShortcut(QCoreApplication.translate("MainWindow", u"Ctrl+O", None))
#endif // QT_CONFIG(shortcut)
        self.actionLoad_VOX_DAT.setText(QCoreApplication.translate("MainWindow", u"Load VOX.DAT...", None))
#if QT_CONFIG(shortcut)
        self.actionLoad_VOX_DAT.setShortcut(QCoreApplication.translate("MainWindow", u"Ctrl+K", None))
#endif // QT_CONFIG(shortcut)
        self.actionSave_RADIO_DAT.setText(QCoreApplication.translate("MainWindow", u"Save RADIO.DAT", None))
        self.actionSave_RADIO_XML.setText(QCoreApplication.translate("MainWindow", u"Save RADIO.XML", None))
        self.actionQuit.setText(QCoreApplication.translate("MainWindow", u"Quit", None))
#if QT_CONFIG(shortcut)
        self.actionQuit.setShortcut(QCoreApplication.translate("MainWindow", u"Ctrl+Q", None))
#endif // QT_CONFIG(shortcut)
        self.actionPreferences.setText(QCoreApplication.translate("MainWindow", u"Preferences", None))
        self.actionAbout_Dialogue_Editor.setText(QCoreApplication.translate("MainWindow", u"About Dialogue Editor...", None))
        self.actionLoad_Radio_XML.setText(QCoreApplication.translate("MainWindow", u"Load Radio.XML...", None))
#if QT_CONFIG(shortcut)
        self.actionLoad_Radio_XML.setShortcut(QCoreApplication.translate("MainWindow", u"Ctrl+L", None))
#endif // QT_CONFIG(shortcut)
        self.actionAnother_option.setText(QCoreApplication.translate("MainWindow", u"Another option", None))
        self.actionCopy.setText(QCoreApplication.translate("MainWindow", u"Copy", None))
        self.actionPasta.setText(QCoreApplication.translate("MainWindow", u"Pasta", None))
        self.actionHow_to_use.setText(QCoreApplication.translate("MainWindow", u"How to use", None))
        self.labelCallOffset.setText(QCoreApplication.translate("MainWindow", u"Call (Offset)", None))
        self.FreqLabel.setText(QCoreApplication.translate("MainWindow", u"Frequency:", None))
        self.playVoxButton.setText(QCoreApplication.translate("MainWindow", u"Play Audio", None))
#if QT_CONFIG(shortcut)
        self.playVoxButton.setShortcut(QCoreApplication.translate("MainWindow", u"Ctrl+P", None))
#endif // QT_CONFIG(shortcut)
        self.VoxBlockAddressLabel.setText(QCoreApplication.translate("MainWindow", u"Vox Block:", None))
        self.VoxAddressLabel.setText(QCoreApplication.translate("MainWindow", u"Vox Address:", None))
        self.previewLabel.setText(QCoreApplication.translate("MainWindow", u"Preview:", None))
        self.groupBox.setTitle(QCoreApplication.translate("MainWindow", u"Timings", None))
        self.labelDialogue.setText(QCoreApplication.translate("MainWindow", u"Dialogue", None))
        self.labelStartFrame.setText(QCoreApplication.translate("MainWindow", u"Start Frame", None))
        self.labelDuration.setText(QCoreApplication.translate("MainWindow", u"Duration", None))
        self.quitButton.setText(QCoreApplication.translate("MainWindow", u"Quit", None))
        self.menuFile.setTitle(QCoreApplication.translate("MainWindow", u"File", None))
        self.menuEdit.setTitle(QCoreApplication.translate("MainWindow", u"Edit", None))
        self.menuHelp.setTitle(QCoreApplication.translate("MainWindow", u"Help", None))
    # retranslateUi

