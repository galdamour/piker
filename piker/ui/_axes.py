"""
Chart axes graphics and behavior.
"""
from typing import List, Tuple, Optional

import pandas as pd
import pyqtgraph as pg
from PyQt5 import QtCore, QtGui
from PyQt5.QtCore import QPointF

from ._style import DpiAwareFont, hcolor, _font
from ..data._source import float_digits

_axis_pen = pg.mkPen(hcolor('bracket'))


class Axis(pg.AxisItem):
    """A better axis that sizes to typical tick contents considering font size.

    """
    def __init__(
        self,
        linked_charts,
        typical_max_str: str = '100 000.00',
        **kwargs
    ) -> None:

        self.linked_charts = linked_charts

        super().__init__(**kwargs)

        self.setTickFont(_font.font)
        self.setStyle(**{
            'textFillLimits': [(0, 0.666)],
            'tickFont': _font.font,
            # 'tickTextWidth': 100,
            # 'tickTextHeight': 20,
            # 'tickTextWidth': 40,
            # 'autoExpandTextSpace': True,
            # 'maxTickLength': -20,

            # doesn't work well on price?
            # 'stopAxisAtTick': (True, True),
        })
        # self.setLabel(**{'font-size': '10pt'})

        self.setTickFont(_font.font)
        self.setPen(_axis_pen)
        self.typical_br = _font._qfm.boundingRect(typical_max_str)

        # size the pertinent axis dimension to a "typical value"
        self.resize()


class PriceAxis(Axis):

    def __init__(
        self,
        *args,
        **kwargs,
    ) -> None:
        super().__init__(*args, orientation='right', **kwargs)

    def resize(self) -> None:
        self.setWidth(self.typical_br.width())

    # XXX: drop for now since it just eats up h space

    def tickStrings(self, vals, scale, spacing):
        digits = float_digits(spacing * scale)

        # print(f'vals: {vals}\nscale: {scale}\nspacing: {spacing}')
        # print(f'digits: {digits}')

        return [
            ('{:,.%df}' % digits).format(v).replace(',', ' ') for v in vals
        ]


class DynamicDateAxis(Axis):

    # time formats mapped by seconds between bars
    tick_tpl = {
        60*60*24: '%Y-%b-%d',
        60: '%H:%M',
        30: '%H:%M:%S',
        5: '%H:%M:%S',
    }

    def resize(self) -> None:
        self.setHeight(self.typical_br.height() + 3)

    def _indexes_to_timestrs(
        self,
        indexes: List[int],
    ) -> List[str]:

        bars = self.linked_charts.chart._array
        bars_len = len(bars)
        times = bars['time']

        epochs = times[list(
            map(int, filter(lambda i: i < bars_len, indexes))
        )]
        # TODO: **don't** have this hard coded shift to EST
        dts = pd.to_datetime(epochs, unit='s')  # - 4*pd.offsets.Hour()

        delay = times[-1] - times[-2]
        return dts.strftime(self.tick_tpl[delay])

    def tickStrings(self, values: List[float], scale, spacing):
        return self._indexes_to_timestrs(values)


