"""One directory definition for download, merge, cleanup, locking and Open."""
from pathlib import Path


def task_directory(task):
    directory = task.get("directory", task["title"])
    return Path(task["path"]) / directory if directory else Path(task["path"])


def task_file(task, suffix=".mp4"):
    return task_directory(task) / (task["name"] + suffix)
