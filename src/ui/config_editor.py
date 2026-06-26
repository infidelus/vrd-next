"""
A minimal in-app text editor for the configuration file.

Editing the config inside the application avoids depending on the operating
system's file associations (which may point at unexpected programs).  It also
lets us validate the JSON before saving, so a typo can't leave the config file
broken on the next start-up.

All user-facing text uses British English.
"""

import json

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)


class ConfigEditorDialog(QDialog):

    def __init__(self, config_file, parent=None):
        super().__init__(parent)

        self._config_file = config_file

        self.setWindowTitle("Edit configuration")
        self.setModal(True)
        self.setMinimumSize(620, 520)

        layout = QVBoxLayout(self)

        info = QLabel(
            "Editing the configuration file. It is checked for valid JSON "
            "before saving, and your changes are applied when you save."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        self._editor = QPlainTextEdit()
        self._editor.setLineWrapMode(QPlainTextEdit.NoWrap)
        # Monospaced font for readability.
        font = QFont("monospace")
        font.setStyleHint(QFont.TypeWriter)
        font.setPointSize(10)
        self._editor.setFont(font)
        self._editor.setTabStopDistance(32)
        layout.addWidget(self._editor, 1)

        self._status = QLabel("")
        self._status.setStyleSheet("color: #d08a8a;")
        layout.addWidget(self._status)

        btn_row = QHBoxLayout()

        reload_btn = QPushButton("Reload from disk")
        reload_btn.setFocusPolicy(Qt.NoFocus)
        reload_btn.clicked.connect(self._load)
        btn_row.addWidget(reload_btn)

        btn_row.addStretch(1)

        cancel = QPushButton("Cancel")
        cancel.setFocusPolicy(Qt.NoFocus)
        cancel.clicked.connect(self.reject)
        btn_row.addWidget(cancel)

        save = QPushButton("Save")
        save.setDefault(True)
        save.setFocusPolicy(Qt.NoFocus)
        save.clicked.connect(self._save)
        btn_row.addWidget(save)

        layout.addLayout(btn_row)

        self._load()

    def _load(self):
        try:
            with open(self._config_file, encoding="utf-8") as f:
                self._editor.setPlainText(f.read())
            self._status.setText("")
        except Exception as exc:
            self._editor.setPlainText("")
            self._status.setText(f"Could not read the file: {exc}")

    def _save(self):
        text = self._editor.toPlainText()

        # Validate before writing - never let a typo corrupt the config.
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            self._status.setText(
                f"Not valid JSON (line {exc.lineno}, column {exc.colno}): "
                f"{exc.msg}. Nothing was saved."
            )
            return

        # Catch keyboard-shortcut clashes here, before the file is written, so
        # a key bound to two actions can't be saved at all.
        shortcuts = {}
        if isinstance(parsed, dict) and isinstance(parsed.get("shortcuts"), dict):
            shortcuts = parsed["shortcuts"]

        from config.shortcuts import find_shortcut_problems
        duplicates, unknown = find_shortcut_problems(shortcuts)

        if duplicates:
            lines = [
                "The same key is assigned to more than one action. A key can "
                "only trigger one action, so please change one of each pair "
                "before saving:",
                "",
            ]
            for combo, actions in duplicates:
                lines.append(f"    {combo}  \u2192  {', '.join(actions)}")
            QMessageBox.warning(
                self, "Duplicate shortcut keys", "\n".join(lines),
            )
            self._status.setText(
                "Duplicate shortcut keys - nothing was saved."
            )
            return

        if unknown:
            lines = [
                "These shortcut keys aren't recognised, so the key won't "
                "respond (only the menu item will):",
                "",
            ]
            for action, combo in unknown:
                lines.append(f"    {action}  \u2192  {combo}")
            lines += ["", "Save anyway?"]
            reply = QMessageBox.question(
                self,
                "Unrecognised shortcut keys",
                "\n".join(lines),
                QMessageBox.Save | QMessageBox.Cancel,
                QMessageBox.Cancel,
            )
            if reply != QMessageBox.Save:
                self._status.setText(
                    "Unrecognised shortcut keys - nothing was saved."
                )
                return

        try:
            with open(self._config_file, "w", encoding="utf-8") as f:
                f.write(text)
        except Exception as exc:
            self._status.setText(f"Could not save: {exc}")
            return

        QMessageBox.information(
            self,
            "Configuration saved",
            "The configuration has been saved and will be applied now.",
        )
        self.accept()
