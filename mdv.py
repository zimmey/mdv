#!/usr/bin/env python3
"""mdv - a lightweight, tabbed Markdown viewer and editor."""

import os
import re
import subprocess
import sys

from PyQt5.QtCore import QEvent, QFileSystemWatcher, QPoint, QSettings, Qt, QTimer, QUrl
from PyQt5.QtGui import (QColor, QDesktopServices, QFont, QFontDatabase, QIcon, QKeySequence, QPalette,
                         QTextBlockFormat, QTextCharFormat, QTextCursor, QTextDocument,
                         QTextFormat, QTextTable, QTextTableFormat)
from PyQt5.QtNetwork import QLocalServer, QLocalSocket
from PyQt5.QtWidgets import (QAction, QApplication, QFileDialog, QHBoxLayout, QLabel,
                             QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit, QPushButton,
                             QShortcut, QStackedWidget, QTabWidget, QTextBrowser, QVBoxLayout,
                             QWidget)

APP = "mdv"
SERVER_NAME = f"mdv-{os.getuid()}"
MAX_RECENT = 10
ZOOM_STEP = 1.1
SPACER = QTextFormat.UserProperty + 1  # marks the collapsed block above front matter
MD_EXTS = (".md", ".markdown", ".mdown", ".mkd", ".txt")
USAGE = """usage: mdv [-n | --new-window] [FILE ...]

Opens FILEs as tabs in the running mdv window, or starts one.
Run without files, it opens a new window.
  -n, --new-window   open FILEs in a separate window instead of adding tabs
  -h, --help         show this help"""


def slugify(text):
    text = re.sub(r"[^\w\- ]", "", text.strip().lower())
    return text.replace(" ", "-")


def split_front_matter(text):
    """Split leading YAML front matter off the text.

    Returns ([(key, value), ...], body). Indented or "- item" lines are folded into the
    previous key's value, so simple nested YAML still shows up readably.
    """
    lines = text.lstrip("\ufeff").split("\n")
    if not lines or lines[0].rstrip() != "---":
        return [], text
    for end in range(1, len(lines)):
        if lines[end].rstrip() in ("---", "..."):
            break
    else:
        return [], text
    meta = []
    for line in lines[1:end]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = re.match(r"([^\s:][^:]*):(?:\s+(.*))?$", line)
        if m and not line[0].isspace():
            value = (m.group(2) or "").strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            meta.append([m.group(1).strip(), value])
        elif meta:
            meta[-1][1] = (meta[-1][1] + "\n" + line.strip()).strip()
    return meta, "\n".join(lines[end + 1:])


VOID_TAGS = "hr|input|wbr|area|base|col|embed|link|meta|source|track"
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")


def fix_html(text):
    """Work around Qt 5.15's Markdown importer, which silently drops everything after
    an unclosed HTML tag such as <br> or <img ...>. Code spans and blocks are untouched.
    """
    def fix(part):
        part = re.sub(r"<br\s*/?>", "\u2028", part, flags=re.I)  # U+2028 = line break in Qt
        part = re.sub(r"<img\b([^>]*?)/?>", img_to_md, part, flags=re.I)
        return re.sub(rf"<({VOID_TAGS})\b([^>]*?)\s*/?>", r"<\1\2/>", part, flags=re.I)

    out, fence = [], None
    for line in text.split("\n"):
        m = FENCE.match(line)
        if fence:
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence):
                fence = None
            out.append(line)
        elif m:
            fence = m.group(1)
            out.append(line)
        else:  # odd-numbered pieces are `code spans`
            pieces = re.split(r"(`+[^`]*`+)", line)
            out.append("".join(p if i % 2 else fix(p) for i, p in enumerate(pieces)))
    return "\n".join(out)


def img_to_md(m):
    attrs = dict(re.findall(r"""(\w+)\s*=\s*["']([^"']*)["']""", m.group(1)))
    return f"![{attrs.get('alt', '')}]({attrs['src']})" if "src" in attrs else ""


