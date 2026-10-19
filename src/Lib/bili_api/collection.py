"""Resolve a Bilibili video or playlist to videos and their individual pages.

Only videos belonging to an explicit collection/series/favorites list are used;
recommendations and unrelated videos are never treated as a playlist.
"""
import re
from urllib.parse import parse_qs, urlsplit

from .exceptions import NetWorkException
from .utils import network


def parse_playlist_url(text):
    text = text.strip().replace("\\&", "&")
    if not re.match(r"^https?://", text, re.I):
        return None
    url = urlsplit(text)
    host = (url.hostname or "").lower()
    if host not in ("space.bilibili.com", "www.bilibili.com", "bilibili.com"):
        return None
    query = parse_qs(url.query)
    value = lambda key: query.get(key, [""])[0]
    path = url.path.rstrip("/")
    match = re.fullmatch(r"/(\d+)/lists/(\d+)", path)
    if host == "space.bilibili.com" and match:
        kind = "series" if value("type") == "series" else "season"
        return {"kind": kind, "mid": match[1], "id": match[2]}
    match = re.fullmatch(r"/(\d+)/channel/(collectiondetail|seriesdetail)", path)
    if host == "space.bilibili.com" and match and value("sid").isdigit():
        return {"kind": "season" if match[2] == "collectiondetail" else "series",
                "mid": match[1], "id": value("sid")}
    if host == "space.bilibili.com" and re.fullmatch(r"/\d+/favlist", path) and value("fid").isdigit():
        return {"kind": "favorites", "id": value("fid")}
    match = re.fullmatch(r"/(?:medialist/(?:play|detail)|list)/ml(\d+)", path)
    if match:
        return {"kind": "favorites", "id": match[1]}
    match = re.fullmatch(r"/(?:list|medialist/play)/(\d+)", path)
    if match:
        if value("business") == "space_collection" and value("business_id").isdigit():
            return {"kind": "season", "mid": match[1], "id": value("business_id")}
        sid = value("sid") or value("business_id")
        if sid.isdigit():
            return {"kind": "series", "mid": match[1], "id": sid}
    return None


def _api(path, params, passport=None):
    headers = {"Referer": "https://www.bilibili.com/", "User-Agent": "Mozilla/5.0"}
    if passport is not None:
        headers["Cookie"] = passport.get_cookie()
    response = network.get_data("https", "api.bilibili.com", "GET", path,
                                query=params, header=headers)
    if response.get("code") != 0:
        raise NetWorkException("列表/视频解析失败：{}（{}）".format(
            response.get("message", "未知错误"), response.get("code")))
    data = response.get("data")
    if not isinstance(data, dict):
        raise NetWorkException("列表/视频接口未返回有效数据")
    return data


def _view(bvid=None, aid=None, passport=None):
    return _api("/x/web-interface/view", {"bvid": bvid} if bvid else {"aid": aid}, passport)


def _list_entries(spec, passport=None, progress=None):
    entries, seen = [], set()
    title = "播放列表"
    page = 1
    while True:
        if progress:
            progress("正在读取列表第 {} 页…".format(page))
        if spec["kind"] == "season":
            data = _api("/x/polymer/web-space/seasons_archives_list",
                        {"mid": spec["mid"], "season_id": spec["id"],
                         "page_num": page, "page_size": 30, "sort_reverse": "false"}, passport)
            batch = data.get("archives") or []
            title = data.get("meta", {}).get("name") or data.get("meta", {}).get("title") or title
            total = data.get("page", {}).get("total", 0)
            has_more = page * 30 < total
        elif spec["kind"] == "series":
            if page == 1:
                meta = _api("/x/series/series", {"series_id": spec["id"]}, passport)
                title = meta.get("meta", {}).get("name") or title
            data = _api("/x/series/archives", {"mid": spec["mid"], "series_id": spec["id"],
                                              "pn": page, "ps": 30, "sort": "asc"}, passport)
            batch = data.get("archives") or []
            total = data.get("page", {}).get("total", 0)
            has_more = page * 30 < total
        else:
            data = _api("/x/v3/fav/resource/list", {"media_id": spec["id"],
                                                   "pn": page, "ps": 20, "order": "mtime"}, passport)
            title = data.get("info", {}).get("title") or title
            batch = data.get("medias") or []
            total = data.get("info", {}).get("media_count", 0)
            has_more = bool(data.get("has_more"))
        added = 0
        for entry in batch:
            bvid = entry.get("bvid") or entry.get("bv_id")
            if bvid and bvid not in seen:
                seen.add(bvid)
                entries.append({**entry, "bvid": bvid})
                added += 1
        if not has_more:
            if total and len(entries) < total:
                raise NetWorkException("列表未读取完整：已获取 {} / {} 个视频，请重试".format(len(entries), total))
            break
        if not batch or not added:
            raise NetWorkException("列表分页未继续返回视频，请稍后重试")
        page += 1
    return title, entries


