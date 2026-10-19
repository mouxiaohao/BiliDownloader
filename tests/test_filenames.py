import pytest

from downloadplan import build_download_tasks
from utils.removeSpecialChars import removeSpecialChars
from utils.taskpaths import task_file


@pytest.mark.parametrize("limit", [None, 0, 27, 40])
def test_clean_name_preserves_full_title_unless_an_explicit_limit_is_supplied(limit):
    title = "完整标题保留中文和emoji😀" * 3 + "第二十一话"
    expected = title if not limit else title[:limit]
    assert removeSpecialChars(title, limit) == expected
    assert removeSpecialChars(title) == title
    assert removeSpecialChars("合法标题:含/特殊字符\n") == "合法标题_含_特殊字符_"


@pytest.mark.parametrize("layout", ["per_video", "shared"])
@pytest.mark.parametrize("legacy_limit", [None, False, True])
def test_long_video_and_part_titles_retain_their_distinguishing_ends(tmp_path, layout, legacy_limit):
    title = "完整视频标题超过二十个字符也必须保留末尾的集数_第二十一话"
    part_name = "老外看石纪元S3第二十二话-00_16_20_000-00_18_40_000-seg8"
    pages = [{"id": "BV1fNGK6jE1r", "video_key": "BV1fNGK6jE1r", "isbvid": True,
              "type": "video", "cid": index, "page": index,
              "name": part_name + str(index), "title": title} for index in (8, 9)]
    options = {"path": str(tmp_path), "output_layout": layout}
    if legacy_limit is not None:
        options["disable_title_limit"] = legacy_limit
    tasks = build_download_tasks(pages, options, ["BV1fNGK6jE1r"])
    video_name = title + " [BV1fNGK6jE1r]"
    for part, page in zip(tasks[:-1], pages):
        expected_part = f"{page['page']:03d}-" + page["name"]
        assert part["name"] == (video_name + "__P" if layout == "shared" else "") + expected_part
        assert part["title"] == video_name
        path = task_file(part)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"full-length-filename")
        assert path.read_bytes() == b"full-length-filename"
    assert tasks[-1]["name"] == video_name
    assert len({task_file(task) for task in tasks}) == len(tasks)
