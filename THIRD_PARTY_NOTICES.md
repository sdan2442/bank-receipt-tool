# 第三方组件与源码

应用源码按 GNU AGPL version 3 发布，全文在 LICENSE 中。第三方组件保留各自的版权和许可。早期本地版本的 Apache-2.0 声明留存在 licenses/legacy 中。

## 主要组件

| 组件 | 本项目版本 | 上游许可 | 上游源码 |
| --- | --- | --- | --- |
| PaddlePaddle | 3.2.2 | Apache-2.0 | https://github.com/PaddlePaddle/Paddle/tree/v3.2.2 |
| PaddleOCR | 3.3.2 | Apache-2.0 | https://github.com/PaddlePaddle/PaddleOCR/tree/v3.3.2 |
| PaddleX | 3.3.13 | Apache-2.0 | https://github.com/PaddlePaddle/PaddleX |
| PP-OCRv5 检测、识别模型 | server / mobile | Apache-2.0 | https://github.com/PaddlePaddle/PaddleOCR |
| PyMuPDF / MuPDF | 1.26.4 / 1.26.7 | AGPL-3.0 | https://github.com/pymupdf/PyMuPDF/tree/1.26.4 |
| PySide6 / Shiboken / Qt | 6.9.2 | LGPL-3.0 / GPL-3.0，详见组件说明 | https://download.qt.io/official_releases/QtForPython/pyside6/ |
| pypdf | 6.1.1 | BSD-3-Clause | https://github.com/py-pdf/pypdf |
| OpenCV Python | 见本次环境清单 | Apache-2.0，另含原生依赖许可 | https://github.com/opencv/opencv-python |
| Pillow | 11.3.0 | MIT-CMU，另含原生依赖许可 | https://github.com/python-pillow/Pillow |
| openpyxl / PyYAML / cn2an | 见本次环境清单 | MIT | 见相应包的上游项目 |
| PyInstaller | 6.16.0 | GPL-2.0-or-later，附引导程序例外 | https://github.com/pyinstaller/pyinstaller |

## 环境清单与许可文件

`scripts/collect_licenses.py` 从本次构建环境逐个收集包发行文件中附带的 LICENSE、COPYING、NOTICE、版权文件，并记录版本和 SHA-256。清单位于 `licenses/third_party_manifest.json`，原始许可文本位于 `licenses/third_party/`。

该清单覆盖构建环境，也包含开发工具及间接依赖。包元数据中的许可名按原样记录。原生依赖的许可文本也保留在相应包目录中，例如 GEOS、PDFium、FreeType、OpenBLAS。bce-python-sdk 的包元数据未附独立许可文本，其 Apache-2.0 全文参见 `licenses/Apache-2.0.txt`。

## 取得对应源码

- 应用对应源码和配置在本仓库，开发及打包方法见 README。
- PyMuPDF 1.26.4 官方源码包入口为 https://pypi.org/project/PyMuPDF/1.26.4/#files 。MuPDF 1.26.7 源码单独提供于同次发行的第三方源码附件。
- Qt 6.9.2 官方源码位于 https://download.qt.io/archive/qt/6.9/6.9.2/ 。
- Qt for Python / Shiboken 6.9.2 源码位于 https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.9.2-src/ 。

本次对应源码的发行附件、固定版本上游地址、校验值和构建入口见 [SOURCE_CODE.md](SOURCE_CODE.md) 与 `licenses/upstream_sources.json`。应用与第三方库未作闭源修改。

Qt DLL 保持独立文件，以便使用者替换兼容版本。程序未加入限制替换库或调试修改版本的机制。

## 二进制发行说明

Windows 离线包随附许可文本和源码取得说明。Qt 及其他共享库保持独立文件，使用者可按各自许可调试、修改和替换兼容版本。

构建环境同时安装 OpenCV contrib 4.10.0.84 与 headless 4.12.0.88，实际 `cv2` 为 4.10.0。公开包不分发未使用的 `opencv_videoio_ffmpeg*.dll` 视频插件，保留静态图像读写功能。可参考 [OpenCV 官方分发说明](https://github.com/opencv/opencv/blob/4.10.0/3rdparty/ffmpeg/readme.txt)。
