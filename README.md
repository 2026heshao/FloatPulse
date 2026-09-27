# FloatPulse · 生活悬浮球

[![Tests](https://github.com/2026heshao/FloatPulse/actions/workflows/tests.yml/badge.svg)](https://github.com/2026heshao/FloatPulse/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-teal.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/Platform-Windows%2010%2F11-blue)](README.md)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue)](requirements.txt)
[![Qt](https://img.shields.io/badge/UI-PyQt6%206.7%2B-41cd52)](requirements.txt)

**一款 Windows 桌面常驻悬浮球工具：碎片收集 → 分类归档 → 转任务/笔记 → 截图钉屏，一个球全搞定。**
原生 PyQt6 控件 + QSS 实现，无 Electron、无浏览器内核，打包后 71 MB，启动 1–2 秒。

| 主窗口（深色） | 悬浮球 + 快捷卡片 |
|---|---|
| ![主窗口](docs/images/preview-main-dark.png) | ![卡片](docs/images/preview-card-dark.png) |

---

## 🔍 与同类工具的区别

启动器负责找东西，截图工具负责看东西，FloatPulse 负责**随手记完之后的那一段**——记下来的碎片怎么归类、怎么变成任务或笔记、怎么导出进你已有的知识库。它不是要替代谁，而是补上这些工具都没做的那一环。

| 能力 | FloatPulse | FocusCapture | Floatyball | Flow Launcher | Snipaste |
|---|---|---|---|---|---|
| 悬浮球常驻入口 | ✅ | ✅ | ✅ | ❌ | ❌ |
| 随手记录碎片 | ✅ 快捕条 + 剪贴板 | ✅ 剪贴板 | ⚠️ 拖放 | ❌ | ❌ |
| 碎片分类管理 | ✅ 来源 + 内容语义双轴 | ❌ 仅时间流 | ❌ | ❌ | ❌ |
| 转成任务 / 笔记 | ✅ | ❌ | ❌ | ❌ | ❌ |
| 临时素材中转 | ✅ 去重 + 缩略图 + 过期清理 | ❌ | ❌ | ❌ | ❌ |
| 截图钉屏 | ✅ 含批注与撤销 | ❌ | ❌ | ❌ | ✅ |
| 软件 / 网址启动 | ✅ 拖 exe 即建 | ❌ | ✅ AHK 动作 | ✅ | ❌ |
| AI 总结 / 对话 | ✅ 可选，本地 + 云端双后端 | ❌ | ❌ | ❌ | ❌ |
| 全局文件搜索 | ❌ | ❌ | ❌ | ✅ 含 Everything | ❌ |
| 插件生态 | ⚠️ 外置插件，起步阶段 | ❌ | ✅ AHK | ✅ 200+ 社区插件 | ❌ |
| 技术栈 | Python + PyQt6 | C# / WPF | AutoHotkey | C# / .NET | 闭源 |
| GitHub Stars | 0 | 0 | 20 | 15,654 | 闭源 |

**表里的 ❌ 不是缺陷，是取舍。** 全局文件搜索交给 Everything 和 Flow Launcher——它们在索引速度与搜索语法上做得更好，再做一个没有意义；插件生态刚起步，目前只有外置插件机制，社区插件的数量不跟任何人比，写 ⚠️ 就是 ⚠️。项目 Stars 是 0 也照实写 0：这是个还在自用打磨阶段的项目，没有外部用户验证，不装成「已被广泛使用」。

*Stars 数据取自 GitHub API，2026-09-27 快照，之后会变化。完整竞品实测数据、赛道分析与「站不住的说法」清单见 [docs/竞品分析-2026-09-27.md](docs/竞品分析-2026-09-27.md)。*

---

## ✨ 功能特性

### 🎈 悬浮球（高频轻量入口）
- 可拖拽、四向吸边隐藏、鼠标移近自动滑出
- 悬停弹出 420×320 快捷卡片，滚轮翻卡、点击换卡
- 拖文件到球 → 自动收入碎片池；拖入 `.exe/.lnk` → 生成软件启动器
- 全局热键快速捕捉条（`Ctrl+Alt+K`）：不打断当前工作随手记
- **截图钉屏（`Ctrl+Alt+S`）**：框选屏幕任意区域，钉成置顶参考浮窗；滚轮缩放内容（光标锚定）、右下角抓手等比例调整窗框、批注（画笔/箭头/马赛克，Ctrl+Z 撤销）、右键复制/保存

### 🃏 快捷卡片（三模式）
| 模式 | 行为 |
|------|------|
| 📚 知识卡片 | 随机抽取知识库段落供速查，悬停自动关闭 |
| 📋 日程任务 | 输入区 + 任务列表 + 截止日期分组（逾期/今天/本周/以后） |
| 📝 随时笔记 | 单条便签，800ms 防抖自动保存 |

### 🤖 AI 助手（可选能力，本地 / 云端双后端）
- **主窗口专属页面**（第一个页面插件）：`Ctrl+Alt+I` 或侧栏「🤖 AI 助手」进入，一键「总结任务 / 整理碎片 / 本周小结」读应用内数据，也可自由问答；输入框 **Enter 直发、Shift+Enter 换行**
- **三种后端一套配置**：统一走 OpenAI 兼容协议——① 云端填 DeepSeek / 硅基流动地址与 key；② 纯本地接 Ollama（`127.0.0.1:11434`）；③ **自带本地推理**：浏览选择 llama-server.exe 与 .gguf 模型，点「启动」由插件拉起服务（默认端口 8093，显卡全量卸载，退出程序自动结束、不占残留显存），本地模式**断网可用**、数据不出机
- **保存并测试连接**：后端设置一键落盘 + 探活，状态行即时反馈 ✓ / ✗ 与失败原因（401 / 404 / 超时各有针对性提示）
- **核心功能保持离线**：AI 是外置插件的**可选能力**，插件必须在 manifest 声明 `capabilities: ["network"]` 才能联网，且只能经宿主网络桥发请求（插件自身拿不到网络库）；插件中心卡片显示「🌐 网络访问」标记，所有请求只记 URL 与耗时进 `app.log` 可审计
- 不装 key、不开本地服务时，程序其余功能与旧版完全一致

### 🖥 主窗口（九面板）
| 面板 | 功能 |
|------|------|
| 🧩 碎片工作台 | 剪贴板文本/路径/文件/知识段落统一收纳，搜索、筛选、合并、复制 |
| 📋 日程任务 | 逾期红标、相对截止时间、撤销条 |
| 📝 笔记管理 | 列表 + 编辑区 + 自动保存 |
| 📚 知识库 | docx 数据源，段落增删改、外部修改检测、增量同步 |
| 🗂 临时素材 | 文件/图片拖拽拾取、缩略图、sha256 去重、过期清理 |
| 🌐 网址导航 | 收藏网址一键打开 |
| 💻 软件导航 | 本地软件快捷方式、图标提取、一键启动 |
| 🔍 全局搜索 | `Ctrl+K` 跨面板搜索（懒创建） |
| ⚙️ 设置 | 主题 / 剪贴板 / 热键 / 悬浮球尺寸 / 开机自启 等 |

### 🛡 工程化细节
- 三层架构（UI / 业务 / 数据）严格解耦，模块化拆分
- JSON 原子写入（临时文件 + `os.replace`）、损坏自动回退
- Windows 互斥量单实例防护、全局异常钩子、多屏/分辨率漂移容错
- 深浅双主题（QSS 模板 + 颜色字典），对比度有测试护栏
- pytest 测试套件 + 离屏 GUI 验证脚本

---

## 🚀 快速开始

### 方式一：双击启动器（推荐）

双击项目根目录的 **`启动v4.bat`**：

| 命令 | 行为 |
|---|---|
| `启动v4.bat` | 启动 v4（开发主线），带控制台实时日志 |
| `启动v4.bat quiet` | 后台启动，无控制台窗口（日常使用） |

> v2 / v3 冻结基线已于 2026-09-27 清理移除，当前只保留 v4 主线；历史版本可从 GitHub 提交记录中查看。

启动器会自动探测已安装 PyQt6 的 Python 并自检依赖。

### 方式二：命令行

```bash
pip install -r requirements.txt
cd v4
python knowledge_ball.py
```

依赖：`PyQt6==6.7.1`、`python-docx==1.1.2`（见 `requirements.txt`）

### 默认热键

| 热键 | 功能 |
|------|------|
| `Ctrl+Alt+K` | 快速捕捉条（随手记碎片） |
| `Ctrl+Alt+S` | 截图钉屏（框选区域置顶参考，支持批注） |
| `Ctrl+K` | 全局搜索（主窗口内） |
| `Esc` | 关闭卡片 / 取消截图 |

---

## 📦 打包分发

采用 PyInstaller **onedir** 模式（启动快、误报低）：

```bash
pip install pyinstaller
python -m PyInstaller --noconfirm --clean --distpath dist2 --workpath build2 FloatPulse.spec
```

产物 `dist2\FloatPulse\` 整个文件夹拷走即可运行，目标电脑无需 Python。
注意：打包运行时 exe 同级需有 `float_data/` 目录，知识卡片模式的数据源 `知识库.docx` 放在该目录内（源码运行时为项目根的 `float_data/`）。

---

## 🗂 目录结构

```
FloatPulse/
├── v4/                # 【开发主线】源码、测试与离屏验证脚本
├── plugins/           # 外置插件包（一包一目录，放进后重启生效）
├── tools/             # 工程脚本（仓库自动巡检等）
├── shared/            # 图标 / 打包 spec
├── float_data/        # 运行数据（碎片 / 笔记 / 任务 / 知识库.docx，自动生成）
├── docs/              # 文档与图片
└── 启动v4.bat          # 启动器
```

未列出的 `dist2/`（打包产物）与 `宣传页/FloatPulse.zip`（分发包）都不进版本库——前者可随时重建，后者走 GitHub Release 附件。

运行数据统一收纳在 `float_data/`（含 `temp_assets/` 与 `知识库.docx`），路径定位见 `v4/src/app_paths.py`；该目录已在 `.gitignore` 中，不会入库。

---

## 🧱 架构一览

```
UI 层    FloatingBall / CardWindow / MainWindow / 九个 Panel
业务层   TaskManager / NoteManager / FragmentManager / DocxManager
         ClipboardMonitor / TempAssetManager / ConfigManager / Hotkey
数据层   docx + 7 个 JSON（原子写入 / 损坏回退 / 增量指纹）
```

开发与测试约定见 `docs/` 下各主题文档；核心测试：

```bash
cd v4
python -m pytest tests/ -q     # 逻辑层 + 护栏测试
python test_init.py            # 启动冒烟
```

---

## 🗺 Roadmap

- [x] v4：截图钉屏、开源运营包
- [x] Obsidian Markdown 导出（笔记 / 碎片 / 任务 → vault，只读、幂等）
- [ ] `/` 命令面板（搜索即入口）

---

## 🤝 贡献

Issue / PR 均欢迎，见 [CONTRIBUTING.md](CONTRIBUTING.md)。
提交前请跑一遍测试套件；UI 改动请附离屏截图（`tools/run_gui_check.py`）。

## 📄 License

[MIT](LICENSE) © 2026 FloatPulse Contributors

---

## English

**FloatPulse** is a Windows floating-ball utility built with native PyQt6 — a lightweight, Electron-free "capture everything" hub living on your desktop edge.

- **Floating ball**: drag, edge-snap, hover to pop a quick card; drop files to capture; drop `.exe/.lnk` to create launchers
- **Quick capture**: global hotkey (`Ctrl+Alt+K`) note bar; clipboard text/image monitoring with dedup
- **Screenshot pin** (`Ctrl+Alt+S`): select any screen region and pin it as an always-on-top reference window
- **Main window**: fragments workbench, tasks with deadline grouping, notes, docx-based knowledge base, asset manager, URL & app launcher, global search
- **Engineering**: layered architecture, atomic JSON writes, single-instance guard, dual theme with contrast tests, pytest suite

```bash
pip install -r requirements.txt
cd v4 && python knowledge_ball.py
```

Windows 10/11, Python 3.10+. Licensed under MIT. Chinese UI only (i18n welcome).
