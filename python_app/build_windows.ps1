# 在 python_app 目录执行：powershell -ExecutionPolicy Bypass -File .\build_windows.ps1
python -m pip install -r requirements.txt
python -m PyInstaller --noconfirm --clean --windowed --name "投了么" --icon assets/touleme-logo.ico --add-data "assets/touleme-logo.png;assets" --add-data "assets/touleme-logo.svg;assets" --collect-all PySide6.QtWebEngineCore --collect-all PySide6.QtWebEngineWidgets main.py
