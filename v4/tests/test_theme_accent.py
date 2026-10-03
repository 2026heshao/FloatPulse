# -*- coding: utf-8 -*-
"""
主题扩展护栏测试  -  test_theme_accent
====================================================================
覆盖 2026-10-03 主题扩展两块：**强调色**与**壁纸**。

之所以单独成文件：
  · test_theme_contrast.py 钉的是「内置两套配色自己有没有配错」，
    本文件钉的是「用户换色之后还能不能看」——两者维度不同。
  · 强调色是**派生**出来的（一个基准 hex → 十余个 token），最容易出现
    「某个色板在某套主题下刚好卡在 AA 线下」这种只在特定组合炸的问题，
    必须全组合（色板 × 主题）遍历，而不是抽两三个样本。

反向验证纪律（见 §可爱名）:对比度断言本身也可能是假的 —— 如果
``contrast_ratio`` 写错成了恒返回 21，所有 >= 4.5 的断言都会永久绿灯。
因此本文件保留两组「必须是坏值」的用例，用来证明这条尺子还能量出差值。
"""

import os

import pytest

from src import accent
from src import appearance
from src import theme
from src import wallpaper
from src.app_paths import get_backgrounds_dir
from src.theme import THEMES, get_main_window_qss, get_card_window_qss, get_menu_qss

ALL_THEMES = ("light", "dark")
ALL_SPECS = list(accent.ACCENT_SPECS)
ALL_PAIRS = [(s.spec_id, t) for s in ALL_SPECS for t in ALL_THEMES]

SURFACE = {"light": THEMES["light"]["surface"],
           "dark": THEMES["dark"]["surface"]}


@pytest.fixture(autouse=True)
def _reset_accent():
    """每个用例后把全局强调色复位。

    accent 是**模块级单点**（全仓数十处 get_colors 都走它），一旦某个
    用例漏了复位，后面的用例会带着陌生颜色跑，报错位置还会指向完全无关
    的断言——这类污染极难排查。
    """
    theme.set_accent(accent.DEFAULT_ACCENT, "")
    yield
    theme.set_accent(accent.DEFAULT_ACCENT, "")


# ====================================================================
# 0. 尺子自检（证明下面的 >= 4.5 断言不是恒真的假护栏）
# ====================================================================
def test_contrast_metric_actually_discriminates():
    """同一支标尺必须先能量出「坏值」，它的「好值」判定才有意义"""
    assert accent.contrast_ratio("#FFFF00", "#FFFFFF") < 4.5, (
        "黄底白字应当是坏值；若这条不成立，说明 contrast_ratio 算错了，"
        "下面所有 >= 4.5 的断言都失去意义")
    assert accent.contrast_ratio("#FFFFFF", "#FFFFFF") == pytest.approx(1.0)
    assert accent.relative_luminance("#FFFFFF") == pytest.approx(1.0)
    assert accent.relative_luminance("#000000") == pytest.approx(0.0)
    # 拼错 tooth:同一个分支 —— 深底 vs 浅底的取色必须不同，否则 ensure_contrast
    # 的 toward_dark 参数写反也不会有人发现
    light_fix = accent.ensure_contrast("#FFD400", "#FFFFFF",
                                       toward_dark=True)
    dark_fix = accent.ensure_contrast("#000080", "#1E2126",
                                      toward_dark=False)
    assert accent.relative_luminance(light_fix) < \
        accent.relative_luminance("#FFD400")
    assert accent.relative_luminance(dark_fix) > \
        accent.relative_luminance("#000080")


