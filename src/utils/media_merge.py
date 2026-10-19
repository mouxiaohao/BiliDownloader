"""Verified, ordered concat with lossless fast path and optional normalization."""
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def find_media_tool(name):
    suffix = ".exe" if sys.platform == "win32" else ""
    roots = [Path.cwd() / "ffmpeg", Path(__file__).resolve().parents[2] / "ffmpeg"]
    if getattr(sys, "frozen", False):
        roots.insert(0, Path(sys.executable).parent / "ffmpeg")
        roots.insert(1, Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "ffmpeg")
    for root in roots:
        for filename in (name + suffix, name + ".upx.exe"):
            candidate = root / filename
            if candidate.is_file():
                return str(candidate)
    executable = shutil.which(name)
    if executable:
        return executable
    raise RuntimeError("未找到 {}，请将其放入程序目录的 ffmpeg 文件夹".format(name))


def run_media_process(command):
    kwargs = {"stdout": subprocess.PIPE, "stderr": subprocess.PIPE}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    result = subprocess.run(command, **kwargs)
    if result.returncode:
        raise RuntimeError(result.stderr.decode("utf-8", errors="replace")[-1800:])
    return result.stdout


def probe_media(path, runner=run_media_process):
    data = runner([find_media_tool("ffprobe"), "-v", "error", "-show_streams",
                   "-show_format", "-show_data_hash", "sha256", "-of", "json", str(path)])
    info = json.loads(data)
    duration = float(info.get("format", {}).get("duration") or 0)
    if not math.isfinite(duration) or duration <= 0 or not any(stream.get("codec_type") == "video" for stream in info["streams"]):
        raise RuntimeError("分P文件无有效视频或时长：{}".format(path))
    return info


def _signature(info):
    keys = ("codec_type", "codec_name", "codec_tag_string", "width", "height", "pix_fmt",
            "sample_rate", "channels", "channel_layout", "time_base", "extradata_hash")
    return [tuple(stream.get(key) for key in keys) for stream in info["streams"]
            if stream.get("codec_type") in ("video", "audio")]


def _concat_file(paths, target):
    # The concat demuxer has its own quoting rules (including apostrophes).
    lines = ["ffconcat version 1.0"]
    for path in paths:
        if "\n" in str(path) or "\r" in str(path):
            raise ValueError("分P文件路径不能包含换行符")
        escaped = str(Path(path).resolve()).replace("\\", "/").replace("'", "'\\''")
        lines.append("file '" + escaped + "'")
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")


def file_identity(path):
    info = Path(path).stat()
    return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns]


def cleanup_merged_sources(result, protected_paths=()):
    """Remove exact verified inputs, never scan/delete a folder's other files.

    Protected inputs are still needed by another task; the queue may retry their
    cleanup later. Changed/in-use files are retained and reported to the user.
    """
    answer = {"deleted": [], "pending": [], "retained": [], "errors": []}
    sources = result.get("source_files", [])
    output = Path(result["output"])
    try:
        if not result.get("verified") or file_identity(output) != result.get("output_identity"):
            raise ValueError("合并文件已变化或未通过验证，保留源分P")
    except (OSError, ValueError) as error:
        answer["retained"] = [item["path"] for item in sources]
        answer["errors"].append(str(error))
        return answer
    key = lambda path: os.path.normcase(str(Path(path).resolve()))
    protected = {key(path) for path in protected_paths}
    for item in sources:
        path = Path(item["path"])
        if key(path) in protected:
            answer["pending"].append(str(path))
            continue
        try:
            if path.parent.resolve() != output.parent.resolve() or path.suffix.lower() != ".mp4" or key(path) == key(output):
                raise ValueError("源分P路径不属于本次输出目录，已保留：{}".format(path))
            if not path.exists():
                continue
            if file_identity(path) != item["identity"]:
                raise ValueError("分P文件已变化，已保留：{}".format(path.name))
            path.unlink()
            answer["deleted"].append(str(path))
        except (OSError, ValueError) as error:
            answer["retained"].append(str(path))
            answer["errors"].append(str(error))
    return answer


