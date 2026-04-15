# MinerU PDF Converter 使用手册

这是一个基于 MinerU API 的 PDF 转换工具，用来把普通 PDF 转成带文字层的可搜索 PDF。

支持两种使用方式：

- 图形界面 `mineru_gui.py`
- 命令行脚本 `mineru_to_searchable_pdf.py`

项目也支持将 GUI 打包为 Windows 下的单文件 `exe`。

## 主要功能

- 选择本地 PDF 并提交给 MinerU 处理
- 生成可搜索的输出 PDF
- 保存 MinerU 返回的原始结果，便于排查问题或重复利用
- 记录运行日志
- 支持打包为 Windows 可执行文件
- 支持推送到 GitHub 后自动构建 `exe`

## 目录说明

- [mineru_gui.py](C:\Users\Administrator\Desktop\pdfreader\mineru_gui.py)：图形界面入口
- [mineru_to_searchable_pdf.py](C:\Users\Administrator\Desktop\pdfreader\mineru_to_searchable_pdf.py)：命令行入口
- [build_exe.ps1](C:\Users\Administrator\Desktop\pdfreader\build_exe.ps1)：本地打包脚本
- [requirements.txt](C:\Users\Administrator\Desktop\pdfreader\requirements.txt)：Python 依赖
- [.github/workflows/build.yml](C:\Users\Administrator\Desktop\pdfreader\.github\workflows\build.yml)：GitHub 自动打包工作流

运行过程中常见输出位置：

- `mineru_output/`：MinerU 返回的中间结果
- `logs/`：GUI 运行日志
- `mineru_gui_config.json`：GUI 保存的 Token 和最近一次输入输出路径
- `dist/`：本地打包后的 `exe`

## 环境要求

- Windows
- Python 3.11 或更高版本
- 可用的 MinerU API Token

## 安装依赖

在项目目录执行：

```powershell
pip install -r requirements.txt
```

## 获取 MinerU Token

本工具依赖 MinerU API。使用前需要准备好 Token。

你可以用以下任一方式提供 Token：

1. 在 GUI 中输入并保存
2. 在命令行中通过 `--token` 传入
3. 通过环境变量 `MINERU_API_TOKEN` 提供

PowerShell 示例：

```powershell
$env:MINERU_API_TOKEN="你的 MinerU Token"
```

## 图形界面使用方法

启动 GUI：

```powershell
python .\mineru_gui.py
```

使用步骤：

1. 点击“选择 PDF”，选中本地源文件
2. 设置输出 PDF 路径
3. 输入 MinerU Token
4. 点击开始转换
5. 等待处理完成

转换完成后，你会得到一个带文字层的 PDF，可以直接搜索文字内容。

GUI 还支持：

- 保存 Token 和最近使用路径
- 打开日志目录
- 打开程序目录
- 在运行中查看实时日志
- 手动停止当前任务

## 命令行使用方法

基础用法：

```powershell
python .\mineru_to_searchable_pdf.py "C:\path\input.pdf" --token "你的 MinerU Token"
```

指定输出 PDF：

```powershell
python .\mineru_to_searchable_pdf.py "C:\path\input.pdf" --token "你的 MinerU Token" --output-pdf "C:\path\output.searchable.pdf"
```

指定 MinerU 原始结果目录：

```powershell
python .\mineru_to_searchable_pdf.py "C:\path\input.pdf" --token "你的 MinerU Token" --output-dir ".\mineru_output\demo"
```

使用环境变量中的 Token：

```powershell
$env:MINERU_API_TOKEN="你的 MinerU Token"
python .\mineru_to_searchable_pdf.py "C:\path\input.pdf"
```

复用已经拿到的 `result.json`，跳过上传和轮询：

```powershell
python .\mineru_to_searchable_pdf.py "C:\path\input.pdf" --result-json ".\mineru_output\demo\result.json"
```

## 常用参数

命令行脚本支持以下常用参数：

- `input_pdf`：输入 PDF 路径
- `--token`：MinerU Token
- `--output-pdf`：输出 PDF 路径
- `--output-dir`：MinerU 原始结果保存目录
- `--poll-interval`：轮询间隔，默认 10 秒
- `--timeout`：最长等待时间，默认 7200 秒
- `--result-json`：复用已有结果，跳过上传和轮询

## 本地打包 exe

先安装依赖：

```powershell
pip install -r requirements.txt
```

执行打包：

```powershell
powershell -ExecutionPolicy Bypass -File .\build_exe.ps1
```

打包成功后输出文件在：

```text
dist\MinerUPdfTool.exe
```

## GitHub 自动打包

仓库已配置 GitHub Actions。推送到 `main` 或 `master` 后会自动执行以下流程：

1. 安装 Python
2. 安装依赖
3. 运行 [build_exe.ps1](C:\Users\Administrator\Desktop\pdfreader\build_exe.ps1)
4. 上传 `dist/MinerUPdfTool.exe` 为构建产物

查看方式：

1. 打开 GitHub 仓库的 `Actions`
2. 进入对应的工作流运行记录
3. 在 `Artifacts` 中下载 `MinerUPdfTool-windows`

## Git 提交建议

本仓库已经通过 [.gitignore](C:\Users\Administrator\Desktop\pdfreader\.gitignore) 排除了以下内容，不建议提交：

- `build/`
- `dist/`
- `__pycache__/`
- 虚拟环境目录
- 日志文件
- `mineru_output/`

建议提交的只有源码、脚本、依赖清单和工作流配置。

## 常见问题

### 1. 提示缺少 Token

请检查是否：

- 在 GUI 中正确填写并保存 Token
- 或命令行传入了 `--token`
- 或已设置环境变量 `MINERU_API_TOKEN`

### 2. 上传或轮询很慢

这是远端 API 处理过程的一部分。大文件会明显更慢，可以通过日志观察当前进度。

### 3. 打包后 exe 无法运行

先确认本地 Python 依赖是否安装完整，再重新执行打包脚本。也建议优先从 GitHub Actions 产物下载一次，确认是否为本地环境问题。

### 4. 生成结果不理想

可优先保留 `mineru_output/` 下的原始结果和 `logs/` 目录中的日志，便于进一步排查。

## 开发说明

如果你准备继续维护这个项目，推荐工作流程如下：

```powershell
pip install -r requirements.txt
python .\mineru_gui.py
```

修改完成后直接提交源码，推送到 GitHub 后由 Actions 自动构建即可。