class Tab(QStackedWidget):
    """One tab: a file, shown rendered or (Ctrl+M) as editable source."""

    def __init__(self, path, window):
        super().__init__()
        self.path = path
        self.window_ = window
        self.text = ""  # file contents as last loaded or saved, i.e. what should be on disk
        self.declined = None  # on-disk version the user chose not to refresh to
        self.bom = self.crlf = False  # restored on save, so only edited lines change
        self.view = Viewer(self)
        self.editor = Editor(self)
        self.addWidget(self.view)
        self.addWidget(self.editor)
        self.editor.document().modificationChanged.connect(lambda: window.update_tab(self))
        self.load()

    @property
    def editing(self):
        return self.currentWidget() is self.editor

    def modified(self):
        return self.editor.document().isModified()

    def read_disk(self):
        """Current file contents, or None if it can't be read."""
        try:
            with open(self.path, "rb") as f:
                return f.read().decode("utf-8", errors="replace")
        except OSError:
            return None

    def load(self):
        """Read the file from disk into the editor and render it."""
        read_only = False
        try:
            with open(self.path, "rb") as f:
                data = f.read()
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:  # saving would destroy the bytes that aren't UTF-8
                text, read_only = data.decode("utf-8", errors="replace"), True
        except OSError:
            text, read_only = f"**Could not read file:** `{self.path}`", True
        self.text = text
        self.declined = None
        self.bom = text.startswith("\ufeff")
        self.crlf = "\r\n" in text
        self.editor.setPlainText((text[1:] if self.bom else text).replace("\r\n", "\n"))
        self.editor.setReadOnly(read_only)
        self.view.render(self.source())

    def source(self):
        """The editor's text. toPlainText() would turn non-breaking spaces into plain ones."""
        return self.editor.document().toRawText().replace("\u2029", "\n")

    def save(self):
        """Write unsaved edits to disk. Returns False if the file couldn't be written."""
        if not self.modified():
            return True
        disk = self.read_disk()
        if disk is not None and disk not in (self.text, self.declined):
            self.window_.ask_reload(self, disk)  # changed behind our back: let the user pick
            if not self.modified():
                return True
        text = self.source()
        if self.crlf:
            text = text.replace("\n", "\r\n")
        if self.bom:
            text = "\ufeff" + text
        try:
            with open(self.path, "wb") as f:  # in place, so permissions and links survive
                f.write(text.encode("utf-8"))
        except OSError as e:
            QMessageBox.warning(self.window_, APP, f"Could not save {self.path}:\n\n{e.strerror}")
            return False
        self.text = text  # so the file watcher doesn't offer to refresh to our own write
        self.declined = None
        self.editor.document().setModified(False)
        return True

    def set_editing(self, on):
        if on == self.editing:
            return
        old, new = (self.view, self.editor) if on else (self.editor, self.view)
        bar = old.verticalScrollBar()
        frac = bar.value() / bar.maximum() if bar.maximum() else 0.0
        if not on:
            self.save()
            self.view.render(self.source())
        self.setCurrentWidget(new)
        # The two views have different heights, so keep the relative position.
        bar = new.verticalScrollBar()
        bar.setValue(int(frac * bar.maximum()))
        new.setFocus()
        self.window_.update_tab(self)

    def eventFilter(self, obj, event):
        # Route Ctrl+wheel to the window's shared zoom rather than per-widget zoom.
        if event.type() == QEvent.Wheel and event.modifiers() & Qt.ControlModifier:
            self.window_.zoom(1 if event.angleDelta().y() > 0 else -1)
            return True
        return super().eventFilter(obj, event)


class Editor(QPlainTextEdit):
    """A tab's source view: the file as plain, editable text."""

    def __init__(self, tab):
        super().__init__()
        self.tab = tab
        self.viewport().installEventFilter(tab)
        self.document().setDocumentMargin(28)

    def contextMenuEvent(self, event):
        menu = self.createStandardContextMenu(event.pos())
        menu.addSeparator()
        menu.addAction(self.tab.window_.edit_action)
        menu.exec_(event.globalPos())


