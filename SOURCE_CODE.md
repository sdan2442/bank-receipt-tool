# 对应源码与构建资料

普通使用者只需 Windows 离线 ZIP。本文和源码附件供需要查看、修改、重新编译程序或库的使用者查阅，不参与 OCR 运行，也无需放进程序目录。

## 本次发行对应源码

[v0.3.11 测试版 Release](https://github.com/sdan2442/bank-receipt-tool/releases/tag/v0.3.11-preview) 提供本次应用源码与 Windows 包，SHA-256 见同页 `SHA256SUMS_v0.3.11_preview.txt`。第三方库与模型版本保持不变，其对应源码继续由 [v0.3.10 Release](https://github.com/sdan2442/bank-receipt-tool/releases/tag/v0.3.10-preview) 提供，校验值见该页 `SHA256SUMS_v0.3.10_preview.txt`。使用程序无需额外下载这些源码附件。

| 附件 | 内容 |
| --- | --- |
| `BankReceiptTool_v0.3.11_source.zip` | 本次应用源码、配置、测试、开发安装脚本与 PyInstaller 打包脚本，位于 v0.3.11 Release |
| `ThirdPartySources_v0.3.10.zip` | PyMuPDF 1.26.4、MuPDF 1.26.7、PySide6/Shiboken 6.9.2、GEOS 3.13.1、Shapely 2.1.2、crc32c 2.9、python-bidi 0.6.11、certifi 2026.7.22、tqdm 4.70.0 官方源码包 |
| `qt-everywhere-src-6.9.2.tar.xz` | Qt 6.9.2 完整官方源码，包含本次收集的 Qt 模块及其第三方源码与许可 |

第三方归档保持上游原样，其 URL、版本、字节数和 SHA-256 记录在 `licenses/upstream_sources.json`。构建环境包版本及原始许可文件索引位于 `licenses/third_party_manifest.json`。本次 Windows 包使用 Python 3.11.9、PyInstaller 6.16.0，应用版本为 v0.3.11。

## 构建入口

应用按 README 中的开发步骤建立 Python 3.11 x64 环境，运行 `setup.ps1` 下载官方模型、校验并测试。运行 `build.ps1` 生成目录式 Windows 包。`BankReceiptTool.spec` 定义配置、四个模型及运行库的收集方式。公开副本中排除了 OpenCV 未使用的视频插件，没有修改第三方库。

各第三方归档内保留上游构建脚本和说明。Qt 使用 CMake/Ninja 及 MSVC，入口为 `qt-everywhere-src-6.9.2/README.md` 和 `configure`。PySide6/Shiboken 构建入口为 `pyside-setup-everywhere-src-6.9.2/setup.py`。PyMuPDF 的 `setup.py` 与 `pyproject.toml` 负责构建包装层及 MuPDF，可将单独提供的 MuPDF 源码归档通过 `PYMUPDF_SETUP_MUPDF_BUILD` 指定为本地源文件。其余 Python 包的构建入口为各自 `pyproject.toml` 或 `setup.py`，GEOS 使用其 CMakeLists.txt。详细选项见归档内的上游说明。

Qt、MuPDF、GEOS 及其他库保留各自许可，全文随 Windows 包的 `licenses/` 提供。共享库保持独立，没有禁止替换兼容库或调试修改版本的限制。

## 固定版本上游入口

- [PyMuPDF 1.26.4](https://pypi.org/project/PyMuPDF/1.26.4/#files)
- [MuPDF 1.26.7](https://mupdf.com/downloads/archive/mupdf-1.26.7-source.tar.gz)
- [Qt 6.9.2](https://download.qt.io/archive/qt/6.9/6.9.2/single/)
- [PySide6 / Shiboken 6.9.2](https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.9.2-src/)
- [GEOS 3.13.1](https://download.osgeo.org/geos/)
- [PaddlePaddle 3.2.2](https://github.com/PaddlePaddle/Paddle/tree/v3.2.2)
- [PaddleOCR 3.3.2](https://github.com/PaddlePaddle/PaddleOCR/tree/v3.3.2)
- [PaddleX](https://github.com/PaddlePaddle/PaddleX)
- [OpenCV Python](https://github.com/opencv/opencv-python)
- [Python 3.11.9](https://www.python.org/downloads/release/python-3119/)

模型的官方来源和许可证见 README 与程序 `_internal/models/models_manifest.json`。所有模型已包含在 Windows 离线包中。
