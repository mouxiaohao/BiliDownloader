# Third-party notices

The application is a fork of Majjcom/BiliDownloader, licensed under MIT. The original LICENSE is included unchanged. Modified source: https://github.com/mouxiaohao/BiliDownloader .

The portable package includes separately executed FFmpeg and ffprobe binaries from Gyan's essentials build. These components have their own GPL licensing; see the accompanying FFmpeg-LICENSE.txt, the version and hashes in build-manifest.json, and https://www.gyan.dev/ffmpeg/builds/ for build information and corresponding source links. FFmpeg upstream source: https://ffmpeg.org/download.html .

PySide6/Qt and Shiboken are dynamically loaded components under their respective LGPL/GPL/commercial terms. Their notices are preserved where included in the installed wheels; source and licensing information: https://www.qt.io/licensing/open-source-lgpl-obligations and https://code.qt.io/pyside/pyside-setup.git/ . Qt libraries remain separate in _internal so a compatible replacement can be supplied.

Other dependencies include Python (PSF license), PyInstaller (GPL with the bootloader exception), Pillow (HPND), qrcode (BSD), PyCryptodome (BSD/public-domain components), Brotli and Zstandard (their accompanying permissive licenses), pyperclip (BSD), and pywin32 (PSF). The application includes the HarmonyOS Sans SC font from the upstream project.