class Viewer(QTextBrowser):
    """A tab's rendered view."""

    def __init__(self, tab):
        super().__init__()
        self.tab = tab
        self.setOpenLinks(False)
        self.anchorClicked.connect(self.on_link)
        self.viewport().installEventFilter(tab)
        self.document().setDocumentMargin(28)

    def render(self, text):
        """Render Markdown source text, keeping the scroll position."""
        bar = self.verticalScrollBar()
        pos = bar.value()
        folder = os.path.dirname(self.tab.path)
        self.setSearchPaths([folder])
        doc = self.document()
        doc.setBaseUrl(QUrl.fromLocalFile(folder + os.sep))
        if self.tab.path.lower().endswith(".txt"):
            doc.setPlainText(text)
            self.style_document()
        else:
            meta, body = split_front_matter(text)
            doc.setMarkdown(fix_html(body), QTextDocument.MarkdownDialectGitHub)
            if meta:
                self.insert_front_matter(meta)
            self.style_document()
        # Park the (invisible) cursor at the top, or Qt scrolls to it at the document end.
        self.moveCursor(QTextCursor.Start)
        bar.setValue(pos)

    def insert_front_matter(self, meta):
        """Show front matter as a compact key/value table above the document."""
        cursor = QTextCursor(self.document())
        tf = QTextTableFormat()
        tf.setBottomMargin(12)
        table = cursor.insertTable(len(meta), 2, tf)
        key_fmt = QTextCharFormat()
        key_fmt.setFontWeight(QFont.Bold)
        key_fmt.setForeground(self.palette().color(QPalette.PlaceholderText))
        for row, (key, value) in enumerate(meta):
            table.cellAt(row, 0).firstCursorPosition().insertText(key, key_fmt)
            table.cellAt(row, 1).firstCursorPosition().insertText(value, QTextCharFormat())
        # Qt always keeps an empty paragraph above a leading table; collapse it.
        top = QTextCursor(self.document())
        bf = QTextBlockFormat()
        bf.setLineHeight(1, QTextBlockFormat.FixedHeight)
        bf.setProperty(SPACER, True)
        top.setBlockFormat(bf)
        cf = QTextCharFormat()
        cf.setFontPointSize(1)
        top.setBlockCharFormat(cf)

    def contextMenuEvent(self, event):
        menu = self.createStandardContextMenu(event.pos())
        menu.addSeparator()
        menu.addAction(self.tab.window_.edit_action)
        menu.exec_(event.globalPos())

    def style_document(self):
        """Add spacing, shade code blocks and pad tables, using the system palette."""
        pal = self.palette()
        code_bg = pal.color(QPalette.AlternateBase)
        if code_bg == pal.color(QPalette.Base):
            code_bg = pal.color(QPalette.Base).darker(115) if pal.color(QPalette.Base).lightness() > 128 \
                else pal.color(QPalette.Base).lighter(140)
        mono = QFontDatabase.systemFont(QFontDatabase.FixedFont).family()
        doc = self.document()
        cursor = QTextCursor(doc)
        cursor.beginEditBlock()
        block = doc.begin()
        while block.isValid():
            old = block.blockFormat()
            if old.hasProperty(SPACER):
                block = block.next()
                continue
            cursor.setPosition(block.position())
            fmt = QTextBlockFormat()
            fmt.setLineHeight(135, QTextBlockFormat.ProportionalHeight)
            is_code = (old.hasProperty(QTextFormat.BlockCodeFence)
                       or old.hasProperty(QTextFormat.BlockCodeLanguage)
                       or block.charFormat().fontFamily() in (mono, "Monospace", "monospace"))
            if is_code and not block.textList() and not cursor.currentTable():
                fmt.setBackground(code_bg)
                fmt.setLeftMargin(10)
                prev_code = block.previous().isValid() and block.previous().blockFormat().background() == code_bg
                fmt.setTopMargin(0 if prev_code else 4)
                fmt.setBottomMargin(0)
                fmt.setLineHeight(100, QTextBlockFormat.ProportionalHeight)  # >100 leaves unshaded gaps
            elif old.headingLevel():
                fmt.setTopMargin(18 if old.headingLevel() <= 2 else 12)
                fmt.setBottomMargin(6)
            cursor.mergeBlockFormat(fmt)
            block = block.next()
        for frame in doc.rootFrame().childFrames():
            if isinstance(frame, QTextTable):
                tf = frame.format()
                tf.setCellPadding(6)
                tf.setCellSpacing(0)
                tf.setBorder(1)
                tf.setBorderBrush(pal.color(QPalette.Mid))
                tf.setBorderCollapse(True)
                tf.setTopMargin(8)
                tf.setBottomMargin(8)
                frame.setFormat(tf)
        cursor.endEditBlock()

    def on_link(self, url):
        if url.scheme() in ("", "file") and not url.path() and url.fragment():
            self.scroll_to_anchor(url.fragment())
            return
        if url.isRelative():
            url = QUrl.fromLocalFile(os.path.dirname(self.tab.path) + os.sep).resolved(url)
        if url.isLocalFile() and url.toLocalFile().lower().endswith(MD_EXTS):
            self.tab.window_.open_file(url.toLocalFile())
            if url.fragment():
                self.tab.window_.current().view.scroll_to_anchor(url.fragment())
        else:
            QDesktopServices.openUrl(url)

    def scroll_to_anchor(self, name):
        target = slugify(name)
        block = self.document().begin()
        while block.isValid():
            if block.blockFormat().headingLevel() and slugify(block.text()) == target:
                top = self.document().documentLayout().blockBoundingRect(block).top()
                self.verticalScrollBar().setValue(int(top))
                return
            block = block.next()


