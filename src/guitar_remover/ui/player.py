"""A small multitrack player: waveforms of the guitar and backing track, with
playback, seeking, loop selection, per-track volume/mute/solo and mix export."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PySide6.QtCore import QEvent, QLineF, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (QColor, QFont, QKeySequence, QPainter, QPalette, QPen, QPixmap,
                           QShortcut)
from PySide6.QtWidgets import (QFileDialog, QGridLayout, QHBoxLayout, QLabel, QMessageBox, QPushButton,
                               QScrollBar, QSizePolicy, QSlider, QToolButton, QVBoxLayout,
                               QWidget)

from .. import APP_NAME, audio_io
from ..playback import PEAK_BLOCK, Player, Track, load_track
from .widgets import mix as mix_color
from .widgets import secondary, small, theme_icon

LANE_H = 116  # preferred lane height; lanes stretch with the window
MIN_LANE_H = 80
RULER_H = 26
TRACK_COLOURS = ["#f5793a", "#3a8ef5", "#2bb673", "#b35cf0", "#e84a6f", "#d9a400"]


def fmt_time(seconds: float, precise: bool = True) -> str:
    seconds = max(0.0, seconds)
    m, s = divmod(seconds, 60)
    return f"{int(m)}:{s:04.1f}" if precise else f"{int(m)}:{int(s):02d}"


class Timeline(QWidget):
    """Ruler plus one waveform lane per track. Click to move the playhead,
    drag to select a region to loop, Ctrl/⌘ + scroll or pinch to zoom."""

    seek_requested = Signal(int)
    selection_changed = Signal(object)  # (start, end) frames or None
    view_changed = Signal()

    def __init__(self, player: Player, parent=None):
        super().__init__(parent)
        self.player = player
        self.tracks = player.tracks
        self.spp = 1.0  # samples per pixel, set by fit()
        self.offset = 0  # first visible sample
        self.selection: tuple[int, int] | None = None
        self._drag_from: int | None = None
        self._cache: QPixmap | None = None
        self.follow = True
        self.setMinimumHeight(RULER_H + MIN_LANE_H * len(self.tracks))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.IBeamCursor)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.grabGesture(Qt.GestureType.PinchGesture)

    def sizeHint(self):
        return QSize(800, RULER_H + LANE_H * len(self.tracks))

    # -- geometry -------------------------------------------------------------
    @property
    def lane_h(self) -> float:
        return (self.height() - RULER_H) / max(1, len(self.tracks))

    @property
    def length(self) -> int:
        return self.player.length

    def max_offset(self) -> int:
        return max(0, int(self.length - self.width() * self.spp))

    def fit(self):
        self.spp = max(1.0, self.length / max(1, self.width()))
        self.offset = 0
        self.invalidate()

    def zoom(self, factor: float, anchor_x: float | None = None):
        if anchor_x is None:
            anchor_x = self.width() / 2
        anchor = self.offset + anchor_x * self.spp
        fit_spp = max(1.0, self.length / max(1, self.width()))
        self.spp = float(np.clip(self.spp / factor, 4.0, fit_spp))
        self.offset = int(np.clip(anchor - anchor_x * self.spp, 0, self.max_offset()))
        self.invalidate()

    def x_to_frame(self, x: float) -> int:
        return int(np.clip(self.offset + x * self.spp, 0, self.length))

    def frame_to_x(self, f: int) -> float:
        return (f - self.offset) / self.spp

    def set_offset(self, off: int):
        off = int(np.clip(off, 0, self.max_offset()))
        if off != self.offset:
            self.offset = off
            self.invalidate()

    def invalidate(self):
        self._cache = None
        self.update()
        self.view_changed.emit()

    def resizeEvent(self, e):
        if e.oldSize().width() <= 0 or self.spp * e.oldSize().width() >= self.length * 0.999:
            self.fit()
        else:
            self.offset = min(self.offset, self.max_offset())
            self.invalidate()
        super().resizeEvent(e)

    def changeEvent(self, e):
        if e.type() in (QEvent.Type.PaletteChange, QEvent.Type.ApplicationPaletteChange,
                        QEvent.Type.StyleChange):
            self._cache = None
            self.update()
        super().changeEvent(e)

    # -- drawing ----------------------------------------------------------------
    def _columns(self, t: Track, width: int) -> tuple[np.ndarray, np.ndarray]:
        """Peak and RMS value for each pixel column in view."""
        start = self.offset
        edges = start + np.arange(width + 1) * self.spp
        if self.spp >= PEAK_BLOCK:
            n = len(t.peaks)
            idx = np.clip((edges / PEAK_BLOCK).astype(np.int64), 0, n)
            lo, hi = idx[:-1], np.maximum(idx[1:], idx[:-1] + 1)
            valid = lo < n
            lo, hi = np.minimum(lo, n - 1), np.minimum(hi, n)
            peak = _reduce_max(t.peaks, lo, hi)
            rms = _reduce_mean(t.rms, lo, hi)
            peak[~valid] = 0
            rms[~valid] = 0
            return peak, rms
        a = int(start)
        b = min(t.frames, int(edges[-1]) + 1)
        seg = np.abs(t.data[a:b]).max(axis=1) if b > a else np.zeros(0, np.float32)
        cols = np.clip(((np.arange(len(seg)) + a - start) / self.spp).astype(np.int64),
                       0, width - 1)
        peak = np.zeros(width, np.float32)
        np.maximum.at(peak, cols, seg)
        return peak, peak * 0.6

    def _render_cache(self) -> QPixmap:
        dpr = self.devicePixelRatioF()
        pm = QPixmap(QSize(int(self.width() * dpr), int(self.height() * dpr)))
        pm.setDevicePixelRatio(dpr)
        pal = self.palette()
        base = pal.color(QPalette.ColorRole.Base)
        window = pal.color(QPalette.ColorRole.Window)
        text = pal.color(QPalette.ColorRole.WindowText)
        dark = window.lightness() < 128
        pm.fill(window)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        w = self.width()

        # Ruler
        p.fillRect(QRectF(0, 0, w, RULER_H), mix_color(window, text, 0.04))
        p.setPen(mix_color(window, text, 0.5))
        f = QFont(self.font())
        f.setPointSizeF(f.pointSizeF() * 0.8)
        p.setFont(f)
        sr = self.player.samplerate
        secs_per_px = self.spp / sr
        step = next((s for s in (0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300)
                     if s / secs_per_px >= 70), 600)
        t0 = self.offset / sr
        first = np.ceil(t0 / step) * step
        tick = first
        while True:
            x = (tick - t0) / secs_per_px
            if x > w:
                break
            p.drawLine(QLineF(x, RULER_H - 7, x, RULER_H))
            p.drawText(QPointF(x + 3, RULER_H - 9), fmt_time(tick, step < 1))
            tick += step
        p.setPen(mix_color(window, text, 0.15))
        p.drawLine(QLineF(0, RULER_H - 0.5, w, RULER_H - 0.5))

        any_solo = any(t.solo for t in self.tracks)
        for i, t in enumerate(self.tracks):
            lane_h = self.lane_h
            top = RULER_H + i * lane_h
            lane = QRectF(0, top + 1, w, lane_h - 2)
            p.fillRect(lane, mix_color(base, text, 0.03 if not dark else 0.05))
            colour = QColor(TRACK_COLOURS[i % len(TRACK_COLOURS)])
            silent = t.muted or (any_solo and not t.solo)
            if silent:
                colour = mix_color(colour, window, 0.72)
            light = mix_color(colour, base, 0.45)
            mid = top + lane_h / 2
            half = lane_h / 2 - 8
            peak, rms = self._columns(t, w)
            scale = half * min(t.gain, 2.0) / 1.0
            pk = np.minimum(peak * scale, half)
            rm = np.minimum(rms * scale, half)
            p.setPen(QPen(light, 1))
            p.drawLines([QLineF(x + 0.5, mid - h, x + 0.5, mid + h)
                         for x, h in enumerate(pk) if h > 0.3])
            p.setPen(QPen(colour, 1))
            p.drawLines([QLineF(x + 0.5, mid - h, x + 0.5, mid + h)
                         for x, h in enumerate(rm) if h > 0.3])
            p.setPen(mix_color(window, text, 0.12))
            p.drawLine(QLineF(0, mid, w, mid))
            p.drawLine(QLineF(0, top + lane_h - 0.5, w, top + lane_h - 0.5))
        p.end()
        return pm

    def paintEvent(self, _):
        if self._cache is None or self._cache.size() != self.size() * self.devicePixelRatioF():
            self._cache = self._render_cache()
        p = QPainter(self)
        p.drawPixmap(0, 0, self._cache)
        pal = self.palette()
        accent = pal.color(QPalette.ColorRole.Accent)
        text = pal.color(QPalette.ColorRole.WindowText)
        h = self.height()
        sel = self.selection
        if sel:
            x1, x2 = self.frame_to_x(sel[0]), self.frame_to_x(sel[1])
            c = QColor(accent)
            c.setAlphaF(0.22)
            p.fillRect(QRectF(x1, 0, x2 - x1, h), c)
            p.setPen(QPen(accent, 1))
            p.drawLine(QLineF(x1, 0, x1, h))
            p.drawLine(QLineF(x2, 0, x2, h))
        x = self.frame_to_x(self.player.position)
        if -2 <= x <= self.width() + 2:
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setPen(QPen(text, 1.5))
            p.drawLine(QLineF(x, 0, x, h))
            p.setBrush(text)
            p.setPen(Qt.PenStyle.NoPen)
            p.drawPolygon([QPointF(x - 5, 0), QPointF(x + 5, 0), QPointF(x, 7)])

    def tick(self):
        """Called by the window's timer while playing."""
        if self.follow and self.player.playing:
            x = self.frame_to_x(self.player.position)
            if x > self.width() * 0.92 or x < 0:
                self.set_offset(int(self.player.position - self.width() * self.spp * 0.08))
        self.update()

    # -- mouse / gestures -------------------------------------------------------
    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_from = self.x_to_frame(e.position().x())
            self._pressed_x = e.position().x()

    def mouseMoveEvent(self, e):
        if self._drag_from is not None and abs(e.position().x() - self._pressed_x) > 3:
            f = self.x_to_frame(e.position().x())
            a, b = sorted((self._drag_from, f))
            self.selection = (a, b)
            self.update()

    def mouseReleaseEvent(self, e):
        if self._drag_from is None:
            return
        if abs(e.position().x() - self._pressed_x) <= 3:
            self.selection = None
            self.selection_changed.emit(None)
            self.seek_requested.emit(self._drag_from)
        else:
            self.selection_changed.emit(self.selection)
            if self.selection:
                self.seek_requested.emit(self.selection[0])
        self._drag_from = None
        self.update()

    def wheelEvent(self, e):
        mods = e.modifiers()
        dy = e.angleDelta().y()
        dx = e.angleDelta().x()
        if mods & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier):
            self.zoom(1.0015 ** dy, e.position().x())
        else:
            pixels = e.pixelDelta()
            delta = (pixels.x() or pixels.y()) if not pixels.isNull() else (dx or dy) / 4
            if mods & Qt.KeyboardModifier.ShiftModifier and pixels.isNull():
                delta = dy / 4
            self.follow = False
            self.set_offset(int(self.offset - delta * self.spp))
        e.accept()

    def event(self, e):
        if e.type() == QEvent.Type.NativeGesture:
            if e.gestureType() == Qt.NativeGestureType.ZoomNativeGesture:
                self.zoom(1 + e.value(), e.position().x())
                return True
        if e.type() == QEvent.Type.Gesture:
            g = e.gesture(Qt.GestureType.PinchGesture)
            if g is not None:
                self.zoom(g.scaleFactor(), self.mapFromGlobal(g.centerPoint().toPoint()).x())
                return True
        return super().event(e)


