import copy
import pickle
from types import SimpleNamespace

import pytest
from PySide6 import QtCore, QtWidgets

from Lib.bili_api import collection
from Lib.bili_api.utils.matchFomat import matchAll
from downloadplan import build_download_tasks
from selectionwidget import SelectionWidget
from downloadwidget import DownloadWidget

BV1 = "BV1deMx6WEzr"
BV2 = "BV1woMY6XEUM"
URL = "https://space.bilibili.com/3493275574995772/lists/8530021?type=season"


def view(bvid, count=3):
    return {"bvid": bvid, "title": "同名视频", "owner": {"mid": 123},
            "pages": [{"page": index, "cid": index + (100 if bvid == BV1 else 200),
                       "part": str(index), "duration": 1} for index in range(1, count + 1)]}


@pytest.mark.parametrize("url,kind", [
    (URL, "season"),
    ("https://space.bilibili.com/123/lists/456?type=series", "series"),
    ("https://space.bilibili.com/123/channel/collectiondetail?sid=456", "season"),
    ("https://space.bilibili.com/123/channel/seriesdetail?sid=456", "series"),
    ("https://space.bilibili.com/123/favlist?fid=456&ftype=create", "favorites"),
    ("https://www.bilibili.com/list/ml456", "favorites"),
    ("https://www.bilibili.com/medialist/detail/ml456", "favorites"),
    ("https://www.bilibili.com/list/123?sid=456&bvid=" + BV1, "series"),
])
def test_url_types_take_priority_over_embedded_video(url, kind):
    assert collection.parse_playlist_url(url)["kind"] == kind
    assert matchAll(url) == "LIST"


def test_arbitrary_hosts_are_not_playlists():
    assert collection.parse_playlist_url("https://evil.example/123/lists/456?type=season") is None


def test_full_pagination_and_order(monkeypatch):
    calls = []
    def api(_path, query, _passport):
        page = query["page_num"]
        calls.append(page)
        start = (page - 1) * 30
        return {"archives": [{"bvid": "BV{:010d}".format(index)} for index in range(start, min(start + 30, 82))],
                "page": {"total": 82}, "meta": {"name": "合集"}}
    monkeypatch.setattr(collection, "_api", api)
    _title, entries = collection._list_entries(collection.parse_playlist_url(URL))
    assert calls == [1, 2, 3]
    assert len(entries) == 82
    assert entries[-1]["bvid"] == "BV0000000081"


def test_incomplete_pagination_is_not_silently_accepted(monkeypatch):
    monkeypatch.setattr(collection, "_api", lambda *_args: {"archives": [{"bvid": BV1}], "page": {"total": 82}})
    with pytest.raises(Exception, match="分页"):
        collection._list_entries(collection.parse_playlist_url(URL))


def test_video_detects_collection_and_preserves_all_pages(monkeypatch):
    first, second = view(BV1), view(BV2, 2)
    first["ugc_season"] = {"id": 8530021, "mid": 123,
                           "sections": [{"episodes": [{**second, "arc": {"videos": 2, "title": "第二话"}}]}]}
    monkeypatch.setattr(collection, "_view", lambda **_kwargs: first)
    monkeypatch.setattr(collection, "_list_entries", lambda *_args: ("合集", [{"bvid": BV1}, {"bvid": BV2}]))
    tree = collection.resolve_tree("https://www.bilibili.com/video/" + BV1 + "?p=2")
    assert [group["key"] for group in tree["videos"]] == [BV1, BV2]
    assert len(tree["page_data"]) == 5
    assert tree["current_bvid"] == BV1
    assert tree["default_all"] is False
    assert [page["page"] for page in tree["videos"][1]["pages"]] == [1, 2]


def test_plain_video_is_usable_if_collection_api_fails(monkeypatch):
    first = view(BV1)
    first["ugc_season"] = {"id": 8530021, "mid": 123}
    monkeypatch.setattr(collection, "_view", lambda **_kwargs: first)
    def failure(*_args):
        raise OSError("offline")
    monkeypatch.setattr(collection, "_list_entries", failure)
    tree = collection.resolve_tree(BV1)
    assert len(tree["page_data"]) == 3
    assert tree["warnings"]


