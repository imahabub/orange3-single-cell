from collections import namedtuple
import numpy as np

from AnyQt.QtCore import Qt, QSize, QRectF, QObject, pyqtSignal as Signal
from AnyQt.QtGui import QColor
from AnyQt.QtWidgets import QGraphicsSceneMouseEvent, QHBoxLayout, QVBoxLayout

import pyqtgraph as pg

from Orange.data import Table
from Orange.widgets import gui
from Orange.widgets.settings import Setting
from Orange.widgets.widget import OWWidget, Input, Output, Msg

from orangecontrib.single_cell.preprocess.scpreprocess import \
    DropoutGeneSelection

DropoutResults = namedtuple("DropoutResults",
                            ["zero_rate", "mean_expr", "decay",
                             "x_offset", "y_offset", "threshold"])


class DropoutGraph(pg.PlotWidget):
    pg.setConfigOption("foreground", "k")

    CURVE_PEN = pg.mkPen(color=QColor(Qt.darkCyan), width=4)

    def __init__(self, parent):
        super().__init__(parent, background="w")
        self.setMouseEnabled(False, False)
        self.hideButtons()
        self.getPlotItem().setContentsMargins(0, 20, 20, 0)
        self.setLabel("bottom", "Mean log2 nonzero expression")
        self.setLabel("left", "Frequency of zero expression")

    def set_data(self, dropout_results):
        self.__plot_dots(dropout_results.mean_expr, dropout_results.zero_rate)
        self.__plot_curve(dropout_results)
        self.__set_range(dropout_results.threshold, dropout_results.mean_expr)

    def __plot_dots(self, x, y):
        self.addItem(pg.ScatterPlotItem(x=x, y=y, size=3))

    def __plot_curve(self, results):
        xmin, xmax = self.__get_xlim(results.threshold, results.mean_expr)
        x = np.arange(xmin, xmax + 0.01, 0.01)
        y = np.exp(-results.decay * (x - results.x_offset)) + results.y_offset
        curve = pg.PlotCurveItem(x=x, y=y, fillLevel=1, pen=self.CURVE_PEN,
                                 brush=pg.mkBrush(color=QColor(0, 250, 0, 50)),
                                 antialias=True)
        self.addItem(curve)

    def __set_range(self, threshold, x):
        xmin, xmax = self.__get_xlim(threshold, x)
        rect = QRectF(xmin, 0, xmin + xmax, 1)
        self.setRange(rect, padding=0)

    @staticmethod
    def __get_xlim(threshold, x):
        xmin = 0 if threshold == 0 else np.log2(threshold)
        return xmin, np.ceil(np.nanmax(x))


class FilterType:
    ByNumber, ByEquation = range(2)


