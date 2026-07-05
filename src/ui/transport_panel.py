from PySide6.QtWidgets import (
    QWidget,
    QLabel,
    QHBoxLayout,
    QFrame,
    QLineEdit,
    QPushButton,
    QSlider,
    QStyle,
)
from utils.timecode import (
    frame_to_timecode,
    parse_timecode,
)
from PySide6.QtCore import Qt, QTimer, QSize, QEvent
from PySide6.QtGui import (
    QShortcut,
    QKeySequence,
    QFont,
    QFontMetrics,
    QPalette,
)
from utils.icons import load_icon


class TimeBox(QFrame):

    def __init__(
            self,
            title,
            editable=False,
    ):

        super().__init__()

        self.setFrameShape(
            QFrame.Box
        )

        layout = QHBoxLayout(
            self
        )

        layout.setContentsMargins(
            6,
            2,
            6,
            2,
        )

        # A blank title means no label - just the timecode (VRD-style IN/OUT).
        self._label = None
        if title:
            self._label = QLabel(
                title
            )

        if editable:

            self.value = QLineEdit(
                "--:--:--.--"
            )

            self.value.setAlignment(
                Qt.AlignCenter
            )

        else:

            self.value = QLabel(
                "--:--:--.--"
            )

            self.value.setAlignment(
                Qt.AlignCenter
            )

        if self._label is not None:
            layout.addWidget(
                self._label
            )

        layout.addWidget(
            self.value
        )

        # Colour the box, text and label from the active palette, so the IN/OUT
        # readouts follow the Light/Dark theme instead of staying black.
        self._applying_palette = False
        self._apply_palette()

    def _apply_palette(self):
        # setStyleSheet below re-polishes the widget, which re-posts a
        # PaletteChange event; without this guard changeEvent would call back in
        # and recurse until the stack overflows.  Re-entrant calls are ignored.
        if self._applying_palette:
            return
        self._applying_palette = True
        try:
            # Read the *application* palette, not self.palette(): once this
            # widget has a stylesheet, its own palette becomes the stylesheet's
            # resolved colours and stops tracking app theme changes, so on a
            # live switch self.palette() would hand back the previous theme's
            # colours.  QApplication.palette() is always the current theme.
            from PySide6.QtWidgets import QApplication
            pal = QApplication.palette()
            base = pal.color(QPalette.Base).name()
            text = pal.color(QPalette.Text).name()
            border = pal.color(QPalette.Mid).name()
            label = pal.color(QPalette.Disabled, QPalette.Text).name()
            self.setStyleSheet(
                "QFrame { background:%s; border:1px solid %s; }" % (base, border)
            )
            self.value.setStyleSheet(
                "color:%s; font-weight:bold; background:%s; border:none;"
                % (text, base)
            )
            if self._label is not None:
                self._label.setStyleSheet(
                    "color:%s; background:%s;" % (label, base)
                )
        finally:
            self._applying_palette = False

    def changeEvent(self, event):
        # Re-colour when the application palette changes (a theme switch).
        if event.type() == QEvent.PaletteChange:
            self._apply_palette()
        super().changeEvent(event)


