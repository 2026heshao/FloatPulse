# -*- coding: utf-8 -*-
"""插件数据管理能力回归 —— ctx.manage（改 / 删 / 撤销，2026-09-28）。

钉死的行为：
  A 权限门禁：未声明 manage → 8 个管理方法一律失败，provider 零调用
  B **只声明 write 不等于拿到 manage**（最关键的一条：两档能力分离）
  C manage 蕴含 write：声明 manage 后 add_* 也能用（超集语义）
  D 参数护栏：id / 令牌必须真正整数（拒 0 / 负数 / bool / 浮点 / 字符串）；
    可选文本参数必须是字符串或 None（None = 该字段不动）
  E 返回语义：update_* / set_* / undo_delete 只认真 bool（1 / "ok" → False）；
    delete_* 只认真正整数令牌（True / 1.5 / 负数 → 0）
  F provider 抛异常 → 隔离为 False / 0，绝不冒泡
  G 部分更新：None 原样透传给 provider（由宿主实现决定保留原值）
  H manifest：capabilities 含 manage 放行；未知能力名仍拒载
  I 门面同一性：ctx.write 与 ctx.manage 指向同一对象，权限方法级判定；
    for_plugin 派生透传 manage_providers，权限按派生时的声明判定
  J 审计：manage 调用写一条 info（动作 / 参数 / 结果）
本文件不 import PyQt6（provider 是鸭子类型替身），无 GUI 可跑。
"""

import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

from src.plugin_api import (                    # noqa: E402
    KNOWN_CAPABILITIES, PluginContext,
)
from src.plugin_loader import validate_manifest  # noqa: E402


class _Sink:
    """记录调用的假 provider"""

    def __init__(self, ret=True):
        self.calls = []
        self.ret = ret

    def __call__(self, *args):
        self.calls.append(args)
        return self.ret


class _Boom:
    def __call__(self, *args):
        raise RuntimeError("provider 炸了")


class _Log:
    def __init__(self):
        self.infos = []
        self.warnings = []

    def info(self, msg):
        self.infos.append(msg)

    def warning(self, msg):
        self.warnings.append(msg)


def _manage_sinks(ret=True, token=7):
    return {
        "update_task": _Sink(ret), "set_task_done": _Sink(ret),
        "delete_task": _Sink(token), "update_fragment": _Sink(ret),
        "delete_fragment": _Sink(token), "update_note": _Sink(ret),
        "delete_note": _Sink(token), "undo_delete": _Sink(ret),
    }


def _ctx(caps=(), manage=None, logger=None, plugin_id="demo"):
    return PluginContext(
        logger=logger, plugin_id=plugin_id, capabilities=caps,
        write_providers={"fragment": _Sink(11), "task": _Sink(22),
                         "note": _Sink(33)},
        manage_providers=manage if manage is not None else {},
    ).for_plugin(plugin_id, "/x", caps)


# ---------------- A/B：权限门禁与两档分离 ----------------
def test_all_manage_methods_rejected_without_capability():
    sinks = _manage_sinks()
    ctx = _ctx(caps=(), manage=sinks)
    assert ctx.manage.update_task(3, title="新标题") is False
    assert ctx.manage.set_task_done(3, True) is False
    assert ctx.manage.delete_task(3) == 0
    assert ctx.manage.update_fragment(1, content="新") is False
    assert ctx.manage.delete_fragment(1) == 0
    assert ctx.manage.update_note(2, title="新") is False
    assert ctx.manage.delete_note(2) == 0
    assert ctx.manage.undo_delete(1) is False
    for s in sinks.values():
        assert s.calls == []                    # provider 一次都没被调用


def test_write_alone_does_not_grant_manage():
    """最关键的一条：write 与 manage 是两档能力，不能顺带升级"""
    sinks = _manage_sinks()
    ctx = _ctx(caps=["write"], manage=sinks)
    assert ctx.write.enabled() is True
    assert ctx.write.can_manage() is False
    assert ctx.manage.delete_task(1) == 0
    assert ctx.manage.update_task(1, title="x") is False
    assert sinks["delete_task"].calls == []
    assert sinks["update_task"].calls == []


def test_manage_implies_write():
    """声明 manage 的插件是超集：add_* 与 manage_* 都能用"""
    ctx = _ctx(caps=["manage"], manage=_manage_sinks())
    assert ctx.manage.enabled() is True
    assert ctx.manage.can_manage() is True
    assert ctx.write.add_note("标题", "正文") == 33   # write 通道可用
    assert ctx.write.add_task("任务") == 22


