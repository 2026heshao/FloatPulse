# -*- coding: utf-8 -*-
"""重拍 README 悬浮快捷卡片截图（深色档）——正式管线（2026-10-06）。

用法：
    C:/Users/LENOVO/AppData/Local/Programs/Python/Python312/python.exe tools/shot_readme_card.py
    （脚本自带 QT_QPA_PLATFORM=offscreen，可离屏运行）

产出（docs/images/ 落盘后自动 shutil.copy2 覆盖到 assets/images/）：
    preview-card-dark.png    深色 + 靛青强调色 + 演示壁纸

为什么 grab 之后要裁一刀（16px 裁剪口径）
====================================================================
CardWindow 的窗口 = 卡片内容 440×340 + 四周 16px ``CARD_MARGIN`` 阴影
留白（card_window._init_window：setFixedSize(W+32, H+32)，阴影由
paintEvent 手绘在留白区）。直接 grab 得到 472×372 的图，四周是一圈
**透明外框环**——README 里图片查看器深浅背景下都会看到一圈脏边。
本脚本 grab 后按 ``pm.copy(CARD_MARGIN, CARD_MARGIN, W, H)`` 裁到卡片
本体，外环整个去掉，只留圆角处天然的抗锯齿半透明像素（深浅主题
同口径）。尺寸全部读 CardWindow 的类常量，卡片将来改尺寸自动跟上。

壁纸数据零污染
====================================================================
与 tools/shot_readme.py 同一套纪律：
- 所有管理器（config / fragments / tasks / notes / nav / temp_assets）
  全部指向临时目录，演示数据不碰真实 ``float_data/``；
- 唯一例外是壁纸：``wallpaper.resolve_path`` 只认真实数据根的
  ``float_data/backgrounds/``，所以演示壁纸（程序生成，无外部图源）
  必须 ``wallpaper.import_image()`` 导入真实目录，跑完
  ``prune_unused([])`` 清掉、tmp 配置里的壁纸键复原；
- 脚本运行前后对真实 ``float_data/`` 整目录做**文件字节级快照**
  （sha256），收尾断言两份快照完全一致，不一致立即报错退出——
  任何意外写入（哪怕 app.log 多一行）都逃不过这一关。

演示内容全部中性（会议要点 / 文档链接 / 命令 / 代码片段），
README 是公开页面，不允许出现任何真实个人数据。
"""

import hashlib
import os
import shutil
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_V4 = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _V4 not in sys.path:
    sys.path.insert(0, _V4)

from PyQt6.QtCore import QRectF, Qt, QTimer                     # noqa: E402
from PyQt6.QtGui import (QBrush, QColor, QFontDatabase,          # noqa: E402
                         QLinearGradient, QPainter, QPixmap,
                      QRadialGradient)                           # noqa: E402
from PyQt6.QtWidgets import QApplication                         # noqa: E402

from src import theme, wallpaper                                # noqa: E402
from src.card_window import CardWindow                          # noqa: E402
from src.config import ConfigManager                            # noqa: E402
from src.fragment_manager import FragmentManager                # noqa: E402
from src.nav_manager import NavManager                          # noqa: E402
from src.note_manager import NoteManager                        # noqa: E402
from src.task_manager import TaskManager                        # noqa: E402
from src.temp_asset_manager import TempAssetManager             # noqa: E402

PROJECT = os.path.dirname(_V4)
OUT_DIR = os.path.join(PROJECT, "docs", "images")
ASSETS_DIR = os.path.join(PROJECT, "assets", "images")
DATA_ROOT = os.path.join(PROJECT, "float_data")   # 真实数据根（零污染对象）

OUT_NAME = "preview-card-dark.png"

DARK_ACCENT = "indigo"   # 与 tools/shot_readme.py 深色「妆」档同款强调色
WALL_W, WALL_H = 880, 680   # 壁纸按 2x 尺寸画，cover 缩到 440×340 依然锐利
GRAB_PUMP_MS = 600          # show → 切页 → grab 各步之间的渲染泵间隔


# ---------------- 壁纸数据零污染：目录字节级快照 ----------------

