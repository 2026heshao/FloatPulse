# -*- coding: utf-8 -*-
"""启动静默更新检查（成熟化 2.3 被动提示）回归测试。

钉住的行为：
  1. should_check_now 真值表：开关关闭 → 不查；同日已查 → 不查；
     隔日 / 从未查过（空值）→ 查
  2. mark_checked 写入 last_update_check（仅内存，today 可注入；落盘
     与否由调用方决定——与 knowledge_ball 的「发起即记账」口径一致）
  3. config 新键默认值与类型（auto_check_updates=bool，last_update_check /
     latest_known_version=str），落盘后重载不丢
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import ConfigManager, DEFAULT_CONFIG        # noqa: E402
from src.update_checker import should_check_now, mark_checked  # noqa: E402


TODAY = "2026-09-30"
YESTERDAY = "2026-09-29"


@pytest.fixture()
def cm(tmp_path):
    """全新（文件不存在）的 ConfigManager：全部走默认值"""
    return ConfigManager(str(tmp_path / "config.json"))


# ====================================================================
# should_check_now 真值表
# ====================================================================
class TestShouldCheckNow:
    def test_never_checked_empty_value(self, cm):
        """last_update_check 为空（从未检查）→ 查"""
        assert cm.get("last_update_check", "") == ""
        assert should_check_now(cm, today=TODAY) is True

    def test_same_day_suppressed(self, cm):
        """今天已查过 → 不查（每天至多一次的频率护栏）"""
        cm.set("last_update_check", TODAY)
        assert should_check_now(cm, today=TODAY) is False

    def test_next_day_allowed(self, cm):
        """昨天查过、今天没查 → 查"""
        cm.set("last_update_check", YESTERDAY)
        assert should_check_now(cm, today=TODAY) is True

    def test_switch_off_wins(self, cm):
        """用户关掉自动检查 → 永不查（哪怕从未检查过）"""
        cm.set("auto_check_updates", False)
        assert should_check_now(cm, today=TODAY) is False

    def test_switch_off_wins_even_same_day_unchecked(self, cm):
        """开关优先级高于日期：关掉后隔日也不查"""
        cm.set("auto_check_updates", False)
        cm.set("last_update_check", YESTERDAY)
        assert should_check_now(cm, today=TODAY) is False

    def test_default_today_no_injection(self, cm):
        """不注入 today 时取本机当天：全新配置当天应为「该查」"""
        assert should_check_now(cm) is True

    def test_none_last_value_treated_as_never(self, cm):
        """last_update_check 意外为 None 也按「从未检查」处理不抛错"""
        cm._config["last_update_check"] = None
        assert should_check_now(cm, today=TODAY) is True


# ====================================================================
# mark_checked 写入
# ====================================================================
class TestMarkChecked:
    def test_writes_injected_day(self, cm):
        mark_checked(cm, today=TODAY)
        assert cm.get("last_update_check") == TODAY

    def test_write_persists_after_save_reload(self, cm, tmp_path):
        """类型为 str → 落盘后重载不回退默认（空串）"""
        mark_checked(cm, today=TODAY)
        cm.save()
        cm2 = ConfigManager(cm._json_path)
        assert cm2.get("last_update_check") == TODAY

    def test_then_same_day_suppressed(self, cm):
        """mark_checked 后同日不再查（发起即记账的口径）"""
        mark_checked(cm, today=TODAY)
        assert should_check_now(cm, today=TODAY) is False

    def test_default_today_writes_iso_date(self, cm):
        """不注入 today 时写入本机当天（YYYY-MM-DD）"""
        from datetime import date
        mark_checked(cm)
        assert cm.get("last_update_check") == date.today().isoformat()


# ====================================================================
# config 新键：默认值 / 类型 / 持久化
# ====================================================================
class TestConfigKeys:
    def test_defaults(self):
        assert DEFAULT_CONFIG["auto_check_updates"] is True
        assert DEFAULT_CONFIG["last_update_check"] == ""
        assert DEFAULT_CONFIG["latest_known_version"] == ""

    def test_latest_known_version_roundtrip(self, cm, tmp_path):
        """发现新版写入 tag（str）→ 落盘重载保留"""
        cm.set("latest_known_version", "v4.8.0")
        cm.save()
        cm2 = ConfigManager(cm._json_path)
        assert cm2.get("latest_known_version") == "v4.8.0"

    def test_type_mismatch_falls_back_to_default(self, tmp_path):
        """手改 config.json 塞错类型 → 该项回退默认，不影响其余加载"""
        import json
        path = tmp_path / "config.json"
        path.write_text(json.dumps({
            "auto_check_updates": "yes",        # 应为 bool
            "last_update_check": 123,           # 应为 str
        }), encoding="utf-8")
        cm = ConfigManager(str(path))
        assert cm.get("auto_check_updates") is True
        assert cm.get("last_update_check") == ""
