# -*- coding: utf-8 -*-
"""首启引导（成熟化 3.4）单元测试。

钉住的行为：
  1. 纯逻辑链：should_show（首次 True / 已完成 False）/ mark_done
     （置 True + 立即落盘）/ mark_show_again（置回 False，重看入口）
  2. WelcomeDialog 步进状态机：next / prev / skip 后 step 索引与
     按钮文案、可用态同步（首步禁用上一步、末步「开始使用」+藏跳过）
  3. 落盘闭环：任何路径关闭向导后 mark_done 都把 first_run_done 置 True，
     下一份 ConfigManager 重新加载后 should_show 为 False（不再骚扰）
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QDialog             # noqa: E402

from src import onboarding                                   # noqa: E402
from src.config import ConfigManager                         # noqa: E402
from src.onboarding import (                                 # noqa: E402
    WelcomeDialog, mark_done, mark_show_again, should_show,
)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class _FakeConfig:
    """dict 兜底配置替身（口径同 test_settings_nav）"""

    def __init__(self, data=None):
        self._d = dict(data or {})

    def get(self, key, default=None):
        return self._d.get(key, default)

    def set(self, key, value):
        self._d[key] = value
        return True

    def save(self):
        self.saved = True


# ====================================================================
# 纯逻辑：should_show / mark_done / mark_show_again
# ====================================================================
class TestOnboardingLogic:
    def test_fresh_config_should_show(self):
        assert should_show(_FakeConfig()) is True
        assert should_show(_FakeConfig({"first_run_done": False})) is True

    def test_done_config_should_not_show(self):
        assert should_show(_FakeConfig({"first_run_done": True})) is False

    def test_mark_done_persists(self, tmp_path):
        """mark_done 置 True 并落盘：重新加载后不再需要引导"""
        path = str(tmp_path / "config.json")
        cm = ConfigManager(path)
        assert should_show(cm) is True
        assert mark_done(cm) is True
        assert cm.get("first_run_done") is True
        cm2 = ConfigManager(path)           # 模拟下次启动重新读盘
        assert cm2.get("first_run_done") is True
        assert should_show(cm2) is False

    def test_mark_show_again_resets(self, tmp_path):
        """「重看引导」入口：置回 False 落盘；关闭后再 mark_done 复位"""
        path = str(tmp_path / "config.json")
        cm = ConfigManager(path)
        mark_done(cm)
        mark_show_again(cm)
        assert should_show(cm) is True
        mark_done(cm)
        assert should_show(cm) is False


# ====================================================================
# WelcomeDialog：三步导航状态机
# ====================================================================
class TestWelcomeDialogNavigation:
    def test_initial_state(self, qapp):
        dlg = WelcomeDialog()
        assert dlg.step == 0
        assert not dlg._prev_btn.isEnabled()       # 首步禁用「上一步」
        assert dlg._next_btn.text() == "下一步"
        assert not dlg._skip_btn.isHidden()        # 非末步显示「跳过」
        dlg.deleteLater()

    def test_next_advances_and_finish_accepts(self, qapp):
        dlg = WelcomeDialog()
        dlg.go_next()
        assert dlg.step == 1
        assert dlg._prev_btn.isEnabled()
        dlg.go_next()
        assert dlg.step == 2
        assert dlg._next_btn.text() == "开始使用"   # 末步按钮换文案
        assert dlg._skip_btn.isHidden()             # 末步藏「跳过」
        dlg.go_next()                               # 末步再下一步 = 开始使用
        assert dlg.result() == QDialog.DialogCode.Accepted
        dlg.deleteLater()

    def test_prev_bounded_at_first_step(self, qapp):
        dlg = WelcomeDialog()
        dlg.go_prev()                               # 首步上一步 = no-op
        assert dlg.step == 0
        dlg.go_next()
        dlg.go_next()
        dlg.go_prev()
        assert dlg.step == 1
        dlg.deleteLater()

    def test_set_step_clamped(self, qapp):
        dlg = WelcomeDialog()
        dlg._set_step(99)
        assert dlg.step == len(WelcomeDialog.STEPS) - 1
        dlg._set_step(-3)
        assert dlg.step == 0
        dlg.deleteLater()

    def test_skip_rejects(self, qapp):
        dlg = WelcomeDialog()
        dlg.skip()
        assert dlg.result() == QDialog.DialogCode.Rejected
        dlg.deleteLater()

    def test_step_count_is_three(self, qapp):
        dlg = WelcomeDialog()
        assert dlg._stack.count() == 3
        assert len(WelcomeDialog.STEPS) == 3
        dlg.deleteLater()


# ====================================================================
# 文案护栏：热键速查只写已注册/README 口径内的热键
# ====================================================================
class TestOnboardingCopy:
    def test_hotkey_rows_match_registered_defaults(self):
        keys = [row[0] for row in onboarding._HOTKEY_ROWS]
        # 与 knowledge_ball 注册、README「默认热键」表同口径
        assert "Ctrl+Alt+K" in keys
        assert "Ctrl+Alt+S" in keys
        # Ctrl+K 依赖 kb-search 插件，必须带「需安装」标注而非裸承诺
        ctrl_k = next(row for row in onboarding._HOTKEY_ROWS
                      if row[0] == "Ctrl+K")
        assert "kb-search" in ctrl_k[1]
        assert all("Ctrl+Q" not in k for k in keys)   # 未实现的热键不许出现

    def test_ball_tips_cover_readme_scenarios(self):
        text = "\n".join(onboarding._BALL_TIPS)
        for keyword in ("吸附", "悬停", "碎片池", ".exe", "托盘"):
            assert keyword in text, f"悬浮球页缺 README 口径要点：{keyword}"