def test_unknown_capability_still_rejected():
    # 2026-09-29 起 + ai（设置页「AI 总配置」，授权在设置页下拉框勾选）
    assert KNOWN_CAPABILITIES == frozenset(
        {"network", "write", "manage", "ai"})
    base = {"id": "x", "name": "X", "version": "1.0.0", "entry": "p.py"}
    m, err = validate_manifest({**base, "capabilities": ["manage"]})
    assert m is not None and err == ""
    m2, err2 = validate_manifest(
        {**base, "capabilities": ["network", "manage"]})
    assert m2 is not None and m2["capabilities"] == ["network", "manage"]
    m3, err3 = validate_manifest({**base, "capabilities": ["mange"]})
    assert m3 is None and "未知能力" in err3


# ---------------- C：参数护栏 ----------------
def test_invalid_ids_rejected_without_calling_provider():
    sinks = _manage_sinks()
    ctx = _ctx(caps=["manage"], manage=sinks)
    for bad in (0, -1, True, False, 1.5, "3", None, []):
        assert ctx.manage.delete_task(bad) == 0
        assert ctx.manage.update_task(bad, title="x") is False
        assert ctx.manage.set_task_done(bad, True) is False
        assert ctx.manage.undo_delete(bad) is False
    for s in sinks.values():
        assert s.calls == []


def test_invalid_text_args_rejected():
    sinks = _manage_sinks()
    ctx = _ctx(caps=["manage"], manage=sinks)
    assert ctx.manage.update_task(3, title=123) is False
    assert ctx.manage.update_task(3, note=["a"]) is False
    assert ctx.manage.update_task(3, deadline={"d": 1}) is False
    assert ctx.manage.update_note(3, title=9) is False
    assert ctx.manage.update_fragment(3, source=3.5) is False
    assert sinks["update_task"].calls == []
    assert sinks["update_note"].calls == []


def test_none_means_leave_field_untouched():
    """部分更新语义：None 原样透传给宿主 provider（由它保留原值）"""
    sinks = _manage_sinks()
    ctx = _ctx(caps=["manage"], manage=sinks)
    assert ctx.manage.update_task(3, title="新标题") is True
    assert sinks["update_task"].calls == [(3, "新标题", None, None)]
    assert ctx.manage.update_note(2, content="新正文") is True
    assert sinks["update_note"].calls == [(2, None, "新正文")]


# ---------------- D/E：返回语义 ----------------
def test_update_requires_real_bool():
    ctx = _ctx(caps=["manage"], manage={"update_task": _Sink(1)})
    assert ctx.manage.update_task(3, title="x") is False   # 1 不是 bool
    ctx2 = _ctx(caps=["manage"], manage={"update_task": _Sink("ok")})
    assert ctx2.manage.update_task(3, title="x") is False
    ctx3 = _ctx(caps=["manage"], manage={"update_task": _Sink(True)})
    assert ctx3.manage.update_task(3, title="x") is True


def test_delete_token_requires_positive_int():
    for ret, want in ((True, 0), (1.5, 0), (-1, 0), (0, 0), (7, 7)):
        ctx = _ctx(caps=["manage"], manage={"delete_task": _Sink(ret)})
        assert ctx.manage.delete_task(3) == want, ret


def test_provider_exception_is_isolated():
    log = _Log()
    ctx = _ctx(caps=["manage"],
               manage={"delete_task": _Boom(), "update_task": _Boom()},
               logger=log)
    assert ctx.manage.delete_task(3) == 0
    assert ctx.manage.update_task(3, title="x") is False
    assert any("管理操作失败" in w for w in log.warnings)


# ---------------- G：通道缺失与审计 ----------------
def test_missing_manage_channel_denied():
    log = _Log()
    ctx = _ctx(caps=["manage"], manage={}, logger=log)
    assert ctx.manage.delete_note(1) == 0
    assert any("未注入管理通道" in w for w in log.warnings)


def test_audit_log_on_success():
    log = _Log()
    ctx = _ctx(caps=["manage"], manage=_manage_sinks(), logger=log)
    assert ctx.manage.delete_fragment(5) == 7
    assert any("[插件管理]" in m and "delete_fragment" in m
               for m in log.infos)


# ---------------- I：门面同一性与派生透明 ----------------
def test_write_and_manage_share_one_gateway():
    ctx = _ctx(caps=["manage"], manage=_manage_sinks())
    assert ctx.write is ctx.manage


def test_for_plugin_propagates_manage_providers_and_permissions():
    sinks = _manage_sinks()
    base = PluginContext(logger=None, manage_providers=sinks)
    assert base.manage.can_manage() is False          # 共享宿主 ctx 无权限
    assert base.manage.delete_task(1) == 0
    child = base.for_plugin("demo", "/x", ["manage"])
    assert child.manage.can_manage() is True
    assert child.manage.delete_task(1) == 7
    assert sinks["delete_task"].calls == [(1,)]
    assert child.manage.manage_sources() == (
        "delete_fragment", "delete_note", "delete_task",
        "set_task_done", "undo_delete", "update_fragment", "update_note",
        "update_task")
