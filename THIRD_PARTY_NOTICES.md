# 第三方组件与源码

应用源码按 GNU AGPL version 3 发布，全文在 LICENSE 中。第三方组件保留各自的版权和许可。早期本地版本的 Apache-2.0 声明留存在 licenses/legacy 中。

## 主要组件

| 组件 | 本项目版本 | 上游许可 | 上游源码 |
| --- | --- | --- | --- |
| PaddlePaddle | 3.2.2 | Apache-2.0 | https://github.com/PaddlePaddle/Paddle/tree/v3.2.2 |
| PaddleOCR | 3.3.2 | Apache-2.0 | https://github.com/PaddlePaddle/PaddleOCR/tree/v3.3.2 |
| PaddleX | 3.3.13 | Apache-2.0 | https://github.com/PaddlePaddle/PaddleX |
| PP-OCRv5 检测、识别模型 | server / mobile | Apache-2.0 | https://github.com/PaddlePaddle/PaddleOCR |
| PyMuPDF / MuPDF | 1.26.4 / 构建内附版本 | AGPL-3.0 | https://github.com/pymupdf/PyMuPDF/tree/1.26.4 |
| PySide6 / Shiboken / Qt | 6.9.2 | LGPL-3.0 / GPL-3.0，详见组件说明 | https://download.qt.io/official_releases/QtForPython/pyside6/ |
| pypdf | 6.1.1 | BSD-3-Clause | https://github.com/py-pdf/pypdf |
| OpenCV Python | 见本次环境清单 | Apache-2.0，另含原生依赖许可 | https://github.com/opencv/opencv-python |
| Pillow | 11.3.0 | MIT-CMU，另含原生依赖许可 | https://github.com/python-pillow/Pillow |
| openpyxl / PyYAML / cn2an | 见本次环境清单 | MIT | 见相应包的上游项目 |
| PyInstaller | 6.16.0 | GPL-2.0-or-later，附引导程序例外 | https://github.com/pyinstaller/pyinstaller |

## 环境清单与许可文件

`scripts/collect_licenses.py` 从本次构建环境逐个收集包发行文件中附带的 LICENSE、COPYING、NOTICE、版权文件，并记录版本和 SHA-256。清单位于 `licenses/third_party_manifest.json`，原始许可文本位于 `licenses/third_party/`。

该清单覆盖构建环境，也包含开发工具及间接依赖。包元数据中的许可名按原样记录。发行版中原生 DLL 及其子依赖的许可，仍需要结合发行包和上游源码核对。

## 取得对应源码

- 应用对应源码和配置在本仓库，开发及打包方法见 README。
- PyMuPDF 1.26.4 官方源码包入口为 https://pypi.org/project/PyMuPDF/1.26.4/#files 。其源码发行包包含用于构建的 MuPDF 源码归档。
- Qt 6.9.2 官方源码位于 https://download.qt.io/archive/qt/6.9/6.9.2/ 。
- Qt for Python / Shiboken 6.9.2 源码位于 https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.9.2-src/ 。

发布包含二进制依赖的离线 EXE 时，应同步提供对应源码取得方式、许可文件和版本信息。依据具体许可要求，需要由发行者提供的对应源码应随发行附件提供。第三方上游网址用于定位版本，发布者仍须履行适用的源码提供义务。

Qt DLL 保持独立文件，以便使用者替换兼容版本。程序未加入限制替换库或调试修改版本的机制。

## 公开发行状态

当前准备公开的是应用源码。离线程序包须在第三方原生依赖、许可文本及对应源码发行材料核对完成后单独发布到 Release。将本文件加入原有 ZIP 并不能代表该二进制包的全部发行要求已核对完成。
