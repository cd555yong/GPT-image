<div align="center">

# GPT 图片生成器

Windows 桌面端 AI 图片生成与编辑工具，基于 OpenAI 兼容接口，集成文生图、编辑、蒙版局部重绘、去背/换背、放大、历史记录与多参考图工作流。

[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](./LICENSE)
[![Platform: Windows](https://img.shields.io/badge/Platform-Windows-0078D4?logo=windows&logoColor=white)](https://www.microsoft.com/windows)
[![UI: Tkinter](https://img.shields.io/badge/UI-Tkinter-FF6F00?logo=python&logoColor=white)](https://docs.python.org/3/library/tkinter.html)

</div>

---

## 项目定位

这是一个偏生产力取向的桌面应用，不是一个 SDK。重点在于把常见的图像生成与编辑动作串成一条顺手的桌面工作流：

- 单图生成与流式预览
- 基于当前主图进行编辑
- 蒙版局部重绘
- 多参考图协同编辑
- 去背、换背、自换背
- AI 描述、对比、撤销重做、历史回看

项目当前是 Windows 优先，主程序仍是单文件 `gpt_image_app.py`，但仓库结构已经按开源项目常见形态整理过：保留源码、文档、示例配置和打包文件，不提交运行产物与本地数据。

## 真实截图

### 主界面

![主界面](docs/screenshot-main.png)

### 工作区

![工作区](docs/screenshot-workspace.png)

### 蒙版模式

![蒙版模式](docs/screenshot-mask.png)

## 当前特性

- 文生图，支持流式逐帧预览
- 智能编辑，支持当前主图 + 多参考图
- 蒙版局部重绘
- 去背、换背、自换背
- 图片放大
- AI 自动描述图片内容
- 前后对比
- 撤销 / 重做
- 历史记录与缩略图缓存
- 拖拽导入、剪贴板导入
- 批量生成
- 风格预设
- OpenAI 兼容接口接入

## 环境要求

- Windows 10/11
- Python 3.10+
- 可访问的 OpenAI 兼容图片接口

`tkinter` 通常随标准 Python 一起安装；拖拽依赖 `tkinterdnd2`，缺失时程序会降级为无拖拽模式。

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 生成本地配置

```bash
copy config.example.json config.json
```

`config.json` 已被 `.gitignore` 忽略，用于保存你的本地接口地址、密码和 UI 选择状态，不应该提交到仓库。

### 3. 配置接口

`config.example.json` / `config.json` 当前支持的核心字段如下：

| 字段 | 说明 |
|:---|:---|
| `api_base` | OpenAI 兼容接口根地址 |
| `auth` | 接口鉴权内容，程序会按当前逻辑拼接请求头 |
| `model` | 当前默认模型 |
| `model_options` | UI 下拉中允许选择的模型列表 |
| `size` | 默认输出尺寸 |
| `format` | 默认输出格式 |
| `quality` | 默认质量 |
| `style` | 默认风格预设 |
| `batch` | 默认批量数量 |
| `compression` | JPEG/WebP 压缩质量 |

默认示例：

```json
{
  "api_base": "http://127.0.0.1:5101/openai",
  "model": "gpt-image-2",
  "model_options": [
    "gpt-image-2",
    "gpt-image-1.5",
    "gpt-image-1",
    "gpt-image-1-mini"
  ],
  "auth": "",
  "size": "1024x1024",
  "format": "png",
  "quality": "high",
  "style": "无（原始）",
  "batch": 1,
  "compression": 100
}
```

说明：

- UI 里的模型框现在是只读下拉，不能手输。
- 可选模型完全由 `model_options` 决定。
- 如果本地没有 `config.json`，程序第一次启动会自动写入默认配置。

### 4. 启动

```bash
python -m gpt_image_app
```

或者直接双击 [`启动.bat`](启动.bat)。

## 常用快捷键

| 快捷键 | 功能 |
|:---|:---|
| `Ctrl + Enter` | 生成 |
| `Ctrl + S` | 保存当前图片 |
| `Ctrl + O` | 上传图片 |
| `Ctrl + Shift + V` | 粘贴图片 |
| `Ctrl + Z` / `Ctrl + Y` | 撤销 / 重做 |
| `Esc` | 停止当前生成或编辑 |

补充说明：

- 文本输入框里的 `Ctrl + V` 保持系统默认文本粘贴行为
- 非文本区域的粘贴快捷键会走图片导入逻辑

## 打包 EXE

项目包含 PyInstaller 规格文件：

```bash
pyinstaller GPT图片生成器.spec
```

这份 `.spec` 已去掉本机绝对路径依赖，改为在构建时动态定位 `tkinterdnd2/tkdnd` 资源。

## 项目结构

```text
gpt_image_app.py         主程序
config.example.json      示例配置
requirements.txt         运行依赖
pyproject.toml           开源项目元数据
GPT图片生成器.spec        PyInstaller 打包配置
启动.bat                 Windows 快速启动脚本
docs/                    README 截图资源
LICENSE                  MIT 许可证
CONTRIBUTING.md          贡献说明
```

## 不应提交到 Git 的内容

这些内容属于运行期数据、构建产物或本机缓存，已经被 `.gitignore` 忽略：

- `config.json`
- `history/`
- `thumbs/`
- `history_db.json`
- `debug_payloads/`
- `debug_log.json`
- `error_log.json`
- `_tmp/`
- `build/`
- `dist/`
- `__pycache__/`
- `.pytest_cache/`

## 开发说明

### 最低限度检查

修改完代码后，至少执行：

```bash
python -m py_compile gpt_image_app.py
```

如果你改了打包链路，再额外跑一次：

```bash
pyinstaller GPT图片生成器.spec
```

### 提交前建议

- 不提交本地配置、日志、历史记录、缩略图和打包产物
- 修改功能时同步更新 `README.md` 和 `config.example.json`
- 如果改了快捷键、模型、配置字段或打包流程，优先更新文档

更多协作约定见 [`CONTRIBUTING.md`](CONTRIBUTING.md)。

## 已知边界

- 当前主要面向 Windows，未针对 macOS / Linux 做系统级验证
- 主程序仍以单文件形态维护，后续如果要长期多人协作，建议逐步拆分模块
- 仓库现在适合开源发布和问题追踪，但还不是完整的 Python 包生态项目

## License

本项目使用 [MIT License](LICENSE)。