class OWDropout(OWWidget):
    name = "Dropout"
    description = "Dropout-based gene selection"
    icon = 'icons/Dropout.svg'
    priority = 205

    class Inputs:
        data = Input("Data", Table)

    class Outputs:
        data = Output("Data", Table)

    class Warning(OWWidget.Warning):
        less_selected = Msg("Cannot select more than {} genes.")

    filter_type = Setting(FilterType.ByNumber)
    n_genes = Setting(1000)
    x_offset = Setting(5)
    y_offset = Setting(0.02)
    decay = Setting(1)
    auto_commit = Setting(True)

    graph_name = "graph.plotItem"

    def __init__(self):
        super().__init__()
        self.data = None  # type: Table
        self.selected = None  # type: np.ndarray
        self.setup_gui()

    def setup_gui(self):
        self._add_graph()
        self._add_controls()

    def _add_graph(self):
        box = gui.vBox(self.mainArea, True, margin=0)
        self.graph = DropoutGraph(self)
        box.layout().addWidget(self.graph)

    def _add_controls(self):
        info_box = gui.widgetBox(self.controlArea, "Info")
        self.info_label = gui.widgetLabel(info_box, "")

        filter_box = gui.radioButtons(
            self.controlArea, self, "filter_type", box="Filter",
            orientation=QVBoxLayout(), callback=self.__filter_type_changed)

        genes_layout = QHBoxLayout()
        formula_layout = QVBoxLayout()
        genes_layout.addWidget(gui.appendRadioButton(
            filter_box, "Number of genes:", addToLayout=False))
        genes_layout.addWidget(gui.spin(
            filter_box, self, "n_genes", 0, 10000, addToLayout=False,
            callback=self.__param_changed))
        formula_layout.addWidget(gui.appendRadioButton(
            filter_box, "Apply exp(-a(x-b))+c", addToLayout=False))
        filter_box.layout().addLayout(genes_layout)
        filter_box.layout().addLayout(formula_layout)

        gui.separator(filter_box, height=1)
        coef_box = gui.hBox(filter_box, False)
        gui.separator(coef_box, width=15)
        common = dict(orientation=Qt.Horizontal,
                      callback=self.__param_changed,
                      alignment=Qt.AlignRight, controlWidth=60)
        gui.doubleSpin(
            coef_box, self, "decay", 0.0, 10.0, 0.01, label="a:", **common)
        gui.doubleSpin(
            coef_box, self, "x_offset", 0.0, 10.0, 0.01, label="b:", **common)
        gui.doubleSpin(
            coef_box, self, "y_offset", 0.0, 1.0, 0.01, label="c:", **common)

        gui.rubber(self.controlArea)
        gui.auto_commit(self.controlArea, self, "auto_commit",
                        "Send Selection", "Send Automatically")

        self.setup_info_label()
        self.enable_controls()

    def __filter_type_changed(self):
        self.enable_controls()
        self.__param_changed()

    def __param_changed(self):
        self.clear()
        self.select_genes()
        self.setup_info_label()
        self.commit()

    @property
    def filter_by_nr_of_genes(self):
        return self.filter_type == FilterType.ByNumber

    @Inputs.data
    def set_data(self, data):
        self.clear()
        self.data = data
        self.select_genes()
        self.setup_info_label()
        self.unconditional_commit()

    def clear(self):
        self.selected = None
        self.graph.clear()

    def select_genes(self):
        self.Warning.less_selected.clear()
        if not self.data:
            return

        if self.filter_by_nr_of_genes:
            kwargs = {"n_genes": self.n_genes}
        else:
            kwargs = {"decay": self.decay,
                      "x_offset": self.x_offset,
                      "y_offset": self.y_offset}
        selector = DropoutGeneSelection(**kwargs)
        results = selector.select_genes(self.data.X) + (selector.decay,
                                                        selector.x_offset,
                                                        selector.y_offset,
                                                        selector.threshold)
        self.selected = results[0]
        self.decay, self.x_offset, self.y_offset = results[-4:-1]
        self.graph.set_data(DropoutResults(*results[1:]))

        n_selected = sum(self.selected)
        if n_selected < self.n_genes and self.filter_by_nr_of_genes:
            self.Warning.less_selected(n_selected)

    def setup_info_label(self):
        text = "No data on input."
        if self.selected is not None:
            k = sum(self.selected)
            n, m = len(self.data), len(self.data.domain.attributes)
            ks = "s" if k != 1 else ""
            ns, ms = "s" if n != 1 else "", "s" if m != 1 else ""
            text = f"Data with {n} cell{ns} and {m} gene{ms}" \
                   f"\n{k} gene{ks} in selection"
        self.info_label.setText(text)

    def enable_controls(self):
        self.controls.n_genes.setEnabled(self.filter_by_nr_of_genes)
        self.controls.decay.setEnabled(not self.filter_by_nr_of_genes)
        self.controls.x_offset.setEnabled(not self.filter_by_nr_of_genes)
        self.controls.y_offset.setEnabled(not self.filter_by_nr_of_genes)

    def commit(self):
        data = None
        if self.selected is not None:
            data = DropoutGeneSelection.filter_columns(self.data,
                                                       self.selected)
        self.Outputs.data.send(data)

    def sizeHint(self):
        return super().sizeHint().expandedTo(QSize(800, 600))

    def send_report(self):
        if self.selected is None:
            return
        if self.filter_by_nr_of_genes:
            caption = f"Number of genes: {self.n_genes}"
        else:
            a = "{:.2f}".format(self.decay) \
                if round(self.decay, 2) != 1 else ""
            b = "-{:.2f}".format(self.x_offset) \
                if round(self.x_offset, 2) != 0 else ""
            c = "+{:.2f}".format(self.y_offset) \
                if round(self.y_offset, 2) != 0 else ""
            caption = f"Applying equation: exp(-{a}(x{b})){c}"
        self.report_plot()
        self.report_caption(caption)


if __name__ == "__main__":
    from Orange.widgets.utils.widgetpreview import WidgetPreview

    table = Table("https://datasets.orange.biolab.si/sc/aml-1k.tab.gz")
    WidgetPreview(OWDropout).run(set_data=table)