def snapshot_data_root():
    """真实 float_data/ 全目录快照：{相对路径: 内容 sha256}。

    含文件与空目录名单（目录本身被增删也算污染），纯只读操作。
    """
    state = {}
    for root, dirs, files in os.walk(DATA_ROOT):
        dirs.sort()
        rel_root = os.path.relpath(root, DATA_ROOT)
        for name in sorted(dirs):
            state[os.path.join(rel_root, name) + "/"] = "dir"
        for name in sorted(files):
            path = os.path.join(root, name)
            digest = hashlib.sha256()
            with open(path, "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    digest.update(chunk)
            state[os.path.join(rel_root, name)] = digest.hexdigest()
    return state


def assert_no_pollution(before, after):
    """拍前 vs 拍后快照必须完全一致，否则列出差异并硬失败。"""
    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    changed = sorted(k for k in set(before) & set(after)
                     if before[k] != after[k] and k != "dir")
    if added or removed or changed:
        for k in added:
            print("[!] float_data 新增: %s" % k)
        for k in removed:
            print("[!] float_data 消失: %s" % k)
        for k in changed:
            print("[!] float_data 内容变化: %s" % k)
        raise SystemExit("零污染断言失败：真实 float_data/ 被写入，见上表")
    print("[OK] 零污染断言通过：float_data/ 拍前拍后字节级一致（%d 项）"
          % len(before))


# ---------------- 演示素材（全部中性内容） ----------------

def load_cjk_font(app):
    """离屏平台不加载任何系统字体，不显式挂字体截图里中文会变方框。

    与 tools/shot_readme.py 同款：msyh 负责中文，seguiemj 负责侧栏
    emoji 图标（🧩 📋 📝 …），挂不上只是退化成方框，不阻断截图。
    """
    loaded = []
    for cand in (r"C:\Windows\Fonts\msyh.ttc",
                 r"C:\Windows\Fonts\seguiemj.ttf",
                 r"C:\Windows\Fonts\seguisym.ttf"):
        if os.path.exists(cand) and app is not None:
            if QFontDatabase.addApplicationFont(cand) >= 0:
                loaded.append(os.path.basename(cand))
    return loaded


def make_demo_wallpaper(path):
    """程序生成一张演示壁纸（深青底 + 三枚高亮柔光大圆）。

    图源说明：仓库里没有合适的壁纸素材（宣传页/设计稿里只有 UI
    截图，不是壁纸），与 tools/shot_readme.py 一样纯代码绘制，
    不引入任何外部图片资源文件。

    与 shot_readme.py 主窗版画法的差异：卡片壳在挂壁纸时仍保留
    max(55, 100 - veil//2)% 实底（veil=40 → 80%），壁纸有效穿透率
    只有 ~12%，所以这里把渐变提亮、光圆 alpha 翻倍以上，
    否则截出来就是一整块纯色，看不出「卡片也吃壁纸」这条特性。
    """
    pm = QPixmap(WALL_W, WALL_H)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    grad = QLinearGradient(0, 0, WALL_W, WALL_H)
    grad.setColorAt(0.0, QColor("#2E4650"))
    grad.setColorAt(0.55, QColor("#24363E"))
    grad.setColorAt(1.0, QColor("#1B2A31"))
    p.fillRect(pm.rect(), QBrush(grad))
    for cx, cy, r, base in ((180, 120, 300, (170, 240, 235)),
                            (WALL_W - 160, 90, 260, (150, 195, 250)),
                            (WALL_W * 0.55, WALL_H - 100, 340, (140, 225, 235))):
        g = QRadialGradient(cx, cy, r)
        col = QColor(base[0], base[1], base[2], 230)
        g.setColorAt(0.0, col)
        g.setColorAt(1.0, QColor(base[0], base[1], base[2], 0))
        p.setBrush(QBrush(g))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QRectF(cx - r, cy - r, r * 2, r * 2))
    p.end()
    pm.save(path, "PNG")


# ---------------- 演示数据（指向临时目录，零真实写入） ----------------

DEMO_FRAGMENTS = [
    ("text", "周会要点：下周三前把报价单模板定稿，接口文档同步给测试"),
    ("link", "https://docs.python.org/3/library/dataclasses.html"),
    ("command", "python -m PyInstaller --noconfirm --clean FloatPulse.spec"),
    ("code", "def load(path):\n    with open(path, encoding=\"utf-8\") as f:\n        return json.load(f)"),
    ("text", "客户来访时间改到周五上午十点，会议室改三楼"),
]

DEMO_CARDS = [
    "知识卡片：FloatPulse 的悬浮球支持四向吸边隐藏，鼠标移近自动滑出。",
    "知识卡片：碎片池里的每一条都可以一键复制回剪贴板。",
]


def build_managers(tmp):
    """全部指向临时目录的演示数据集（README 公开页，不许有个人信息）"""
    config = ConfigManager(os.path.join(tmp, "config.json"))
    frags = FragmentManager(os.path.join(tmp, "fragments.json"))
    tasks = TaskManager(os.path.join(tmp, "schedule.json"))
    notes = NoteManager(os.path.join(tmp, "notes.json"))
    nav = NavManager(os.path.join(tmp, "nav.json"))
    assets = TempAssetManager(tmp)

    for ftype, content in DEMO_FRAGMENTS:
        frags.add_fragment(ftype, content, source="剪贴板")
    tasks.add_task("整理本周项目进度", "把三个里程碑的完成度写进周报",
                   "2026-10-13")
    notes.add_note("会议纪要：确认了下阶段先做数据迁移，再补权限模块。",
                   "9 月复盘")
    gid = nav.add_group("常用")
    nav.add_site(gid, "Python 文档", "https://docs.python.org/3/")
    return config, frags, tasks, notes, nav, assets


