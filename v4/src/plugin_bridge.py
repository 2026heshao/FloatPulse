# -*- coding: utf-8 -*-
"""插件桥：宿主受限能力的 provider 工厂（2026-10-05 D2/T04 外迁）。

从 ``knowledge_ball.main()`` 整体平移的五组闭包收进一个显式注入的门面：

  - kb_blocked()          : 知识库写入安全闸（外部改动检测）
  - kb_paragraph(num)     : 知识库编号（1 起）→ ParagraphInfo
  - write_providers()     : 受限写入口（fragment/task/note/knowledge，只增）
  - manage_providers()    : 数据管理入口（改/删 + 撤销令牌）
  - ai_providers()        : AI 总配置实时读取

定位（见 docs/拆分计划-wiring-2026-10-05.md §0.3）：**受限能力门面**——
插件拿不到管理器本体，所有写/改/删都包一层「写库 + 刷新 UI」。

生命周期等价说明（§7 T04 / 风险表 10）：撤销栈原是
``_make_manage_providers`` 的函数局部变量（该工厂启动期仅被 main() 调用
一次），本类把它**升为实例属性** ``_undo_stack`` / ``_undo_seq``——桥实例
同样只构造一次并存活到进程退出，捕获生命周期完全等价。

铁律：

  1. **禁止 import knowledge_ball / main_window / card_window**（防 import
     环）；宿主窗口与悬浮球以鸭子类型注入（``ui`` / ``ball``），只调用其
     公开方法（refresh_* / show_toast / refresh_badge）；
  2. 撤销栈是本类**唯一可变状态**，provider 一律无状态（每次实时读配置）；
  3. 注入时机：main() 在 MainWindow / FloatingBall / 各 manager 全部就绪后
     构造本桥，PluginContext 构造处（钉死段）以 ``bridge.write_providers()``
     等形式取走三组 provider dict。
"""

from typing import Any, Callable, Dict

from src.ai_server import AI_SERVER
from src.fragment_manager import TYPE_CLIPBOARD_TEXT
from src.logger import get_logger


