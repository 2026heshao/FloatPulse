# -*- coding: utf-8 -*-
"""
====================================================================
热键绑定助手 - ConfigHotkeyBinding / reapply_hotkey_bindings
====================================================================
D4 样板收敛（成熟化路线图 4.3，2026-09-30）：截图钉屏 / 插件热键各自持有
GlobalHotkeyManager（独立实例是**对的**——避免 unregister_all 互踢，见 V4架构
审查 2026-09-26 附录），但「注销 → 读配置 → 重注册 → 失败告警」的样板在
knowledge_ball 里曾重复三遍（含 2026-10-03 已整体删除的快捕条），且随热键数量
翻倍。本模块把样板收敛为绑定表驱动：

  - ConfigHotkeyBinding     : 单个配置驱动热键（开关键 + 热键串键 + 回调），
                              reapply() 与原手写样板逐行等价；
  - reapply_hotkey_bindings : 绑定表 [(热键串, 回调, 失败告警), ...] 的批量
                              注册（插件热键走这里；冲突让位过滤留在装配侧）。

行为契约（与原 knowledge_ball 手写实现逐字节等价）：
  1. 每次重注册前先 unregister_all()——旧注册必被踢掉；
  2. 总开关关闭 → 保持注销态直接返回（不注册任何键）；
  3. 注册失败只写 warning 日志，不抛异常、不回滚。
====================================================================
"""

from src.logger import get_logger


class ConfigHotkeyBinding:
    """配置驱动的单个全局热键（当前用于截图钉屏）。

    参数：
      manager        : GlobalHotkeyManager（独立实例，勿与其他绑定共用）
      config         : ConfigManager（get(key, default) 读开关与热键串）
      enabled_key    : 开关键名（bool，缺省 True）
      hotkey_key     : 热键串键名
      default_hotkey : 热键串缺省值（如 "Ctrl+Alt+S"）
      callback       : 热键触发回调（无参）
      fail_log       : 注册失败告警模板（"{hotkey}" 占位，输出与原实现一致）
      pre_hooks      : unregister_all 之后、开关判定之前依次调用的钩子。
                       例如配置变更时先收起某个表面，顺序与原实现一致
                       （先注销、再收起、再判开关）。
    """

    def __init__(self, manager, config, enabled_key, hotkey_key,
                 default_hotkey, callback, fail_log, pre_hooks=()):
        self._manager = manager
        self._config = config
        self._enabled_key = enabled_key
        self._hotkey_key = hotkey_key
        self._default_hotkey = default_hotkey
        self._callback = callback
        self._fail_log = fail_log
        self._pre_hooks = tuple(pre_hooks)

    @property
    def manager(self):
        """所属 GlobalHotkeyManager（退出收尾 unregister_all 用）"""
        return self._manager

    def reapply(self) -> bool:
        """按当前配置重注册（开关/热键串变更/恢复默认时调用）。

        顺序：unregister_all → pre_hooks → 开关判定 → 注册（失败仅告警）。
        返回 True=已注册；False=总开关关闭或注册失败。
        """
        self._manager.unregister_all()
        for hook in self._pre_hooks:
            hook()
        if not self._config.get(self._enabled_key, True):
            return False
        hotkey_text = self._config.get(self._hotkey_key, self._default_hotkey)
        if not self._manager.register(hotkey_text, self._callback):
            get_logger().warning(self._fail_log.format(hotkey=hotkey_text))
            return False
        return True

    def unregister(self):
        """注销本绑定全部热键（转发 manager.unregister_all）"""
        self._manager.unregister_all()


def reapply_hotkey_bindings(manager, bindings) -> int:
    """注销全部后按绑定表 [(hotkey_text, callback, fail_log), ...] 逐一注册。

    fail_log 为调用方拼好的最终告警文案（如插件表按 act.hotkey/act.id
    预先 f-string 化）。单条失败只告警不中断（后续热键照常尝试），
    返回成功条数。插件热键的「核心热键冲突让位」过滤在装配侧完成——
    本函数只做注销 + 注册 + 失败告警的样板。
    """
    manager.unregister_all()
    ok = 0
    for hotkey_text, callback, fail_log in bindings:
        if manager.register(hotkey_text, callback):
            ok += 1
        else:
            get_logger().warning(fail_log)
    return ok