# ====================================================================
# 1. 每个色板 × 每套主题，全部过关 WCAG AA
# ====================================================================
@pytest.mark.parametrize("spec_id,theme_name", ALL_PAIRS)
def test_accent_tokens_meet_wcag_aa(spec_id, theme_name):
    # 走 derive 而不是 build_accent_override：default 那支是「空 override」
    # （直接用 THEMES 原文），但它的**派生结果**同样要过关 —— 用户先选
    # 别的色板再切回墨绿走的正是这条路径。
    spec = next(s for s in ALL_SPECS if s.spec_id == spec_id)
    tokens = accent.derive_accent_tokens(spec.base_for(theme_name), theme_name)
    surface = SURFACE[theme_name]

    # ① 主色当强调文字/描边用：$surface 上要够看清
    assert accent.contrast_ratio(tokens["primary"], surface) >= 4.5, (
        "%s/%s：主色 %s 在 %s 上对比度不足 4.5:1"
        % (spec_id, theme_name, tokens["primary"], surface))
    # ② 主色当按钮实底用：字要够看清
    assert accent.contrast_ratio(tokens["on_primary"],
                                 tokens["primary"]) >= 4.5
    # ③ hover / pressed 也在 AA 内（按钮最容易被忘的一档）
    for key in ("primary_hover", "primary_pressed"):
        ratio = accent.contrast_ratio(tokens["on_primary"], tokens[key])
        assert ratio >= 4.5, (
            "%s/%s：%s=%s 与 on_primary 对比度仅 %.2f"
            % (spec_id, theme_name, key, tokens[key], ratio))
    # ④ hover/pressed 必须「看得出来」，派生算法退化成同色等于没反馈
    assert tokens["primary_hover"] != tokens["primary"]
    assert tokens["primary_pressed"] != tokens["primary"]
    # ⑤ 次按钮文字（浅色主题下是压暗一档）同样要在面板底上看得清
    assert accent.contrast_ratio(tokens["secondary_text"], surface) >= 4.5


def test_every_spec_covers_both_themes():
    for spec in ALL_SPECS:
        light_u = accent.relative_luminance(spec.base_for("light"))
        dark_u = accent.relative_luminance(spec.base_for("dark"))
        assert dark_u > light_u, (
            "%s：深色主题的那一支应该比浅色支更亮（深底要提亮才看得见）"
            % spec.spec_id)
    assert len(ALL_SPECS) >= 6, "色板数量太少，谈不上「多个颜色」"
    assert accent.CUSTOM_ACCENT in accent.ACCENT_IDS
    assert accent.DEFAULT_ACCENT in accent.ACCENT_IDS


# ====================================================================
# 2. 与内置值的吻合度（派生算法不能把「默认色」变样）
# ====================================================================
def test_default_palette_reproduces_builtin_values():
    """用内置基准色推导，结果必须和 THEMES 里手写的逐字节一致。

    这条是跨模块交叉验证：accent.py 与 theme.py 是**独立**的两份数据，
    两边对得上才说明派生链没有把原设计漂走。
    """
    light = accent.derive_accent_tokens(THEMES["light"]["primary"], "light")
    dark = accent.derive_accent_tokens(THEMES["dark"]["primary"], "dark")
    assert light["primary"] == THEMES["light"]["primary"]
    assert dark["primary"] == THEMES["dark"]["primary"]
    # secondary_text 是「压暗一档」派生出来的，容差在观感不可辨的量级
    assert accent.contrast_ratio(light["secondary_text"],
                                 THEMES["light"]["secondary_text"]) < 1.05
    assert dark["secondary_text"] == THEMES["dark"]["secondary_text"]


def test_default_accent_is_a_no_op():
    """默认强调色必须零侵入：get_colors 原字典原样返回"""
    assert theme.get_colors("light") is THEMES["light"]
    assert theme.get_colors("dark") is THEMES["dark"]
    assert accent.build_accent_override(accent.DEFAULT_ACCENT,
                                        "light") == {}


# ====================================================================
# 3. 非法输入的兜底
# ====================================================================
@pytest.mark.parametrize("bad", [None, "", "nope", 123, [], "#FFF", "#GGGGGG",
                                 "#00000", "  #0F6E56  #"])
def test_invalid_custom_hex_falls_back(bad):
    """脏 HEX 一律回落到「未设置」，而不是产出一個奇怪的颜色"""
    assert accent.normalize_hex(bad) == ""
    assert accent.build_accent_override(accent.CUSTOM_ACCENT, "light",
                                        bad) == {}


@pytest.mark.parametrize("good", ["#0F6E56", "0f6e56", "  #15607F ",
                                  "#AABBCC"])
def test_valid_custom_hex_is_normalized(good):
    value = accent.normalize_hex(good)
    assert value.startswith("#") and len(value) == 7
    assert value == value.upper()


def test_unknown_accent_id_falls_back_to_default():
    assert accent.build_accent_override("no-such-palette", "light") == {}
    theme.set_accent("no-such-palette")
    assert theme.get_accent() == (accent.DEFAULT_ACCENT, "")
    assert theme.get_colors("light") is THEMES["light"]


def test_custom_accent_safety_net_darkens_too_light_colors():
    """用户填柠檬黄不能真的按柠檬黄上，按钮会白字糊掉"""
    tokens = accent.build_accent_override(accent.CUSTOM_ACCENT, "light",
                                          "#FFD400")
    assert tokens["primary"] != "#FFD400"
    assert accent.contrast_ratio(tokens["primary"], SURFACE["light"]) >= 4.5
    # 但也不能把用户的选择彻底扔掉：色相应当还在原色的邻域
    assert accent.contrast_ratio(tokens["primary"], "#FFD400") < 8.0