def _reduce_max(values: np.ndarray, lo: np.ndarray, hi: np.ndarray) -> np.ndarray:
    # Columns span >= 1 block and are contiguous, so reduceat on lo is exact
    # except the final column, which we bound by hi[-1].
    out = np.maximum.reduceat(values, lo).astype(np.float32)
    out[-1] = values[lo[-1]:hi[-1]].max()
    same = np.r_[lo[1:] == lo[:-1], False]  # reduceat quirk when lo[i] == lo[i+1]
    out[same] = values[lo[same]]
    return out


def _reduce_mean(values: np.ndarray, lo: np.ndarray, hi: np.ndarray) -> np.ndarray:
    csum = np.r_[0.0, np.cumsum(values, dtype=np.float64)]
    return ((csum[hi] - csum[lo]) / np.maximum(1, hi - lo)).astype(np.float32)


class TrackHeader(QWidget):
    """Name, mute/solo and volume for one lane."""

    changed = Signal()

    def __init__(self, track: Track, colour: str, parent=None):
        super().__init__(parent)
        self.track = track
        self.colour = QColor(colour)
        self.setMinimumHeight(MIN_LANE_H)
        self.setFixedWidth(210)
        name = QLabel(track.name)
        f = name.font()
        f.setWeight(QFont.Weight.DemiBold)
        name.setFont(f)
        name.setToolTip(str(track.path or track.name))
        self.mute = QPushButton("Mute")
        self.mute.setCheckable(True)
        self.mute.setToolTip("Silence this track")
        self.solo = QPushButton("Solo")
        self.solo.setCheckable(True)
        self.solo.setToolTip("Hear only this track")
        for b in (self.mute, self.solo):
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.toggled.connect(self._apply)
        self.vol = QSlider(Qt.Orientation.Horizontal)
        self.vol.setRange(0, 200)
        self.vol.setValue(int(track.gain * 100))
        self.vol.setToolTip("Volume (double-click to reset)")
        self.vol.setAccessibleName(f"{track.name} volume")
        self.vol.valueChanged.connect(self._apply)
        self.vol.installEventFilter(self)
        self.vol_label = small(secondary(QLabel()), 0.85)
        self.vol_label.setMinimumWidth(38)
        self.vol_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        buttons = QHBoxLayout()
        buttons.setSpacing(6)
        buttons.addWidget(self.mute)
        buttons.addWidget(self.solo)
        buttons.addStretch(1)
        vol_row = QHBoxLayout()
        vol_row.addWidget(self.vol, 1)
        vol_row.addWidget(self.vol_label)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 8, 10, 8)
        lay.setSpacing(4)
        lay.addWidget(name)
        lay.addLayout(buttons)
        lay.addLayout(vol_row)
        lay.addStretch(1)
        self._apply()

    def eventFilter(self, obj, e):
        if obj is self.vol and e.type() == QEvent.Type.MouseButtonDblClick:
            self.vol.setValue(100)
            return True
        return super().eventFilter(obj, e)

    def _apply(self, *_):
        self.track.gain = self.vol.value() / 100
        self.track.muted = self.mute.isChecked()
        self.track.solo = self.solo.isChecked()
        v = self.vol.value()
        self.vol_label.setText("Off" if v == 0 else f"{v}%")
        self.changed.emit()

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(QRectF(0, 6, 5, self.height() - 12), self.colour)


