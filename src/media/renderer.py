from PySide6.QtCore import Qt
from PySide6.QtGui import (
    QImage,
    QPixmap,
)


def render_frame(
        frame,
        container,
        width=None,
        height=None,
        fast=False,
):

    img = frame.to_ndarray(
        format="rgb24"
    )

    h, w, c = img.shape

    qimg = QImage(
        img.data,
        w,
        h,
        w * c,
        QImage.Format_RGB888
    )

    pix = QPixmap.fromImage(
        qimg
    )

    #
    # Correct DAR
    #

    try:

        stream = (
            container
            .streams
            .video[0]
        )

        dar = (
            stream
            .display_aspect_ratio
        )

        if dar:

            corrected_width = int(
                h
                *
                float(
                    dar
                )
            )

            pix = pix.scaled(

                corrected_width,

                h,

                Qt.IgnoreAspectRatio,

                Qt.FastTransformation if fast else Qt.SmoothTransformation

            )

    except Exception:

        pass

    if width:

        pix = pix.scaled(

            width,

            height,

            Qt.KeepAspectRatio,

            Qt.SmoothTransformation,

        )

    return pix