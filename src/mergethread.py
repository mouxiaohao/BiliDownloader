import os
from pathlib import Path
import subprocess
import sys

from downloadthread import DownloadTask
from utils.media_merge import merge_parts


class PartMergeTask(DownloadTask):
    def _run_process(self, command):
        kwargs = {"stdout": subprocess.PIPE, "stderr": subprocess.PIPE}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        self._check_control()
        process = subprocess.Popen(command, **kwargs)
        with self._process_lock:
            self._process = process
        try:
            while True:
                self._check_control()
                try:
                    stdout, stderr = process.communicate(timeout=0.2)
                    break
                except subprocess.TimeoutExpired:
                    continue
            self._check_control()
            if process.returncode:
                raise RuntimeError(stderr.decode("utf-8", errors="replace")[-1800:])
            return stdout
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.communicate(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate()
            with self._process_lock:
                self._process = None

    def _run_download(self):
        dependencies = self.task["dependencies"]
        if any(part["state"] != "completed" for part in dependencies):
            raise RuntimeError("分P下载未全部成功，暂不合并")
        keys = {part.get("video_key") for part in dependencies}
        if len(keys) != 1 or self.task["video_key"] not in keys:
            raise RuntimeError("禁止将不同视频的分P拼接在一起")
        ordered = sorted(dependencies, key=lambda part: part["page"])
        paths = [Path(part["path"]) / part["title"] / (part["name"] + ".mp4") for part in ordered]
        output = Path(self.task["path"]) / self.task["title"] / (self.task["name"] + ".mp4")
        self.total_size = sum(os.path.getsize(path) for path in paths)
        self._emit_progress(force=True)
        self.enable_restart.emit()
        self.pause_available.emit(True)
        self.task["merge_result"] = merge_parts(paths, output, self.task.get("merge_mode", "auto"),
                                                self._run_process, self.update_status.emit)
        self.video_finished_size = self.total_size