class PlayerWindow(QWidget):
    def __init__(self, paths: list[tuple[str, Path]], title: str, parent=None):
        super().__init__(parent, Qt.WindowType.Window)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle(f"{title} · {APP_NAME}")
        self.title = title
        self.tracks = [load_track(p, name) for name, p in paths]
        self.player = Player(self.tracks)
        self.resize(980, RULER_H + LANE_H * len(self.tracks) + 110)
        self.setMinimumWidth(640)

        self.timeline = Timeline(self.player)
        self.timeline.seek_requested.connect(self._seek)
        self.timeline.selection_changed.connect(self._selection)
        self.timeline.view_changed.connect(self._sync_scrollbar)
        self.scroll = QScrollBar(Qt.Orientation.Horizontal)
        self.scroll.valueChanged.connect(self._scrolled)

        headers = QVBoxLayout()
        headers.setContentsMargins(0, RULER_H, 0, 0)
        headers.setSpacing(0)
        self.headers = []
        for i, t in enumerate(self.tracks):
            h = TrackHeader(t, TRACK_COLOURS[i % len(TRACK_COLOURS)])
            h.changed.connect(self.timeline.invalidate)
            headers.addWidget(h, 1)
            self.headers.append(h)

        body = QGridLayout()
        body.setSpacing(0)
        body.setContentsMargins(0, 0, 0, 0)
        body.addLayout(headers, 0, 0)
        body.addWidget(self.timeline, 0, 1)
        body.addWidget(self.scroll, 1, 1)
        body.setColumnStretch(1, 1)
        body.setRowStretch(0, 1)

        # Transport
        def tool(icon: str, fallback: str, tip: str, slot, shortcut: str | None = None):
            b = QToolButton()
            ic = theme_icon(icon)
            if ic.isNull():
                b.setText(fallback)
            else:
                b.setIcon(ic)
                b.setIconSize(QSize(20, 20))
            b.setToolTip(tip + (f" ({shortcut})" if shortcut else ""))
            b.setAutoRaise(True)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.clicked.connect(slot)
            return b

        self.to_start = tool("MediaSkipBackward", "⏮", "Go to start", self._home, "Home")
        self.back = tool("MediaSeekBackward", "⏪", "Back 5 seconds", lambda: self._skip(-5), "←")
        self.play_btn = tool("MediaPlaybackStart", "▶", "Play or pause", self.toggle, "Space")
        self.play_btn.setIconSize(QSize(28, 28))
        self.fwd = tool("MediaSeekForward", "⏩", "Forward 5 seconds", lambda: self._skip(5), "→")
        self.loop_btn = QPushButton("Loop Selection")
        self.loop_btn.setCheckable(True)
        self.loop_btn.setToolTip("Repeat the selected part (L). Drag across the waveform to select.")
        self.loop_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.loop_btn.toggled.connect(self._apply_loop)
        self.loop_btn.setEnabled(False)
        self.time = QLabel()
        tf = QFont(self.time.font())
        tf.setStyleHint(QFont.StyleHint.Monospace)
        tf.setFeature(QFont.Tag("tnum"), 1)
        tf.setPointSizeF(tf.pointSizeF() * 1.15)
        self.time.setFont(tf)
        self.sel_label = small(secondary(QLabel("Drag across the waveforms to select a part")),
                               0.88)

        zoom_out = tool("ZoomOut", "−", "Zoom out", lambda: self.timeline.zoom(1 / 1.6), "−")
        zoom_in = tool("ZoomIn", "+", "Zoom in", lambda: self.timeline.zoom(1.6), "+")
        zoom_fit = tool("ZoomFitBest", "Fit", "Show the whole song", self.timeline.fit, "0")
        self.master = QSlider(Qt.Orientation.Horizontal)
        self.master.setRange(0, 100)
        self.master.setValue(100)
        self.master.setFixedWidth(110)
        self.master.setToolTip("Overall volume")
        self.master.valueChanged.connect(lambda v: setattr(self.player, "master", v / 100))
        vol_icon = QLabel()
        ic = theme_icon("AudioVolumeHigh")
        if not ic.isNull():
            vol_icon.setPixmap(ic.pixmap(18, 18))
        export = QPushButton("Export Mix…")
        export.setToolTip("Save what you hear (with your volume, mute and solo settings) "
                          "as a new file")
        export.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        export.clicked.connect(self.export_mix)

        transport = QHBoxLayout()
        transport.setContentsMargins(14, 8, 14, 8)
        transport.setSpacing(4)
        for w in (self.to_start, self.back, self.play_btn, self.fwd):
            transport.addWidget(w)
        transport.addSpacing(10)
        transport.addWidget(self.time)
        transport.addSpacing(14)
        transport.addWidget(self.loop_btn)
        transport.addStretch(1)
        for w in (zoom_out, zoom_fit, zoom_in):
            transport.addWidget(w)
        transport.addSpacing(10)
        transport.addWidget(vol_icon)
        transport.addWidget(self.master)
        transport.addSpacing(10)
        transport.addWidget(export)

        hint = QHBoxLayout()
        hint.setContentsMargins(16, 8, 16, 10)
        hint.addWidget(self.sel_label, 1)
        hint.addWidget(small(secondary(QLabel(
            "Space play/pause · ← → skip · click to jump · "
            + ("⌘" if sys.platform == "darwin" else "Ctrl") + " + scroll to zoom")), 0.85))

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addLayout(transport)
        root.addLayout(body, 1)
        root.addLayout(hint)

        for key, slot in [("Space", self.toggle), ("Left", lambda: self._skip(-5)),
                          ("Right", lambda: self._skip(5)), ("Home", self._home),
                          ("End", lambda: self._seek(self.player.length)),
                          ("L", self.loop_btn.toggle), ("+", lambda: self.timeline.zoom(1.6)),
                          ("=", lambda: self.timeline.zoom(1.6)),
                          ("-", lambda: self.timeline.zoom(1 / 1.6)), ("0", self.timeline.fit),
                          (QKeySequence.StandardKey.Close, self.close)]:
            sc = QShortcut(QKeySequence(key), self)
            sc.activated.connect(slot)

        self.timer = QTimer(self)
        self.timer.setInterval(33)
        self.timer.timeout.connect(self._tick)
        self.timer.start()
        self._was_playing = False
        self._update_time()

    # -- transport ------------------------------------------------------------
    def toggle(self):
        if self.player.playing:
            self.player.pause()
        else:
            self.timeline.follow = True
            if not self.player.play():
                QMessageBox.warning(self, "Can't play audio", self.player.error or "")
        self._update_play_icon()

    def _update_play_icon(self):
        ic = theme_icon("MediaPlaybackPause" if self.player.playing else "MediaPlaybackStart")
        if ic.isNull():
            self.play_btn.setText("⏸" if self.player.playing else "▶")
        else:
            self.play_btn.setIcon(ic)

    def _seek(self, frame: int):
        self.player.seek(frame)
        self.timeline.follow = True
        self.timeline.update()
        self._update_time()

    def _skip(self, s: float):
        self.player.skip(s)
        self.timeline.follow = True
        x = self.timeline.frame_to_x(self.player.position)
        if not 0 <= x <= self.timeline.width():
            self.timeline.set_offset(int(self.player.position - self.timeline.width()
                                         * self.timeline.spp * 0.3))
        self.timeline.update()
        self._update_time()

    def _home(self):
        self._seek(self.player.loop[0] if self.player.loop else 0)
        self.timeline.set_offset(0 if not self.player.loop else self.timeline.offset)

    def _selection(self, sel):
        self.loop_btn.setEnabled(sel is not None)
        sr = self.player.samplerate
        if sel:
            a, b = sel
            self.sel_label.setText(f"Selected {fmt_time(a / sr)} to {fmt_time(b / sr)} "
                                   f"({fmt_time((b - a) / sr)})")
        else:
            self.sel_label.setText("Drag across the waveforms to select a part")
            self.loop_btn.setChecked(False)
        self._apply_loop()

    def _apply_loop(self, *_):
        on = self.loop_btn.isChecked() and self.timeline.selection is not None
        self.player.set_loop(self.timeline.selection if on else None)

    def _tick(self):
        if self.player.playing or self._was_playing:
            self.timeline.tick()
            self._update_time()
        if self._was_playing != self.player.playing:
            self._was_playing = self.player.playing
            self._update_play_icon()

    def _update_time(self):
        sr = self.player.samplerate
        self.time.setText(f"{fmt_time(self.player.position / sr)} / "
                          f"{fmt_time(self.player.length / sr)}")

    def _scrolled(self, v: int):
        self.timeline.follow = False
        self.timeline.set_offset(v)

    def _sync_scrollbar(self):
        tl = self.timeline
        self.scroll.blockSignals(True)
        self.scroll.setRange(0, tl.max_offset())
        self.scroll.setPageStep(int(tl.width() * tl.spp))
        self.scroll.setSingleStep(max(1, int(tl.spp * 20)))
        self.scroll.setValue(tl.offset)
        self.scroll.setVisible(tl.max_offset() > 0)
        self.scroll.blockSignals(False)

    # -- export ---------------------------------------------------------------
    def export_mix(self):
        first = self.tracks[0].path
        folder = first.parent if first else Path.home()
        sel = self.timeline.selection
        suffix = " (selection)" if sel else ""
        name, _ = QFileDialog.getSaveFileName(
            self, "Export Mix", str(folder / f"{self.title} - My Mix{suffix}.wav"),
            "WAV (*.wav);;FLAC (*.flac);;MP3 (*.mp3)")
        if not name:
            return
        path = Path(name)
        fmt = {".flac": "flac", ".mp3": "mp3"}.get(path.suffix.lower(), "wav24")
        a, b = sel if sel else (0, self.player.length)
        audio = self.player.render(a, b)
        self.setCursor(Qt.CursorShape.WaitCursor)
        try:
            audio_io.write(path, audio, self.player.samplerate, fmt)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Export failed", str(exc))
        finally:
            self.unsetCursor()

    def closeEvent(self, e):
        self.timer.stop()
        self.player.close()
        super().closeEvent(e)


def open_player(guitar: Path, backing: Path, title: str, parent=None) -> PlayerWindow:
    label_g = guitar.stem.rsplit(" - ", 1)[-1]
    label_b = backing.stem.rsplit(" - ", 1)[-1]
    win = PlayerWindow([(label_g, guitar), (label_b, backing)], title, parent)
    win.show()
    return win