class FindBar(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window_ = window
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 2, 6, 2)
        lay.addWidget(QLabel("Find:"))
        self.edit = QLineEdit()
        self.edit.returnPressed.connect(lambda: self.find(
            QApplication.keyboardModifiers() & Qt.ShiftModifier))
        self.edit.textChanged.connect(lambda: self.find(False, restart=True))
        lay.addWidget(self.edit, 1)
        for label, back in (("Prev", True), ("Next", False)):
            b = QPushButton(label)
            b.clicked.connect(lambda _, back=back: self.find(back))
            lay.addWidget(b)
        self.status = QLabel()
        self.status.setMinimumWidth(80)
        lay.addWidget(self.status)
        close = QPushButton("✕")
        close.setFlat(True)
        close.clicked.connect(self.close_bar)
        lay.addWidget(close)
        self.hide()

    def open_bar(self):
        self.show()
        self.edit.setFocus()
        self.edit.selectAll()

    def close_bar(self):
        self.hide()
        v = self.window_.current()
        if v:
            v.currentWidget().setFocus()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.close_bar()
        else:
            super().keyPressEvent(e)

    def find(self, backward=False, restart=False):
        v = self.window_.current() and self.window_.current().currentWidget()
        text = self.edit.text()
        if not v or not text:
            self.status.setText("")
            return
        if restart:  # search-as-you-type: re-search from where the last match began
            c = v.textCursor()
            c.setPosition(c.selectionStart() if c.hasSelection() else 0)
            v.setTextCursor(c)
        flags = QTextDocument.FindBackward if backward else QTextDocument.FindFlags()
        if not v.find(text, flags):
            c = v.textCursor()
            c.movePosition(QTextCursor.End if backward else QTextCursor.Start)
            v.setTextCursor(c)
            found = v.find(text, flags)
            self.status.setText("wrapped" if found else "not found")
        else:
            self.status.setText("")


