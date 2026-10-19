"""Build part tasks and per-video dependent merge tasks without UI state."""
import hashlib
import re
from collections import OrderedDict

from utils.removeSpecialChars import removeSpecialChars


def build_download_tasks(pages, options, merge_videos=(), merge_mode="auto"):
    groups = OrderedDict()
    tasks = []
    for page in pages:
        key = str(page.get("video_key", page["id"]))
        def clean(value):
            value = re.sub(r"[\x00-\x1f\x7f]", "_", str(value))
            return removeSpecialChars(value, None if options.get("disable_title_limit") else 20).rstrip(" .") or "_"
        part = {key_: value for key_, value in options.items() if key_ != "disable_title_limit"}
        part.update({key_: page[key_] for key_ in ("id", "isbvid", "cid", "type")})
        part.update({"name": "{:03d}-{}".format(page["page"], clean(page["name"])),
                     "title": "{} [{}]".format(clean(page["title"]), clean(key)),
                     "video_key": key, "page": page["page"], "kind": "part",
                     "saveDanmaku": page.get("saveDanmaku", False),
                     "onlyAudio": page.get("onlyAudio", False)})
        tasks.append(part)
        groups.setdefault(key, []).append(part)
    for key, parts in groups.items():
        if key not in merge_videos or len(parts) < 2:
            continue
        if any(part["onlyAudio"] for part in parts):
            raise ValueError("合并分P的视频不能选择“仅下载音频”")
        parts.sort(key=lambda part: part["page"])
        numbers = "_".join("{:03d}".format(part["page"]) for part in parts)
        if len(numbers) > 60:
            digest = hashlib.sha256(numbers.encode("ascii")).hexdigest()[:8]
            numbers = "{:03d}-{:03d}_{}P_{}".format(parts[0]["page"], parts[-1]["page"], len(parts), digest)
        tasks.append({"kind": "merge", "path": parts[0]["path"], "title": parts[0]["title"],
                      "name": "合并分P_" + numbers, "video_key": key,
                      "dependencies": parts, "merge_mode": merge_mode})
    return tasks