class TransportPanel(QWidget):

    def __init__(
            self,
            window,
    ):

        super().__init__()

        self.window = window

        layout = QHBoxLayout(
            self
        )

        layout.setContentsMargins(
            0,
            8,
            0,
            8,
        )

        layout.setSpacing(
            8
        )

        # No "Cursor" label either - just the timecode, with a tooltip.  Width
        # is set below alongside IN/OUT so all three match.
        self.cursor_box = TimeBox(
            "",
            editable=True,
        )

        self.cursor_box.setToolTip(
            "Cursor position (double-click to type a time)"
        )

        self.cursor_box.value.setReadOnly(
            True
        )

        self.cursor_box.value.mouseDoubleClickEvent = (
            self.cursor_double_click
        )

        self.cursor_box.value.returnPressed.connect(
            self.cursor_enter
        )

        # The cursor box must never hold keyboard focus, or the arrow keys go
        # into it (it's read-only, so they'd do nothing and stop reaching the
        # main window's navigation) - which is what locked the keyboard after
        # the go-to dialog.  Double-click still fires regardless of focus.
        self.cursor_box.value.setFocusPolicy(
            Qt.NoFocus
        )

        self.escape_shortcut = QShortcut(
            QKeySequence("Escape"),
            self.cursor_box.value,
        )

        self.escape_shortcut.activated.connect(
            self.cursor_escape
        )

        # No "IN"/"OUT" labels (VRD-style): the position - left of the cursor
        # for IN, right for OUT - and the marker arrows make it clear.  Bare
        # timecodes also mean one predictable width, with nothing to clip.
        self.in_box = TimeBox(
            ""
        )
        self.in_box.setToolTip("In point")

        self.out_box = TimeBox(
            ""
        )
        self.out_box.setToolTip("Out point")

        self.mark_in_btn = QPushButton()
        self.mark_in_btn.setIcon(load_icon("mark_in"))

        self.mark_out_btn = QPushButton()
        self.mark_out_btn.setIcon(load_icon("mark_out"))

        for button in [

            self.mark_in_btn,
            self.mark_out_btn,

        ]:
            button.setFixedWidth(
                44
            )

            button.setIconSize(
                QSize(22, 18)
            )

            button.setFocusPolicy(
                Qt.NoFocus
            )

            # No custom stylesheet: the mark buttons use the same native theme
            # style as the playback buttons, so their normal / hover / pressed
            # look matches the rest of the transport row in every theme.

        self.mark_in_btn.clicked.connect(
            self.window.mark_in
        )

        self.mark_out_btn.clicked.connect(
            self.window.mark_out
        )

        self.mark_in_btn.clicked.connect(
            lambda: self._flash_button(self.mark_in_btn)
        )

        self.mark_out_btn.clicked.connect(
            lambda: self._flash_button(self.mark_out_btn)
        )

        self.mark_in_btn.setToolTip("Set in point at cursor")
        self.mark_out_btn.setToolTip("Set out point at cursor")

        #
        # Fixed widths so the boxes never resize when their placeholder
        # ("--:--:--.--") is replaced by a real timecode - otherwise setting
        # an IN/OUT point would change a box's width and shove the whole
        # centred row sideways.  With no label, the box only has to fit a full
        # timecode; size it from the actual bold font (so it's right on any
        # font/DPI) with comfortable padding so the text never clips, and use
        # the same width for both IN and OUT.
        #
        _value_font = QFont(self.out_box.value.font())
        _value_font.setBold(True)
        _value_fm = QFontMetrics(_value_font)

        _box_w = (
            _value_fm.horizontalAdvance("00:00:00.00")
            + 6 + 6        # left/right contents margins
            + 2            # frame border
            + 20           # comfortable padding so nothing clips
        )

        self.in_box.setFixedWidth(
            _box_w
        )

        self.out_box.setFixedWidth(
            _box_w
        )

        # The cursor box now has no label either, so it gets the same width as
        # IN/OUT - keeping the comfortable padding around the time.
        self.cursor_box.setFixedWidth(
            _box_w
        )

        layout.addStretch()

        layout.addWidget(
            self.in_box
        )

        #
        # Place each marker arrow CENTRALLY between its timecode and the cursor
        # (VRD-style): equal gaps on both sides of each arrow, so the row reads
        #   IN  ◀[  Cursor  ]▶  OUT
        # with the arrows floating midway rather than hugging the cursor.
        #
        _gap = 30

        layout.addSpacing(
            _gap
        )

        layout.addWidget(
            self.mark_in_btn
        )

        layout.addSpacing(
            _gap
        )

        layout.addWidget(
            self.cursor_box
        )

        layout.addSpacing(
            _gap
        )

        layout.addWidget(
            self.mark_out_btn
        )

        layout.addSpacing(
            _gap
        )

        layout.addWidget(
            self.out_box
        )

        layout.addStretch()

    def refresh_theme(self):
        """Re-colour the readouts and reload the mark-button icons after a live
        Light/Dark theme switch, so the panel updates without a restart."""
        self.mark_in_btn.setIcon(load_icon("mark_in"))
        self.mark_out_btn.setIcon(load_icon("mark_out"))
        for box in self.findChildren(TimeBox):
            box._apply_palette()

    def _flash_button(self, button):
        """Briefly highlight a button just after it's clicked, so it's obvious
        the press registered even for a very quick click.  Sets a `flash`
        property the stylesheet keys off, then clears it shortly after."""
        button.setProperty("flash", True)
        button.style().unpolish(button)
        button.style().polish(button)
        QTimer.singleShot(170, lambda: self._unflash_button(button))

    def _unflash_button(self, button):
        button.setProperty("flash", False)
        button.style().unpolish(button)
        button.style().polish(button)

    def set_enabled(self, on):
        """Enable/disable the interactive controls (used when no video is loaded)."""
        self.mark_in_btn.setEnabled(on)
        self.mark_out_btn.setEnabled(on)
        self.cursor_box.value.setEnabled(on)

    def update_transport(self):
        # With no video loaded, the readouts revert to the empty placeholder.
        has_video = bool(self.window.frames)
        cursor = self.window.current_frame if has_video else None
        pending_in = self.window.selection.pending_in if has_video else None
        pending_out = self.window.selection.pending_out if has_video else None

        if (
                not
                self.cursor_box.value.hasFocus()
        ):
            self.cursor_box.value.setText(
                frame_to_timecode(cursor)
            )

        self.in_box.value.setText(
            frame_to_timecode(pending_in)
        )

        self.out_box.value.setText(
            frame_to_timecode(pending_out)
        )


    def cursor_double_click(
            self,
            event,
    ):

        # Double-clicking the cursor opens the go-to dialog (VRD-style) rather
        # than editing the box in place - the dialog does the same job and also
        # accepts frame numbers and relative +/- jumps.
        self.window.goto_timecode()

    def cursor_enter(
            self,
    ):

        text = (
            self.cursor_box.value.text()
        )

        frame = parse_timecode(
            text
        )

        if frame is None:
            return

        frame = max(
            0,
            min(
                len(
                    self.window.frames
                ) - 1,
                frame,
            )
        )

        self.window.current_frame = frame

        self.window.update_display()

        self.cursor_box.value.setReadOnly(
            True
        )

        self.window.setFocus()

    def cursor_escape(
            self,
    ):

        self.cursor_box.value.setText(

            frame_to_timecode(
                self.window.current_frame
            )

        )

        self.cursor_box.value.setReadOnly(
            True
        )

        self.window.setFocus()

    def toggle_play(self):

        self.window.playing = (
            not
            self.window.playing
        )

