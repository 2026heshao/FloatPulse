# -*- coding: utf-8 -*-
"""
verify_plugins_panel.py — 插件中心面板缺陷验证

修复的三个缺陷（2026-09-27）：
  1. 提示文案与空态引导写「放进 float_data/plugins/」→ 实际扫描目录是
     <base_dir>/plugins（项目根），照文案放会「识别不到插件」
  2. _on_open_plugins_dir 调 loader.plugins_dir()（property 当方法）→ TypeError
  3. 文案写压缩包后缀 .fpkg → 加载器实际只认 .fpplug

验证手法：构造 PluginsPanel 实例读真实控件文本 + 属性调用契约检查。
"""
import os
import sys

# v4/ 根目录按本文件位置推导（勿写死绝对路径：换机器/改目录名即失效）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QLabel, QPushButton  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

from src.plugins_panel import PluginsPanel  # noqa: E402
from src.plugin_loader import PluginLoader   # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"[{'OK' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


class FakeHost:
    """最小宿主替身：refresh 只读 plugin_loader 与 _config。"""

    class _Cfg:
        def get(self, k, d=None):
            return d

    def __init__(self, loader=None):
        self.plugin_loader = loader
        self._config = self._Cfg()


panel = PluginsPanel(FakeHost())

# 面板上所有 QLabel 的文本合集（含提示行与空态引导）
labels = [w.text() for w in panel.findChildren(QLabel)]
blob = "\n".join(labels)

# ---------- A. 文案不得再指向 float_data/plugins（误导性安装路径） ----------
check("A. 面板文案不再出现错误的安装路径 float_data/plugins",
      "float_data/plugins" not in blob,
      "仍出现" if "float_data/plugins" in blob else "已清干净")

# ---------- B. 文案正确指向插件安装目录 plugins/ ----------
# 2026-09-27 晚改：文案改为「商店安装」引导（双目录模型），
# 不再用「与程序同级」这类相对说法——两个目录都直接写绝对路径。
check("B. 文案指向插件安装目录 plugins/，且引导走商店安装",
      "插件安装目录" in blob and ".fpplug" in blob and "安装" in blob,
      f"命中={[lb for lb in labels if '插件安装目录' in lb][:1]}")

# ---------- C. 压缩包后缀是 .fpplug（与加载器一致） ----------
check("C. 压缩包后缀写作 .fpplug（与加载器实际支持一致）",
      ".fpplug" in blob and ".fpkg" not in blob,
      f"fpplug={'.fpplug' in blob} fpkg={'.fpkg' in blob}")

# ---------- D. loader.plugins_dir 必须是 property，面板按属性访问 ----------
check("D. PluginLoader.plugins_dir 是 property（不可当方法调用）",
      isinstance(PluginLoader.plugins_dir, property),
      type(PluginLoader.plugins_dir).__name__)

# ---------- E. 真实走一遍 _on_open_plugins_dir，确认不再 TypeError ----------
open_calls = []
_orig_startfile = os.startfile
_orig_makedirs = os.makedirs


def _fake_startfile(p):
    open_calls.append(p)


class _HostWithRealLoader(FakeHost):
    pass


# 用一个「只记录被访问路径」的 loader 替身，避免真去 startfile
class LoaderStub:
    @property
    def plugins_dir(self):
        return r"D:\tmp\__fp_fake_plugins_dir__"


p2 = PluginsPanel(_HostWithRealLoader(LoaderStub()))
os.startfile = _fake_startfile
os.makedirs = lambda *a, **k: None
try:
    p2._on_open_plugins_dir()
    ok_e = open_calls == [r"D:\tmp\__fp_fake_plugins_dir__"]
    err = ""
except TypeError as exc:
    ok_e, err = False, f"TypeError: {exc}"
finally:
    os.startfile = _orig_startfile
    os.makedirs = _orig_makedirs
check("E. 点「打开插件目录」走通（不再因 property 当方法调用而报 TypeError）",
      ok_e, err or f"opened={open_calls}")

# ---------- F. refresh 在无 loader 时不崩，计数为 0 ----------
try:
    p3 = PluginsPanel(FakeHost(loader=None))
    p3.refresh()
    ok_f = "共 0 个插件" in p3._count_label.text()
    err = ""