def merge_parts(paths, output, mode="auto", runner=run_media_process, status=None):
    if len(paths) < 2:
        raise ValueError("合并至少需要两个分P")
    if mode not in ("auto", "copy"):
        raise ValueError("未知的分P合并方式")
    sources = [Path(path).resolve() for path in paths]
    output = Path(output).resolve()
    if output in sources:
        raise ValueError("合并输出不能覆盖输入分P")
    if any(not path.is_file() or not path.stat().st_size for path in sources):
        raise RuntimeError("分P尚未全部下载完成，取消合并")
    source_files = [{"path": str(path), "identity": file_identity(path)} for path in sources]
    if status:
        status("正在核对分P媒体参数")
    infos = [probe_media(path, runner) for path in sources]
    compatible = all(_signature(info) == _signature(infos[0]) for info in infos[1:])
    if not compatible and mode == "copy":
        raise RuntimeError("分P编码、分辨率或音频参数不一致，无法无损合并。原分P已保留，可改用自动转码合并。")
    expected_duration = sum(float(info["format"]["duration"]) for info in infos)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".bili-merge-", dir=output.parent) as temporary:
        directory = Path(temporary)
        inputs = sources
        ffmpeg = find_media_tool("ffmpeg")
        if not compatible:
            first_video = next(stream for stream in infos[0]["streams"] if stream["codec_type"] == "video")
            width = int(first_video["width"]) // 2 * 2
            height = int(first_video["height"]) // 2 * 2
            rate = first_video.get("avg_frame_rate") or "30"
            if rate in ("0/0", "0"):
                rate = "30"
            inputs = []
            for index, (source, info) in enumerate(zip(sources, infos), 1):
                if status:
                    status("参数不同，正在转码分P：{} / {}".format(index, len(sources)))
                normalized = directory / ("{:04d}.mp4".format(index))
                command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", str(source)]
                has_audio = any(stream.get("codec_type") == "audio" for stream in info["streams"])
                if not has_audio:
                    command += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
                command += ["-map", "0:v:0", "-map", "0:a:0" if has_audio else "1:a:0",
                            "-vf", "scale={}:{}:force_original_aspect_ratio=decrease,pad={}:{}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={}".format(width, height, width, height, rate),
                            "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-pix_fmt", "yuv420p",
                            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
                            "-video_track_timescale", "90000", "-t", str(info["format"]["duration"]),
                            "-shortest", str(normalized)]
                runner(command)
                inputs.append(normalized)
        if status:
            status("正在按P号顺序{}合并".format("无损" if compatible else ""))
        manifest = directory / "parts.ffconcat"
        _concat_file(inputs, manifest)
        merged = directory / "merged.mp4"
        runner([ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                "-f", "concat", "-safe", "0", "-i", str(manifest), "-map", "0:v:0",
                "-map", "0:a:0?", "-c", "copy", "-movflags", "+faststart", str(merged)])
        result = probe_media(merged, runner)
        actual = float(result["format"]["duration"])
        if abs(actual - expected_duration) > max(1.0, len(paths) * 0.15):
            raise RuntimeError("合并后时长不符：预期 {:.3f}s，实际 {:.3f}s。原分P已保留。".format(expected_duration, actual))
        expected_audio = any(stream.get("codec_type") == "audio" for info in infos for stream in info["streams"])
        if expected_audio and not any(stream.get("codec_type") == "audio" for stream in result["streams"]):
            raise RuntimeError("合并结果缺少音频，原分P已保留")
        if any(file_identity(item["path"]) != item["identity"] for item in source_files):
            raise RuntimeError("分P在合并过程中发生变化，保留原文件并取消保存")
        os.replace(merged, output)
    return {"output": str(output), "duration": actual, "lossless": compatible,
            "parts": len(sources), "verified": True,
            "source_files": source_files, "output_identity": file_identity(output)}