def tree_data(default_all=False):
    groups = [collection._make_video(view(BV1), 1, BV1), collection._make_video(view(BV2, 2), 2, BV1)]
    return {"title": "合集", "videos": groups, "page_data": [page for group in groups for page in group["pages"]],
            "current_bvid": BV1, "default_all": default_all}


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_tree_tristate_individual_selection_and_per_video_merge(app, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    owner = QtWidgets.QWidget()
    owner.input_pages = [None, SimpleNamespace(meta=QtCore.QByteArray(pickle.dumps(tree_data())))]
    widget = SelectionWidget(owner)
    widget.data_update(False)
    assert sum(page["selected"] for page in widget.data["page_data"]) == 3
    widget.video_items[BV2].setCheckState(0, QtCore.Qt.CheckState.Checked)
    assert sum(page["selected"] for page in widget.data["page_data"]) == 5
    widget.data["videos"][0]["pages"][1]["tree_item"].setCheckState(0, QtCore.Qt.CheckState.Unchecked)
    assert widget.video_items[BV1].checkState(0) == QtCore.Qt.CheckState.PartiallyChecked
    widget.merge_boxes[BV1].setChecked(True)
    assert widget.data["merge_videos"] == [BV1]
    widget.select_all(False)
    assert not widget.ui.button_next.isEnabled()
    assert widget.data["merge_videos"] == []
    widget.select_current()
    assert sum(page["selected"] for page in widget.data["page_data"]) == 3
    widget.close()


def test_task_plan_never_merges_across_videos_and_sorts_parts():
    tree = tree_data()
    pages = [copy.deepcopy(tree["videos"][0]["pages"][2]), copy.deepcopy(tree["videos"][1]["pages"][0]),
             copy.deepcopy(tree["videos"][0]["pages"][0])]
    tasks = build_download_tasks(pages, {"path": "download", "quality": 16}, [BV1, BV2])
    merges = [task for task in tasks if task["kind"] == "merge"]
    assert len(merges) == 1
    assert [part["page"] for part in merges[0]["dependencies"]] == [1, 3]
    assert {part["video_key"] for part in merges[0]["dependencies"]} == {BV1}
    first = next(task for task in tasks if task["video_key"] == BV1)
    second = next(task for task in tasks if task["video_key"] == BV2)
    assert first["directory"] != second["directory"]  # Same title, distinct BV output folders.
    assert [(task["kind"], task["video_key"]) for task in tasks] == [
        ("part", BV1), ("part", BV1), ("merge", BV1), ("part", BV2)]
    pages[0]["onlyAudio"] = True
    with pytest.raises(ValueError, match="仅下载音频"):
        build_download_tasks(pages, {"path": "download"}, [BV1])


def test_scheduler_waits_for_success_and_locks_merge_inputs(app, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    widget = DownloadWidget(None)
    widget.timer.stop()
    first = {"path": str(tmp_path), "title": "video", "name": "P1", "state": "failed"}
    messages = []
    merge = {"kind": "merge", "path": str(tmp_path), "title": "video", "name": "merged", "dependencies": [first],
             "widget": SimpleNamespace(update_status=messages.append)}
    widget.tasks = [merge]
    started = []
    monkeypatch.setattr(widget, "_start_task", started.append)
    widget.timer_timeout()
    assert not started and "失败" in messages[-1]
    first["state"] = "completed"
    widget.timer_timeout()
    assert started == [merge]
    started.clear()
    widget.running_tasks = [merge]
    overlapping = {"path": str(tmp_path), "title": "video", "name": "P1"}
    unrelated = {"path": str(tmp_path), "title": "other", "name": "P1"}
    widget.tasks = [unrelated, overlapping]
    widget.timer_timeout()
    assert started == [unrelated]
    assert widget.tasks == [overlapping]
    widget.close()


@pytest.mark.parametrize("layout", ["per_video", "shared"])
@pytest.mark.parametrize("delete", [True, False])
def test_output_layout_avoids_collisions_and_cleanup_is_explicit(layout, delete, tmp_path):
    from utils.taskpaths import task_directory, task_file
    tree = tree_data()
    tasks = build_download_tasks(tree["page_data"], {"path": str(tmp_path), "output_layout": layout,
                                                   "delete_after_merge": delete}, [BV1, BV2])
    assert len({str(task_file(task)) for task in tasks}) == len(tasks)
    merges = [task for task in tasks if task["kind"] == "merge"]
    assert len(merges) == 2 and all(task["delete_after_merge"] == delete for task in merges)
    if layout == "shared":
        assert {task_directory(task) for task in tasks} == {tmp_path}
        assert all(task["video_key"] in task["name"] for task in tasks)
    else:
        assert len({task_directory(task) for task in tasks}) == 2


def test_storage_and_cleanup_preferences_persist(app, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    owner = QtWidgets.QWidget()
    owner.input_pages = [None, SimpleNamespace(meta=QtCore.QByteArray(pickle.dumps(tree_data())))]
    widget = SelectionWidget(owner)
    widget.data_update(False)
    widget.output_layout.setCurrentIndex(widget.output_layout.findData("shared"))
    widget.merge_boxes[BV1].setChecked(True)
    assert widget.delete_parts.isEnabled()
    widget.delete_parts.setChecked(False)
    widget.ui.button_next.click()
    another = SelectionWidget(owner)
    another.data_update(False)
    assert another.data["output_layout"] == "shared"
    assert another.data["delete_after_merge"] is False
    owner.close()


def test_settings_save_does_not_overwrite_new_storage_preferences(app, monkeypatch, tmp_path):
    from settingswidget import SettingsWidget
    from utils import configUtils
    monkeypatch.chdir(tmp_path)
    settings = SettingsWidget(None)
    settings.load_settings()
    configUtils.setUserData(configUtils.Configs.OUTPUT_LAYOUT, "shared")
    configUtils.setUserData(configUtils.Configs.DELETE_PARTS_AFTER_MERGE, False)
    settings.ui.spin_threads.setValue(2)
    settings.save_settings()
    assert configUtils.getUserData(configUtils.Configs.OUTPUT_LAYOUT) == "shared"
    assert configUtils.getUserData(configUtils.Configs.DELETE_PARTS_AFTER_MERGE) is False
    settings.close()


def scheduler_plan(tmp_path):
    tasks = build_download_tasks(tree_data()["page_data"], {"path": str(tmp_path)}, [BV1, BV2])
    for task in tasks:
        task["state"] = "queued"
        task["widget"] = SimpleNamespace(update_status=lambda _text: None)
    return tasks


def test_ready_merge_has_priority_with_full_download_slots(app, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    widget = DownloadWidget(None)
    widget.timer.stop()
    tasks = scheduler_plan(tmp_path)
    first_merge = next(task for task in tasks if task["kind"] == "merge")
    for part in first_merge["dependencies"]:
        part["state"] = "completed"
    later_parts = [task for task in tasks if task["video_key"] == BV2]
    widget.all_tasks = tasks
    # Put later downloads ahead of the ready merge to reproduce the old bug.
    widget.tasks = list(reversed(later_parts + [first_merge]))
    widget.max_thread_count = 1
    widget.running_tasks = [{"kind": "part", "path": str(tmp_path), "title": "unrelated", "name": "active"}]
    started = []
    def start(task):
        task["state"] = "running"
        widget.running_tasks.append(task)
        started.append(task)
    monkeypatch.setattr(widget, "_start_task", start)
    widget.timer_timeout()
    assert started == [first_merge]
    assert all(part["state"] == "queued" for part in later_parts)
    widget.close()


def test_downloads_continue_while_merge_runs_and_next_merge_waits(app, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    widget = DownloadWidget(None)
    widget.timer.stop()
    tasks = scheduler_plan(tmp_path)
    widget.all_tasks = tasks
    widget.tasks = list(reversed(tasks))
    widget.max_thread_count = 1
    started = []
    def start(task):
        task["state"] = "running"
        widget.running_tasks.append(task)
        started.append(task)
    monkeypatch.setattr(widget, "_start_task", start)
    first_merge, second_merge = [task for task in tasks if task["kind"] == "merge"]
    for expected in first_merge["dependencies"]:
        widget.timer_timeout()
        assert started[-1] is expected
        expected["state"] = "completed"
        widget.running_tasks.remove(expected)
    widget.timer_timeout()
    assert widget.running_tasks == [first_merge, second_merge["dependencies"][0]]
    # Keep the first merge running while every remaining download finishes.
    for part in second_merge["dependencies"]:
        assert widget.running_tasks == [first_merge, part]
        part["state"] = "completed"
        widget.running_tasks.remove(part)
        widget.timer_timeout()
    assert widget.running_tasks == [first_merge]
    assert widget.tasks == [second_merge]
    first_merge["state"] = "completed"
    widget.running_tasks.remove(first_merge)
    widget.timer_timeout()
    assert widget.running_tasks == [second_merge]
    widget.close()


@pytest.mark.parametrize("slots", [1, 2, 4])
def test_download_slots_fill_across_video_boundaries(app, monkeypatch, tmp_path, slots):
    monkeypatch.chdir(tmp_path)
    widget = DownloadWidget(None)
    widget.timer.stop()
    tasks = scheduler_plan(tmp_path)
    widget.all_tasks = tasks
    widget.tasks = list(reversed(tasks))
    widget.max_thread_count = slots
    def start(task):
        task["state"] = "running"
        widget.running_tasks.append(task)
    monkeypatch.setattr(widget, "_start_task", start)
    parts = [task for task in tasks if task["kind"] == "part"]
    widget.timer_timeout()
    assert widget.running_tasks == parts[:slots]
    # As soon as a slot frees, start the next part even if earlier parts of
    # the preceding video are still downloading and its merge is not ready.
    completed = widget.running_tasks.pop(0)
    completed["state"] = "completed"
    widget.timer_timeout()
    assert widget.running_tasks == parts[1:slots + 1]
    assert len(widget.running_tasks) == slots
    if slots >= 2:
        if slots == 2:
            # Finish A's third part first while its second part stays active.
            widget.running_tasks.remove(parts[2])
            parts[2]["state"] = "completed"
            widget.timer_timeout()
        assert {task["video_key"] for task in widget.running_tasks} == {BV1, BV2}
        assert any(part["state"] == "running" for part in parts if part["video_key"] == BV1)
    widget.close()


@pytest.mark.parametrize("state", ["queued", "paused", "failed"])
def test_waiting_paused_or_failed_video_does_not_block_other_downloads(app, monkeypatch, tmp_path, state):
    monkeypatch.chdir(tmp_path)
    widget = DownloadWidget(None)
    widget.timer.stop()
    tasks = scheduler_plan(tmp_path)
    widget.all_tasks = tasks
    first_merge, second_merge = [task for task in tasks if task["kind"] == "merge"]
    first_merge["state"] = state
    first_merge["dependencies"][0]["state"] = "paused"
    # The earlier merge is either waiting on a paused input, paused itself,
    # or failed. None of these states may hold back an unrelated video.
    pending = [first_merge] if state == "queued" else []
    widget.tasks = list(reversed(pending + second_merge["dependencies"]))
    widget.max_thread_count = 2
    def start(task):
        task["state"] = "running"
        widget.running_tasks.append(task)
    monkeypatch.setattr(widget, "_start_task", start)
    widget.timer_timeout()
    assert widget.running_tasks == second_merge["dependencies"]
    widget.close()


def test_later_video_can_merge_before_earlier_video_is_ready(app, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    widget = DownloadWidget(None)
    widget.timer.stop()
    tasks = scheduler_plan(tmp_path)
    widget.all_tasks = tasks
    first_merge, second_merge = [task for task in tasks if task["kind"] == "merge"]
    for part in second_merge["dependencies"]:
        part["state"] = "completed"
    widget.tasks = [second_merge, first_merge]
    widget.max_thread_count = 1
    widget.running_tasks = [first_merge["dependencies"][0]]
    def start(task):
        task["state"] = "running"
        widget.running_tasks.append(task)
    monkeypatch.setattr(widget, "_start_task", start)
    widget.timer_timeout()
    assert widget.running_tasks == [first_merge["dependencies"][0], second_merge]
    assert widget.tasks == [first_merge]
    widget.close()
