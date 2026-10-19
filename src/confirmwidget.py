import pickle
import traceback

from PySide6 import QtWidgets, QtGui, QtCore
from PySide6.QtCore import Signal, QByteArray

from Lib.bili_api import video, bangumi
from Lib.bili_api.exceptions import NetWorkException
from Lib.bili_api.utils import loadImage
from ui_confirmwidget import Ui_ConfirmWidget
from utils import configUtils
from Lib.bili_api.collection import resolve_tree
from Lib.bili_api.utils.passport import BiliPassport, decode_cookie


class ConfirmWidget(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super(ConfirmWidget, self).__init__(parent)
        self.ui = Ui_ConfirmWidget()
        self.ui.setupUi(self)
        self.err = True
        self.img = None
        self.meta = None

    def on_size_change(self):
        if self.img is None or self.img.isNull():
            return
        img = self.img.scaledToWidth(
            self.ui.cover_label.width(), QtCore.Qt.TransformationMode.SmoothTransformation
        )
        self.ui.cover_label.setPixmap(QtGui.QPixmap.fromImage(img))

    @QtCore.Slot(str, bool)
    def update_info(self, data: str, err: bool):
        self.ui.text_info.setText(data)
        self.err = err

    @QtCore.Slot(QtGui.QImage)
    def update_image(self, img: QtGui.QImage):
        self.img = img
        self.on_size_change()

    @QtCore.Slot(QByteArray)
    def update_meta(self, data: QByteArray):
        self.meta = data

    @QtCore.Slot()
    def load_end(self):
        QtCore.QObject.disconnect(self.load_thread, None, self, None)
        del self.load_thread
        self.ui.button_back.setEnabled(True)
        if not self.err:
            self.ui.button_next.setEnabled(True)

    def save_cover(self):
        default_save_path = configUtils.getUserData(configUtils.Configs.DOWNLOAD_PATH,
                                                    QtCore.QDir("Download").absolutePath())
        save_path = QtWidgets.QFileDialog.getSaveFileName(self, "设置保存位置", default_save_path, "PNG图片(*.png)")
        if len(save_path[0]) == 0:
            return
        self.img.save(
            save_path[0] + (".png" if not save_path[0].endswith(".png") else '')
        )
        QtWidgets.QMessageBox.information(self, "信息", "保存成功")

    def data_update(self, _back):
        self.img = QtGui.QImage(":/res/Placeholde.png")
        img = self.img.scaledToWidth(
            self.ui.cover_label.width(), QtCore.Qt.TransformationMode.SmoothTransformation
        )
        self.ui.cover_label.setPixmap(QtGui.QPixmap.fromImage(img))
        self.ui.text_info.setText("")
        self.ui.button_next.setDisabled(True)
        self.ui.button_back.setEnabled(False)
        fmt, content = self.parent().input_pages[0].get_content()
        self.load_thread = load_map[fmt](self, content)
        self.load_thread.update_info.connect(self.update_info)
        self.load_thread.update_image.connect(self.update_image)
        self.load_thread.update_meta.connect(self.update_meta)
        self.load_thread.finished.connect(self.load_end)
        self.load_thread.start()


class LoadInfoBase(QtCore.QThread):
    update_image = Signal(QtGui.QImage)
    update_info = Signal(str, bool)
    update_meta = Signal(QByteArray)

    def __init__(self, parent: ConfirmWidget, fmt: str, content: str) -> None:
        super(LoadInfoBase, self).__init__(parent)
        self.format = fmt
        self.content = content

    def run(self):
        try:
            self.load_data()
        except NetWorkException as ex:
            show = str(ex)
            self.update_info.emit(show, True)
        except Exception as ex:
            show = "未知错误\n" + str(ex) + "\n\n"
            show += traceback.format_exc()
            self.update_info.emit(show, True)

    def load_data(self):
        pass


class LoadInfoTree(LoadInfoBase):
    def __init__(self, parent, content):
        super().__init__(parent, "TREE", content)

    def progress(self, text):
        if self.isInterruptionRequested():
            raise InterruptedError("已取消解析")
        self.update_info.emit(text, False)

    def load_data(self):
        raw = configUtils.getUserData(configUtils.Configs.PASSPORT)
        passport = None
        if raw:
            cookie = raw.get("data")
            if cookie is None and raw.get("secure_data"):
                cookie = decode_cookie(raw["secure_data"], configUtils.getUserData(configUtils.Configs.PASSPORT_CRYPT_KEY))
            if cookie:
                passport = BiliPassport({k: v for k, v in cookie.items() if k != "Expires"})
        tree = resolve_tree(self.content, passport, self.progress)
        self.update_meta.emit(QByteArray(pickle.dumps(tree)))
        text = "{}\n\n共 {} 个视频，{} 个分P".format(tree["title"], len(tree["videos"]), len(tree["page_data"]))
        if tree["playlist"]:
            text += "\n已识别所属播放列表，可在下一页按视频和分P选择。"
        if tree["current_bvid"] and len(tree["videos"]) > 1:
            text += "\n默认只勾选当前视频；点击全选可下载整个列表。"
        if tree["warnings"]:
            text += "\n\n提示：\n" + "\n".join(tree["warnings"][:5])
        self.update_info.emit(text, False)
        if tree.get("cover"):
            try:
                self.update_image.emit(QtGui.QImage.fromData(loadImage.load_bili_image(tree["cover"])))
            except Exception:
                pass


class LaodInfoAV(LoadInfoBase):
    def __init__(self, parent: QtCore.QObject, aid: str) -> None:
        super(LaodInfoAV, self).__init__(parent, "AV", aid)

    def load_data(self):
        data = video.get_video_info(aid=self.content[2:])
        online = video.get_video_online_count(cid=data["cid"], aid=self.content[2:])
        pic_url = data["pic"]
        show: str = data["title"]
        show += "\n\nBV号: " + data["bvid"]
        show += "\nAV号: AV" + str(data["aid"])
        show += "\nUP主: " + data["owner"]["name"]
        show += "\n在线人数: " + online["total"]
        show += "\n\n简介:\n" + data["desc"]
        self.update_info.emit(show, False)
        page_data = []
        for i in data["pages"]:
            part = {
                "isbvid": self.format == "BV",
                "id": self.content[2:],
                "cid": i["cid"],
                "page": i["page"],
                "name": i["part"],
                "title": data["title"],
                "type": "video",
            }
            page_data.append(part)
        meta_data = {
            "title": data["title"],
            "page_data": page_data
        }
        pack = pickle.dumps(meta_data)
        pack = QByteArray(pack)
        self.update_meta.emit(pack)
        try:
            pic_data = loadImage.load_bili_image(pic_url)
            img = QtGui.QImage.fromData(pic_data)
            self.update_image.emit(img)
        except:
            pass


class LoadInfoBV(LoadInfoBase):
    def __init__(self, parent: QtCore.QObject, vid: str) -> None:
        super(LoadInfoBV, self).__init__(parent, "BV", vid)

    def load_data(self):
        data = video.get_video_info(bvid=self.content)
        online = video.get_video_online_count(cid=data["cid"], bvid=self.content)
        pic_url = data["pic"]
        show: str = data["title"]
        show += "\n\nBV号: " + data["bvid"]
        show += "\nAV号: AV" + str(data["aid"])
        show += "\nUP主: " + data["owner"]["name"]
        show += "\n在线人数: " + online["total"]
        show += "\n\n简介:\n" + data["desc"]
        self.update_info.emit(show, False)
        page_data = []
        for i in data["pages"]:
            part = {
                "isbvid": self.format == "BV",
                "id": self.content,
                "cid": i["cid"],
                "page": i["page"],
                "name": i["part"],
                "title": data["title"],
                "type": "video",
            }
            page_data.append(part)
        meta_data = {
            "title": data["title"],
            "page_data": page_data
        }
        pack = pickle.dumps(meta_data)
        pack = QByteArray(pack)
        self.update_meta.emit(pack)
        try:
            pic_data = loadImage.load_bili_image(pic_url)
            img = QtGui.QImage.fromData(pic_data)
            self.update_image.emit(img)
        except:
            pass


class LoadInfoMD(LoadInfoBase):
    def __init__(self, parent: QtCore.QObject, vid: str) -> None:
        super(LoadInfoMD, self).__init__(parent, "MD", vid)

    def load_data(self):
        data: dict = bangumi.get_bangumi_info(self.content[2:])
        ssid = data["media"]["season_id"]
        ss_data = bangumi.get_bangumi_detailed_info(season_id=ssid)
        show = data["media"]["title"]
        show += "\n\nMD号: MD" + str(data["media"]["media_id"])
        if "rating" in data["media"]:
            show += "\n评分: " + str(data["media"]["rating"]["score"])
        else:
            show += "\n评分: 暂无评分"
        show += "\n\n简介:\n" + ss_data["data"]["evaluate"]
        show += "\n\n制作:\n" + ss_data["data"]["staff"]
        cover_url = data["media"]["cover"]
        self.update_info.emit(show, False)
        page_data = []
        i = 0
        for v in ss_data["data"]["episodes"]:
            part = {
                "isbvid": True,
                "id": v["bvid"],
                "cid": v["cid"],
                "page": i + 1,
                "name": v["title"] + "-" + v["long_title"],
                "title": data["media"]["title"],
                "type": "video"
            }
            page_data.append(part)
            i += 1
        meta_data = {
            "title": data["media"]["season_id"],
            "page_data": page_data
        }
        pack = pickle.dumps(meta_data)
        pack = QByteArray(pack)
        self.update_meta.emit(pack)
        try:
            pic_data = loadImage.load_bili_image(cover_url)
            img = QtGui.QImage.fromData(pic_data)
            self.update_image.emit(img)
        except:
            pass


class LoadInfoEP(LoadInfoBase):
    def __init__(self, parent: ConfirmWidget, content: str) -> None:
        super().__init__(parent, "EP", content)

    def load_data(self):
        data = bangumi.get_bangumi_detailed_info(ep_id=self.content[2:])
        show = data["info"]["media"]["title"]
        show += "\n\nMD号: MD" + str(data["info"]["media"]["media_id"])
        if "rating" in data["info"]["media"]:
            show += "\n评分: " + str(data["info"]["media"]["rating"]["score"])
        else:
            show += "\n评分: 暂无评分"
        show += "\n\n简介:\n" + data["data"]["evaluate"]
        show += "\n\n制作:\n" + data["data"]["staff"]
        cover_url = data["info"]["media"]["cover"]
        self.update_info.emit(show, False)
        page_data = []
        i = 0
        for v in data["data"]["episodes"]:
            part = {
                "isbvid": True,
                "id": v["bvid"],
                "cid": v["cid"],
                "page": i + 1,
                "name": v["title"] + "-" + v["long_title"],
                "title": data["info"]["media"]["title"],
                "type": "video"
            }
            page_data.append(part)
            i += 1
        meta_data = {
            "title": data["info"]["media"]["title"],
            "page_data": page_data
        }
        pack = pickle.dumps(meta_data)
        pack = QByteArray(pack)
        self.update_meta.emit(pack)
        try:
            pic_data = loadImage.load_bili_image(cover_url)
            img = QtGui.QImage.fromData(pic_data)
            self.update_image.emit(img)
        except:
            pass


load_map = {
    "AV": LoadInfoTree,
    "BV": LoadInfoTree,
    "LIST": LoadInfoTree,
    "MD": LoadInfoMD,
    "EP": LoadInfoEP,
}
