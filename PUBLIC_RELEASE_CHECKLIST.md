# 公开发布检查清单

## 源码仓库

只提交受版本控制的源码、配置、脚本、测试、许可证和说明文档。不要直接上传整个开发目录。

发布前执行：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe scripts\privacy_audit.py --source .
```

`.gitignore` 会排除模型、虚拟环境、构建目录、历史快照、日志、断点、OCR 审计、处理结果和所有扫描文档格式。首次提交前仍应执行 `git status --short`，逐项确认待提交文件。

## Windows 可运行版本

公开发布时上传整个 `dist\BankReceiptTool_v0_3_10` 目录生成的压缩包。该目录包含模型和运行库，用户可完整解压后离线运行。

压缩前执行：

```powershell
.\.venv\Scripts\python.exe scripts\privacy_audit.py --binary .\dist\BankReceiptTool_v0_3_10
Get-FileHash .\dist\BankReceiptTool_v0_3_10\银行回单工具.exe -Algorithm SHA256
```

不要把开发目录中的 `models/`、任何输出目录或历史任务目录手动复制进发布包。发布说明应列出 SHA-256、Windows 10/11 x64、CPU 模式、离线运行说明和 Paddle/PaddleOCR 许可证来源。

## 上传位置

- 源码：GitHub/Gitee 仓库。
- 约 1 GB 的离线程序包：GitHub Release、Gitee Release、对象存储或网盘；普通 Git 提交通常不适合大文件。
- 如果平台限制单文件大小，可使用 7-Zip 分卷；所有分卷必须来自同一个已审计目录，并同时提供校验值。

任何重新打包、加入示例文件或修改配置后，都必须重新运行隐私审计和启动测试。