# ---------------- 主流程 ----------------

def main():
    if not os.path.isdir(DATA_ROOT):
        raise SystemExit("真实数据根不存在：%s" % DATA_ROOT)
    before = snapshot_data_root()          # 拍前快照（第一道防线）
    os.makedirs(OUT_DIR, exist_ok=True)

    app = QApplication(sys.argv)
    load_cjk_font(app)   # 挂不上只是中文/emoji 变方框，不阻断截图

    tmp = tempfile.mkdtemp(prefix="fp_shot_card_")
    try:
        config, frags, tasks, notes, nav, assets = build_managers(tmp)

        # 演示壁纸：导入真实数据根（resolve_path 只认 get_backgrounds_dir()，
        # 导进 tmp 会静默不生效）；拍完 prune_unused([]) 清掉。
        wp_tmp = os.path.join(tmp, "demo-wallpaper.png")
        make_demo_wallpaper(wp_tmp)
        wp_name, err = wallpaper.import_image(wp_tmp)
        if err:
            raise SystemExit("演示壁纸导入失败：%s" % err)

        # 强调色是 theme 模块级运行时状态（离屏下没有主窗 _apply_theme
        # 帮忙推进），必须建卡片前手动 set；与 shot_readme.py 深档同款
        theme.set_accent(DARK_ACCENT)

        # 壁纸键先写进（临时）配置：set_config_manager 注入时会立即
        # apply_background_from_config 读走，卡片壳连带重刷
        config.set("wallpaper", wp_name)
        config.set("wallpaper_mode", "cover")
        config.set("wallpaper_opacity", 100)
        config.set("wallpaper_blur", 0)
        # veil 默认 82% 太重看不清壁纸；演示档降到 40（与 shot_readme
        # 深色主窗同口径），卡片壳仍保底 55% 实底，文字可读
        config.set("wallpaper_veil", 40)

        card = CardWindow(theme="dark")
        card.set_cards(DEMO_CARDS)
        card.set_fragment_manager(frags)
        card.set_task_manager(tasks)
        card.set_note_manager(notes)
        card.set_nav_manager(nav)
        card.set_config_manager(config)
        card.set_asset_manager(assets)
        card.switch_mode("fragment")

        state = {"done": False}

        def shoot():
            grab = card.grab()
            expect = card.WINDOW_WIDTH + card.CARD_MARGIN * 2, \
                card.WINDOW_HEIGHT + card.CARD_MARGIN * 2
            if (grab.width(), grab.height()) != expect:
                raise SystemExit("grab 尺寸 %sx%s ≠ 预期 %sx%s"
                                 % (grab.width(), grab.height(), *expect))
            # 16px 裁剪口径：裁掉四周阴影留白，只留卡片本体
            out = grab.copy(card.CARD_MARGIN, card.CARD_MARGIN,
                            card.WINDOW_WIDTH, card.WINDOW_HEIGHT)
            path = os.path.join(OUT_DIR, OUT_NAME)
            if not out.save(path, "PNG"):
                raise SystemExit("保存失败：%s" % path)
            print("[OK] %s  %dx%d（已裁掉 %dpx 阴影外环）"
                  % (path, out.width(), out.height(), card.CARD_MARGIN))
            state["done"] = True
            # 收尾：壁纸配置复原 + 真实 backgrounds 里的演示图清掉
            config.set("wallpaper", "")
            removed = wallpaper.prune_unused([])
            print("[i] 已清理演示壁纸 %d 个文件" % removed)
            card.hide()
            app.quit()

        def pump():
            QTimer.singleShot(GRAB_PUMP_MS, shoot)

        card.show()
        QTimer.singleShot(GRAB_PUMP_MS, pump)
        app.exec()

        if not state["done"]:
            raise SystemExit("截图流程未完成（定时器链中断）")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # 拍后快照 + 零污染断言（第二道防线）
    after = snapshot_data_root()
    assert_no_pollution(before, after)

    # docs/ 被 .gitignore 整目录忽略，README 读的是 assets/images/ ——
    # 必须复制过去覆盖（中文路径 bash cp 不可靠，用 shutil.copy2）
    src = os.path.join(OUT_DIR, OUT_NAME)
    dst = os.path.join(ASSETS_DIR, OUT_NAME)
    os.makedirs(ASSETS_DIR, exist_ok=True)
    shutil.copy2(src, dst)
    print("[OK] → %s" % dst)


if __name__ == "__main__":
    main()
