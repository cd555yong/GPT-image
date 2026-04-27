# 贡献指南

感谢你愿意改进这个项目。

当前仓库以 Windows 桌面应用为主，协作目标很务实：不要把功能改花，不要把仓库弄脏，优先保持主流程稳定。

## 本地开发

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 生成本地配置

```bash
copy config.example.json config.json
```

`config.json` 是本地私有配置，已经被忽略，不要提交。

### 3. 启动应用

```bash
python -m gpt_image_app
```

## 提交约定

请不要提交以下内容：

- `config.json`
- `history/`
- `thumbs/`
- `history_db.json`
- `debug_payloads/`
- `debug_log.json`
- `error_log.json`
- `build/`
- `dist/`
- `_tmp/`
- `__pycache__/`

## 修改原则

- 优先修真实问题，不做无关重构
- 改动 UI 时同步更新 README 截图说明或文字说明
- 改动配置字段时同步更新 `config.example.json` 和 `README.md`
- 改动打包流程时同步更新 `GPT图片生成器.spec`
- 保持 Windows 下双击 `启动.bat` 和 `python -m gpt_image_app` 都可启动

## 最低检查

提交前至少执行：

```bash
python -m py_compile gpt_image_app.py
```

如果你动了打包相关逻辑，再执行：

```bash
pyinstaller GPT图片生成器.spec
```

## Issue / PR 建议

提交问题或变更时，尽量附上：

- 复现步骤
- 实际结果
- 预期结果
- 截图或日志片段
- 使用的模型、接口类型和配置差异

## 适合继续改进的方向

- 将 `gpt_image_app.py` 逐步拆成独立模块
- 补充最小自动化测试
- 补充英文 README 或双语文档
- 为发布版本整理 changelog 和 release notes