def _embedded_videos(info):
    result = {}
    for section in (info.get("ugc_season") or {}).get("sections") or []:
        for episode in section.get("episodes") or []:
            bvid = episode.get("bvid")
            pages = episode.get("pages")
            arc = episode.get("arc") or {}
            if bvid and pages and len(pages) >= int(arc.get("videos") or 1):
                result[bvid] = {**arc, "bvid": bvid, "pages": pages,
                                "title": arc.get("title") or episode.get("title"),
                                "pic": arc.get("pic")}
    return result


def _make_video(info, index, current_bvid):
    bvid = info["bvid"]
    title = info.get("title") or bvid
    pages = []
    for number, page in enumerate(info.get("pages") or [], 1):
        pages.append({"isbvid": True, "id": bvid, "video_key": bvid,
                      "cid": page["cid"], "page": int(page.get("page") or number),
                      "name": page.get("part") or "P{}".format(number), "title": title,
                      "duration": page.get("duration", 0), "type": "video"})
    pages.sort(key=lambda item: item["page"])
    if not pages:
        raise NetWorkException("视频没有可下载的分P")
    return {"key": bvid, "title": title, "index": index, "pages": pages,
            "current": bvid == current_bvid, "error": None}


def resolve_tree(text, passport=None, progress=None):
    """Return plain serializable data. Network calls run in the caller's worker."""
    spec = parse_playlist_url(text)
    cache, warnings = {}, []
    current_bvid = None
    cover = None
    if spec is None:
        bv = re.search(r"BV[0-9A-Za-z]{10}", text, re.I)
        av = re.search(r"av(\d+)", text, re.I)
        if not bv and not av:
            raise ValueError("请输入视频 BV/AV 号或支持的合集、系列、收藏夹地址")
        info = _view(bvid=("BV" + bv[0][2:]) if bv else None,
                     aid=av[1] if av and not bv else None, passport=passport)
        current_bvid = info["bvid"]
        cache.update(_embedded_videos(info))
        cache[current_bvid] = info
        cover = info.get("pic")
        season = info.get("ugc_season") or {}
        if season.get("id"):
            spec = {"kind": "season", "mid": str(season.get("mid") or info["owner"]["mid"]),
                    "id": str(season["id"])}
        else:
            group = _make_video(info, 1, current_bvid)
            return {"title": group["title"], "videos": [group], "page_data": group["pages"],
                    "playlist": None, "current_bvid": current_bvid, "cover": cover,
                    "warnings": [], "default_all": True}
    try:
        title, entries = _list_entries(spec, passport, progress)
    except Exception as error:
        if current_bvid is None:
            raise
        # Keep the requested video usable when the optional collection API fails.
        warnings.append("已检测到合集，但读取完整列表失败，可重新解析或直接输入合集地址：{}".format(error))
        entries = [{"bvid": current_bvid}]
        title = cache[current_bvid]["title"]
    if not entries:
        raise NetWorkException("列表为空或没有访问权限，请登录后重试")
    if not cache:
        # A single view response often includes all episodes and their pages.
        # This avoids one request per video for large UGC collections.
        for entry in entries:
            try:
                info = _view(bvid=entry["bvid"], passport=passport)
                cache.update(_embedded_videos(info))
                cache[info["bvid"]] = info
                cover = info.get("pic")
                break
            except Exception as error:
                entry["error"] = str(error)
    groups = []
    for index, entry in enumerate(entries, 1):
        bvid = entry["bvid"]
        if progress:
            progress("正在整理视频及分P：{} / {}".format(index, len(entries)))
        try:
            if entry.get("error"):
                raise NetWorkException(entry["error"])
            info = cache.get(bvid) or _view(bvid=bvid, passport=passport)
            groups.append(_make_video(info, index, current_bvid))
        except Exception as error:
            groups.append({"key": bvid, "title": entry.get("title") or bvid, "index": index,
                           "pages": [], "current": bvid == current_bvid, "error": str(error)})
            warnings.append("{}：{}".format(entry.get("title") or bvid, error))
    if current_bvid and not any(group["key"] == current_bvid for group in groups):
        groups.insert(0, _make_video(cache[current_bvid], 0, current_bvid))
    pages = [page for group in groups for page in group["pages"]]
    if not pages:
        raise NetWorkException("列表中没有可读取分P的视频。" + "；".join(warnings[:3]))
    return {"title": title, "videos": groups, "page_data": pages, "playlist": spec,
            "current_bvid": current_bvid, "cover": cover, "warnings": warnings,
            "default_all": current_bvid is None}
