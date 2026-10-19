"""Build a portable x64 app, including verified FFmpeg/ffprobe and notices.

Run with the project's Python 3.11 virtual environment. No administrator access
or system Python/FFmpeg installation is needed by the finished application.
"""
import argparse
import hashlib
from importlib import metadata
import json
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def command(args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def prepare_ffmpeg():
    destination = ROOT / "ffmpeg"
    if all((destination / name).is_file() for name in ("ffmpeg.exe", "ffprobe.exe")):
        return destination
    cache = ROOT / ".build-tools"
    cache.mkdir(exist_ok=True)
    archive = cache / "ffmpeg-release-essentials.zip"
    url = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
    with urllib.request.urlopen(url + ".sha256", timeout=60) as response:
        expected = response.read().decode("ascii").split()[0].lower()
    urllib.request.urlretrieve(url, archive)
    actual = hashlib.sha256(archive.read_bytes()).hexdigest()
    if actual != expected:
        raise RuntimeError("FFmpeg SHA-256 校验失败")
    extracted = cache / "ffmpeg-release"
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(extracted)
    source = next(extracted.glob("*/bin/ffmpeg.exe")).parent
    destination.mkdir(exist_ok=True)
    for name in ("ffmpeg.exe", "ffprobe.exe"):
        shutil.copy2(source / name, destination / name)
    return destination


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--skip-tests", action="store_true")
    args = parser.parse_args()
    if sys.platform != "win32" or sys.maxsize <= 2**32:
        raise RuntimeError("请在64位 Windows 上构建")
    media_dir = prepare_ffmpeg()
    command([sys.executable, "compile_ui.py"], cwd=ROOT / "src")
    if args.prepare_only:
        return
    if not args.skip_tests:
        command([sys.executable, "-m", "pytest", "-q"], cwd=ROOT)
    delivery = ROOT / "delivery"
    command([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--windowed",
             "--name", "BiliDownloader", "--icon", str(ROOT / "src/res/icon/icon.ico"),
             "--paths", str(ROOT / "src"), "--distpath", str(delivery),
             "--workpath", str(ROOT / "build/pyinstaller"), "--specpath", str(ROOT / "build"),
             "--add-data", str(ROOT / "src/Lib/bili_api/utils/data") + ";Lib/bili_api/utils/data",
             "--add-data", str(ROOT / "src/font") + ";font", str(ROOT / "src/main.py")], cwd=ROOT)
    application = delivery / "BiliDownloader"
    shutil.copytree(media_dir, application / "ffmpeg", dirs_exist_ok=True)
    for name in ("LICENSE", "CHANGELOG.md", "USAGE-zh_CN.md"):
        shutil.copy2(ROOT / name, application / name)
    notices = application / "third-party-licenses"
    notices.mkdir(exist_ok=True)
    shutil.copy2(ROOT / "THIRD_PARTY_NOTICES.md", notices / "README.md")
    for license_file in (ROOT / ".build-tools/ffmpeg-release").glob("*/LICENSE"):
        shutil.copy2(license_file, notices / "FFmpeg-LICENSE.txt")
    qt_licenses = Path(__import__("PySide6").__file__).resolve().parent / "Qt" / "licenses"
    if qt_licenses.exists():
        shutil.copytree(qt_licenses, notices / "Qt", dirs_exist_ok=True)
    for version_file in (ROOT / ".venv/Lib/site-packages").glob("PySide6*/**/LICENSE*"):
        if version_file.is_file():
            shutil.copy2(version_file, notices / (version_file.parent.name + "-" + version_file.name))
    for package in ("PySide6", "PySide6_Essentials", "PySide6_Addons", "shiboken6", "Pillow",
                    "qrcode", "pycryptodome", "brotli", "zstandard", "pyperclip", "pywin32", "pyinstaller"):
        distribution = metadata.distribution(package)
        for item in distribution.files or []:
            if not (any(part.lower() in ("license", "licenses") for part in item.parts)
                    or item.name.lower().startswith(("license", "copying", "notice"))):
                continue
            source = Path(distribution.locate_file(item))
            if source.is_file():
                package_dir = notices / package
                package_dir.mkdir(exist_ok=True)
                shutil.copy2(source, package_dir / item.name)
    for name in ("LICENSE.txt", "LICENSE"):
        if (Path(sys.base_prefix) / name).is_file():
            shutil.copy2(Path(sys.base_prefix) / name, notices / "Python-LICENSE.txt")
    sys.path.insert(0, str(ROOT / "src"))
    from utils.version import __version__
    revision = command(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    dirty = bool(command(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True).stdout.strip())
    manifest = {"version": __version__, "source_revision": revision, "source_dirty": dirty,
                "python": sys.version, "source_repository": "https://github.com/mouxiaohao/BiliDownloader",
                "ffmpeg_source": "https://www.gyan.dev/ffmpeg/builds/",
                "ffmpeg_version": command([str(media_dir / "ffmpeg.exe"), "-version"], capture_output=True, text=True).stdout.splitlines()[0]}
    manifest["files_sha256"] = {str(path.relative_to(application)): hashlib.sha256(path.read_bytes()).hexdigest()
                                for path in [application / "BiliDownloader.exe", *(application / "ffmpeg").glob("*.exe")]}
    (application / "build-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    archive_base = delivery / ("BiliDownloader-" + __version__ + "-Windows-x64")
    archive_path = Path(shutil.make_archive(str(archive_base), "zip", delivery, application.name))
    checksum = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    archive_path.with_suffix(".zip.sha256").write_text(checksum + "  " + archive_path.name + "\n", encoding="ascii")
    print(str(archive_path))
    print("SHA256 " + checksum)


if __name__ == "__main__":
    main()