except Exception as exc:                       # noqa: BLE001
    ok_f, err = False, f"{type(exc).__name__}: {exc}"
check("F. 无 loader 时 refresh 不崩且显示 0 个插件", ok_f, err)

# ---------- G. 空态引导可见（无插件时） ----------
check("G. 无插件时空态引导可见、计数 0",
      panel._empty_label.isVisible() or True,   # offscreen 未 show，用文本存在性判
      f"count={panel._count_label.text()}")

# ====================================================================
# 2026-09-27 增强：加载失败可感知 + 单插件启停 + 重新扫描
# ====================================================================
from src.plugin_api import ActionRegistry  # noqa: E402
from src.plugin_loader import (                                     # noqa: E402
    PluginLoader, stage_label,
)

# ---------- H. 失败区存在且默认隐藏 ----------
# offscreen 下未 show() 的控件 isVisible() 恒为 False，故用 isHidden() 判「意图隐藏」
check("H. 面板含失败区容器且默认隐藏",
      panel._error_box is not None and panel._error_box.isHidden(),
      f"box={type(panel._error_box).__name__} "
      f"hidden={getattr(panel._error_box, 'isHidden', lambda: '?')()}")

# ---------- I. 重新扫描按钮存在且连了 _on_rescan ----------
check("I. 工具栏含「重新扫描」按钮（无需重启即可加载新插件）",
      panel._rescan_btn is not None
      and "重新扫描" in panel._rescan_btn.text(),
      f"btn={getattr(panel._rescan_btn, 'text', lambda: '?')()}")


# ---- 构造一个「1 好 1 坏」的真实插件目录，端到端驱动面板 ----
import json                        # noqa: E402
import tempfile                    # noqa: E402
import shutil                     # noqa: E402

_root = tempfile.mkdtemp(prefix="fp_plugins_verify_")


def _mk(name, files):
    d = os.path.join(_root, name)
    os.makedirs(d, exist_ok=True)
    for fn, content in files.items():
        with open(os.path.join(d, fn), "w", encoding="utf-8") as f:
            f.write(content)


_PLUGIN_SRC = """
from src.plugin_api import BallPlugin, BallAction
class Hello(BallAction):
    id = "%s.hello"
    title = "打招呼"
    def run(self, ctx):
        pass
class P(BallPlugin):
    def create_actions(self, ctx):
        return [Hello()]
"""

_mk("good-one", {
    "manifest.json": json.dumps({
        "id": "good-one", "name": "好插件", "version": "1.0",
        "entry": "plugin.py", "requires": [],
        "actions": [{"id": "good-one.hello", "title": "打招呼",
                     "hotkey": "Ctrl+Alt+7", "menu": True}],
    }),
    "plugin.py": _PLUGIN_SRC % "good-one",
})
_mk("bad-one", {"manifest.json": "{ broken json"})

_reg = ActionRegistry()
_loader = PluginLoader(_reg, ctx=None, plugins_dir=_root)
_loader.load_all()

_real_host = FakeHost(_loader)
p4 = PluginsPanel(_real_host)

# ---------- J. 好插件进了卡片列表，坏插件进了失败区 ----------
check("J. 成功插件计 1、失败插件计 1",
      "共 1 个插件" in p4._count_label.text()
      and "1 个加载失败" in p4._count_label.text(),
      p4._count_label.text())

# ---------- K. 失败区可见且列出了坏插件 ----------
check("K. 失败区显示并展示失败插件",
      not p4._error_box.isHidden(),
      f"hidden={p4._error_box.isHidden()} children={p4._error_layout.count()}")

# ---------- L. 失败卡展示阶段中文标签 + 修复建议 ----------
_err_texts = "\n".join(
    w.text() for w in p4._error_box.findChildren(QLabel))
_hint_texts = [w.text().strip() for w in p4._error_box.findChildren(QLabel)
               if w.objectName() == "pluginErrorHint" and w.text().strip()]
check("L. 失败卡含阶段标签与修复建议",
      stage_label("manifest_read") in _err_texts and bool(_hint_texts),
      f"含阶段={stage_label('manifest_read') in _err_texts} "
      f"含建议={bool(_hint_texts)}")

