import pickle
from collections import OrderedDict
from types import SimpleNamespace

from PySide6 import QtWidgets, QtCore
from utils import configUtils


class SelectionWidget(QtWidgets.QWidget):
    """Checkable playlist -> video -> part tree; merge stays inside each video."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.data = None
        self.video_items = {}
        self.merge_boxes = {}
        self._building = False
        self.ui = SimpleNamespace()
        layout = QtWidgets.QVBoxLayout(self)
        hint = QtWidgets.QLabel("勾选列表或视频可选择其全部分P，也可展开后逐P勾选。\n下载按设置的任务数并发进行；合并队列独立运行，各视频所选分P下载齐后即可合并。")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        toolbar = QtWidgets.QHBoxLayout()
        for title, handler in (("全选", lambda: self.select_all(True)),
                               ("全不选", lambda: self.select_all(False)),
                               ("只选当前视频", self.select_current)):
            button = QtWidgets.QPushButton(title)
            button.clicked.connect(handler)
            toolbar.addWidget(button)
            if title == "只选当前视频":
                self.current_button = button
        self.merge_all = QtWidgets.QCheckBox("所选视频均合并分P")
        self.merge_all.setToolTip("每个视频分别合并；不会把不同视频拼在一起。可在下方选择是否删除分P。")
        self.merge_all.toggled.connect(self.set_merge_all)
        toolbar.addWidget(self.merge_all)
        toolbar.addStretch()
        layout.addLayout(toolbar)
        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderLabels(["播放列表 / 视频 / 分P", "合并分P"])
        self.tree.setColumnWidth(1, 140)
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.tree.setAlternatingRowColors(True)
        self.tree.itemChanged.connect(self.on_item_changed)
        layout.addWidget(self.tree, 1)
        options = QtWidgets.QHBoxLayout()
        options.addWidget(QtWidgets.QLabel("合并方式："))
        self.merge_mode = QtWidgets.QComboBox()
        self.merge_mode.addItem("优先无损，不兼容时转码", "auto")
        self.merge_mode.addItem("仅无损合并，不兼容时保留分P并提示", "copy")
        self.merge_mode.currentIndexChanged.connect(self.update_selection)
        options.addWidget(self.merge_mode)
        options.addStretch()
        layout.addLayout(options)
        storage = QtWidgets.QHBoxLayout()
        storage.addWidget(QtWidgets.QLabel("存放方式："))
        self.output_layout = QtWidgets.QComboBox()
        self.output_layout.addItem("每个视频单独一个文件夹", "per_video")
        self.output_layout.addItem("所有视频放在同一个所选文件夹", "shared")
        self.output_layout.setToolTip("合并成片以视频标题（与视频文件夹同名，含BV号）命名。统一存放的未合并分P另带P号，防止覆盖。")
        self.output_layout.currentIndexChanged.connect(self.update_selection)
        storage.addWidget(self.output_layout)
        self.delete_parts = QtWidgets.QCheckBox("合并成功后删除参与合并的分P")
        self.delete_parts.setChecked(True)
        self.delete_parts.setToolTip("仅清理参与本次成功合并的分P视频；合并失败或文件发生变化时保留。独立音频和弹幕保留。")
        self.delete_parts.toggled.connect(self.update_selection)
        storage.addWidget(self.delete_parts)
        storage.addStretch()
        layout.addLayout(storage)
        self.summary = QtWidgets.QLabel()
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        buttons = QtWidgets.QHBoxLayout()
        self.ui.button_back = QtWidgets.QPushButton("返回")
        self.ui.button_next = QtWidgets.QPushButton("继续")
        self.ui.button_next.clicked.connect(self.save_output_options)
        buttons.addWidget(self.ui.button_back)
        buttons.addWidget(self.ui.button_next)
        layout.addLayout(buttons)

    def _normalize_legacy(self):
        if "videos" in self.data:
            return
        groups = OrderedDict()
        for page in self.data["page_data"]:
            key = str(page["id"])
            page["video_key"] = key
            groups.setdefault(key, {"key": key, "title": page["title"], "pages": [],
                                    "current": True, "error": None})["pages"].append(page)
        self.data["videos"] = list(groups.values())
        self.data["default_all"] = True

    def data_update(self, back):
        if back:
            return
        self._building = True
        self.tree.clear()
        self.video_items.clear()
        self.merge_boxes.clear()
        self.data = pickle.loads(self.parent().input_pages[1].meta.data())
        self._normalize_legacy()
        saved_layout = configUtils.getUserData(configUtils.Configs.OUTPUT_LAYOUT, "per_video")
        self.output_layout.setCurrentIndex(max(0, self.output_layout.findData(saved_layout)))
        self.delete_parts.setChecked(configUtils.getUserData(configUtils.Configs.DELETE_PARTS_AFTER_MERGE, True))
        self.merge_all.setChecked(False)
        self.root = QtWidgets.QTreeWidgetItem(self.tree, [str(self.data["title"]), ""])
        self.root.setFlags(self.root.flags() | QtCore.Qt.ItemFlag.ItemIsAutoTristate | QtCore.Qt.ItemFlag.ItemIsUserCheckable)
        self.root.setCheckState(0, QtCore.Qt.CheckState.Unchecked)
        for group in self.data["videos"]:
            label = "{}  [{}]（{}P）".format(group["title"], group["key"], len(group["pages"]))
            if group.get("current"):
                label += "  ← 当前视频"
            item = QtWidgets.QTreeWidgetItem(self.root, [label, ""])
            self.video_items[group["key"]] = item
            if group.get("error"):
                item.setText(0, label + "  解析失败")
                item.setToolTip(0, group["error"])
                item.setDisabled(True)
                continue
            item.setFlags(item.flags() | QtCore.Qt.ItemFlag.ItemIsAutoTristate | QtCore.Qt.ItemFlag.ItemIsUserCheckable)
            checked = self.data.get("default_all", True) or group.get("current")
            for page in group["pages"]:
                part = QtWidgets.QTreeWidgetItem(item, ["P{:03d}  {}".format(page["page"], page["name"]), ""])
                part.setFlags(part.flags() | QtCore.Qt.ItemFlag.ItemIsUserCheckable)
                part.setCheckState(0, QtCore.Qt.CheckState.Checked if checked else QtCore.Qt.CheckState.Unchecked)
                page["tree_item"] = part
                page["video_key"] = group["key"]
            box = QtWidgets.QCheckBox("合并所选分P")
            box.setToolTip("至少选择2个分P；仅合并当前视频中的所选分P，顺序按P号排列。是否删除分P由下方选项决定。")
            box.toggled.connect(self.update_selection)
            self.merge_boxes[group["key"]] = box
            self.tree.setItemWidget(item, 1, box)
            item.setExpanded(bool(group.get("current")) or group is self.data["videos"][0])
        self.root.setExpanded(True)
        self.current_button.setEnabled(any(group.get("current") for group in self.data["videos"]))
        self._building = False
        self.update_selection()

    def select_all(self, checked):
        if self.data is None:
            return
        self._building = True
        for page in self.data["page_data"]:
            if "tree_item" in page:
                page["tree_item"].setCheckState(0, QtCore.Qt.CheckState.Checked if checked else QtCore.Qt.CheckState.Unchecked)
        self._building = False
        self.update_selection()

    def select_current(self):
        self._building = True
        for group in self.data["videos"]:
            for page in group["pages"]:
                page["tree_item"].setCheckState(0, QtCore.Qt.CheckState.Checked if group.get("current") else QtCore.Qt.CheckState.Unchecked)
        self._building = False
        self.update_selection()

    def set_merge_all(self, checked):
        self._building = True
        for box in self.merge_boxes.values():
            box.setChecked(checked)
        self._building = False
        self.update_selection()

    def on_item_changed(self, _item, _column):
        self.update_selection()

    def update_selection(self, *_args):
        if self._building or self.data is None:
            return
        selected_count, selected_videos = 0, 0
        merge_keys = []
        for group in self.data["videos"]:
            count = 0
            for page in group["pages"]:
                page["selected"] = page["tree_item"].checkState(0) == QtCore.Qt.CheckState.Checked
                count += int(page["selected"])
            selected_count += count
            selected_videos += int(count > 0)
            box = self.merge_boxes.get(group["key"])
            if box:
                box.setEnabled(count >= 2)
                if count >= 2 and box.isChecked():
                    merge_keys.append(group["key"])
        self.data["merge_videos"] = merge_keys
        self.data["merge_mode"] = self.merge_mode.currentData()
        self.data["output_layout"] = self.output_layout.currentData()
        self.data["delete_after_merge"] = self.delete_parts.isChecked()
        self.delete_parts.setEnabled(bool(merge_keys))
        self.summary.setText("已选 {} 个视频 / {} 个分P；{} 个视频将分别合并分P。".format(selected_videos, selected_count, len(merge_keys)))
        self.ui.button_next.setEnabled(selected_count > 0)

    def save_output_options(self):
        data = configUtils.UserDataHelper()
        data.set(data.CFGS.OUTPUT_LAYOUT, self.output_layout.currentData())
        data.set(data.CFGS.DELETE_PARTS_AFTER_MERGE, self.delete_parts.isChecked())
        data.save()
