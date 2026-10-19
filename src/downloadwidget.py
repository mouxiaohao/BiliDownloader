import os
import time
import uuid
from pathlib import Path

from PySide6 import QtWidgets, QtCore

from downloaditem import DownloadItem
from downloadthread import (
    DownloadTask,
    RESULT_CANCELLED,
    RESULT_COMPLETED,
    RESULT_FAILED,
    RESULT_PAUSED,
)
from ui_downloadwidget import Ui_DownloadWidget
from utils import configUtils
from mergethread import PartMergeTask
from utils.media_merge import cleanup_merged_sources, file_identity
from utils.taskpaths import task_directory, task_file


class DownloadWidget(QtWidgets.QWidget):
    def __init__(self, parent: QtWidgets.QWidget = ...) -> None:
        super().__init__(parent)
        self.ui = Ui_DownloadWidget()
        self.ui.setupUi(self)

        self.ui.listWidget.verticalScrollBar().setSingleStep(10)
        self.max_thread_count = configUtils.getUserData(
            configUtils.Configs.MAX_THREAD_COUNT, 4
        )

        self.tasks = []
        self.finished = []
        self.running_tasks = []
        self.paused_tasks = []
        self.all_tasks = []
        self.pending_cleanups = []
        self.retained_paths = {}
        self._temporary_files_cleaned = False
        self._cleanup_attempts = 0

        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.timer_timeout)
        self.timer.start()

        self.ui.button_clean.clicked.connect(self.on_clean_button_clicked)

    def push_task(self, task: dict):
        task["state"] = "queued"
        task["parent"] = self
        task["pending_action"] = None
        task["tempName"] = "{}_{}".format(task["name"], uuid.uuid4().hex)
        task["widget"] = DownloadItem(self)
        item = QtWidgets.QListWidgetItem()
        item.setSizeHint(QtCore.QSize(0, 106))
        task["item"] = item
        self.all_tasks.append(task)
        self.tasks.insert(0, task)
        self.ui.listWidget.addItem(item)
        self.ui.listWidget.setItemWidget(item, task["widget"])
        task["widget"].setup_info(task)
        task["widget"].pause_requested.connect(
            lambda task=task: self.pause_task(task)
        )
        task["widget"].resume_requested.connect(
            lambda task=task: self.resume_task(task)
        )
        task["widget"].restart_requested.connect(
            lambda task=task: self.restart_task(task)
        )
        return task

    def _start_task(self, task):
        task["started_at"] = time.monotonic()
        thread = PartMergeTask(self) if task.get("kind") == "merge" else DownloadTask(self)
        task["thread"] = thread
        task["pending_action"] = None
        thread.setup(task)
        self.running_tasks.append(task)
        task["widget"].set_task_state("running")
        thread.update_progress.connect(task["widget"].update_progress)
        thread.update_status.connect(task["widget"].update_status)
        thread.update_finished.connect(task["widget"].update_finished)
        thread.enable_restart.connect(task["widget"].enable_button)
        thread.pause_available.connect(task["widget"].set_pause_available)
        thread.actions_available.connect(task["widget"].set_actions_available)
        thread.finished.connect(self._on_thread_finished)
        thread.start()

    @QtCore.Slot()
    def _on_thread_finished(self):
        thread = self.sender()
        if not isinstance(thread, DownloadTask):
            return
        task = thread.task
        if task.get("thread") is not thread:
            thread.deleteLater()
            return
        if task in self.running_tasks:
            self.running_tasks.remove(task)

        pending_action = task.get("pending_action")
        result = thread.result
        task["finished_at"] = time.monotonic()
        thread.deleteLater()

        if pending_action == "restart":
            task["pending_action"] = None
            if self._remove_partial_files(task):
                task["widget"].update_progress(0, 0)
                task["widget"].set_task_state("queued")
                self.tasks.append(task)
            else:
                task["widget"].update_status("重新开始失败：无法删除临时文件")
                task["widget"].set_task_state("failed")
        elif result == RESULT_PAUSED:
            task["widget"].set_task_state("paused")
            if task not in self.paused_tasks:
                self.paused_tasks.append(task)
        elif result == RESULT_COMPLETED:
            task["widget"].set_task_state("completed")
            if task not in self.finished:
                self.finished.append(task)
            if task.get("kind") == "merge":
                if task.get("delete_after_merge", False):
                    self.pending_cleanups.append(task)
                else:
                    task["widget"].update_status("合并完成，保留分P")
            elif not task.get("delete_after_merge") and not task.get("onlyAudio"):
                try:
                    path = task_file(task).resolve()
                    self.retained_paths[os.path.normcase(str(path))] = file_identity(path)
                except OSError:
                    pass
        elif result in (RESULT_FAILED, RESULT_CANCELLED):
            task["widget"].set_task_state("failed")
        self._cleanup_finished_merges()
        if self.timer.isActive():
            self.timer_timeout()

    def _cleanup_finished_merges(self):
        for task in list(self.pending_cleanups):
            result = task.get("merge_result", {})
            protected = set()
            for other in self.all_tasks:
                if other is task or other.get("state") == "completed":
                    continue
                inputs = other.get("dependencies", []) if other.get("kind") == "merge" else [other]
                protected.update(str(task_file(part).resolve()) for part in inputs)
            source_files = result.get("source_files", [])
            retained = [item["path"] for item in source_files
                        if self.retained_paths.get(os.path.normcase(str(Path(item["path"]).resolve()))) == item["identity"]]
            candidates = [item for item in source_files if item["path"] not in retained]
            cleanup = cleanup_merged_sources({**result, "source_files": candidates}, protected)
            previous = task.get("cleanup_result", {})
            cleanup["deleted"] = list(dict.fromkeys(previous.get("deleted", []) + cleanup["deleted"]))
            cleanup["retained"] = list(dict.fromkeys(retained + cleanup["retained"]))
            task["cleanup_result"] = cleanup
            deleted = {os.path.normcase(str(Path(path).resolve())) for path in cleanup["deleted"]}
            for part in self.all_tasks:
                if part.get("kind") != "merge" and os.path.normcase(str(task_file(part).resolve())) in deleted:
                    part["source_deleted"] = True
                    if part.get("widget"):
                        part["widget"].update_status("已合并，分P已清理")
            widget = task.get("widget")
            if cleanup["pending"]:
                if widget:
                    widget.update_status("合并完成，部分分P等待其他任务结束后清理")
                continue
            self.pending_cleanups.remove(task)
            task["cleanup_finished_at"] = time.monotonic()
            if widget:
                if cleanup["retained"] or cleanup["errors"]:
                    widget.update_status("合并完成，部分分P已保留（文件变化、占用或其他任务需要）")
                    widget.setToolTip("\n".join(cleanup["errors"]))
                else:
                    widget.update_status("合并完成，分P已清理")

    def pause_task(self, task):
        state = task["state"]
        if state == "queued":
            if task in self.tasks:
                self.tasks.remove(task)
            if task not in self.paused_tasks:
                self.paused_tasks.append(task)
            task["widget"].set_task_state("paused")
        elif state == "running":
            if not task["thread"].request_pause():
                task["widget"].set_actions_available(False)
                return
            task["widget"].set_task_state("pausing")

    def resume_task(self, task):
        if task["state"] != "paused":
            return
        if task in self.paused_tasks:
            self.paused_tasks.remove(task)
        task["widget"].set_task_state("queued")
        # Resumed tasks run before tasks that have not started yet.
        self.tasks.append(task)

    def restart_task(self, task):
        state = task["state"]
        if state in ("running", "pausing"):
            if not task["thread"].request_cancel():
                task["widget"].set_actions_available(False)
                return
            task["pending_action"] = "restart"
            task["widget"].set_task_state("restarting")
            return

        if task in self.tasks:
            self.tasks.remove(task)
        if task in self.paused_tasks:
            self.paused_tasks.remove(task)
        if task in self.finished:
            self.finished.remove(task)
        if not self._remove_partial_files(task):
            task["widget"].update_status("重新开始失败：无法删除临时文件")
            task["widget"].set_task_state("failed")
            return
        task["widget"].update_progress(0, 0)
        task["widget"].set_task_state("queued")
        self.tasks.append(task)

    @staticmethod
    def _remove_partial_files(task):
        root_dir = QtCore.QDir(str(task_directory(task)))
        if not root_dir.exists():
            task["streamStates"] = {}
            task.pop("resolvedMedia", None)
            return True
        all_removed = True
        names = (
            "{}_temp.mp4",
            "{}_temp.m4a",
            "{}_temp.flac",
            "{}_temp.ass",
            "{}_merge.mp4",
        )
        for name in names:
            file_name = name.format(task["tempName"])
            if root_dir.exists(file_name) and not root_dir.remove(file_name):
                all_removed = False
        if all_removed:
            task["streamStates"] = {}
            task.pop("resolvedMedia", None)
        return all_removed

    @staticmethod
    def _task_key(task):
        path = str(task_file(task, suffix=""))
        return os.path.normcase(os.path.abspath(path))

    @classmethod
    def _task_keys(cls, task):
        # A running merge also reads its input files. Prevent another queued
        # download of the same part from replacing them during concatenation.
        return {cls._task_key(task)} | {cls._task_key(part) for part in task.get("dependencies", [])}

    @QtCore.Slot()
    def on_clean_button_clicked(self):
        for task in self.finished:
            self.ui.listWidget.takeItem(self.ui.listWidget.row(task["item"]))
            if task in self.all_tasks:
                self.all_tasks.remove(task)
            task.pop("item", None)
            task.pop("widget", None)
            task.pop("thread", None)
        self.finished = []

    def update_tab_changes(self, old, now):
        pass

    def shutdown(self):
        self.timer.stop()
        threads = [task.get("thread") for task in self.running_tasks]
        for thread in threads:
            if thread is not None:
                thread.request_cancel()
        if any(
            thread is not None and thread.isRunning() for thread in threads
        ):
            return False
        if not self._temporary_files_cleaned:
            all_removed = True
            for task in self.all_tasks:
                if not self._remove_partial_files(task):
                    all_removed = False
                    widget = task.get("widget")
                    if widget is not None:
                        widget.update_status("退出时无法删除部分临时文件")
            self._temporary_files_cleaned = all_removed
            if not all_removed:
                self._cleanup_attempts += 1
                return self._cleanup_attempts >= 3
        return True

    @QtCore.Slot()
    def timer_timeout(self):
        self._cleanup_finished_merges()
        if not self.tasks:
            return
        active_keys = set().union(*(self._task_keys(task) for task in self.running_tasks))
        # Merges have their own worker slot. Downloads fill their configured
        # slots below, independently of video boundaries and merge progress.
        merge_running = any(task.get("kind") == "merge" for task in self.running_tasks)
        for index in range(len(self.tasks) - 1, -1, -1):
            task = self.tasks[index]
            if task.get("kind") != "merge":
                continue
            dependencies = task.get("dependencies", [])
            if any(part["state"] != "completed" for part in dependencies):
                failed = any(part["state"] == "failed" for part in dependencies)
                task["widget"].update_status("等待分P完成（有失败项，请重试）" if failed else "等待所选分P下载完成")
                continue
            if not merge_running and not self._task_keys(task) & active_keys:
                self._start_task(self.tasks.pop(index))
                active_keys.update(self._task_keys(task))
                break
        while sum(task.get("kind") != "merge" for task in self.running_tasks) < max(1, self.max_thread_count):
            candidate = None
            for index in range(len(self.tasks) - 1, -1, -1):
                task = self.tasks[index]
                if task.get("kind") == "merge":
                    continue
                if not self._task_keys(task) & active_keys:
                    candidate = index
                    break
            if candidate is None:
                break
            task = self.tasks.pop(candidate)
            self._start_task(task)
            active_keys.update(self._task_keys(task))