class MainWindow(QMainWindow):
    def __init__(self, offset=False):
        super().__init__()
        self.settings = QSettings(APP, APP)
        self.zoom_level = int(self.settings.value("zoom", 0))
        self.base_pt = QApplication.font().pointSizeF() + 1

        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.tabs.setDocumentMode(True)
        self.tabs.tabCloseRequested.connect(self.close_tab)
        self.tabs.currentChanged.connect(self.on_tab_changed)
        self.last_tab = None
        # Background tabs aren't prompted; check them for changes when they're shown.
        self.tabs.currentChanged.connect(lambda: QTimer.singleShot(0, self.check_current))
        self.asking = False
        QApplication.instance().applicationStateChanged.connect(self.on_app_state)

        self.findbar = FindBar(self)
        central = QWidget()
        lay = QVBoxLayout(central)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.tabs)
        lay.addWidget(self.findbar)
        self.setCentralWidget(central)

        self.watcher = QFileSystemWatcher()
        self.watcher.fileChanged.connect(self.on_file_changed)
        self.pending = set()
        self.reload_timer = QTimer(singleShot=True, interval=200)
        self.reload_timer.timeout.connect(self.reload_pending)

        self.build_menus()
        self.setAcceptDrops(True)
        self.resize(900, 1000)
        geom = self.settings.value("geometry")
        if geom:
            self.restoreGeometry(geom)
        if offset:  # a second window shouldn't sit exactly on top of the first
            self.move(self.pos() + QPoint(40, 40))
        self.update_title()

    # ---- menus -------------------------------------------------------------
    def build_menus(self):
        def act(menu, text, slot, shortcut=None):
            a = QAction(text, self)
            if shortcut:
                a.setShortcuts(shortcut if isinstance(shortcut, list) else [shortcut])
            a.triggered.connect(slot)
            menu.addAction(a)
            return a

        m = self.menuBar().addMenu("&File")
        act(m, "New &Window", new_window, QKeySequence("Ctrl+Shift+N"))
        act(m, "&Open…", self.open_dialog, QKeySequence.Open)
        self.recent_menu = m.addMenu("Open &Recent")
        self.recent_menu.aboutToShow.connect(self.fill_recent)
        act(m, "&Save", lambda: self.current() and self.current().save(), QKeySequence.Save)
        act(m, "&Reload", self.reload_current, QKeySequence("F5"))
        act(m, "&Close Tab", lambda: self.close_tab(self.tabs.currentIndex()), QKeySequence("Ctrl+W"))
        m.addSeparator()
        act(m, "&Quit", self.close, QKeySequence("Ctrl+Q"))

        m = self.menuBar().addMenu("&View")
        act(m, "&Find…", self.findbar.open_bar, QKeySequence.Find)
        act(m, "Find &Next", lambda: self.findbar.find(False), QKeySequence("F3"))
        act(m, "Find &Previous", lambda: self.findbar.find(True), QKeySequence("Shift+F3"))
        self.edit_action = act(m, "&Edit Source", self.toggle_editing,
                               [QKeySequence("Ctrl+M"), QKeySequence("Ctrl+U")])
        self.edit_action.setCheckable(True)
        m.addSeparator()
        act(m, "Zoom &In", lambda: self.zoom(1), [QKeySequence("Ctrl+="), QKeySequence("Ctrl++")])
        act(m, "Zoom &Out", lambda: self.zoom(-1), QKeySequence("Ctrl+-"))
        act(m, "&Reset Zoom", lambda: self.zoom(None), QKeySequence("Ctrl+0"))
        m.addSeparator()
        act(m, "Next Tab", lambda: self.cycle_tab(1), [QKeySequence("Ctrl+Tab"), QKeySequence("Ctrl+PgDown")])
        act(m, "Previous Tab", lambda: self.cycle_tab(-1),
            [QKeySequence("Ctrl+Shift+Tab"), QKeySequence("Ctrl+PgUp")])

    def fill_recent(self):
        self.recent_menu.clear()
        recent = self.recent()
        for p in recent:
            a = self.recent_menu.addAction(p)
            a.triggered.connect(lambda _, p=p: self.open_file(p))
        if recent:
            self.recent_menu.addSeparator()
            self.recent_menu.addAction("Clear List").triggered.connect(
                lambda: self.settings.setValue("recent", []))
        else:
            self.recent_menu.addAction("(empty)").setEnabled(False)

    def recent(self):
        r = self.settings.value("recent", []) or []
        return [r] if isinstance(r, str) else list(r)

    def add_recent(self, path):
        r = [p for p in self.recent() if p != path]
        self.settings.setValue("recent", ([path] + r)[:MAX_RECENT])

    # ---- tabs --------------------------------------------------------------
    def current(self):
        return self.tabs.currentWidget()

    def viewers(self):
        return [self.tabs.widget(i) for i in range(self.tabs.count())]

    def open_dialog(self):
        start = os.path.dirname(self.current().path) if self.current() else os.path.expanduser("~")
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Open", start, "Markdown (*.md *.markdown *.mdown *.mkd *.txt);;All files (*)")
        for p in paths:
            self.open_file(p)

    def open_file(self, path):
        path = os.path.abspath(path)
        for i, v in enumerate(self.viewers()):
            if v.path == path:
                self.tabs.setCurrentIndex(i)
                return
        if not os.path.isfile(path):
            QMessageBox.warning(self, APP, f"File not found:\n{path}")
            return
        v = Tab(path, self)
        self.apply_zoom(v)
        i = self.tabs.addTab(v, os.path.basename(path))
        self.tabs.setTabToolTip(i, path)
        self.tabs.setCurrentIndex(i)
        self.update_tab(v)
        self.watcher.addPath(path)
        self.add_recent(path)

    def close_tab(self, i):
        v = self.tabs.widget(i)
        if v is None or not (v.save() or self.confirm_discard(v, "Close it anyway")):
            return
        if v is self.last_tab:
            self.last_tab = None  # saved or discarded above; don't try again on the tab change
        self.tabs.removeTab(i)
        if v.path not in [w.path for w in self.viewers()]:
            self.watcher.removePath(v.path)
        v.deleteLater()

    def cycle_tab(self, step):
        n = self.tabs.count()
        if n:
            self.tabs.setCurrentIndex((self.tabs.currentIndex() + step) % n)

    def on_tab_changed(self):
        # Auto-save the tab being left.
        if self.last_tab is not None and self.last_tab is not self.current() and not self.asking:
            self.last_tab.save()
        self.last_tab = self.current()
        self.update_title()

    def toggle_editing(self, checked):
        v = self.current()
        if v:
            v.set_editing(checked)

    def update_tab(self, v):
        """Show v's mode and unsaved state in its tab label (and the title, if current)."""
        i = self.tabs.indexOf(v)
        if i < 0:
            return
        label = ("• " if v.modified() else "") + os.path.basename(v.path)
        if v.editing:
            label += " (read-only)" if v.editor.isReadOnly() else " (source)"
        self.tabs.setTabText(i, label)
        if v is self.current():
            self.update_title()

    def update_title(self):
        v = self.current()
        self.edit_action.setChecked(bool(v and v.editing))
        self.setWindowTitle(f"{'*' if v.modified() else ''}{os.path.basename(v.path)} — {APP}"
                            if v else APP)

    def confirm_discard(self, v, question):
        """Ask whether to throw away v's unsaved edits (after a failed save, say)."""
        return QMessageBox.warning(
            self, APP, f"{os.path.basename(v.path)} has unsaved edits.\n\n{question} and discard them?",
            QMessageBox.Discard | QMessageBox.Cancel, QMessageBox.Cancel) == QMessageBox.Discard

    def reload_current(self):
        v = self.current()
        if v and (not v.modified() or self.confirm_discard(v, "Reload it from disk")):
            v.load()

    def on_app_state(self, state):
        # Auto-save when mdv loses focus (unless that's to one of our own dialogs).
        if state != Qt.ApplicationActive and not self.asking:
            for v in self.viewers():
                v.save()

    # ---- auto-reload -------------------------------------------------------
    def on_file_changed(self, path):
        self.pending.add(path)
        self.reload_timer.start()

    def reload_pending(self):
        retry = False
        for path in list(self.pending):
            if not os.path.exists(path):  # editor may be mid delete-and-replace
                retry = True
                continue
            self.pending.discard(path)
            if path not in self.watcher.files():
                self.watcher.addPath(path)
            if self.current() and self.current().path == path:
                self.check_current()
        if retry:
            self.reload_timer.start(500)

    def check_current(self):
        """If the active tab's file differs from what's shown, offer to refresh."""
        v = self.current()
        if self.asking or not v:
            return
        disk = v.read_disk()
        if disk is None or disk in (v.text, v.declined):
            return
        self.ask_reload(v, disk)
        # The file may have changed again (or the tab switched) while the dialog was up.
        QTimer.singleShot(0, self.check_current)

    def ask_reload(self, v, disk):
        """v's file on disk (now `disk`) differs from what v has; offer to reload it."""
        self.asking = True
        try:
            name = os.path.basename(v.path)
            if v.modified():
                box = QMessageBox(QMessageBox.Warning, APP,
                                  f"{name} has changed on disk, and you have unsaved edits.\n\n"
                                  "Reload it and discard your edits, or keep your version? "
                                  "Keeping yours overwrites the file on disk when it's saved.",
                                  parent=self)
                refresh = box.addButton("Reload", QMessageBox.DestructiveRole)
                box.setDefaultButton(box.addButton("Keep Mine", QMessageBox.RejectRole))
            else:
                box = QMessageBox(QMessageBox.Question, APP,
                                  f"{name} has changed on disk.\n\n"
                                  "Refresh to show the new version?", parent=self)
                refresh = box.addButton("Refresh", QMessageBox.AcceptRole)
                box.addButton(QMessageBox.Cancel)
                box.setDefaultButton(refresh)
            box.setInformativeText(v.path)
            box.exec_()
            if box.clickedButton() is refresh:
                v.load()
            else:
                v.declined = disk
        finally:
            self.asking = False

    # ---- zoom --------------------------------------------------------------
    def zoom(self, step):
        self.zoom_level = 0 if step is None else max(-8, min(15, self.zoom_level + step))
        self.settings.setValue("zoom", self.zoom_level)
        for v in self.viewers():
            self.apply_zoom(v)

    def apply_zoom(self, v):
        size = self.base_pt * ZOOM_STEP ** self.zoom_level
        f = v.view.font()
        f.setPointSizeF(size)
        v.view.setFont(f)
        v.view.document().setDefaultFont(f)
        v.view.render(v.source())  # re-render so headings/code scale with the base font
        f = QFontDatabase.systemFont(QFontDatabase.FixedFont)
        f.setPointSizeF(size)
        v.editor.setFont(f)
        v.editor.setTabStopDistance(4 * v.editor.fontMetrics().horizontalAdvance(" "))

    # ---- drag & drop / lifecycle ------------------------------------------
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        for url in e.mimeData().urls():
            if url.isLocalFile():
                self.open_file(url.toLocalFile())

    def closeEvent(self, e):
        for v in self.viewers():
            if not (v.save() or self.confirm_discard(v, "Quit anyway")):
                self.tabs.setCurrentWidget(v)
                e.ignore()
                return
        self.settings.setValue("geometry", self.saveGeometry())
        super().closeEvent(e)

    def bring_to_front(self):
        self.setWindowState((self.windowState() & ~Qt.WindowMinimized) | Qt.WindowActive)
        self.show()
        self.raise_()
        self.activateWindow()
        if QApplication.platformName() == "xcb":
            x11_activate(int(self.winId()))