class AxisLabel(pg.GraphicsObject):

    _w_margin = 0
    _h_margin = 0

    def __init__(
        self,
        parent: Axis,
        digits: int = 2,
        bg_color: str = 'bracket',
        fg_color: str = 'black',
        opacity: int = 1,
        font_size: Optional[int] = None,
    ):
        super().__init__(parent)
        self.parent = parent
        self.opacity = opacity
        self.label_str = ''
        self.digits = digits

        self._txt_br: QtCore.QRect = None

        self._dpifont = DpiAwareFont()
        self._dpifont.configure_to_dpi(_font._screen)

        if font_size is not None:
            # print(f"SETTING FONT TO: {font_size}")
            self._dpifont._set_qfont_px_size(font_size)

        # self._font._fm = QtGui.QFontMetrics(self._font)

        self.bg_color = pg.mkColor(hcolor(bg_color))
        self.fg_color = pg.mkColor(hcolor(fg_color))

        # self.pic = QtGui.QPicture()
        # p = QtGui.QPainter(self.pic)

        self.rect = None

        # p.setPen(self.fg_color)

        self.setFlag(self.ItemIgnoresTransformations)

    def paint(self, p, option, widget):
        # p.drawPicture(0, 0, self.pic)
        # p.setCompositionMode(QtGui.QPainter.CompositionMode_SourceOver)

        if self.label_str:

            if not self.rect:
                self._size_br_from_str(self.label_str)

            p.setFont(self._dpifont.font)
            p.setPen(self.fg_color)
            p.setOpacity(self.opacity)
            p.fillRect(self.rect, self.bg_color)

            # this adds a nice black outline around the label for some odd
            # reason; ok by us
            p.drawRect(self.rect)

            p.drawText(self.rect, self.text_flags, self.label_str)

    def boundingRect(self):  # noqa
        if self.label_str:
            self._size_br_from_str(self.label_str)
            return self.rect

        return QtCore.QRectF()

        # return self.rect or QtCore.QRectF()

    def _size_br_from_str(self, value: str) -> None:
        """Do our best to render the bounding rect to a set margin
        around provided string contents.

        """
        # size the filled rect to text and/or parent axis
        br = self._txt_br = self._dpifont.boundingRect(value)

        # px_per_char = self._font._fm.averageCharWidth()
        # br = br * 1.88
        txt_h, txt_w = br.height(), br.width()
        # print(f'orig: {txt_h}')
        # txt_h = (br.topLeft() - br.bottomRight()).y()
        # txt_w = len(value) * px_per_char
        # txt_w *= 1.88
        # txt_h *= 1.88
        # print(f'calced: {txt_h}')

        h, w = self.size_hint()

        self.rect = QtCore.QRectF(
            0, 0,
            (w or txt_w) + self._w_margin,
            (h or txt_h) + self._h_margin,
        )


# _common_text_flags = (
#     QtCore.Qt.TextDontClip |
#     QtCore.Qt.AlignCenter |
#     QtCore.Qt.AlignTop |
#     QtCore.Qt.AlignHCenter |
#     QtCore.Qt.AlignVCenter
# )


class XAxisLabel(AxisLabel):

    text_flags = (
        QtCore.Qt.TextDontClip
        | QtCore.Qt.AlignCenter
    )

    def size_hint(self) -> Tuple[float, float]:
        # size to parent axis height
        return self.parent.height(), None

    def update_label(
        self,
        abs_pos: QPointF,  # scene coords
        data: float,  # data for text
        offset: int = 1  # if have margins, k?
    ) -> None:

        timestrs = self.parent._indexes_to_timestrs([int(data)])

        if not timestrs.any():
            return

        self.label_str = timestrs[0]

        w = self.boundingRect().width()
        self.setPos(QPointF(
            abs_pos.x() - w / 2 - offset,
            0,
        ))


class YAxisLabel(AxisLabel):
    _h_margin = 3

    text_flags = (
        # QtCore.Qt.AlignLeft
        QtCore.Qt.AlignVCenter
        | QtCore.Qt.TextDontClip
    )

    def size_hint(self) -> Tuple[float, float]:
        # size to parent axis width
        return None, self.parent.width()

    def update_label(
        self,
        abs_pos: QPointF,  # scene coords
        data: float,  # data for text
        offset: int = 1  # if have margins, k?
    ) -> None:

        # this is read inside ``.paint()``
        self.label_str = '{data: ,.{digits}f}'.format(
            digits=self.digits, data=data).replace(',', ' ')

        br = self.boundingRect()
        h = br.height()
        self.setPos(QPointF(
            0,
            abs_pos.y() - h / 2 - offset
        ))


class YSticky(YAxisLabel):
    """Y-axis label that sticks to where it's placed despite chart resizing.
    """
    def __init__(
        self,
        chart,
        *args,
        **kwargs
    ) -> None:

        super().__init__(*args, **kwargs)

        self._chart = chart
        chart.sigRangeChanged.connect(self.update_on_resize)
        self._last_datum = (None, None)

    def update_on_resize(self, vr, r):
        # TODO: add an `.index` to the array data-buffer layer
        # and make this way less shitty...
        index, last = self._last_datum
        if index is not None:
            self.update_from_data(
                index,
                last,
            )

    def update_from_data(
        self,
        index: int,
        value: float,
    ) -> None:
        self._last_datum = (index, value)
        self.update_label(
            self._chart.mapFromView(QPointF(index, value)),
            value
        )
