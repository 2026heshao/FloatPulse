# -*- coding: utf-8 -*-
"""离屏验证：插件页面按钮 hover 反馈（2026-10-03）。

背景：五个插件页面的按钮原是裸 QPushButton——QSS 全局 :hover 只有
文字色变化（与基态同色），背景过渡归 SmoothButton overlay，裸按钮
没有 → 鼠标放置零反馈。修法：30 处构造换 SmoothButton（签名兼容，
QSS 外观不变）。

覆盖：
  A 根因与修法（像素证据）
    A1 裸 QPushButton 没有 overlay 动画属性（无反馈机制，钉根因）
    A2 SmoothButton 未命名：hp=1 后渲染像素与基态不同（反馈生效）
    A3 SmoothButton#secondaryBtn：hp=1 后渲染像素与基态不同
    A4 press（pp=1）渲染像素与基态不同（按下反馈）
  B 静态契约
    B1 五插件源码零裸 QPushButton 构造、SmoothButton 在用（与 pytest
       test_plugin_button_feedback 同口径）
  M 护栏自检（能红灯）
    M1 AST 扫描器喂坏样例必须报出
    M2 hp 置位手法真的改变渲染（grab 对比不是恒真）
"""
import ast
import io
import os
import sys

V4 = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(V4)
sys.path.insert(0, V4)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QPushButton, QWidget  # noqa: E402

from src.controls import SmoothButton  # noqa: E402

PASS = 0
FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail}")


def _bare_lines(src: str):
    tree = ast.parse(src)
    return [n.lineno for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            and n.func.id == "QPushButton"]


def _smooth_count(src: str):
    tree = ast.parse(src)
    return sum(1 for n in ast.walk(tree)
               if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
               and n.func.id == "SmoothButton")


app = QApplication.instance() or QApplication(sys.argv)

host = QWidget()
host.resize(300, 200)
host.show()
app.processEvents()

print("== M. 护栏自检（能红灯）==")
sample_bad = "b = QPushButton('x')\n"
sample_good = "b = SmoothButton('x')\n"
check("M1 AST 扫描器喂坏样例必须报出",
      _bare_lines(sample_bad) == [1] and _bare_lines(sample_good) == [])

smooth = SmoothButton("发送", host)
smooth.setObjectName("probeSmooth")
smooth.resize(120, 32)
smooth.move(10, 10)
smooth.show()
app.processEvents()
base_img = smooth.grab().toImage()
smooth._hp = 1.0
smooth.update()
app.processEvents()
hover_img = smooth.grab().toImage()
check("M2 hp 置位真的改变渲染（grab 对比非恒真）", base_img != hover_img,
      "置位前后 grab 相同——对比手法失效")
smooth._hp = 0.0

print("== A. 根因与修法（像素证据）==")
bare = QPushButton("发送", host)
check("A1 裸 QPushButton 没有 overlay 动画属性（本次根因）",
      not hasattr(bare, "hp") and not hasattr(bare, "_hp"))
bare.deleteLater()

smooth._hp = 0.0
smooth.update()
app.processEvents()
b0 = smooth.grab().toImage()
smooth._hp = 1.0
smooth.update()
app.processEvents()
b1 = smooth.grab().toImage()
check("A2 未命名 SmoothButton hover 渲染变化（primary_hover 实底）",
      b0 != b1)

smooth._hp = 0.0
smooth._pp = 1.0
smooth.update()
app.processEvents()
check("A4 press（pp=1）渲染变化（下沉 + 叠色）",
      b0 != smooth.grab().toImage())
smooth._pp = 0.0
smooth._hp = 0.0
smooth.update()
app.processEvents()

sec = SmoothButton("复制结果", host)
sec.setObjectName("secondaryBtn")
sec.resize(120, 32)
sec.move(10, 60)
sec.show()
app.processEvents()
s0 = sec.grab().toImage()
sec._hp = 1.0
sec.update()
app.processEvents()
s1 = sec.grab().toImage()
check("A3 secondaryBtn hover 渲染变化（primary 18% 淡染）", s0 != s1)
sec.deleteLater()
smooth.deleteLater()

print("== B. 静态契约（与 pytest 同口径）==")
plugins = ("ai-assistant", "ai-text-workshop", "kb-search",
           "recurring-tasks", "vault")
for name in plugins:
    src = io.open(os.path.join(REPO, "plugins", name, "plugin.py"),
                  encoding="utf-8").read()
    check("B1 %s 零裸 QPushButton 且 SmoothButton 在用" % name,
          _bare_lines(src) == [] and _smooth_count(src) > 0,
          "bare=%s smooth=%d" % (_bare_lines(src), _smooth_count(src)))

host.hide()
print(f"\n共 {PASS + FAIL} 项，通过 {PASS}，失败 {FAIL}")
sys.exit(0 if FAIL == 0 else 1)
