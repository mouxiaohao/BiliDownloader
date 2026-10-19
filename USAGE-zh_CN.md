# BiliDownloader 1.5.0：播放列表与分P

将便携包完整解压，双击 `BiliDownloader/BiliDownloader.exe`。无需另装 Python 或 FFmpeg；请保留同目录的 `_internal`、`ffmpeg` 文件夹。

1. 粘贴视频地址/BV号，或者直接粘贴合集、系列、收藏夹地址。
2. 点击继续，解析后会显示播放列表的视频数和分P总数。
3. 在“播放列表 → 视频 → 分P”树形列表中选择下载内容。勾选视频可选中其全部分P；展开后可逐P选择；列表支持全选、全不选和只选当前视频。
4. 在某个视频右侧勾选“合并所选分P”，可将**该视频中选中的至少两个分P**按P号顺序合成长视频。不同BV的视频始终独立，未选中的分P不会参与。
5. “所选视频均合并分P”可以批量设置，但每个视频仍单独生成合并文件。默认优先无损合并；参数不一致时自动转为 H.264/AAC。也可选择仅无损模式，不兼容时提示并保留源文件。
6. 下一步设置画质、编码和目录后提交。下载页会显示分P任务及独立的合并任务；只有该视频的所选分P全部成功后才会合并。失败项可重试，合并任务会继续等待。

从视频地址进入并识别出合集时，默认只勾选当前视频；直接输入列表地址时默认全选列表。所有操作都需提交才会开始下载。

每个BV保存到独立目录，合并文件以“合并分P_...”命名，原分P文件保留。大会员、登录与地区限制仍由B站及账号权限决定。番剧保持按集选择，合并功能不会把番剧的不同集当作同一视频的分P。

## 从源码构建

使用64位 Windows、Python 3.11（原项目固定的 Pillow/PySide6 版本适用），在仓库根目录运行：

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r src\requirements.txt -r src\requirements_win.txt -r requirements-build.txt
.venv\Scripts\python.exe tools\build_windows.py
```

构建脚本会编译 Qt 界面，运行测试，打包独立程序，并生成 ZIP、SHA-256 与 `build-manifest.json`。缺少 FFmpeg/ffprobe 时从 Gyan 下载并核验 SHA-256，依赖不需要安装到系统目录。

显式运行联网验证（会访问该地址；加 `--download` 才会下载两个分P及另一个视频的一个分P，并仅合并前者）：

```powershell
.venv\Scripts\python.exe src\main.py --self-check --url 'https://space.bilibili.com/3493275574995772/lists/8530021?type=season' --report '.validation\live.json' --download
```

便携版同样支持这些验证参数。验证配置和下载存放在报告旁的隔离目录，不读取个人登录信息。
