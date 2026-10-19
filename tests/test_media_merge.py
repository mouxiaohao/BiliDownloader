from pathlib import Path

import pytest

from utils.media_merge import find_media_tool, merge_parts, probe_media, run_media_process


def make_clip(path, color, size="160x90", frequency=440, audio=True):
    command = [find_media_tool("ffmpeg"), "-v", "error", "-nostdin", "-y", "-f", "lavfi", "-i",
               "color=c={}:s={}:r=25:d=1".format(color, size)]
    if audio:
        command += ["-f", "lavfi", "-i", "sine=frequency={}:sample_rate=48000:duration=1".format(frequency)]
    command += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-t", "1", str(path)]
    run_media_process(command)


def pixel_at(path, time):
    data = run_media_process([find_media_tool("ffmpeg"), "-v", "error", "-nostdin", "-ss", str(time), "-i", str(path),
                              "-frames:v", "1", "-vf", "scale=1:1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"])
    return tuple(data[:3])


def test_lossless_concat_preserves_order_audio_duration_and_apostrophe_path(tmp_path):
    first, second = tmp_path / "P01's 红.mp4", tmp_path / "P03 蓝.mp4"
    make_clip(first, "red")
    make_clip(second, "blue", frequency=880)
    output = tmp_path / "长视频.mp4"
    result = merge_parts([first, second], output)
    assert result["lossless"]
    assert result["duration"] == pytest.approx(2.0, abs=0.15)
    assert {stream["codec_type"] for stream in probe_media(output)["streams"]} >= {"video", "audio"}
    red, blue = pixel_at(output, 0.3), pixel_at(output, 1.3)
    assert red[0] > 200 and red[2] < 40
    assert blue[2] > 200 and blue[0] < 40
    assert first.is_file() and second.is_file()


def test_incompatible_parts_normalize_and_missing_audio_is_filled(tmp_path):
    first, second = tmp_path / "a.mp4", tmp_path / "b.mp4"
    make_clip(first, "red")
    make_clip(second, "blue", size="320x180", audio=False)
    with pytest.raises(RuntimeError, match="参数不一致"):
        merge_parts([first, second], tmp_path / "copy.mp4", mode="copy")
    output = tmp_path / "auto.mp4"
    result = merge_parts([first, second], output)
    assert not result["lossless"]
    assert result["duration"] == pytest.approx(2.0, abs=0.2)
    assert pixel_at(output, 1.3)[2] > 200
    assert any(stream["codec_type"] == "audio" for stream in probe_media(output)["streams"])


def test_failure_does_not_replace_previous_output_or_delete_sources(tmp_path):
    first = tmp_path / "a.mp4"
    make_clip(first, "red")
    output = tmp_path / "previous.mp4"
    output.write_bytes(b"existing output")
    with pytest.raises(RuntimeError, match="尚未全部"):
        merge_parts([first, tmp_path / "missing.mp4"], output)
    assert output.read_bytes() == b"existing output"
    assert first.exists()
    assert not list(tmp_path.glob(".bili-merge-*"))
