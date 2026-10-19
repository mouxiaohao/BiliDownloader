"""Opt-in diagnostics for source and packaged builds; never runs on normal launch."""
import argparse
import json
import os
from pathlib import Path
import pickle
import time
import traceback


def main(arguments):
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--url", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--download", action="store_true")
    args = parser.parse_args(arguments)
    report_path = Path(args.report).resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtWidgets, QtCore, QtGui
    from Lib.bili_api.collection import resolve_tree
    from downloadplan import build_download_tasks
    from utils import configUtils
    from utils.media_merge import find_media_tool, probe_media, run_media_process
    from utils.version import __version__
    from mainwindow import MainWindow

    runtime = report_path.parent / (report_path.stem + "-runtime")
    runtime.mkdir(exist_ok=True)
    os.chdir(runtime)
    configUtils.setUserData("isnew", False)
    configUtils.setUserData("version", __version__)
    configUtils.setUserData(configUtils.Configs.AUTO_FILL_DATA_FROM_CLIPBOARD, False)
    configUtils.setUserData(configUtils.Configs.SHOW_DOWNLOAD_TIP, False)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    font_path = Path(__file__).resolve().parent / "font" / "HarmonyOS_Sans_SC_Regular.ttf"
    if font_path.exists():
        QtGui.QFontDatabase.addApplicationFont(str(font_path))
        app.setFont(QtGui.QFont("HarmonyOS Sans SC", 10))
    report = {"version": __version__, "url": args.url, "ok": False}
    window = None
    try:
        tree = resolve_tree(args.url)
        report.update({"title": tree["title"], "videos": len(tree["videos"]),
                       "parts": len(tree["page_data"]), "warnings": tree["warnings"],
                       "current_bvid": tree["current_bvid"], "playlist": tree["playlist"],
                       "video_ids": [group["key"] for group in tree["videos"]],
                       "first_video": {"id": tree["videos"][0]["key"],
                                       "parts": len(tree["videos"][0]["pages"])}})
        window = MainWindow()
        input_widget = window.ui.widget_input
        input_widget.input_pages[0].ui.line_input.setText(args.url)
        report["input_format"] = input_widget.input_pages[0].get_content()[0]
        assert input_widget.input_pages[0].ui.button_next.isEnabled()
        input_widget.input_pages[1].meta = QtCore.QByteArray(pickle.dumps(tree))
        selection = input_widget.input_pages[2]
        selection.data_update(False)
        report["initial_selected_parts"] = sum(page["selected"] for page in selection.data["page_data"])
        selection.select_all(False)
        assert not selection.ui.button_next.isEnabled()
        first = next((group for group in selection.data["videos"] if group.get("current")), selection.data["videos"][0])
        assert len(first["pages"]) >= 2, "测试视频至少需要两个分P"
        for page in first["pages"][:2]:
            page["tree_item"].setCheckState(0, QtCore.Qt.CheckState.Checked)
        selection.merge_boxes[first["key"]].setChecked(True)
        second = next((group for group in selection.data["videos"] if group["key"] != first["key"] and group["pages"]), None)
        if second:
            second["pages"][0]["tree_item"].setCheckState(0, QtCore.Qt.CheckState.Checked)
        assert selection.data["merge_videos"] == [first["key"]]
        options = {"path": str(runtime / "downloads"), "quality": 16, "codec": 7,
                   "reserveAudio": False, "fnval": 4048, "specialAudio": None,
                   "aiLanguage": None, "disable_title_limit": False}
        selected = [page for page in selection.data["page_data"] if page["selected"]]
        tasks = build_download_tasks(selected, options, selection.data["merge_videos"], "auto")
        report["planned_tasks"] = [{"kind": task["kind"], "video_key": task["video_key"],
                                    "name": task["name"]} for task in tasks]
        merges = [task for task in tasks if task["kind"] == "merge"]
        assert len(merges) == 1
        assert {part["video_key"] for part in merges[0]["dependencies"]} == {first["key"]}
        input_widget.input_pages[0].hide()
        input_widget.ui.gridLayout.removeWidget(input_widget.input_pages[0])
        input_widget.ui.gridLayout.addWidget(selection, 0, 0, 1, 1)
        input_widget.current_input_page = 2
        selection.show()
        window.resize(1200, 850)
        window.show()
        app.processEvents()
        screenshot = report_path.with_suffix(".png")
        window.grab().save(str(screenshot))
        report["screenshot"] = str(screenshot)
        if args.download:
            Path(options["path"]).mkdir(parents=True, exist_ok=True)
            queue = window.ui.widget_download
            # Exercise the real settings page and Submit handler as well as
            # the download/merge scheduler, rather than injecting queue jobs.
            config = input_widget.input_pages[3]
            errors = []
            original_critical = QtWidgets.QMessageBox.critical
            QtWidgets.QMessageBox.critical = lambda *values, **_kwargs: errors.append(str(values[-1]))
            try:
                config.data_update(False)
                quality_deadline = time.monotonic() + 120
                while getattr(config, "load_thread", None) is not None:
                    app.processEvents()
                    if time.monotonic() > quality_deadline:
                        raise TimeoutError("画质读取超时")
                    time.sleep(0.01)
            finally:
                QtWidgets.QMessageBox.critical = original_critical
            if errors:
                raise RuntimeError("画质界面错误：" + "; ".join(errors))
            quality = next((name for name, number in config.quality_match.items() if number == 16), None)
            if quality is None:
                raise RuntimeError("测试视频没有360P画质")
            config.ui.combo_quality.setCurrentText(quality)
            config.ui.line_path.setText(options["path"])
            assert config.ui.button_submit.isEnabled()
            config.ui.button_submit.click()
            tasks = list(queue.all_tasks)
            merges = [task for task in tasks if task["kind"] == "merge"]
            assert len(merges) == 1
            report["submission_via_config_ui"] = True
            deadline = time.monotonic() + 480
            while any(task["state"] not in ("completed", "failed") for task in tasks):
                app.processEvents()
                if any(task["state"] == "failed" for task in tasks):
                    failed = next(task for task in tasks if task["state"] == "failed")
                    raise RuntimeError(failed["widget"].ui.label_status.text())
                if time.monotonic() > deadline:
                    raise TimeoutError("下载与合并验证超时")
                time.sleep(0.02)
            report["task_results"] = [{"kind": task["kind"], "video_key": task["video_key"],
                                       "state": task["state"], "name": task["name"]} for task in tasks]
            report["merge_result"] = merges[0]["merge_result"]
            media = probe_media(report["merge_result"]["output"])
            report["streams"] = [{key: stream.get(key) for key in ("codec_type", "codec_name", "width", "height", "duration")}
                                 for stream in media["streams"]]
            run_media_process([find_media_tool("ffmpeg"), "-v", "error", "-nostdin", "-i",
                               report["merge_result"]["output"], "-f", "null", "-"])
            report["full_decode"] = "passed"
        report["ok"] = True
    except Exception:
        report["error"] = traceback.format_exc()
    finally:
        if window:
            window.close()
            deadline = time.monotonic() + 45
            while (window.isVisible() or window.ui.widget_download.running_tasks) and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(0.02)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report["ok"] else 1
