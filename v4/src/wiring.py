# -*- coding: utf-8 -*-
"""wiring：程序装配编排层（2026-10-05 D2/T05 外迁自 knowledge_ball.main()）。

定位（docs/拆分计划-wiring-2026-10-05.md §0.3）：**纯组装层**——只 connect、
只注入、不持业务状态（唯一例外：QuitCoordinator 的幂等收尾标记与
PluginHostBridge 的撤销栈，均已在各自模块说明生命周期等价性）。

main() 组装序（§4）：构造 → 注入 → 装配。本模块从宿主装配入口拆出的编排：

  - FullscreenCoordinator      : 全屏让位检测的启停/接线/番茄钟对齐
  - QuitCoordinator            : 退出收尾 + 托盘回调 + aboutToQuit 诊断
  - HeartbeatWatch             : UI 心跳看门狗（实例持 QTimer 引用防 GC）
  - PluginWiring               : 插件热键/总闸/页面注册编排
  - schedule_silent_update_check : 启动后延迟静默检查更新
  - wire_pomodoro_notifications  : 番茄钟相位完成通知接线
  - wire_cross_window_signals    : 大小窗口数据双向同步（§映射 #1–8.7b）
  - wire_scheme_follow           : 系统深浅色「跟随系统」整链路换肤
  - maybe_show_onboarding        : 首启三步欢迎向导

铁律：

  1. **禁止 import knowledge_ball / main_window / card_window**（防 import
     环）；宿主对象以鸭子类型注入，只调用其公开 API（D3 收口后的公开面）；
  2. 不声明 pyqtSignal（ QObject 信号表仍集中在各自宿主类）；
  3. 连接顺序与原 main() 内联实现一致（先 connect 后调用，时序不变）；
  4. 行为零变化：各方法体自 knowledge_ball 闭包逐字搬运，仅把闭包捕获的
     main() 局部变量换成构造注入的引用槽。
"""

import time
from typing import Any, Callable, Dict

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication, QSystemTrayIcon

from src.ai_server import AI_SERVER
from src.hotkey_binding import reapply_hotkey_bindings
from src.logger import get_logger, mark_session_end
from src.pomodoro import PHASE_FOCUS
from src.theme import DEFAULT_THEME, resolve_theme_name
from src.toast import ToastCenter


# ======================================================================
# 全屏让位（B8）
# ======================================================================
class FullscreenCoordinator:
    """全屏应用检测编排：启停开关 + 让位回调 + 番茄钟对齐。

    fs_watcher     : FullscreenWatcher（谁在前台全屏）
    config_manager : ConfigManager（hide_on_fullscreen 开关）
    ball           : FloatingBall（set_fullscreen_hidden / pomodoro_busy，
                     均为 D3 公开 API）
    """

    def __init__(self, fs_watcher: Any, config_manager: Any, ball: Any):
        self._fs_watcher = fs_watcher
        self._config = config_manager
        self._ball = ball

    def apply(self) -> None:
        """按配置启停全屏检测（设置页开关变更时重新应用）"""
        if self._config.get("hide_on_fullscreen", True):
            if not self._fs_watcher.is_running():
                self._fs_watcher.start()
        else:
            if self._fs_watcher.is_running():
                self._fs_watcher.stop()
            self._ball.set_fullscreen_hidden(False)

    def _on_fullscreen_changed(self, is_fs: bool) -> None:
        """前台全屏应用出现/退出 → 悬浮球自动让位/恢复

        例外：番茄钟计时中（含暂停）不让位——球上进度环在显示
        倒计时，藏起来就看不到剩余时间了（用户约定 2026-09-27）。
        """
        if is_fs and self._ball.pomodoro_busy():
            get_logger().info("全屏检测：番茄钟计时中，悬浮球保持可见不让位")
            return
        self._ball.set_fullscreen_hidden(is_fs)
        get_logger().info(
            f"全屏检测：{'进入全屏，悬浮球让位' if is_fs else '退出全屏，悬浮球恢复'}")

    def _sync_for_pomodoro(self, _state: str) -> None:
        """番茄钟开始/结束 → 与全屏让位状态对齐。

        计时开始时若正处于全屏，取消让位把球亮出来；
        计时结束/停止时若仍处于全屏，补做让位。
        """
        if not self._fs_watcher.is_running() or not self._fs_watcher.is_fullscreen():
            return
        self._ball.set_fullscreen_hidden(not self._ball.pomodoro_busy())

    def wire(self) -> "FullscreenCoordinator":
        """完成全部连接并按配置启停；返回 self 便于 main() 链式持有"""
        self._fs_watcher.fullscreen_changed.connect(self._on_fullscreen_changed)
        self.apply()
        # 番茄钟状态变更 → 与全屏让位状态对齐
        # （公开中继信号，勿戳 ball._pomodoro 私有成员）
        self._ball.pomodoro_state_changed.connect(self._sync_for_pomodoro)
        return self


