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
    parser.add_argument("--folder-layout", choices=("per_video", "shared"), default="per_video")
    parser.add_argument("--keep-parts", action="store_true")
    parser.add_argument("--merge-next-video", action="store_true")
    parser.add_argument("--part-numbers", type=int, nargs="+", default=[1, 2])
    parser.add_argument("--download-slots", type=int, choices=range(1, 17), default=4)
    args = parser.parse_args(arguments)
    report_path = Path(args.report).resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtWidgets, QtCore, QtGui
    from Lib.bili_api.collection import resolve_tree
    from downloadplan import build_download_tasks
    from utils import configUtils
    from utils.media_merge import find_media_tool, probe_media, run_media_process
    from utils.taskpaths import task_file
    from utils.version import __version__
    from mainwindow import MainWindow

    runtime = report_path.parent / (report_path.stem + "-runtime")
    runtime.mkdir(exist_ok=True)
    os.chdir(runtime)
    configUtils.setUserData("isnew", False)
    configUtils.setUserData("version", __version__)
    configUtils.setUserData(configUtils.Configs.AUTO_FILL_DATA_FROM_CLIPBOARD, False)
    configUtils.setUserData(configUtils.Configs.SHOW_DOWNLOAD_TIP, False)
    configUtils.setUserData(configUtils.Configs.OUTPUT_LAYOUT, args.folder_layout)
    configUtils.setUserData(configUtils.Configs.DELETE_PARTS_AFTER_MERGE, not args.keep_parts)
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
        report["current_video_title"] = first["title"]
        assert len(first["pages"]) >= 2, "测试视频至少需要两个分P"
        first_parts = [page for page in first["pages"] if page["page"] in args.part_numbers]
        assert len(first_parts) >= 2 and len(first_parts) == len(set(args.part_numbers)), "测试合并需要至少两个有效P号"
        for page in first_parts:
            page["tree_item"].setCheckState(0, QtCore.Qt.CheckState.Checked)
        selection.merge_boxes[first["key"]].setChecked(True)
        second = next((group for group in selection.data["videos"] if group["key"] != first["key"] and group["pages"]), None)
        if second:
            for page in second["pages"][:2 if args.merge_next_video else 1]:
                page["tree_item"].setCheckState(0, QtCore.Qt.CheckState.Checked)
            if args.merge_next_video:
                assert len(second["pages"]) >= 2
                selection.merge_boxes[second["key"]].setChecked(True)
        expected_merges = {first["key"]}
        if second and args.merge_next_video:
            expected_merges.add(second["key"])
        assert set(selection.data["merge_videos"]) == expected_merges
        options = {"path": str(runtime / "downloads"), "quality": 16, "codec": 7,
                   "reserveAudio": False, "fnval": 4048, "specialAudio": None,
                   "aiLanguage": None, "disable_title_limit": False,
                   "output_layout": args.folder_layout, "delete_after_merge": not args.keep_parts}
        report.update({"folder_layout": args.folder_layout, "delete_after_merge": not args.keep_parts})
        selected = [page for page in selection.data["page_data"] if page["selected"]]
        tasks = build_download_tasks(selected, options, selection.data["merge_videos"], "auto")
        report["planned_tasks"] = [{"kind": task["kind"], "video_key": task["video_key"],
                                    "name": task["name"]} for task in tasks]
        merges = [task for task in tasks if task["kind"] == "merge"]
        assert len(merges) == len(expected_merges)
        assert {task["video_key"] for task in merges} == expected_merges
        assert all({part["video_key"] for part in task["dependencies"]} == {task["video_key"]} for task in merges)
        report["selected_part_numbers"] = [page["page"] for page in first_parts]
        report["selected_original_part_names"] = [page["name"] for page in first_parts]
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
            queue.max_thread_count = args.download_slots
            report["download_slots"] = args.download_slots
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
            assert len(merges) == len(expected_merges)
            assert all(task_file(task).name == task["title"] + ".mp4" for task in merges)
            report["merged_filename_uses_video_title"] = True
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
                                       "state": task["state"], "name": task["name"],
                                       "started_at": task.get("started_at"), "finished_at": task.get("finished_at"),
                                       "source_deleted": task.get("source_deleted", False)} for task in tasks]
            report["merge_result"] = next(task["merge_result"] for task in merges if task["video_key"] == first["key"])
            report["merge_results"] = [{"video_key": task["video_key"], "result": task["merge_result"],
                                        "cleanup": task.get("cleanup_result"),
                                        "cleanup_finished_at": task.get("cleanup_finished_at")} for task in merges]
            media = probe_media(report["merge_result"]["output"])
            report["streams"] = [{key: stream.get(key) for key in ("codec_type", "codec_name", "width", "height", "duration")}
                                 for stream in media["streams"]]
            for merge in merges:
                run_media_process([find_media_tool("ffmpeg"), "-v", "error", "-nostdin", "-i",
                                   merge["merge_result"]["output"], "-f", "null", "-"])
                for part in merge["dependencies"]:
                    assert task_file(part).exists() == args.keep_parts
                if not args.keep_parts:
                    assert not merge["cleanup_result"]["pending"]
                    assert not merge["cleanup_result"]["errors"]
                    assert len(merge["cleanup_result"]["deleted"]) == len(merge["dependencies"])
            if second:
                first_merge = next(task for task in merges if task["video_key"] == first["key"])
                parts = [task for task in tasks if task["kind"] == "part"]
                def overlap(left, right):
                    return max(0.0, min(left["finished_at"], right["finished_at"])
                               - max(left["started_at"], right["started_at"]))
                merge_download_overlap = max(overlap(merge, part) for merge in merges for part in parts)
                cross_video_overlap = max(overlap(left, right) for left in parts for right in parts
                                          if left["video_key"] != right["video_key"])
                events = sorted((task[key], delta, task["kind"] == "merge") for task in tasks
                                for key, delta in (("started_at", 1), ("finished_at", -1)))
                running = [0, 0]
                peak = [0, 0]
                for _time, delta, merge_event in events:
                    running[merge_event] += delta
                    peak[merge_event] = max(peak[merge_event], running[merge_event])
                assert peak[0] <= args.download_slots
                assert peak[1] == 1
                if args.download_slots == 1:
                    # The next part must run while the preceding video merges.
                    assert merge_download_overlap > 0
                if args.download_slots >= 3:
                    assert cross_video_overlap > 0
                report["independent_queues"] = {
                    "passed": True, "peak_downloads": peak[0], "peak_merges": peak[1],
                    "merge_download_overlap_seconds": merge_download_overlap,
                    "cross_video_download_overlap_seconds": cross_video_overlap,
                    "first_merge_started_before_all_downloads_finished":
                        first_merge["started_at"] < max(part["finished_at"] for part in parts)}
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
