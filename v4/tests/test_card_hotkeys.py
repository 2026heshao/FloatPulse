# -*- coding: utf-8 -*-
"""小卡片 Ctrl+1~7 切页快捷键护栏（2026-10-04 细节强化 P2-7 最小集）。

两层钉法（缺一即红）：
  1. 存在层——findChildren(QShortcut) 钉死键位集合恰为 Esc + Ctrl+1~7
     （少注册/多注册/键序与 _TAB_KEYS 错位都红）；
  2. 路由层——对每个 Ctrl+N 直接 emit activated，断言 _switch_mode
     真切到对应页（防「快捷键存在但连错槽」的哑弹）。

QTest 真实击键路径（焦点/活动窗口语义）在离屏平台不稳定，不在此
钉——存在层已保证键位注册，路由层保证信号到行为的端到端。
"""
import os
import sys

import pytest
from PyQt6.QtGui import QShortcut

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def card(qapp):
    from src.card_window import CardWindow
    w = CardWindow()
    yield w
    w.deleteLater()


def _shortcuts_by_key(card):
    return {sc.key().toString(): sc for sc in card.findChildren(QShortcut)}


# ====================================================================
# 1. 存在层：键位集合恰为 Esc + Ctrl+1~7
# ====================================================================
def test_keyset_is_exactly_esc_and_ctrl_1_to_7(card):
    keys = sorted(_shortcuts_by_key(card))
    expected = sorted(["Esc"] + ["Ctrl+%d" % n for n in range(1, 8)])
    assert keys == expected


# ====================================================================
# 2. 路由层：Ctrl+N 必须切到 _TAB_KEYS 第 N 项对应页
# ====================================================================
def test_ctrl_n_routes_to_matching_tab(card):
    from src.card_window import _TAB_KEYS
    by_key = _shortcuts_by_key(card)
    for i, tab_key in enumerate(_TAB_KEYS[:7]):
        card._switch_mode(_TAB_KEYS[0])        # 每档先回首页再切，防残留
        assert card._stack.currentIndex() == 0
        by_key["Ctrl+%d" % (i + 1)].activated.emit()
        assert card._last_mode == tab_key
        assert card._stack.currentIndex() == i


# ====================================================================
# 3. Esc 语义不回归：仍是隐藏（hide）而非退出
# ====================================================================
def test_esc_hides_not_closes(card, qapp):
    card.show()
    qapp.processEvents()
    _shortcuts_by_key(card)["Esc"].activated.emit()
    assert not card.isVisible()
