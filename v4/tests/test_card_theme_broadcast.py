# -*- coding: utf-8 -*-
"""小卡片主题广播同步护栏（2026-10-03 用户报告「设置里的主题配色没有
同步到悬浮球的小卡片上」）

根因：CardWindow.apply_theme 曾有「主题未变提前返回」幂等守卫——强调色
变化时主窗 refresh_appearance 以**原主题名**重广播 theme_changed，全链
订阅方（主窗/悬浮球/便签/快捕条/截图钉屏）都无条件重设，唯独卡片在这
里短路，整批停在旧强调色上（卡片 QSS 由 get_card_window_qss 现读
accent 生成，重设即同步）。修复 = 去掉提前返回，保留非法值校验。

钉法（三层，缺一即红）：
  1. 非法主题名仍拒收（守卫语义收窄不误伤）
  2. 同主题重广播必须重进 _apply_style（spy 计数——直接钉死「不短路」）
  3. 强调色端到端：set_accent → 同主题 apply_theme → 容器 QSS 含新
     get_colors()["primary"] 色值（accent 全局态用 try/finally 复位，
     不污染同进程其他测试）
"""
import pytest

from src import accent, theme


@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(autouse=True)
def _reset_accent():
    """每例前后把强调色复位到出厂态（set_accent 是进程级全局状态）"""
    theme.set_accent(accent.DEFAULT_ACCENT, "")
    yield
    theme.set_accent(accent.DEFAULT_ACCENT, "")


def _make_card():
    from src.card_window import CardWindow
    return CardWindow()


# ====================================================================
# 1. 非法主题名仍拒收（守卫收窄不误伤）
# ====================================================================
def test_apply_theme_rejects_invalid(qapp):
    card = _make_card()
    before = card.current_theme
    card.apply_theme("rainbow")
    card.apply_theme("")
    card.apply_theme(None)
    assert card.current_theme == before


# ====================================================================
# 2. 同主题重广播必须重进 _apply_style（核心回归闸：钉死「不短路」）
# ====================================================================
def test_same_theme_reapply_restyles(qapp, monkeypatch):
    card = _make_card()
    assert card.current_theme in ("light", "dark")

    calls = []
    monkeypatch.setattr(
        card, "_apply_style", lambda: calls.append(1))

    card.apply_theme(card.current_theme)        # 同主题：旧实现在此短路
    assert calls, "同主题 apply_theme 也必须重设样式（强调色重广播链）"

    # 换主题路径行为不变：同样重设
    other = "light" if card.current_theme == "dark" else "dark"
    card.apply_theme(other)
    assert len(calls) == 2
    assert card.current_theme == other


# ====================================================================
# 3. 强调色端到端：同主题广播后容器 QSS 反映新强调色
# ====================================================================
def test_accent_change_propagates_via_same_theme_broadcast(qapp):
    card = _make_card()
    name = card.current_theme

    old_qss = card._container.styleSheet()
    old_primary = theme.get_colors(name)["primary"]

    # 换一个强调色：优先取色值确实不同的非默认 id，否则用自定义 hex
    chosen = None
    for ident in accent.ACCENT_IDS:
        if ident == accent.DEFAULT_ACCENT:
            continue
        theme.set_accent(ident, "")
        if theme.get_colors(name)["primary"] != old_primary:
            chosen = ident
            break
    if chosen is None:
        theme.set_accent(accent.DEFAULT_ACCENT, "#FF0000")
    try:
        new_primary = theme.get_colors(name)["primary"]
        assert new_primary != old_primary, "测试前提：强调色确实变了"

        card.apply_theme(name)                  # 同主题重广播（修复路径）

        new_qss = card._container.styleSheet()
        assert new_qss != old_qss, "QSS 必须重设（不能停在旧强调色）"
        assert str(new_primary).lower() in new_qss.lower(), (
            f"容器 QSS 未反映新强调色 primary={new_primary}")
    finally:
        theme.set_accent(accent.DEFAULT_ACCENT, "")


# ====================================================================
# 4. light/dark 直切行为回归（既有语义不因守卫收窄而变）
# ====================================================================
def test_theme_switch_still_updates_qss(qapp):
    from src.card_window import _card_shell_qss

    card = _make_card()
    other = "light" if card.current_theme == "dark" else "dark"

    old_qss = card._container.styleSheet()
    card.apply_theme(other)
    assert card.current_theme == other
    assert card._container.styleSheet() != old_qss
    # QSS 内容与 _apply_style 的确定性输出逐字一致（新主题令牌现捞）
    expected = (theme.get_card_window_qss(other)
                + _card_shell_qss(theme.get_colors(other)))
    assert card._container.styleSheet() == expected