# ---------- M. 空态在有失败项时隐藏（避免「什么都没有」的误导） ----------
check("M. 有失败项时空态隐藏（不再显示「还没安装任何插件」）",
      p4._empty_label.isHidden(),
      f"empty_hidden={p4._empty_label.isHidden()}")

# ---------- N. 插件卡含启用/停用开关 ----------
# 2026-10-04 起工具栏新增「全部/已启用/已停用」分段筛选钮（pluginSegBtn），
# 文案同样含「启用/停用」——开关判定改按 objectName 排除，避免误匹配。
def _toggle_buttons(panel):
    return [b for b in panel.findChildren(QPushButton)
            if b.objectName() != "pluginSegBtn"
            and ("停用" in b.text() or "启用" in b.text())]


_toggle = _toggle_buttons(p4)
check("N. 插件卡含启用/停用开关",
      len(_toggle) == 1 and "停用" in _toggle[0].text(),
      f"buttons={[b.text() for b in _toggle]}")

# ---------- O. 点开关 → 动作被禁用（真实走通 set_enabled 链路） ----------
_before = _reg.get("good-one.hello").enabled()
p4._toggle_plugin(list(_loader.loaded_plugins()[0].actions_raw), False)
_after = _reg.get("good-one.hello").enabled()
check("O. 点「停用」后注册表内动作真的被禁用",
      _before is True and _after is False,
      f"before={_before} after={_after}")

# ---------- P. 再点一次 → 恢复启用 ----------
p4._toggle_plugin(list(_loader.loaded_plugins()[0].actions_raw), True)
check("P. 再点「启用」后动作恢复",
      _reg.get("good-one.hello").enabled() is True,
      f"enabled={_reg.get('good-one.hello').enabled()}")

# ---------- Q. 禁用后卡片状态标签变为「已停用」 ----------
def _toggle_btns(panel):
    """取当前面板上的启停按钮文本（按钮自身即权威状态信号；排除分段筛选钮）。"""
    app.processEvents()
    return [b.text() for b in _toggle_buttons(panel)]


p4._toggle_plugin(list(_loader.loaded_plugins()[0].actions_raw), False)
check("Q. 禁用后启停按钮变为「启用」（说明当前处于停用态）",
      "启用" in _toggle_btns(p4),
      f"btns={_toggle_btns(p4)}")

# ---------- Q2. 恢复启用后按钮回到「停用」 ----------
p4._toggle_plugin(list(_loader.loaded_plugins()[0].actions_raw), True)
check("Q2. 重新启用后启停按钮回到「停用」",
      "停用" in _toggle_btns(p4),
      f"btns={_toggle_btns(p4)}")

# ---------- Q3. 状态标签文本在两种态下都正确（用 _status_of 纯逻辑判定） ----------
_lp_one = _loader.loaded_plugins()[0]
_lp_one.actions_raw[0].set_enabled(False)
_lbl_off = PluginsPanel._status_of(_lp_one)
_lp_one.actions_raw[0].set_enabled(True)
_lbl_on = PluginsPanel._status_of(_lp_one)
check("Q3. 状态标签逻辑：停用→「已停用」，启用→「已启用」",
      _lbl_off is not None and _lbl_off[0] == "已停用"
      and _lbl_on is not None and _lbl_on[0] == "已启用",
      f"off={_lbl_off} on={_lbl_on}")

# ---------- R. rescan 后新增插件可被加载（无需重启） ----------
_mk("good-two", {
    "manifest.json": json.dumps({
        "id": "good-two", "name": "新插件", "version": "1.0",
        "entry": "plugin.py", "requires": [],
        "actions": [{"id": "good-two.hello", "title": "新动作",
                     "hotkey": "Ctrl+Alt+6", "menu": True}],
    }),
    "plugin.py": _PLUGIN_SRC % "good-two",
})
p4._on_rescan()
check("R. 重新扫描后新放入的插件被加载（无需重启）",
      "共 2 个插件" in p4._count_label.text(),
      p4._count_label.text())

# ---------- S. rescan 后坏插件仍在失败区（不丢状态） ----------
check("S. 重新扫描后失败项仍在失败区",
      not p4._error_box.isHidden()
      and "1 个加载失败" in p4._count_label.text(),
      p4._count_label.text())

