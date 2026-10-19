"""Build part tasks and per-video dependent merge tasks without UI state."""
import hashlib
import re
import uuid
from collections import OrderedDict

from utils.removeSpecialChars import removeSpecialChars


def build_download_tasks(pages, options, merge_videos=(), merge_mode="auto"):
    layout = options.get("output_layout", "per_video")
    if layout not in ("per_video", "shared"):
        raise ValueError("未知的存放方式")
    delete_after_merge = bool(options.get("delete_after_merge", True))
    batch_id = uuid.uuid4().hex
    groups = OrderedDict()
    tasks = []
    for page in pages:
        key = str(page.get("video_key", page["id"]))
        def clean(value):
            value = re.sub(r"[\x00-\x1f\x7f]", "_", str(value))
            return removeSpecialChars(value, None if options.get("disable_title_limit") else 20).rstrip(" .") or "_"
        part = {key_: value for key_, value in options.items()
                if key_ not in ("disable_title_limit", "output_layout", "delete_after_merge")}
        part.update({key_: page[key_] for key_ in ("id", "isbvid", "cid", "type")})
        video_name = "{} [{}]".format(clean(page["title"]), clean(key))
        name = "{:03d}-{}".format(page["page"], clean(page["name"]))
        part.update({"name": video_name + "__P" + name if layout == "shared" else name,
                     "title": video_name, "directory": "" if layout == "shared" else video_name,
                     "video_key": key, "page": page["page"], "kind": "part",
                     "delete_after_merge": False,
                     "saveDanmaku": page.get("saveDanmaku", False),
                     "onlyAudio": page.get("onlyAudio", False)})
        groups.setdefault(key, []).append(part)
    for video_order, (key, parts) in enumerate(groups.items()):
        parts.sort(key=lambda part: part["page"])
        for part in parts:
            part.update({"batch_id": batch_id, "video_order": video_order})
        tasks.extend(parts)
        if key not in merge_videos or len(parts) < 2:
            continue
        if any(part["onlyAudio"] for part in parts):
            raise ValueError("合并分P的视频不能选择“仅下载音频”")
        for part in parts:
            part["delete_after_merge"] = delete_after_merge
        numbers = "_".join("{:03d}".format(part["page"]) for part in parts)
        if len(numbers) > 60:
            digest = hashlib.sha256(numbers.encode("ascii")).hexdigest()[:8]
            numbers = "{:03d}-{:03d}_{}P_{}".format(parts[0]["page"], parts[-1]["page"], len(parts), digest)
        name = "合并分P_" + numbers
        tasks.append({"kind": "merge", "path": parts[0]["path"], "title": parts[0]["title"],
                      "directory": parts[0]["directory"],
                      "name": parts[0]["title"] + "__" + name if layout == "shared" else name,
                      "video_key": key, "dependencies": parts, "merge_mode": merge_mode,
                      "batch_id": batch_id, "video_order": video_order,
                      "delete_after_merge": delete_after_merge})
    return tasks