class VolumeControl(QWidget):
    """Speaker icon + volume slider, mirroring VRD's bottom-left control.

    The slider is 0-100 and drives window.audio.set_volume (0.0-1.0) live;
    the chosen level is persisted (via window._save_volume) when the drag ends
    or mute is toggled.  Clicking the icon toggles mute.
    """

    def __init__(
            self,
            window,
            initial=80,
    ):

        super().__init__()

        self.window = window

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.icon = QPushButton()
        self.icon.setFlat(True)
        self.icon.setFixedWidth(26)
        self.icon.setFocusPolicy(Qt.NoFocus)

        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 100)
        self.slider.setFixedWidth(100)
        self.slider.setFocusPolicy(Qt.NoFocus)

        layout.addWidget(self.icon)
        layout.addWidget(self.slider)

        self._restore = int(initial) or 80

        self.icon.clicked.connect(self._toggle_mute)
        self.slider.valueChanged.connect(self._on_changed)
        self.slider.sliderReleased.connect(self._persist)

        # Set the loaded level (also pushes it to the audio controller and
        # updates the icon, via the valueChanged signal).
        self.slider.setValue(int(initial))
        self._update_icon(int(initial))

        # If Qt Multimedia isn't installed, audio is a no-op - make that clear
        # rather than leaving a slider that silently does nothing.
        audio = getattr(window, "audio", None)
        if audio is not None and not getattr(audio, "available", False):
            self.setEnabled(False)
            self.setToolTip(
                "Playback audio needs Qt Multimedia "
                "(e.g. pip install PySide6-Addons)."
            )

    def _vol_icon(self, value):
        style = self.style()
        if value <= 0:
            return style.standardIcon(QStyle.SP_MediaVolumeMuted)
        return style.standardIcon(QStyle.SP_MediaVolume)

    def _update_icon(self, value):
        try:
            self.icon.setIcon(self._vol_icon(value))
        except Exception:
            # Fall back to text if the style lacks these standard icons.
            self.icon.setText("x" if value <= 0 else "<)")

    def _on_changed(self, value):
        self._update_icon(value)

        audio = getattr(self.window, "audio", None)
        if audio is not None:
            audio.set_volume(value / 100.0)

    def _persist(self):
        if hasattr(self.window, "_save_volume"):
            self.window._save_volume(self.slider.value())

    def _toggle_mute(self):
        if self.slider.value() > 0:
            self._restore = self.slider.value()
            self.slider.setValue(0)
        else:
            self.slider.setValue(self._restore or 80)

        self._persist()


