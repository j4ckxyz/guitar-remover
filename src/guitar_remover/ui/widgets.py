"""Custom widgets. Everything paints with the system palette so light/dark mode
and the user's accent colour are picked up automatically."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QFont, QIcon, QPainter, QPalette, QPen
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QProgressBar, QPushButton,
                               QSizePolicy, QSlider, QToolButton, QVBoxLayout, QWidget)

from ..audio_io import AUDIO_EXTENSIONS
from .icon import pick_path


def theme_icon(name: str) -> QIcon:
    """Native icons: SF Symbols on macOS, Segoe Fluent on Windows (Qt >= 6.7)."""
    enum = getattr(QIcon.ThemeIcon, name, None)
    icon = QIcon.fromTheme(enum) if enum is not None else QIcon()
    return icon


def reveal_in_file_manager(path: Path) -> None:
    path = Path(path)
    if sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(path)])
    elif sys.platform == "win32":
        subprocess.Popen(["explorer", "/select,", str(path)])
    else:
        # Most Linux file managers (Files, Dolphin, Nemo, Thunar…) implement this
        # freedesktop interface, which opens the folder with the file selected.
        try:
            from PySide6.QtDBus import QDBusConnection, QDBusMessage
            msg = QDBusMessage.createMethodCall(
                "org.freedesktop.FileManager1", "/org/freedesktop/FileManager1",
                "org.freedesktop.FileManager1", "ShowItems")
            msg.setArguments([[QUrl.fromLocalFile(str(path)).toString()], ""])
            reply = QDBusConnection.sessionBus().call(msg)
            if reply.type() != QDBusMessage.MessageType.ErrorMessage:
                return
        except Exception:
            pass
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))


def open_file(path: Path) -> None:
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))


FILE_MANAGER = {"darwin": "Finder", "win32": "Explorer"}.get(sys.platform, "Files")


def mix(a: QColor, b: QColor, t: float) -> QColor:
    return QColor.fromRgbF(a.redF() * (1 - t) + b.redF() * t,
                           a.greenF() * (1 - t) + b.greenF() * t,
                           a.blueF() * (1 - t) + b.blueF() * t)


def audio_files(urls) -> list[Path]:
    """Expand dropped files/folders to supported audio files."""
    out: list[Path] = []
    for u in urls:
        if not u.isLocalFile():
            continue
        p = Path(u.toLocalFile())
        if p.is_dir():
            out += sorted(f for f in p.rglob("*") if f.suffix.lower() in AUDIO_EXTENSIONS
                          and not f.name.startswith("."))
        elif p.suffix.lower() in AUDIO_EXTENSIONS:
            out.append(p)
    return out


class DropZone(QWidget):
    """Big friendly target: drop songs here or click to browse."""

    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(150)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self._kbd_focus = False
        self.setAccessibleName("Add songs")
        self.setToolTip("Drop audio files or folders here, or click to choose")
        self._hover = False
        self._active = False  # something is being dragged over the window
        self.compact = False

    def set_drag_active(self, on: bool):
        self._active = on
        self.update()

    def set_compact(self, on: bool):
        self.compact = on
        self.setMinimumHeight(88 if on else 150)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Fixed if on else QSizePolicy.Policy.Expanding)
        self.updateGeometry()
        self.update()

    def sizeHint(self):
        return QSize(460, 88 if self.compact else 190)

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def focusInEvent(self, e):
        self._kbd_focus = e.reason() in (Qt.FocusReason.TabFocusReason,
                                         Qt.FocusReason.BacktabFocusReason)
        self.update()
        super().focusInEvent(e)

    def focusOutEvent(self, e):
        self._kbd_focus = False
        self.update()
        super().focusOutEvent(e)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit()
        else:
            super().keyPressEvent(e)

    def paintEvent(self, _):
        pal = self.palette()
        accent = pal.color(QPalette.ColorRole.Accent)
        base = pal.color(QPalette.ColorRole.Window)
        text = pal.color(QPalette.ColorRole.WindowText)
        dark = base.lightness() < 128

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5)
        hot = self._active or self._hover or self._kbd_focus
        fill = mix(base, accent, 0.14 if self._active else (0.07 if hot else 0.0))
        fill = mix(fill, text, 0.035 if not self._active else 0.0)
        border = accent if hot else mix(base, text, 0.30 if dark else 0.25)
        pen = QPen(border, 2.0 if self._active else 1.5)
        pen.setStyle(Qt.PenStyle.DashLine)
        pen.setDashPattern([5, 4])
        p.setPen(pen)
        p.setBrush(fill)
        p.drawRoundedRect(r, 14, 14)

        # Pick glyph
        glyph = accent if hot else mix(base, text, 0.45)
        cy = r.center().y() - (0 if self.compact else 22)
        if self.compact:
            gx = r.left() + 44
        else:
            gx = r.center().x()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(glyph)
        p.drawPath(pick_path(gx, cy, 30 if self.compact else 40))
        cut = QPen(base, 2.4)
        cut.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(cut)
        for i, h in enumerate((5, 10, 7)):
            x = gx - 7 + i * 7
            p.drawLine(QPointF(x, cy - h / 2 - 2), QPointF(x, cy + h / 2 - 2))

        title = "Drop songs here" if not self._active else "Release to add"
        sub = "or click to choose files · MP3, WAV, FLAC, M4A and more"
        f = QFont(self.font())
        f.setPointSizeF(f.pointSizeF() * (1.25 if not self.compact else 1.1))
        f.setWeight(QFont.Weight.DemiBold)
        p.setFont(f)
        p.setPen(text)
        if self.compact:
            tr = QRectF(r.left() + 84, r.top(), r.width() - 100, r.height() / 2 + 2)
            p.drawText(tr, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom, title)
        else:
            tr = QRectF(r.left(), cy + 30, r.width(), 28)
            p.drawText(tr, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, title)
        f2 = QFont(self.font())
        f2.setPointSizeF(f2.pointSizeF() * 0.95)
        p.setFont(f2)
        p.setPen(mix(base, text, 0.62))
        if self.compact:
            sr = QRectF(r.left() + 84, r.center().y() + 4, r.width() - 100, 22)
            p.drawText(sr, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, sub)
        else:
            sr = QRectF(r.left() + 12, cy + 58, r.width() - 24, 22)
            p.drawText(sr, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, sub)


class ElidedLabel(QLabel):
    """Shows long paths as "/Users/me/…/Backing Tracks" instead of cutting them off."""

    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        self._full = text
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setText(text)

    def setText(self, text: str):
        self._full = text
        self._elide()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._elide()

    def _elide(self):
        fm = self.fontMetrics()
        super().setText(fm.elidedText(self._full, Qt.TextElideMode.ElideMiddle, self.width()))


class StepSlider(QWidget):
    """A slider with words at both ends and a live caption underneath, so
    people choose "Faster ↔ Better" instead of typing numbers."""

    valueChanged = Signal(int)

    def __init__(self, left: str, right: str, steps: int, parent=None):
        super().__init__(parent)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, steps - 1)
        self.slider.setPageStep(1)
        self.slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.slider.setTickInterval(1)
        self.slider.valueChanged.connect(self.valueChanged)
        lo, hi = QLabel(left), QLabel(right)
        for lab in (lo, hi):
            lab.setForegroundRole(QPalette.ColorRole.PlaceholderText)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(lo)
        row.addWidget(self.slider, 1)
        row.addWidget(hi)
        self.caption = QLabel()
        self.caption.setWordWrap(True)
        self.caption.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        # Room for two lines, so a busy queue can't squash the caption.
        self.caption.setMinimumHeight(self.caption.fontMetrics().lineSpacing() * 2 + 4)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        lay.addLayout(row)
        lay.addWidget(self.caption)

    def value(self) -> int:
        return self.slider.value()

    def setValue(self, v: int):
        self.slider.setValue(v)

    def setCaption(self, html: str):
        self.caption.setText(html)


def secondary(label: QLabel) -> QLabel:
    label.setForegroundRole(QPalette.ColorRole.PlaceholderText)
    return label


def small(label: QLabel, scale: float = 0.92) -> QLabel:
    f = label.font()
    f.setPointSizeF(f.pointSizeF() * scale)
    label.setFont(f)
    return label


def fmt_duration(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    if seconds < 60:
        return f"{seconds} s"
    m, s = divmod(seconds, 60)
    if m < 60:
        return f"{m} min {s:02d} s" if m < 10 and s else f"{m} min"
    h, m = divmod(m, 60)
    return f"{h} h {m} min"


class JobRow(QFrame):
    """One song in the queue: name, progress, and actions when done."""

    cancel_requested = Signal(int)
    open_player_requested = Signal(int)
    retry_requested = Signal(int)
    remove_requested = Signal(int)

    def __init__(self, job_id: int, src: Path, parent=None):
        super().__init__(parent)
        self.job_id = job_id
        self.src = src
        self.result = None
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setAutoFillBackground(False)

        self.title = QLabel(src.stem)
        f = self.title.font()
        f.setWeight(QFont.Weight.DemiBold)
        self.title.setFont(f)
        self.title.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self.title.setMinimumWidth(80)
        self.title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.title.setToolTip(str(src))
        self.status = small(secondary(QLabel("Waiting…")))
        self.status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setMaximumHeight(8)
        self.bar.setRange(0, 1000)
        self.bar.setValue(0)

        self.close_btn = QToolButton()
        self.close_btn.setAutoRaise(True)
        ic = theme_icon("WindowClose")
        if ic.isNull():
            ic = theme_icon("EditDelete")
        if ic.isNull():
            self.close_btn.setText("✕")
        else:
            self.close_btn.setIcon(ic)
        self.close_btn.setToolTip("Cancel")
        self.close_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.close_btn.clicked.connect(self._close_clicked)

        self.actions = QWidget()
        al = QHBoxLayout(self.actions)
        al.setContentsMargins(0, 2, 0, 0)
        al.setSpacing(6)
        self.open_player = QPushButton("Open Player")
        self.open_player.setToolTip("See the waveforms, play them together, and change "
                                    "the volume of each track")
        self.open_player.setDefault(True)
        self.open_player.clicked.connect(lambda: self.open_player_requested.emit(self.job_id))
        self.reveal = QPushButton(f"Show in {FILE_MANAGER}")
        self.retry = QPushButton("Try Again")
        play = theme_icon("MediaPlaybackStart")
        if not play.isNull():
            self.open_player.setIcon(play)
        folder = theme_icon("FolderOpen")
        if not folder.isNull():
            self.reveal.setIcon(folder)
        self.reveal.clicked.connect(lambda: self.result and reveal_in_file_manager(self.result.backing))
        self.retry.clicked.connect(lambda: self.retry_requested.emit(self.job_id))
        for b in (self.open_player, self.reveal, self.retry):
            al.addWidget(b)
        al.addStretch(1)
        self.actions.hide()
        self.retry.hide()

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.addWidget(self.title, 1)
        top.addWidget(self.close_btn)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 8, 6, 10)
        lay.setSpacing(4)
        lay.addLayout(top)
        lay.addWidget(self.bar)
        lay.addWidget(self.status)
        lay.addWidget(self.actions)
        self.state = "waiting"

    def paintEvent(self, e):
        pal = self.palette()
        base = pal.color(QPalette.ColorRole.Window)
        text = pal.color(QPalette.ColorRole.WindowText)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(mix(base, text, 0.12), 1))
        p.setBrush(mix(base, text, 0.04 if base.lightness() > 128 else 0.06))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 10, 10)

    def _close_clicked(self):
        if self.state in ("waiting", "running"):
            self.cancel_requested.emit(self.job_id)
        else:
            self.remove_requested.emit(self.job_id)

    def set_waiting(self, text="Waiting…"):
        self.state = "waiting"
        self.status.setText(text)
        self.bar.show()
        self.bar.setRange(0, 1000)
        self.bar.setValue(0)
        self.actions.hide()
        self.close_btn.setToolTip("Cancel")

    def set_running(self, stage: str, frac: float, eta: float | None = None):
        self.state = "running"
        if frac < 0:
            self.bar.setRange(0, 0)  # busy indicator
        else:
            self.bar.setRange(0, 1000)
            self.bar.setValue(int(frac * 1000))
        text = stage
        if stage == "Separating" and frac >= 0:
            text = f"Separating · {frac * 100:.0f}%"
            if eta is not None:
                text += f" · about {fmt_duration(eta)} left"
        self.status.setText(text)

    def set_done(self, result, notes: str = ""):
        self.state = "done"
        self.result = result
        self.bar.hide()
        speed = result.audio_seconds / max(result.seconds, 0.01)
        txt = f"Done in {fmt_duration(result.seconds)} · {speed:.1f}× faster than real time"
        if notes:
            txt += f"\n{notes}"
        self.status.setText(txt)
        self.status.setToolTip(str(result.folder))
        self.actions.show()
        self.open_player.show()
        self.reveal.show()
        self.retry.hide()
        self.close_btn.setToolTip("Remove from list")

    def set_failed(self, message: str, details: str = ""):
        self.state = "failed"
        self.bar.hide()
        self.status.setText(f"⚠︎ {message}")
        self.status.setToolTip(details[-2000:] if details else message)
        self.actions.show()
        self.open_player.hide()
        self.reveal.hide()
        self.retry.show()
        self.close_btn.setToolTip("Remove from list")

    def set_cancelled(self):
        self.state = "cancelled"
        self.bar.hide()
        self.status.setText("Cancelled")
        self.actions.show()
        self.open_player.hide()
        self.reveal.hide()
        self.retry.show()
        self.close_btn.setToolTip("Remove from list")
