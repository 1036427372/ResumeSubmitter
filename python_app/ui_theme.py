"""Central visual language for the desktop application."""

APP_STYLESHEET = """
* { font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif; font-size: 14px; color: #14213d; }
QMainWindow, QWidget { background: #f5f7fb; }
QFrame#sidebar { background: #102a43; border: 0; }
QLabel#sideBrand { color: #ffffff; font-size: 22px; font-weight: 700; background: transparent; }
QLabel#sideHint { color: #a9c5e5; font-size: 13px; background: transparent; }
QLabel#sideLibrary { color: #b7cae0; font-size: 13px; line-height: 1.5; background: #183b5c; border-radius: 10px; padding: 12px; }
QPushButton[nav="true"] { background: transparent; color: #d8e7f7; text-align: left; border: 0; border-radius: 6px; padding: 10px 12px; font-size: 14px; font-weight: 600; }
QPushButton[nav="true"]:hover { background: #1a4165; color: #ffffff; }
QPushButton[nav="true"]:checked { background: #2f6fed; color: #ffffff; }
QLabel#pageTitle { color: #163b6b; font-size: 21px; font-weight: 700; }
QFrame#hero { background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #14213d, stop:1 #3157a8); border-radius: 18px; }
QLabel#heroTitle { color: white; font-size: 22px; font-weight: 700; background: transparent; }
QLabel#heroText { color: #dbe7ff; font-size: 14px; background: transparent; }
QFrame#card { background: white; border: 1px solid #e4eaf4; border-radius: 8px; }
QLabel#cardTitle { color: #1d3d78; font-size: 15px; font-weight: 700; background: transparent; }
QLabel#muted { color: #66758a; font-size: 13px; background: transparent; }
QStackedWidget { background: transparent; }
QLineEdit, QComboBox { background: white; border: 1px solid #dce4f0; border-radius: 6px; padding: 9px 10px; font-size: 14px; selection-background-color: #cfe0ff; }
QLineEdit:focus, QComboBox:focus { border: 2px solid #4b81ed; padding: 8px 9px; }
QTextEdit { background: white; border: 1px solid #dce4f0; border-radius: 7px; padding: 9px; font-size: 14px; selection-background-color: #cfe0ff; }
QTextEdit:focus { border: 2px solid #4b81ed; padding: 8px; }
QPushButton { background: #2f6fed; color: white; border: 0; border-radius: 6px; padding: 9px 14px; font-size: 14px; font-weight: 700; }
QPushButton:hover { background: #215dcc; }
QPushButton:pressed { background: #184aa7; }
QPushButton[secondary="true"] { background: #edf3ff; color: #2459bc; }
QPushButton[secondary="true"]:hover { background: #dce9ff; }
QPushButton[danger="true"] { background: #fff0f0; color: #bd3232; }
QPushButton[danger="true"]:hover { background: #ffe1e1; }
QScrollArea { border: 0; background: transparent; }
QHeaderView::section { background: #f1f5fb; color: #50627a; border: 0; border-bottom: 1px solid #e3eaf4; padding: 10px; font-size: 14px; font-weight: 700; }
QTableWidget { background: white; border: 1px solid #e2e9f3; border-radius: 7px; gridline-color: #edf1f6; selection-background-color: #e6efff; font-size: 14px; }
QTableWidget::item { padding: 10px 12px; }
QTableWidget QLineEdit { font-size: 16px; min-height: 34px; padding: 6px 8px; }
QTableCornerButton::section { background: #f1f5fb; border: 0; }
QLabel#fieldLabel { color: #50627a; font-size: 14px; font-weight: 600; }
QLabel#sectionHint { color: #52667f; font-size: 15px; }
QProgressBar { border: 0; border-radius: 7px; background: #e6ecf5; height: 10px; text-align: center; color: transparent; }
QProgressBar::chunk { border-radius: 7px; background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #3979ef, stop:1 #6e55e8); }
QStatusBar { background: white; border-top: 1px solid #e5ebf3; color: #66758a; }
QMessageBox { background: #f5f7fb; }
QDialog { background: #f5f7fb; }
QDialog QLabel { font-size: 16px; }
QCheckBox { padding: 9px 4px; font-size: 16px; }
QCheckBox::indicator { width: 17px; height: 17px; border: 1px solid #b9c7dc; border-radius: 4px; background: white; }
QCheckBox::indicator:checked { background: #2f6fed; border-color: #2f6fed; }
"""