# ======================================================================
# 退出收尾 + 托盘回调（1.4 / F1）
# ======================================================================
class QuitCoordinator:
    """退出与托盘编排：安全退出、显隐切换、aboutToQuit 诊断日志。

    幂等引擎 ``shutdown_once`` 由宿主注入（模块级函数钉死在
    knowledge_ball.py，test_quit_cleanup 回归面），本类只持有收尾状态标记
    ``_shutdown_state`` 与**晚绑定引用槽**——截图/插件热键管理器、截图钉屏、
    剪贴板监听在 main() 集成段才创建，经 register_resources 统一注入；
    首次收尾调用必然发生在事件循环期（与原闭包晚绑定语义一致）。
    """

    def __init__(self, *, ball: Any, main_window: Any, card_window: Any,
                 sticky_manager: Any, config_manager: Any,
                 shutdown_once: Callable):
        self._ball = ball
        self._main_window = main_window
        self._card_window = card_window
        self._sticky_manager = sticky_manager
        self._config = config_manager
        self._shutdown_once = shutdown_once
        self._shutdown_state: Dict[str, bool] = {"done": False}
        # 晚绑定引用槽（register_resources 注入前为 None；
        # 步骤经 shutdown_once 的单步容错保护，未注入时只告警不阻断）
        self._shot_hotkey_mgr: Any = None
        self._plugin_hotkey_mgr: Any = None
        self._screenshot_pin: Any = None
        self._clipboard_monitor: Any = None

    def register_resources(self, shot_hotkey_mgr: Any, plugin_hotkey_mgr: Any,
                           screenshot_pin: Any,
                           clipboard_monitor: Any) -> None:
        """晚注入引用槽：各组件在集成段创建完毕后调用一次"""
        self._shot_hotkey_mgr = shot_hotkey_mgr
        self._plugin_hotkey_mgr = plugin_hotkey_mgr
        self._screenshot_pin = screenshot_pin
        self._clipboard_monitor = clipboard_monitor

    def shutdown_resources(self) -> None:
        """幂等收尾：热键管理器注销 + AI_SERVER.stop + 剪贴板监听停止"""
        self._shutdown_once(self._shutdown_state, [
            # global_hotkey.py：程序退出前务必 unregister_all()，否则
            # 组合键残留占用到进程结束
            ("截图热键注销", lambda: self._shot_hotkey_mgr.unregister_all()),
            ("插件热键注销", lambda: self._plugin_hotkey_mgr.unregister_all()),
            # 命令直达键（2026-10-06）：管理器在 MainWindow.__init__ 创建，
            # 经宿主公开方法注销（getattr 守卫——旧宿主无此方法时跳过）
            ("命令直达键注销", self._unregister_command_hotkeys),
            # AI 本地服务（llama-server）：退出时主动停止，不再只靠 JobObject 兜底
            ("AI 本地服务停止", lambda: AI_SERVER.stop()),
            # 剪贴板监听断开（stop 自身幂等：未启动时直接返回）
            ("剪贴板监听停止", lambda: self._clipboard_monitor.stop()),
        ])

    def _unregister_command_hotkeys(self) -> None:
        """注销主窗命令直达键（宿主公开 API；缺方法时静默跳过）"""
        unreg = getattr(self._main_window, "unregister_command_hotkeys", None)
        if callable(unreg):
            unreg()

    def safe_quit(self) -> None:
        """安全退出函数：重置卡片状态为默认首页，再退出程序"""
        self.shutdown_resources()
        # 关闭截图覆盖层与全部钉图（V4 截图钉屏；晚绑定：集成代码在其后定义）
        try:
            self._screenshot_pin.close_all()
        except Exception:
            pass
        # 桌面便签：几何立即落盘后收掉全部窗口（笔记数据保留）
        try:
            self._sticky_manager.save_now()
            self._sticky_manager.close_all()
        except Exception:
            pass
        # 重置卡片窗口状态为默认首页
        self._card_window.reset_to_home()
        self._main_window.allow_close = True
        QApplication.quit()

    def toggle_main_window(self) -> None:
        """托盘单击：主窗口显隐切换"""
        if self._main_window.isVisible():
            self._main_window.hide()
        else:
            self._main_window.show()
            self._main_window.raise_()
            self._main_window.activateWindow()

    def toggle_ball_visibility(self) -> None:
        """托盘菜单：显示/隐藏悬浮球（开关落盘，重启保持）"""
        visible = not self._ball.isVisible()
        self._ball.setVisible(visible)
        self._config.set("ball_visible", visible)
        self._config.save()

    def bind_about_to_quit(self, app: Any, fragment_manager: Any,
                           wakeup_stop: Dict[str, bool]) -> None:
        """接入 aboutToQuit：退出诊断日志 + 兜底落盘（原 _on_about_to_quit）"""
        coordinator = self

        def _on_about_to_quit():
            # 正常退出标记：必须排在所有退出日志之前（见 mark_session_end），
            # 与启动标记圈出本次会话的日志区间，供人工排障
            mark_session_end()
            get_logger().info("程序准备退出（aboutToQuit 信号触发）")
            # 显式收尾（1.4，幂等）：若 safe_quit 已收尾过则直接跳过
            try:
                coordinator.shutdown_resources()
            except Exception:
                pass
            # 通知唤醒后台线程停止（避免退出后仍阻塞等待）
            try:
                wakeup_stop["flag"] = True
            except Exception:
                pass
            # 强制落盘所有未决碎片变更（去抖窗口内可能仍有待写数据）
            try:
                if fragment_manager is not None:
                    fragment_manager.flush()
            except Exception:
                pass
            # 强制落盘未决的笔记编辑（防抖窗口内可能仍有待写数据）
            try:
                coordinator._main_window.flush_pending_notes()
            except Exception:
                pass
            # 立即落盘悬浮球位置（C4）：兜底 600ms 防抖窗口内尚未写入的位置
            try:
                coordinator._ball.save_position_now()
            except Exception:
                pass
            # 立即落盘主窗口几何：同样是兜底防抖窗口（窗口在托盘态时不可见）
            try:
                _save_geom = getattr(coordinator._main_window,
                                     "save_geometry_now", None)
                if _save_geom is not None:
                    _save_geom()
            except Exception:
                pass

        app.aboutToQuit.connect(_on_about_to_quit)