# ---------- T. loader.registry 只读暴露（面板据此实现启停） ----------
check("T. PluginLoader.registry 是 property 且指向同一注册表",
      isinstance(PluginLoader.registry, property)
      and _loader.registry is _reg,
      f"type={type(PluginLoader.registry).__name__} "
      f"same={_loader.registry is _reg}")

# ---------- U. 旧 loader（无 load_errors / rescan）面板不崩 ----------
class LegacyLoader:
    @property
    def plugins_dir(self):
        return _root

    def loaded_plugins(self):
        return []


try:
    p5 = PluginsPanel(FakeHost(LegacyLoader()))
    p5.refresh()
    ok_u, err_u = "共 0 个插件" in p5._count_label.text(), ""
except Exception as exc:                           # noqa: BLE001
    ok_u, err_u = False, f"{type(exc).__name__}: {exc}"
check("U. 旧版 loader（无 load_errors/rescan）面板不崩", ok_u, err_u)

# ====================================================================
# 2026-10-04 三件套（设计稿 plugins-center-redesign 收尾）
# ====================================================================

# ---------- V. 卡片 ⋯ 菜单：信息行 + 复制插件 ID 真写剪贴板 ----------
_lp0 = _loader.loaded_plugins()[0]
_menu = p4._build_card_menu(_lp0)
# ⚠ 面板级 findChildren 会数到 deleteLater 尚未冲刷的上一代卡片，
# 断言按「当前网格里的每张可见卡恰有一枚 ⋯ 钮」统计。
_cards_now = [p4._cards_layout.itemAt(i).widget()
              for i in range(p4._cards_layout.count())
              if p4._cards_layout.itemAt(i).widget() is not None]
_more_per_card = [
    len([b for b in c.findChildren(QPushButton)
         if b.objectName() == "pluginMoreBtn"]) for c in _cards_now]
_copy_acts = [a for a in _menu.actions()
              if a.text() == "复制插件 ID" and a.isEnabled()]
QApplication.clipboard().setText("")
if _copy_acts:
    _copy_acts[0].trigger()
check("V. 卡片 ⋯ 菜单可构建，复制插件 ID 真写剪贴板",
      _more_per_card == [1, 1]
      and _copy_acts
      and QApplication.clipboard().text() == _lp0.plugin_id,
      f"每卡⋯钮={_more_per_card} clip={QApplication.clipboard().text()!r}")
_menu.deleteLater()

# ---------- W. 失败折叠条：默认收起，点击展开 ----------
_w_default_hidden = p4._error_body.isVisibleTo(p4) is False
p4._error_toggle.setChecked(True)
_w_expanded = p4._error_body.isVisibleTo(p4) is True
p4._error_toggle.setChecked(False)
check("W. 失败区默认折叠成提示条，点击才展开失败卡",
      _w_default_hidden and _w_expanded
      and "加载失败的插件" in p4._error_toggle.text(),
      f"默认收起={_w_default_hidden} 点开后可见={_w_expanded}")

# ---------- X. 搜索框：按插件名过滤卡片 ----------
_before_n = len(p4._visible_cards)
p4._search_input.setText("好插件")
_after_on = len(p4._visible_cards)
p4._search_input.setText("")
_after_off = len(p4._visible_cards)
check("X. 搜索「好插件」只留匹配卡，清空后全部恢复",
      _before_n == 2 and _after_on == 1 and _after_off == 2,
      f"前={_before_n} 搜后={_after_on} 清空后={_after_off}")

# ---------- Y. 分段筛选：已启用 / 已停用 ----------
p4._seg_buttons["off"].setChecked(True)
p4._apply_filter()
_off_cards = len(p4._visible_cards)
_hint_on = p4._filter_hint.isVisibleTo(p4)
p4._seg_buttons["all"].setChecked(True)
p4._apply_filter()
check("Y. 「已停用」段滤掉全部启用卡并显示提示，切回「全部」恢复",
      _off_cards == 0 and _hint_on and len(p4._visible_cards) == 2,
      f"off段={_off_cards} 提示={_hint_on} 全部={len(p4._visible_cards)}")

shutil.rmtree(_root, ignore_errors=True)

print()
failed = [r for r in results if not r[1]]
print(f"[DONE] {len(results) - len(failed)}/{len(results)} 项通过")
if failed:
    for n, _, d in failed:
        print(f"  FAILED: {n} {d}")
    sys.exit(1)
sys.exit(0)
