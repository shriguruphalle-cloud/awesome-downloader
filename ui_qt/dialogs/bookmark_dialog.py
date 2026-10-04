"""Edit bookmark: Chrome's right-click "Edit..." -- a saved page's name and
address, changed in place (it keeps its spot on the bar)."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QLineEdit, QVBoxLayout

from ..widgets.button import Button
from .base import CinematicDialog, button_row, header


class EditBookmarkDialog(CinematicDialog):
    """`normalize(text)` turns what's typed in the URL box into an address,
    or None when it isn't one (Save stays off until it is)."""

    def __init__(self, parent, title, url, dark_mode=True, normalize=None):
        super().__init__(parent, "Edit bookmark", dark_mode=dark_mode)
        self._normalize = normalize or (lambda text: (text or "").strip() or None)
        self.result_title = self.result_url = None
        self.setMinimumWidth(460)

        col = QVBoxLayout(self)
        col.setContentsMargins(24, 22, 24, 20)
        col.setSpacing(6)
        col.addLayout(header("Edit bookmark"))
        col.addSpacing(10)

        name_label = QLabel("Name")
        name_label.setObjectName("muted")
        col.addWidget(name_label)
        self.name = QLineEdit(title or "")
        self.name.setPlaceholderText(url or "")
        self.name.setAccessibleName("Name")
        col.addWidget(self.name)
        col.addSpacing(8)

        url_label = QLabel("URL")
        url_label.setObjectName("muted")
        col.addWidget(url_label)
        self.url = QLineEdit(url or "")
        self.url.setAccessibleName("URL")
        col.addWidget(self.url)
        col.addSpacing(16)

        cancel = Button("Cancel")
        cancel.clicked.connect(self.reject)
        self.save = Button("Save")
        self.save.setObjectName("accent")
        self.save.setMinimumWidth(96)
        self.save.setDefault(True)
        self.save.clicked.connect(self._save)
        col.addLayout(button_row(cancel, self.save))

        self.url.textChanged.connect(self._check)
        self.name.returnPressed.connect(self._save)
        self.url.returnPressed.connect(self._save)
        self._check()
        self.name.setFocus(Qt.FocusReason.OtherFocusReason)
        self.name.selectAll()

    def _check(self):
        self.save.setEnabled(self._normalize(self.url.text()) is not None)

    def _save(self):
        url = self._normalize(self.url.text())
        if url is None:
            return
        self.result_url = url
        self.result_title = self.name.text().strip() or url
        self.accept()

    def values(self):
        """(name, address) once saved."""
        return self.result_title, self.result_url