# ======================================================================
# UI 心跳看门狗
# ======================================================================
class HeartbeatWatch:
    """UI 心跳看门狗：事件循环阻塞 >800ms 时记录（诊断卡顿/未响应）。

    QTimer 在主线程事件循环里调度；循环被长任务阻塞时下一跳会迟到，
    相邻两跳的间隔 = 实际阻塞时长。平时零输出，只在真卡顿时留痕。
    实例持有 QTimer 引用防 GC（原 main() 局部 _hb_timer 语义）。
    """

    def __init__(self, threshold_ms: int = 800, interval_ms: int = 250):
        self._threshold_ms = threshold_ms
        self._last = time.monotonic()
        self._timer = QTimer()
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def _tick(self) -> None:
        now = time.monotonic()
        gap = (now - self._last) * 1000
        self._last = now
        if gap > self._threshold_ms:
            get_logger().warning(
                f"[UI心跳] 事件循环阻塞 {gap:.0f}ms（本条在阻塞结束后补记）")


# ======================================================================
# 插件装配编排
# ======================================================================
class PluginWiring:
    """插件系统装配编排：热键绑定 / 总闸 / 页面注册 / 保留集刷新。

    loader/registry/ctx : PluginLoader / ActionRegistry / PluginContext
    hotkey_mgr          : 插件专用 GlobalHotkeyManager（重注册会
                          unregister_all()，与截图热键管理器分离）
    main_window / ball  : 宿主窗口与悬浮球（仅公开 API）
    """

    def __init__(self, *, loader: Any, registry: Any, ctx: Any,
                 hotkey_mgr: Any, config_manager: Any, main_window: Any,
                 ball: Any):
        self._loader = loader
        self._registry = registry
        self._ctx = ctx
        self._hotkey_mgr = hotkey_mgr
        self._config = config_manager
        self._main_window = main_window
        self._ball = ball

    def apply_hotkeys(self) -> None:
        """按当前注册表绑定插件热键；核心热键优先级最高，冲突的插件让位。

        D4 样板收敛：注销 + 注册 + 失败告警走 reapply_hotkey_bindings，
        这里只负责按注册表筛出绑定表（冲突让位 / 停用跳过）。
        """
        core_norm = {
            str(self._config.get("screenshot_hotkey", "Ctrl+Alt+S"))
            .strip().lower().replace(" ", ""),
        }
        bindings = []
        for act in self._registry.all_actions():
            hotkey = act.declared_hotkey()
            if not hotkey or not act.enabled():
                continue
            if hotkey in core_norm:
                get_logger().warning(
                    f"[插件] 热键与核心功能冲突，插件让位：{act.hotkey}（{act.id}）")
                continue
            bindings.append((
                act.hotkey,
                lambda aid=act.id: self._registry.trigger(aid, self._ctx),
                f"[插件] 热键注册失败（可能被占用）：{act.hotkey}（{act.id}）",
            ))
        reapply_hotkey_bindings(self._hotkey_mgr, bindings)

    def apply_plugins(self, _enabled=None) -> None:
        """插件总闸：开 → 加载/登记；关 → 摘动作 + 摘页面（模块仍驻留）"""
        if self._config.get("plugins_enabled", True):
            self._loader.load_all()
            self._apply_disabled_plugins()
            self._register_plugin_pages()
        else:
            self._unregister_all_plugin_pages()
            self._loader.deactivate()
        self._ball.refresh_plugin_menu()
        self.apply_hotkeys()

    def _apply_disabled_plugins(self) -> None:
        """按配置回置「被单独停用」的插件状态（2026-09-27）。

        插件中心的启停开关此前只改内存（重启即复原，用户对「停用」的预期落空），
        现在开关写入 config 的 ``plugins_disabled``，这里在登记完成后统一回置。
        未知 id 静默跳过——配置里残留已删除插件的 id 是正常情况。
        """
        for pid in (self._config.get("plugins_disabled", None) or []):
            if not isinstance(pid, str) or not pid:
                continue
            n = self._loader.set_plugin_enabled(pid, False)
            if not n:
                get_logger().info(
                    f"[插件] 配置里记录了停用 {pid}，但该插件未加载（已忽略）")

    def _register_plugin_pages(self) -> None:
        """页面插件：manifest.page → 主窗口导航页（2026-09-27）。

        create_page 抛异常只跳过该插件，绝不拖垮加载（与动作同级容错）。
        register_plugin_page 幂等，插件中心「重新扫描」重入安全。
        被**单独停用**的插件（plugins_disabled）跳过——动作已被回置摘除，
        页面若照常注册就会出现「停用了页面还挂在导航栏」（2026-09-27 修复）。
        """
        disabled = {p for p in (
            self._config.get("plugins_disabled", None) or [])
            if isinstance(p, str) and p}
        for lp in self._loader.loaded_plugins():
            if lp.plugin_id in disabled:
                get_logger().info(f"[插件] 页面跳过（插件被停用）：{lp.plugin_id}")
                continue
            page_spec = lp.manifest.get("page")
            if not page_spec:
                continue
            page_key = f"plugin:{lp.plugin_id}"
            try:
                widget = lp.plugin.create_page(lp.ctx)
                if widget is None:
                    get_logger().warning(
                        f"[插件] 声明了 page 但 create_page 返回空，跳过：{lp.plugin_id}")
                    continue
                self._main_window.register_plugin_page(
                    page_key, page_spec["title"], widget)
                lp.page_key = page_key     # 插件热键动作经 parent_window 切页用
                get_logger().info(f"[插件] 页面已注入主窗口：{page_key}")
            except Exception as exc:       # noqa: BLE001 - 页面失败不拖垮插件系统
                get_logger().warning(
                    f"[插件] 页面注入失败，跳过：{lp.plugin_id}（{exc!r}）",
                    exc_info=True)

    def _unregister_all_plugin_pages(self) -> None:
        """总闸关闭 → 把全部插件页从主窗口摘掉（2026-09-27）。

        此前总闸关只 deactivate 摘动作/热键，页面与导航键残留在主窗口。
        page_key 记录在 LoadedPlugin 上（注册时写入），逐个注销后清空；
        独立 try 容错——页面注销失败不阻断总闸关闭流程。
        """
        unreg = getattr(self._main_window, "unregister_plugin_page", None)
        if not callable(unreg):
            return
        for lp in self._loader.loaded_plugins():
            key = getattr(lp, "page_key", None)
            if not key:
                continue
            try:
                unreg(key)
            except Exception:              # noqa: BLE001 - 单页失败不阻断
                get_logger().warning(f"[插件] 页面注销失败：{key}", exc_info=True)
            try:
                lp.page_key = None
            except Exception:              # noqa: BLE001
                pass

    def refresh_core_hotkey_reservation(self) -> None:
        """核心热键变更 → 刷新保留集并重绑插件热键（插件始终让位）"""
        self._registry.reserve_hotkeys((
            self._config.get("screenshot_hotkey", "Ctrl+Alt+S"),
        ))
        self.apply_hotkeys()

    def wire(self) -> None:
        """主窗口插件相关信号接线（连接顺序与原 main() 一致）"""
        self._main_window.plugins_changed.connect(self.apply_plugins)
        self._main_window.screenshot_changed.connect(
            self.refresh_core_hotkey_reservation)