# ====================================================================
# 4. QSS 链路（Template.substitute 严格模式，缺键直接 KeyError）
# ====================================================================
@pytest.mark.parametrize("spec_id,theme_name", ALL_PAIRS)
def test_qss_renders_with_every_accent(spec_id, theme_name):
    theme.set_accent(spec_id)
    colors = theme.get_colors(theme_name)
    expected = colors["primary"]
    for qss_fn in (get_main_window_qss, get_card_window_qss, get_menu_qss):
        qss = qss_fn(theme_name)
        if spec_id == accent.DEFAULT_ACCENT:
            continue
        assert expected in qss, (
            "%s/%s：%s 的输出里找不到新主色 %s"
            % (spec_id, theme_name, qss_fn.__name__, expected))


def test_accent_merge_does_not_mutate_themes():
    theme.set_accent("ocean")
    merged = theme.get_colors("light")
    assert merged is not THEMES["light"]
    assert THEMES["light"]["primary"] == "#0F6E56", "THEMES 被污染了"
    # 放回 default 后必须恢复身份相等（缓存不能赖着不走）
    theme.set_accent("default")
    assert theme.get_colors("light") is THEMES["light"]


def test_accent_follows_theme_resolution():
    """"follow" 解析要在 accent 之前发生，否则 QSS 模板会拿到错分支"""
    theme.set_accent("rose")
    light = theme.get_colors("light")
    dark = theme.get_colors("dark")
    assert light["primary"] != dark["primary"]
    assert dark["_name"] == "dark" and light["_name"] == "light"


# ====================================================================
# 5. 壁纸：参数收敛 + 文件管理（纯逻辑，无 GUI）
# ====================================================================
def test_spec_sanitize_clamps_everything():
    spec = wallpaper.sanitize_spec(
        name="x.png", mode="wormhole", opacity=999, blur=-5, veil=None)
    assert spec["mode"] == wallpaper.DEFAULT_MODE
    assert spec["opacity"] == 100
    assert spec["blur"] == 0
    assert spec["veil"] == wallpaper.DEFAULT_VEIL
    assert spec["name"] == "x.png"


def test_safe_name_blocks_traversal():
    assert wallpaper.safe_name("../../evil.png") == "evil.png"
    assert wallpaper.safe_name("C:\\tmp\\a\\b.png") == "b.png"
    assert wallpaper.safe_name(None) == ""
    assert wallpaper.safe_name("") == ""


def test_wallpaper_spec_path_is_empty_when_missing(tmp_path):
    class _Cfg(dict):
        def get(self, key, default=None):
            return dict.get(self, key, default)

    cfg = _Cfg({"wallpaper": "ghost.png", "wallpaper_mode": "cover",
                "wallpaper_opacity": 100, "wallpaper_blur": 0,
                "wallpaper_veil": 40})
    spec = appearance.wallpaper_spec(cfg, str(tmp_path))
    assert spec["path"] == "", "文件不存在时绝不能回一个拼出来的路径"
    assert spec["veil"] == 40


def test_wallpaper_import_dedupe_and_prune(tmp_path):
    # 造一张真的 PNG（最小合法 1x1）
    png = bytes.fromhex(
        "89504e470d0a1a0a0000000d4948445200000001000000010802000000"
        "907753de0000000c49444154789c6360000002000154a24f7d00000000"
        "49454e44ae426082")
    src = tmp_path / "photo.png"
    src.write_bytes(png)

    name, err = wallpaper.import_image(str(src), str(tmp_path))
    assert err == "" and name, "PNG 应当能导入：%s" % err
    assert name in wallpaper.list_images(str(tmp_path))
    assert os.path.isdir(get_backgrounds_dir(str(tmp_path)))

    # 同一张图再导一次不该产生第二个文件
    name2, err2 = wallpaper.import_image(str(src), str(tmp_path))
    assert name2 == name and err2 == ""
    assert len(wallpaper.list_images(str(tmp_path))) == 1

    # 不在保留名单里的会被清掉
    assert wallpaper.prune_unused([], str(tmp_path)) == 1
    assert wallpaper.list_images(str(tmp_path)) == []

    # 非图片文件会被魔数检查挡下
    bad = tmp_path / "fake.png"
    bad.write_bytes(b"this is definitely not a picture")
    name3, err3 = wallpaper.import_image(str(bad), str(tmp_path))
    assert name3 == "" and err3


