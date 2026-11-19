import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app.ui.sidebar import Sidebar


@pytest.fixture
def sidebar():
    app = QApplication.instance() or QApplication([])
    widget = Sidebar()
    widget.set_open(True, animated=False)
    widget.set_recent([("Wonderful Tonight", "/scores/a.json"), ("Blackbird", "/scores/b.json")])
    widget.show()
    app.processEvents()
    yield widget
    widget.close()


def recent_items(sidebar):
    return [sidebar.recent_layout.itemAt(i).widget() for i in range(sidebar.recent_layout.count())]


def test_clicking_remove_button_removes_without_opening(sidebar):
    selected, removed = [], []
    sidebar.recent_selected.connect(selected.append)
    sidebar.recent_removed.connect(removed.append)
    item = recent_items(sidebar)[1]
    QTest.mouseClick(item, Qt.MouseButton.LeftButton, pos=item.remove_rect().center().toPoint())
    assert removed == ["/scores/b.json"]
    assert selected == []


def test_clicking_item_body_still_opens_score(sidebar):
    selected, removed = [], []
    sidebar.recent_selected.connect(selected.append)
    sidebar.recent_removed.connect(removed.append)
    item = recent_items(sidebar)[0]
    QTest.mouseClick(item, Qt.MouseButton.LeftButton, pos=QPoint(40, item.height() // 2))
    assert selected == ["/scores/a.json"]
    assert removed == []