# ======================================================================
# 独立装配函数
# ======================================================================
def schedule_silent_update_check(config_manager: Any, tray: Any) -> None:
    """2.3 启动后延迟静默检查更新（默认开，每天至多一次）。

    立场不变：不自动下载、失败静默、不携带任何本机数据。延迟 15 秒
    错开启动高峰；频率判定走 update_checker 的纯逻辑（should_check_now
    / mark_checked），托盘只在真的发现新版本时打扰一次。
    """
    def _silent_update_check():
        from src import update_checker
        from src.app_version import APP_VERSION
        from src.plugin_net import make_async_getter
        if not update_checker.should_check_now(config_manager):
            return
        # 发起即记账（成功失败都算当天已查），写盘在这里负责
        update_checker.mark_checked(config_manager)
        config_manager.save()

        def _on_silent_result(result: dict):
            if not result.get("ok"):
                return                  # 离线 / 限流：静默，不扰民
            tag = update_checker.extract_tag(result.get("body") or "")
            if not tag or not update_checker.is_newer(tag):
                return                  # 无新版：不动 latest_known_version
            config_manager.set("latest_known_version", tag)
            config_manager.save()
            tray.show_message(
                f"发现新版本 {tag}",
                f"当前 v{APP_VERSION}——到 设置 → 关于 查看更新内容",
                QSystemTrayIcon.MessageIcon.Information, 8000)
            get_logger().info(
                f"[更新] 静默检查发现新版本 {tag}（当前 v{APP_VERSION}）")

        getter = make_async_getter()
        if not getter(update_checker.RELEASES_API_URL,
                      update_checker.check_headers(),
                      update_checker.CHECK_TIMEOUT_S, _on_silent_result):
            pass  # 桥拒绝时也会回调一次 ok=False 的结果，静默即可

    QTimer.singleShot(15000, _silent_update_check)