def test_import_rejects_missing_and_unsupported(tmp_path):
    name, err = wallpaper.import_image(str(tmp_path / "nope.png"),
                                      str(tmp_path))
    assert name == "" and err
    txt = tmp_path / "a.txt"
    txt.write_bytes(b"hello")
    name2, err2 = wallpaper.import_image(str(txt), str(tmp_path))
    assert name2 == "" and err2


# ====================================================================
# 6. 配置白名单：新枚举必须进 set 校验
# ====================================================================
def test_config_whitelists_cover_new_enums():
    from src import config

    assert "accent" in config._CONFIG_VALUE_WHITELISTS
    assert config._CONFIG_VALUE_WHITELISTS["accent"] == accent.ACCENT_IDS
    assert "wallpaper_mode" in config._CONFIG_VALUE_WHITELISTS
    assert tuple(config._CONFIG_VALUE_WHITELISTS["wallpaper_mode"]) \
        == tuple(wallpaper.MODES)

    keys = ("accent", "accent_custom", "wallpaper", "wallpaper_mode",
            "wallpaper_opacity", "wallpaper_blur", "wallpaper_veil")
    for key in keys:
        assert key in config.DEFAULT_CONFIG, "缺少默认项 %s" % key
        assert key in config._CONFIG_TYPES, "缺少类型声明 %s" % key
    # bool 不进 RANGES；这里新增的全是数值/枚举，数值必须有范围
    for key in ("wallpaper_opacity", "wallpaper_blur", "wallpaper_veil"):
        assert key in config._CONFIG_RANGES, "缺少范围 %s" % key
    assert "accent" not in config._CONFIG_RANGES  # 枚举走白名单，不进范围表


def test_sync_theme_extras_reads_config():
    class _Cfg(dict):
        def get(self, key, default=None):
            return dict.get(self, key, default)

    cfg = _Cfg({"accent": "graphite", "accent_custom": "",
                "wallpaper": "", "wallpaper_mode": "tile",
                "wallpaper_opacity": 60, "wallpaper_blur": 4,
                "wallpaper_veil": 50})
    spec = appearance.sync_theme_extras(cfg)
    assert theme.get_accent() == ("graphite", "")
    assert spec["mode"] == "tile" and spec["blur"] == 4
    assert spec["path"] == ""


# ====================================================================
# 旧薄荷字面量收口护栏（2026-10-03 P2）：除 theme.py 的常量定义行与
# 仍为活 token 的 primary_lite / primary_deep 行外，源码任何位置不得
# 再出现旧薄荷三色字面量（大小写不敏感；注释也算——措辞引用常量名）。
# 反向验证：临时在 quick_capture.py 塞一行 x = "#5BC0BE" 必须变红。
# ====================================================================
OLD_MINT_HEXES = ("#5bc0be", "#3d9e9c", "#6fffe9")
SRC_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")


def _old_mint_offenders():
    offenders = []
    for name in sorted(os.listdir(SRC_DIR)):
        if not name.endswith(".py"):
            continue
        path = os.path.join(SRC_DIR, name)
        with open(path, encoding="utf-8") as f:
            for lineno, line in enumerate(f, 1):
                low = line.lower()
                if not any(h in low for h in OLD_MINT_HEXES):
                    continue
                if name == "theme.py" and (
                        "FALLBACK_ACCENT" in line
                        or '"primary_lite"' in line
                        or '"primary_deep"' in line):
                    continue
                offenders.append(f"src/{name}:{lineno}: {line.strip()[:70]}")
    return offenders


def test_no_old_mint_literals_outside_theme_whitelist():
    offenders = _old_mint_offenders()
    assert not offenders, (
        "旧薄荷字面量残留（统一改用 theme.FALLBACK_ACCENT*）：\n"
        + "\n".join(offenders))


def test_fallback_constants_match_the_pinned_values():
    assert theme.FALLBACK_ACCENT == "#5BC0BE"
    assert theme.FALLBACK_ACCENT_DEEP == "#3D9E9C"
    assert theme.FALLBACK_ACCENT_LITE == "#6FFFE9"


def test_primary_lite_token_kept_for_undo_and_splash():
    """活 token 不许被护栏误伤：误删会让撤销条变色（P2 任务书 §2.3）。"""
    assert THEMES["light"]["primary_lite"] == "#6FFFE9"
    assert THEMES["light"]["primary_deep"] == "#3D9E9C"