def x11_activate(wid):
    """Ask the window manager to activate a window the way a taskbar does.

    Cinnamon's "strict" focus mode ignores an app raising itself, but honours
    _NET_ACTIVE_WINDOW requests marked as coming from a pager (source = 2).
    """
    try:
        from Xlib import X, display, protocol
    except ImportError:
        return
    d = display.Display()
    try:
        root = d.screen().root
        ev = protocol.event.ClientMessage(
            window=d.create_resource_object("window", wid),
            client_type=d.intern_atom("_NET_ACTIVE_WINDOW"),
            data=(32, [2, X.CurrentTime, 0, 0, 0]))
        root.send_event(ev, event_mask=X.SubstructureRedirectMask | X.SubstructureNotifyMask)
        d.flush()
    finally:
        d.close()


# ---- instances -------------------------------------------------------------
# The first instance listens on a local socket; files opened later (e.g. by
# double-clicking) are handed to it so they become tabs.  Launching mdv with no
# files (e.g. Ctrl+click on its taskbar entry) or with --new-window starts a
# further, independent instance that leaves the socket alone.
def new_window():
    subprocess.Popen([sys.executable, os.path.realpath(__file__), "--new-window"],
                     close_fds=True, start_new_session=True)


def connect_running():
    """Return a socket connected to the running instance, or None."""
    sock = QLocalSocket()
    sock.connectToServer(SERVER_NAME)
    return sock if sock.waitForConnected(300) else None