def wire_pomodoro_notifications(ball: Any, main_window: Any, tray: Any) -> None:
    """番茄钟（球体进度环 + 右键菜单 + 任务绑定，V4）通知接线。"""

    def _on_pomodoro_phase_finished(phase, title):
        """相位计满 → 托盘气泡（非模态）+ 主窗口任务页刷新（番茄计数变了）"""
        if phase == PHASE_FOCUS:
            body = (f"专注完成「{title}」，休息一下！" if title
                    else "专注完成，休息一下！")
            tray.show_message(
                "🍅 番茄钟", body,
                QSystemTrayIcon.MessageIcon.Information, 6000)
            main_window.refresh_tasks()
        get_logger().info(
            f"[番茄钟] 相位完成: phase={phase}, task={title or '自由专注'}")

    ball.pomodoro_phase_finished.connect(_on_pomodoro_phase_finished)
    main_window.pomodoro_changed.connect(ball.apply_pomodoro_config)
    # 任务页右键「专注此任务」→ 球体开始绑定式专注
    main_window.task_focus_requested.connect(ball.start_focus)


def wire_cross_window_signals(ball: Any, main_window: Any,
                              config_manager: Any, docx_manager: Any,
                              temp_asset_manager: Any,
                              clipboard_monitor: Any,
                              sticky_manager: Any) -> None:
    """信号槽桥梁：大小窗口数据双向同步（原 main() #2–#8.7b 段）。

    连接顺序与原内联实现逐条一致；getattr 守卫（image_captured）逐字搬运。
    """
    # 2. 小卡片数据变更 → 大窗口刷新对应面板（若可见）
    def _on_card_data_changed(kind):
        if kind == "task":
            main_window.refresh_tasks()
            ball.refresh_badge()      # 任务增删/完成后同步球体徽标（A4）
        elif kind == "note":
            main_window.refresh_notes()
        elif kind == "asset":
            # 素材变更要刷素材页（原实现误刷碎片页，导致大窗口素材列表不更新）
            main_window.refresh_temp_assets()
        elif kind == "fragment":
            main_window.refresh_fragments()
    ball.card_window.data_changed.connect(_on_card_data_changed)

    # 3. 大窗口主题切换 → 悬浮球 + 小卡片应用主题
    main_window.theme_changed.connect(ball.apply_theme)
    # 3b. 主题切换 → 桌面便签全部换肤
    main_window.theme_changed.connect(sticky_manager.apply_theme)
    # 3c. 主题切换 → 存活中的轻提示气泡原地换肤（ToastCenter 类级广播，
    #     新气泡由 show_toast 传 current_theme，双保险）
    main_window.theme_changed.connect(ToastCenter.set_theme)

    # 3b+. 壁纸参数改动（图 / 适配 / 模糊 / 遮罩 / 不透明度）→ 小卡片跟进
    # 背景。主窗在 refresh_wallpaper 里自己刷 GlassPanel，不走
    # theme_changed 全量换肤（那会把卡片整套 QSS / 图标重刷一遍，
    # 设置页步进器每档 ± 都白付一次）。
    def _on_wallpaper_changed():
        ball.card_window.apply_background_from_config(config_manager)
    main_window.wallpaper_changed.connect(_on_wallpaper_changed)

    # 4. 剪贴板新增碎片 → 大窗口刷新碎片页面（若可见）+ 球体脉冲反馈
    def _on_fragment_added(_content=None):
        main_window.refresh_fragments()
        ball.pulse()                  # 成功反馈（A4）
    clipboard_monitor.fragment_added.connect(_on_fragment_added)

    # 4b. 剪贴板图片入库（Y2）→ 刷新素材页面 + 球体脉冲 + 轻提示
    # 文案纪律（轻提示设计稿铁律④）：气泡内禁 emoji——图标由图标管线
    # 按语义绘制；长后缀拆进 msg 副标题，标题保持一行可读。
    def _on_clipboard_image(_asset_id=None):
        main_window.refresh_temp_assets()
        ball.card_window.notify_assets_changed()
        main_window.show_toast("剪贴板图片已存入素材池",
                               msg="素材页可查看", kind="success")
        ball.pulse()
    if getattr(clipboard_monitor, "image_captured", None) is not None:
        clipboard_monitor.image_captured.connect(_on_clipboard_image)

    # 5. 大窗口数据变更 → 小卡片刷新
    def _on_main_data_changed(kind):
        if kind == "task":
            ball.refresh_badge()      # 大窗口任务变更 → 球体徽标同步（A4）
            if ball.card_window.isVisible():
                ball.card_window.refresh_page("task")
        elif kind == "knowledge":
            # 知识库编辑后重新加载卡片并同步到小卡片
            new_cards = docx_manager.get_cards()
            ball.update_cards(new_cards)
        elif kind == "nav" and ball.card_window.isVisible():
            # 网址导航编辑后刷新小卡片导航页
            ball.card_window.refresh_page("nav")
        elif kind == "asset":
            # 临时素材变更后刷新小卡片素材页（可见立即重建，隐藏则置脏）
            ball.card_window.notify_assets_changed()
        elif kind == "fragment" and ball.card_window.isVisible():
            # 碎片变更后刷新小卡片碎片页
            ball.card_window.refresh_page("fragment")
        elif kind in ("note", "task"):
            # 笔记/任务被删除后，对应桌面便签自动关闭（孤儿窗口不留）
            sticky_manager.validate_open_windows()
    main_window.data_changed.connect(_on_main_data_changed)

    # 5b. 任务便签里改了备注/完成态 → 走主窗口 data_changed 刷新任务页
    #（data_changed("task") 又会触发上面 validate_open_windows，无副作用）
    sticky_manager.task_data_changed.connect(
        lambda: main_window.data_changed.emit("task"))

    # 6. 主窗口悬浮球开关 → 显示/隐藏悬浮球
    main_window.ball_visibility_changed.connect(
        lambda visible: ball.setVisible(visible)
    )

    # 7. 主窗口小卡片保持显示开关 → 实时应用并刷新卡片关闭按钮可见性
    def _on_card_always_show_changed(always_show: bool):
        # 配置已由 main_window 保存；这里刷新关闭按钮与内容净空（两者必须一起变，
        # 否则按钮出现了、内容却没让位，又会压住首行）
        if ball.card_window.isVisible():
            ball.card_window.refresh_always_show_layout()
    main_window.card_always_show_changed.connect(_on_card_always_show_changed)

    # 8. 主窗口临时素材上限变更 → 更新管理器并清理过期素材
    def _on_asset_limits_changed(max_count: int, max_days: int,
                                 max_file_mb: int):
        temp_asset_manager.update_limits(
            max_assets=max_count, max_days=max_days, max_file_mb=max_file_mb)
        # 清理后刷新大小窗口的素材页
        main_window.refresh_temp_assets()
        ball.card_window.notify_assets_changed()
        get_logger().info(
            f"临时素材上限已更新: max_count={max_count}, max_days={max_days}, "
            f"max_file_mb={max_file_mb}")
    main_window.asset_limits_changed.connect(_on_asset_limits_changed)

    # 8.5 主窗口动画速度档位变更 → 实时应用到悬浮球（统一缩放动画时长）
    main_window.anim_speed_changed.connect(ball.set_anim_speed)

    # 8.6 主窗口空闲吸边隐藏秒数变更 → 实时应用到悬浮球
    main_window.auto_hide_seconds_changed.connect(ball.set_auto_hide_seconds)

    # 8.6b 主窗口自动隐藏总开关变更 → 实时应用到悬浮球
    # （关闭时停表并把半隐藏的球滑回屏内）
    main_window.auto_hide_enabled_changed.connect(ball.set_auto_hide_enabled)

    # 8.7 主窗口悬浮球大小变更 → 实时应用到悬浮球（保持球心不动，位置即落盘）
    main_window.ball_size_changed.connect(ball.apply_ball_size)

    # 8.7b 主窗口小卡片图标大小变更 → 实时应用到小卡片软件导航页
    main_window.mini_icon_size_changed.connect(
        ball.card_window.apply_icon_size)


