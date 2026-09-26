# 贡献指南

感谢关注 FloatPulse！这是一个学习性质的开源项目，欢迎任何形式的贡献。

## 提交 Issue

- **Bug**：请附 `data/app.log` 中的相关日志片段、复现步骤、Windows 版本与 Python 版本
- **功能建议**：先说使用场景（「我想在……的时候……」），再说期望行为

## 提交 PR

1. 所有开发在 `v4/` 目录进行，**不要改动 `v2/`、`v3/`**（冻结基线）
2. 运行数据（`data/`、`temp_assets/`）不要提交
3. 提交前跑通验证四件套：

```bash
cd v4
python -m py_compile src/*.py knowledge_ball.py   # 语法
python -m pytest tests/ -q                        # 测试
python tools/run_gui_check.py <离屏脚本>           # GUI 离屏验证
python test_init.py                               # 启动冒烟
```

4. UI 改动请附离屏渲染截图；新增配置项需同步默认值/类型/范围三处定义

## 代码约定

- 模块头部保留职责说明注释；UI 层不直接读写数据文件
- 新增主题相关颜色必须走 `theme.py` 颜色字典（浅色/深色都要定义）
- 图标使用全角符号而非 emoji（QSS 着色兼容性）