def send_to_running(sock, paths):
    sock.write(("\n".join(paths) + "\n").encode())
    sock.waitForBytesWritten(1000)
    sock.disconnectFromServer()


def start_server(window):
    QLocalServer.removeServer(SERVER_NAME)  # clear a stale socket from a crash
    server = QLocalServer(window)
    server.listen(SERVER_NAME)

    def on_connection():
        conn = server.nextPendingConnection()
        buf = bytearray()

        def read():
            buf.extend(bytes(conn.readAll()))

        def done():
            read()
            for p in buf.decode(errors="replace").splitlines():
                if p:
                    window.open_file(p)
            window.bring_to_front()
            conn.deleteLater()

        conn.readyRead.connect(read)
        conn.disconnected.connect(done)

    server.newConnection.connect(on_connection)
    return server


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP)
    app.setDesktopFileName("mdv")
    app.setWindowIcon(QIcon(os.path.join(os.path.dirname(os.path.realpath(__file__)), "mdv.svg")))
    pal = app.palette()
    if pal.color(QPalette.Base).lightness() < 128 and pal.color(QPalette.Link).lightness() < 128:
        pal.setColor(QPalette.Link, QColor("#6cb6ff"))  # default blue is unreadable on dark themes
        app.setPalette(pal)
    args = sys.argv[1:]
    if "-h" in args or "--help" in args:
        print(USAGE)
        return 0
    paths = [os.path.abspath(a) for a in args if not a.startswith("-")]
    separate = "-n" in args or "--new-window" in args or not paths
    sock = connect_running()
    if sock and not separate:
        send_to_running(sock, paths)
        return 0
    win = MainWindow(offset=separate)
    if sock:
        sock.disconnectFromServer()  # the first instance keeps receiving opened files
    else:
        win._server = start_server(win)
    for p in paths:
        win.open_file(p)
    win.show()
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