def wire_scheme_follow(main_window: Any, config_manager: Any) -> None:
    """3c. 系统深浅色变化 → 「跟随系统」模式整链路换肤（3.1）。

    只复用既有换主题路径：主窗口 _apply_theme 重取配色（get_colors 内部
    经 resolve_theme_name 现读系统 scheme），theme_changed 再把具体主题
    名广播给球 / 卡片 / 便签 / 截图钉屏；显式 light/dark 模式忽略。
    """
    def _on_system_scheme_changed(_scheme):
        if config_manager.get("theme", DEFAULT_THEME) != "follow":
            return
        resolved = resolve_theme_name("follow")
        if main_window.current_theme == "follow":
            main_window.reapply_theme()
        main_window.theme_changed.emit(resolved)
        get_logger().info(f"[主题] 系统深浅色变化，跟随系统 → {resolved}")

    from PyQt6.QtGui import QGuiApplication as _QGuiApp
    _scheme_hints = (_QGuiApp.instance().styleHints()
                     if _QGuiApp.instance() else None)
    if _scheme_hints is not None and hasattr(_scheme_hints,
                                             "colorSchemeChanged"):
        _scheme_hints.colorSchemeChanged.connect(_on_system_scheme_changed)


def maybe_show_onboarding(config_manager: Any, main_window: Any) -> None:
    """3.4 首启引导：首次使用时弹出三步欢迎向导（设置页「🚀 启动与
    系统 → 🔄 重看引导」可重看，同一 dialog）。

    延迟 800ms：不抢启动闪屏淡出的风头，主窗口先完整露脸。
    _onboarding_ref 为函数局部（自包含，无晚绑定），防重入引用不外泄。
    """
    from src import onboarding
    _onboarding_ref = {"dlg": None}

    def _show_onboarding():
        # 防重入：已开着（设置页路径）就置前，不再叠一个
        existing = _onboarding_ref["dlg"]
        if existing is not None and existing.isVisible():
            existing.raise_()
            existing.activateWindow()
            return
        dlg = onboarding.WelcomeDialog(host=main_window, parent=main_window)
        _onboarding_ref["dlg"] = dlg
        dlg.exec()
        # 关闭即落盘：完成 / Esc / 跳过 / 标题栏 × 任何路径都置 True，
        # 之后不再骚扰（设置页「重看引导」路径在 settings_panel 里
        # 经 finished 信号走同一个 mark_done，闭环同源）
        onboarding.mark_done(config_manager)
        dlg.deleteLater()
        _onboarding_ref["dlg"] = None

    if onboarding.should_show(config_manager):
        QTimer.singleShot(800, _show_onboarding)