class PluginHostBridge:
    """宿主受限能力门面：write / manage / ai 三组 provider 的工厂。

    参数全部显式注入（依赖注入，无全局单例）：

    fragment_manager : FragmentManager  碎片库（写/改/删）
    task_manager     : TaskManager      任务库（写/改/删 + 专注次数）
    note_manager     : NoteManager      笔记库（写/改/删）
    docx_manager     : DocxManager      docx 知识库（整篇回写模型，见 kb_blocked）
    config_manager   : ConfigManager    配置（ai_providers 实时读）
    ui               : MainWindow       面板刷新 + toast（鸭子类型，仅公开方法）
    ball             : FloatingBall     球体徽标刷新（鸭子类型，仅公开方法）
    """

    def __init__(self, *, fragment_manager: Any, task_manager: Any,
                 note_manager: Any, docx_manager: Any,
                 config_manager: Any, ui: Any, ball: Any):
        self._fragment_manager = fragment_manager
        self._task_manager = task_manager
        self._note_manager = note_manager
        self._docx_manager = docx_manager
        self._config_manager = config_manager
        self._ui = ui
        self._ball = ball
        # 撤销栈（原 _make_manage_providers 函数局部 → 升实例属性）：
        # _undo_stack 最多留最近 50 次删除记录，_undo_seq 单调递增令牌序号
        self._undo_stack = []          # [{"token","kind","payload","used"}]
        self._undo_seq = 1

    # ------------------------------------------------------------------
    # 知识库安全闸 / 段落定位（write 与 manage 两组 provider 共用）
    # ------------------------------------------------------------------
    def kb_blocked(self) -> bool:
        """知识库写入前的安全闸：检测到外部改动就拒绝写。

        知识库是用户可直接用 Word/WPS 打开编辑的 docx。外部改过之后内存
        模型已过期，而 ``DocxManager.save()`` 是**整篇回写**——此时落盘会把
        用户在 Word 里的改动整篇覆盖掉。宁可拒绝并要求先重新加载。

        write 与 manage 两组 provider 共用。
        """
        if self._docx_manager.check_external_modification():
            get_logger().warning(
                "[插件] 知识库检测到外部修改，已拒绝写入"
                "（请先在知识库页点「🔄 重新加载」）")
            return True
        return False

    def kb_paragraph(self, num):
        """知识库编号（1 起）→ ParagraphInfo；非法或越界返回 None"""
        if not isinstance(num, int) or isinstance(num, bool) or num < 1:
            return None
        items = self._docx_manager.get_paragraphs()
        if num > len(items):
            return None
        return items[num - 1]

    # ------------------------------------------------------------------
    # 撤销栈（原闭包内 _push_undo / _take_undo）
    # ------------------------------------------------------------------
    def _push_undo(self, kind: str, payload: dict) -> int:
        """删除快照进栈，返回撤销令牌；容量上限只留最近 50 次删除"""
        token = self._undo_seq
        self._undo_seq += 1
        self._undo_stack.append({"token": token, "kind": kind,
                                 "payload": payload, "used": False})
        if len(self._undo_stack) > 50:      # 容量上限：只留最近 50 次删除
            self._undo_stack.pop(0)
        return token

    def _take_undo(self, token: int):
        """取出未用过的令牌记录并标记已用（一令牌只能用一次）"""
        for rec in self._undo_stack:
            if rec["token"] == token and not rec["used"]:
                rec["used"] = True
                return rec
        return None

    # ------------------------------------------------------------------
    # 受限写入口（2026-09-27）
    # ------------------------------------------------------------------
    def write_providers(self) -> Dict[str, Callable]:
        """插件写入口的宿主实现（2026-09-27 受限写能力）。

        四个 provider 各自包一层「写库 + 刷新 UI」，插件拿不到管理器本体：
          - 只增不改删：这里**刻意不提供** update / delete
          - 碎片 source 由 PluginWriter 补 ``插件:<id>``，落库可追溯
          - 写成功立即刷新对应面板与悬浮球徽标，与卡片数据变更走同一链路
          - 知识库额外过 ``kb_blocked()`` 安全闸
        """
        def _add_fragment(content, source):
            fid = self._fragment_manager.add_fragment(
                TYPE_CLIPBOARD_TEXT, content, source)
            self._ui.refresh_fragments()
            if fid:
                # 轻提示反馈（2026-10-05）：插件 AI 动作写入此前静默
                self._ui.show_toast("已加入碎片")
            return fid

        def _add_task(title, note, deadline):
            tid = self._task_manager.add_task(title, note, deadline)
            self._ui.refresh_tasks()
            self._ball.refresh_badge()          # 任务数变了，球体徽标同步
            if tid:
                shown = title if len(title) <= 16 else title[:15] + "…"
                self._ui.show_toast(f"已添加任务：{shown}")
            return tid

        def _add_note(title, content):
            nid = self._note_manager.add_note(content, title)
            self._ui.refresh_notes()
            if nid:
                self._ui.show_toast("已存为笔记")
            return nid

        def _add_knowledge(content):
            """追加一段知识，返回编号（从 1 开始，与面板一致）；失败 0"""
            if self.kb_blocked():
                return 0
            idx = self._docx_manager.append_paragraph(content)
            if idx < 0:
                return 0
            if not self._docx_manager.save():
                # 落盘失败 → 丢弃内存改动，避免面板显示磁盘上没有的段落
                self._docx_manager.reload()
                return 0
            self._ui.refresh_knowledge()
            return idx + 1

        return {"fragment": _add_fragment, "task": _add_task,
                "note": _add_note, "knowledge": _add_knowledge}

    # ------------------------------------------------------------------
    # 数据管理入口（2026-09-28）
    # ------------------------------------------------------------------
    def manage_providers(self) -> Dict[str, Callable]:
        """插件管理入口的宿主实现（2026-09-28 数据管理能力）。

        改 / 删都包一层「改库 + 刷新 UI」；删除前抓整条快照进撤销栈，
        插件拿到的是**撤销令牌**（>0 = 成功），可经 ``undo_delete`` 恢复。

        撤销走"重新插入"路径（宿主 add_* 是唯一入口，不去碰内部 id 分配），
        恢复后编号可能是新的，但内容与关键状态（完成态 / 专注次数）原样还原。
        知识库段落例外：按删除前的 0 基位置 ``insert_paragraph_before`` 放回
        原位（结构化文档里位置本身就是信息）。
        安全三层：能力声明（manage）→ 插件侧分级确认（改删需用户点确认）
        → 这里的内容护栏 + 撤销栈 + 每次操作进审计日志。
        """
        # ---------- 任务 ----------
        def _update_task(tid, title, note, deadline):
            task = self._task_manager.get_task(tid)
            if task is None:
                return False
            cur = task.to_dict()          # 部分更新：None = 保留原值
            ok = self._task_manager.update_task(
                tid,
                cur["title"] if title is None else title,
                cur["note"] if note is None else note,
                cur["deadline"] if deadline is None else deadline)
            if ok:
                self._ui.refresh_tasks()
            return bool(ok)

        def _set_task_done(tid, done):
            ok = self._task_manager.set_done(tid, done)
            if ok:
                self._ui.refresh_tasks()
                self._ball.refresh_badge()
            return bool(ok)

        def _delete_task(tid):
            task = self._task_manager.get_task(tid)
            if task is None:
                return 0
            payload = task.to_dict()
            if not self._task_manager.delete_task(tid):
                return 0
            self._ui.refresh_tasks()
            self._ball.refresh_badge()
            return self._push_undo("task", payload)

        # ---------- 碎片 ----------
        def _update_fragment(fid, content, source):
            ok = self._fragment_manager.update_fragment(fid, content=content,
                                                        source=source)
            if ok:
                self._ui.refresh_fragments()
            return bool(ok)

        def _delete_fragment(fid):
            frag = self._fragment_manager.get_fragment(fid)
            if frag is None:
                return 0
            payload = frag.to_dict()
            if not self._fragment_manager.delete_fragment(fid):
                return 0
            self._ui.refresh_fragments()
            return self._push_undo("fragment", payload)

        # ---------- 笔记 ----------
        def _update_note(nid, title, content):
            cur = self._note_manager.get_note(nid)
            if cur is None:
                return False
            ok = self._note_manager.update_note(
                nid, cur.content if content is None else content, title=title)
            if ok:
                self._ui.refresh_notes()
            return bool(ok)

        def _delete_note(nid):
            cur = self._note_manager.get_note(nid)
            if cur is None:
                return 0
            payload = cur.to_dict()
            if not self._note_manager.delete_note(nid):
                return 0
            self._ui.refresh_notes()
            return self._push_undo("note", payload)

        # ---------- 知识库（位置型标识：必须校验内容指纹） ----------
        # 编号会随删除前移，docx 又能被外部编辑，所以「编号 N」可能是过期
        # 引用。这里比对调用方回传的段落指纹，对不上就拒改拒删——宁可失败
        # 也不改错段落。
        def _update_knowledge(num, content, expect_hash):
            if self.kb_blocked():
                return False
            cur = self.kb_paragraph(num)
            if cur is None or cur.hash != expect_hash:
                get_logger().warning(
                    f"[插件] 知识库第 {num} 段指纹不匹配，已拒绝修改"
                    "（内容可能已变化）")
                return False
            if not self._docx_manager.update_paragraph_text(num - 1, content):
                return False
            if not self._docx_manager.save():
                self._docx_manager.reload()
                return False
            self._ui.refresh_knowledge()
            return True

        def _delete_knowledge(num, expect_hash):
            if self.kb_blocked():
                return 0
            cur = self.kb_paragraph(num)
            if cur is None or cur.hash != expect_hash:
                get_logger().warning(
                    f"[插件] 知识库第 {num} 段指纹不匹配，已拒绝删除"
                    "（内容可能已变化）")
                return 0
            # pos 记**删除前**的 0 基位置：撤销时照它放回原位
            payload = {"text": cur.text, "pos": num - 1}
            if not self._docx_manager.delete_paragraph(num - 1):
                return 0
            if not self._docx_manager.save():
                self._docx_manager.reload()
                return 0
            self._ui.refresh_knowledge()
            return self._push_undo("knowledge", payload)

        # ---------- 撤销 ----------
        def _undo_delete(token):
            rec = self._take_undo(token)
            if rec is None:
                return False
            kind, p = rec["kind"], rec["payload"]
            if kind == "knowledge":
                if self.kb_blocked():
                    return False
                text = p.get("text") or ""
                pos = int(p.get("pos") or 0)
                items = self._docx_manager.get_paragraphs()
                if pos < 0 or pos > len(items):
                    pos = len(items)          # 越界（别处又改过）→ 追加到末尾
                if pos < len(items):
                    new_idx = self._docx_manager.insert_paragraph_before(
                        pos, text)
                else:
                    new_idx = self._docx_manager.append_paragraph(text)
                if new_idx < 0:
                    return False
                if not self._docx_manager.save():
                    self._docx_manager.reload()
                    return False
                self._ui.refresh_knowledge()
                return True
            if kind == "task":
                new_id = self._task_manager.add_task(
                    p.get("title") or "", p.get("note") or "",
                    p.get("deadline") or "")
                if not new_id:
                    return False
                if p.get("done"):
                    self._task_manager.set_done(new_id, True)
                for _ in range(int(p.get("focus_sessions") or 0)):
                    self._task_manager.add_focus_session(new_id, 1)
                self._ui.refresh_tasks()
                self._ball.refresh_badge()
            elif kind == "fragment":
                new_id = self._fragment_manager.add_fragment(
                    p.get("type") or TYPE_CLIPBOARD_TEXT,
                    p.get("content") or "", p.get("source") or "撤销恢复")
                if not new_id:
                    return False
                self._ui.refresh_fragments()
            else:                             # note
                new_id = self._note_manager.add_note(
                    p.get("content") or "", p.get("title") or "")
                if not new_id:
                    return False
                self._ui.refresh_notes()
            return True

        return {
            "update_task": _update_task, "set_task_done": _set_task_done,
            "delete_task": _delete_task,
            "update_fragment": _update_fragment,
            "delete_fragment": _delete_fragment,
            "update_note": _update_note, "delete_note": _delete_note,
            "update_knowledge": _update_knowledge,
            "delete_knowledge": _delete_knowledge,
            "undo_delete": _undo_delete,
        }

    # ------------------------------------------------------------------
    # AI 总配置（2026-09-29）
    # ------------------------------------------------------------------
    def ai_providers(self) -> Dict[str, Callable]:
        """AI 总配置 provider（2026-09-29 设置页「🧠 AI 总配置」）。

        插件单一真相源：设置页配好云端 / 本地 + 下拉框勾选接入插件后，
        声明 ``capabilities=["ai"]`` 且被勾选的插件经 ``ctx.ai`` 实时读取。
        **params 每次调用都实时读配置**——设置页改完即生效，插件无需
        重建页面或重启程序；is_attached 按插件 id 查 ``ai_plugins`` 列表。
        未接入的插件照旧用各自私有配置（向后兼容），互不影响。
        """
        def _is_attached(plugin_id):
            attached = self._config_manager.get("ai_plugins", []) or []
            return str(plugin_id or "") in attached

        def _params():
            try:
                port = int(
                    self._config_manager.get("ai_local_port", 8095) or 8095)
            except (TypeError, ValueError):
                port = 8095
            return {
                "mode": str(self._config_manager.get("ai_backend_mode", "cloud")
                            or "cloud"),
                "base_url": str(self._config_manager.get("ai_cloud_base_url",
                                                         "") or "").strip(),
                "api_key": str(self._config_manager.get("ai_cloud_api_key",
                                                        "") or ""),
                "model": str(self._config_manager.get("ai_cloud_model", "")
                             or "").strip(),
                "local_port": port,
                "local_ready": AI_SERVER.status == "ready",
                "local_status": AI_SERVER.status,
                "local_detail": AI_SERVER.detail,
            }

        def _add_listener(fn):
            return AI_SERVER.add_listener(fn)

        def _remove_listener(fn):
            return AI_SERVER.remove_listener(fn)

        def _stop_local():
            AI_SERVER.stop()
            return True

        return {
            "is_attached": _is_attached, "params": _params,
            "add_listener": _add_listener, "remove_listener": _remove_listener,
            "stop_local": _stop_local,
        }