class TransportControls(QWidget):

    def __init__(
            self,
            window,
    ):

        super().__init__()

        self.window = window

        layout = QHBoxLayout(
            self
        )

        layout.setContentsMargins(
            0,
            0,
            0,
            0,
        )

        layout.setSpacing(
            8
        )

        self.back_120_btn = QPushButton()
        self.back_30_btn = QPushButton()
        self.frame_back_btn = QPushButton()

        self.play_btn = QPushButton()

        self.frame_forward_btn = QPushButton()
        self.forward_30_btn = QPushButton()
        self.forward_120_btn = QPushButton()

        self._icon_for = {
            self.back_120_btn: "seek_back_far",
            self.back_30_btn: "seek_back",
            self.frame_back_btn: "step_back",
            self.play_btn: "play",
            self.frame_forward_btn: "step_forward",
            self.forward_30_btn: "seek_forward",
            self.forward_120_btn: "seek_forward_far",
        }
        for _btn, _name in self._icon_for.items():
            _btn.setIcon(load_icon(_name))

        buttons = [

            self.back_120_btn,
            self.back_30_btn,
            self.frame_back_btn,

            self.play_btn,

            self.frame_forward_btn,
            self.forward_30_btn,
            self.forward_120_btn,

        ]

        self.back_120_btn.clicked.connect(
            lambda:
            self.window.jump_frames(
                -(self.window.fps * 120)
            )
        )

        self.back_30_btn.clicked.connect(
            lambda:
            self.window.jump_frames(
                -(self.window.fps * 30)
            )
        )

        self.frame_back_btn.clicked.connect(
            lambda:
            self.window.jump_frames(
                -1
            )
        )

        self.play_btn.clicked.connect(
            self.toggle_play
        )

        self.frame_forward_btn.clicked.connect(
            lambda:
            self.window.jump_frames(
                1
            )
        )

        self.forward_30_btn.clicked.connect(
            lambda:
            self.window.jump_frames(
                self.window.fps * 30
            )
        )

        self.forward_120_btn.clicked.connect(
            lambda:
            self.window.jump_frames(
                self.window.fps * 120
            )
        )

        self.back_120_btn.setToolTip("Back 2 minutes")
        self.back_30_btn.setToolTip("Back 30 seconds")
        self.frame_back_btn.setToolTip("Previous frame")
        self.play_btn.setToolTip("Play / Pause")
        self.frame_forward_btn.setToolTip("Next frame")
        self.forward_30_btn.setToolTip("Forward 30 seconds")
        self.forward_120_btn.setToolTip("Forward 2 minutes")

        #
        # Centre the play buttons with pure stretches - identical to how the
        # IN/OUT row above centres - so both rows share the same centre line.
        #
        # The volume control is NOT placed in this layout; it would push the
        # buttons off-centre and a balancing spacer never matched its real
        # width exactly (which is what kept nudging the two rows apart).
        # Instead it's a free child pinned to the far left in resizeEvent,
        # sitting over the (empty) left stretch where it can't disturb the
        # centred buttons.
        #

        self.volume = VolumeControl(
            window,
            initial=(
                window.config
                .get("settings", {})
                .get("volume", 80)
            ),
        )

        self.volume.setParent(self)
        self.volume.setFixedSize(
            self.volume.sizeHint()
        )

        layout.addStretch()

        for button in buttons:

            #
            # Don't let transport buttons keep keyboard focus; arrow keys
            # must always drive video navigation on the main window, never
            # move focus between these buttons.
            #

            button.setFocusPolicy(
                Qt.NoFocus
            )

            button.setIconSize(
                QSize(24, 24)
            )

            button.setFixedSize(
                40, 32
            )

            layout.addWidget(
                button
            )

        # The play button is the primary control - give its blue disc a little
        # more room so it reads as the focal point of the row (VRD-style).
        self.play_btn.setIconSize(
            QSize(28, 28)
        )

        layout.addStretch()

    def _position_volume(self):
        if not hasattr(self, "volume"):
            return
        self.volume.move(
            0,
            max(0, (self.height() - self.volume.height()) // 2),
        )

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._position_volume()

    def showEvent(self, event):
        super().showEvent(event)
        self.volume.raise_()
        self._position_volume()

    def toggle_play(self):

        self.window.playing = (
            not
            self.window.playing
        )

        if self.window.playing:

            self.window._play_anchor_time = None

            #
            # Reset to the source frame rate; scrubbing/arrow transport may
            # have left the timer at a different interval.
            #

            self.window.transport_timer.setInterval(
                round(1000 / self.window.fps)
            )

            self.window.transport_timer.start()

        else:

            self.window._play_anchor_time = None

            self.window.transport_timer.stop()

            #
            # Bring the strip / scene bar up to the paused frame.
            #

            self.window.update_display()

        self.update_buttons()

    def set_enabled(self, on):
        """Enable/disable all transport buttons (used when no video is loaded)."""
        for button in (
                self.back_120_btn,
                self.back_30_btn,
                self.frame_back_btn,
                self.play_btn,
                self.frame_forward_btn,
                self.forward_30_btn,
                self.forward_120_btn,
        ):
            button.setEnabled(on)

    def update_buttons(self):

        if self.window.playing:

            self.play_btn.setIcon(
                load_icon("pause")
            )

        else:

            self.play_btn.setIcon(
                load_icon("play")
            )

    def refresh_theme(self):
        """Reload the playback-button icons for the current theme (called after
        a live Light/Dark switch, once the icon cache has been cleared)."""
        for btn, name in self._icon_for.items():
            btn.setIcon(load_icon(name))
        self.update_buttons()          # play/pause reflects the current state

class ActionBar(QWidget):
    """Bottom row: Add Selection, Add Unselected, Save Video."""

    def __init__(
            self,
            window,
    ):

        super().__init__()

        self.window = window

        layout = QHBoxLayout(
            self
        )

        layout.setContentsMargins(
            0,
            4,
            0,
            4,
        )

        layout.setSpacing(
            8
        )

        self.add_selection_btn = QPushButton(
            "Add Selection"
        )

        self.add_unselected_btn = QPushButton(
            "Add Unselected"
        )

        self.save_video_btn = QPushButton(
            "Save Video"
        )

        layout.addStretch()

        for button in (
                self.add_selection_btn,
                self.add_unselected_btn,
                self.save_video_btn,
        ):
            button.setFocusPolicy(
                Qt.NoFocus
            )

            layout.addWidget(
                button
            )

        layout.addStretch()

        self.add_selection_btn.clicked.connect(
            self.window.commit_selection
        )

        self.add_unselected_btn.clicked.connect(
            self.window.add_unselected
        )

        self.save_video_btn.clicked.connect(
            self.window.export_video
        )

    def set_enabled(self, on):
        """Enable/disable the action buttons (used when no video is loaded)."""
        for button in (
                self.add_selection_btn,
                self.add_unselected_btn,
                self.save_video_btn,
        ):
            button.setEnabled(on)
