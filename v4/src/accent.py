# -*- coding: utf-8 -*-
"""
====================================================================
强调色（accent）模块  -  accent
====================================================================
把「主色调」从写死的两套内置值，扩展成「预置色板 + 自定义 HEX」。

设计铁律（与本项目其它纯逻辑模块一致）：
  · **纯 Python，禁 import PyQt6** —— 色值计算要在无 GUI 环境与
    单元测试里直接跑；UI 侧只负责把结果塞进 QSS。
  · **每个色板只写两个基准 hex**（浅色一支、深色一支），其余全部派生。
    浅/深两版的明度差异是有意设计的（不是同一色的两种透明度），
    所以本模块不做「一键反色」，只做显式定义 + 安全网校正。
  · **自定义 HEX 一律过安全网**：在 `$surface` 上不到 WCAG AA
    4.5:1 就顺着明度轴压/提，直到达标。用户选柠檬黄不会被静默丢掉，
    但也不会产出白底上看不见的按钮。

派生链（一处取 `#0F6E56` 这样的基准色，产出完整 token 组）：
  primary → primary_hover / primary_pressed / primary_a{08,12,18,30} /
  primary_alpha / primary_border / primary_border_strong /
  list_border / list_item_{hover,selected} /
  on_primary（自动选白/墨）/ on_accent / accent_soft /
  secondary_text / focus_ring

不在此口径内的键：`primary_lite` / `primary_deep` 保留内置薄荷值
（撤销条按钮与启动屏渐变在用，theme.py 注释里已标明与新版主色解耦），
本模块不碰；同样不碰 `cat_*`（语义类别色，与主色无关）。
"""

from __future__ import annotations

import re

# ====================================================================
# 常量
# ====================================================================
DEFAULT_ACCENT = "default"      # 沿用 theme.py 内置值（零 override）
CUSTOM_ACCENT = "custom"        # 走 accent_custom 的自定义 HEX

_HEX_RE = re.compile(r"^#?([0-9A-Fa-f]{6})$")

THEME_LIGHT = "light"
THEME_DARK = "dark"

# 派生时的明度步进（百分点）。与内置值反向拟合得出：
#   浅色 hover #0F6E56→#227A64（+7%L），pressed →#0C5A47（-5%L）
#   深色 hover #5DCAA5→#68CFAC（+3%L），pressed →#4BB894（-6%L）
_L_HOVER = {THEME_LIGHT: 7, THEME_DARK: 3}
_L_PRESSED = {THEME_LIGHT: -5, THEME_DARK: -6}

# 半透明令牌的 alpha（浅/深两版的小差异是既有设计，逐项照搬）
_PRIMARY_ALPHA = {THEME_LIGHT: 0.12, THEME_DARK: 0.15}
_BORDER_STRONG = {THEME_LIGHT: 0.40, THEME_DARK: 0.45}
_LIST_BORDER = {THEME_LIGHT: 0.22, THEME_DARK: 0.20}
_LIST_SELECTED = {THEME_LIGHT: 0.16, THEME_DARK: 0.18}

# accent_soft = 主色往 $surface 方向混合的比例（越大越接近底色）
_ACCENT_SOFT_MIX = {THEME_LIGHT: 0.88, THEME_DARK: 0.88}

# 各主题容器底色（对比度校正的参照面，取自 theme.py 的 $surface）
_SURFACE_HEX = {THEME_LIGHT: "#FFFFFF", THEME_DARK: "#1E2126"}

# 文本对比度下限（WCAG AA 正文字号）
AA_TEXT = 4.5


# ====================================================================
# 色板定义
# ====================================================================
class AccentSpec:
    """一个色板：id + 显示名 + 深浅两支基准色。

    只用 NamedTuple 的语义（不可变、可比较），这里手写类是为了让
    ``label`` 有默认值之外的可读性，且不引入 dataclasses 的额外 import。
    """

    __slots__ = ("spec_id", "label", "light", "dark")

    def __init__(self, spec_id: str, label: str, light: str, dark: str):
        # 这里不做 normalize —— 规范化要在 hex_to_rgb 里兜底，而该函数
        # 定义在模块后段（色板常量在模块加载时就要实例化，顺序上取不到）。
        self.spec_id = spec_id
        self.label = label
        self.light = light
        self.dark = dark

    def base_for(self, theme_name: str) -> str:
        """取该色板在指定主题下的基准色（未知主题名按浅色处理）"""
        raw = self.dark if theme_name == THEME_DARK else self.light
        return normalize_hex(raw) or raw

    def __repr__(self):  # pragma: no cover - 调试用
        return (f"AccentSpec({self.spec_id!r}, {self.label!r}, "
                f"{self.light}, {self.dark})")


ACCENT_SPECS = (
    AccentSpec(DEFAULT_ACCENT, "墨绿（内置）", "#0F6E56", "#5DCAA5"),
    AccentSpec("ocean",        "静海蓝",      "#15607F", "#6BB3DE"),
    AccentSpec("indigo",       "靛青",        "#414FB3", "#A3ABF5"),
    AccentSpec("violet",       "紫藤",        "#6B3F9E", "#C3A6EE"),
    AccentSpec("rose",         "胭脂",        "#A32D4B", "#F09AAB"),
    AccentSpec("amber",        "琥珀",        "#8A5A12", "#E8B45C"),
    AccentSpec("copper",       "赭石",        "#8C4A2F", "#DFA184"),
    AccentSpec("graphite",     "石墨",        "#4A5058", "#B7BDC6"),
)

_ACCENT_BY_ID = {s.spec_id: s for s in ACCENT_SPECS}

# 合法 id 集合（含 custom）：供 config 的 _CONFIG_VALUE_WHITELISTS 使用
ACCENT_IDS = frozenset(list(_ACCENT_BY_ID) + [CUSTOM_ACCENT])


def accent_specs() -> tuple:
    """全部色板（只读语义：返回元组，避免外部改动模块状态）"""
    return ACCENT_SPECS


def accent_base(spec_id: str, theme_name: str) -> str:
    """取某色板在某主题下的基准 hex；未知 id 一律回落内置。"""
    spec = _ACCENT_BY_ID.get(spec_id)
    if spec is None:
        spec = _ACCENT_BY_ID[DEFAULT_ACCENT]
    return spec.base_for(theme_name)


def accent_label(spec_id: str) -> str:
    spec = _ACCENT_BY_ID.get(spec_id)
    return spec.label if spec is not None else spec_id


# ====================================================================
# 基础色算（RGB / HSL / WCAG）
# ====================================================================
def normalize_hex(value) -> str:
    """把用户输入收敛成 ``#RRGGBB`` 大写；非法返回空串。

    容错范围刻意放宽：允许省略 ``#``、大小写混写。不允许 3 位简写
    （``#abc`` 会让用户以为支持而实际渲染出意外颜色，宁可直接报错）。
    """
    if not isinstance(value, str):
        return ""
    m = _HEX_RE.match(value.strip())
    if not m:
        return ""
    return "#" + m.group(1).upper()


def is_valid_hex(value) -> bool:
    return bool(normalize_hex(value))


def hex_to_rgb(value: str) -> tuple:
    """``#RRGGBB`` → (r, g, b)；非法值回落黑色。"""
    text = normalize_hex(value)
    if not text:
        return (0, 0, 0)
    return (int(text[1:3], 16), int(text[3:5], 16), int(text[5:7], 16))


def rgb_to_hex(rgb) -> str:
    r, g, b = (int(round(max(0, min(255, v)))) for v in rgb)
    return "#%02X%02X%02X" % (r, g, b)


def _srgb_channel(c: float) -> float:
    c = c / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def relative_luminance(value: str) -> float:
    """WCAG 相对亮度（0=黑，1=白）"""
    r, g, b = hex_to_rgb(value)
    return (0.2126 * _srgb_channel(r)
            + 0.7152 * _srgb_channel(g)
            + 0.0722 * _srgb_channel(b))


def contrast_ratio(fg: str, bg: str) -> float:
    """WCAG 对比度（1~21）"""
    l1 = relative_luminance(fg)
    l2 = relative_luminance(bg)
    hi, lo = (l1, l2) if l1 >= l2 else (l2, l1)
    return (hi + 0.05) / (lo + 0.05)


def _rgb_to_hsl(rgb) -> tuple:
    r, g, b = (v / 255.0 for v in rgb)
    hi = max(r, g, b)
    lo = min(r, g, b)
    lum = (hi + lo) / 2.0
    d = hi - lo
    if d == 0:
        return (0.0, 0.0, lum)
    s = d / (2.0 - hi - lo) if lum > 0.5 else d / (hi + lo)
    if hi == r:
        h = ((g - b) / d) % 6.0
    elif hi == g:
        h = (b - r) / d + 2.0
    else:
        h = (r - g) / d + 4.0
    return (h * 60.0, s, lum)


def _hsl_to_rgb(h: float, s: float, lum: float) -> tuple:
    h = h % 360.0
    c = (1.0 - abs(2.0 * lum - 1.0)) * s
    x = c * (1.0 - abs((h / 60.0) % 2.0 - 1.0))
    m = lum - c / 2.0
    seg = int(h // 60.0) % 6
    table = ((c, x, 0.0), (x, c, 0.0), (0.0, c, x),
             (0.0, x, c), (x, 0.0, c), (c, 0.0, x))
    r, g, b = table[seg]
    return (round((r + m) * 255.0), round((g + m) * 255.0),
            round((b + m) * 255.0))


def shift_lightness(value: str, delta_pct: float) -> str:
    """沿 HSL 明度轴平移 ``delta_pct`` 个百分点，保留色相与饱和度。

    ``delta_pct`` 可正可负，内部夹在 [0,100] —— 撞到端点就停在端点，
    不会溢出成别的颜色。
    """
    h, s, lum = _rgb_to_hsl(hex_to_rgb(value))
    lum = max(0.0, min(1.0, lum + delta_pct / 100.0))
    return rgb_to_hex(_hsl_to_rgb(h, s, lum))


def mix(foreground: str, background: str, bg_ratio: float) -> str:
    """把前景色往背景色方向混合（``bg_ratio``=1 全底，=0 全前景）。

    混合在线性 RGB 上进行即可满足审美需求（这里只做视觉淡染，
    不做色彩管理），比 sRGB 直接插值更不容易发灰。
    """
    f = hex_to_rgb(foreground)
    b = hex_to_rgb(background)
    k = max(0.0, min(1.0, float(bg_ratio)))
    return rgb_to_hex(tuple(f[i] + (b[i] - f[i]) * k for i in range(3)))


def ensure_contrast(value: str, surface: str,
                    target: float = AA_TEXT, toward_dark: bool = True
                    ) -> str:
    """安全网：逐步压暗（或提亮）直到在 ``surface`` 上达到 ``target``。

    一次 1 个百分点走 100 步足够覆盖整条明度轴；万一走完仍不达标
    （极端情况：目标超过 21），返回最后一站的极值，不会死循环。
    """
    step = -1.0 if toward_dark else 1.0
    current = rgb_to_hex(hex_to_rgb(value))
    for _ in range(100):
        if contrast_ratio(current, surface) >= target:
            return current
        current = shift_lightness(current, step)
    return current


def _pick_variant(primary: str, on_primary: str, delta_pct: float) -> str:
    """按明度步进派生 hover/pressed，但保证 ``on_primary`` 仍达 AA。

    步进朝 0 逐档收回：既保留「hover 提亮 / pressed 下沉」的设计意图，
    又不会出现某支色板刚好卡在 AA 线下的问题。

    两个方向都走不通时（只可能发生在「安全网刚把基准色压到 AA 边界」
    的极端自定义色上），掉头往反方向找 —— hover 必须**看得出来**，
    宁可 direction 反直觉，也不能点了没反应。
    """
    for sign in (1, -1):
        d = int(delta_pct)
        while d != 0:
            cand = shift_lightness(primary, d)
            if contrast_ratio(on_primary, cand) >= AA_TEXT:
                return cand
            d -= 1 if delta_pct > 0 else -1
        delta_pct = -delta_pct
    return primary


# ====================================================================
# 派生：基准色 → 整套 token
# ====================================================================
def derive_accent_tokens(base_hex: str, theme_name: str) -> dict:
    """从一个基准色派生该主题下的完整强调色 token 组。

    ``theme_name`` 只认 "light" / "dark"，其他值按 light 处理
    （theme.py 的 resolve_theme_name 已保证传进来的只有这两值）。
    """
    theme = THEME_DARK if theme_name == THEME_DARK else THEME_LIGHT
    surface = _SURFACE_HEX[theme]

    # 1) 安全网：主色要能当按钮实底（上文的字自动挑），也要能当
    #    「$surface 上的强调文字/描边」用 —— 统一按 4.5:1 收紧即可，
    #    非文本 UI（焦点环 3:1）是这条约束的子集。
    primary = ensure_contrast(base_hex, surface, AA_TEXT,
                              toward_dark=(theme == THEME_LIGHT))

    rgb = hex_to_rgb(primary)
    def rgba(alpha: float) -> str:
        return "rgba(%d, %d, %d, %s)" % (rgb[0], rgb[1], rgb[2], alpha)

    # 2) 主色底上的文字：白 vs 深墨，挑对比度高的那个。深墨刻意保留
    #    一滴主色血统（明度 12% 而非纯黑），贴近内置值的观感。
    h, s, _l = _rgb_to_hsl(rgb)
    ink_hex = rgb_to_hex(_hsl_to_rgb(h, s, 0.12))
    on_primary = ("#FFFFFF"
                  if contrast_ratio("#FFFFFF", primary)
                  >= contrast_ratio(ink_hex, primary) else ink_hex)

    # 3) hover / pressed：先按设计意图的明度步进走，若「on_primary 仍达
    #    AA」不成立就逐档收回步进（朝 primary 靠），最坏退化成 primary
    #    本身 —— 宁可 hover 几乎不可见，也不产出白字发虚的按钮。
    hover = _pick_variant(primary, on_primary, _L_HOVER[theme])
    pressed = _pick_variant(primary, on_primary, _L_PRESSED[theme])

    # 4) 次按钮文字 / 焦点环：浅色主题下用更沉的那一档（对齐内置值
    #    primary=#0F6E56 / secondary_text=#0C5A47 的关系），深色主题下
    #    直接用主色本身（深底的对比度本就宽裕，再提亮反而发飘）。
    muted = pressed if theme == THEME_LIGHT else primary

    return {
        "primary":               primary,
        "primary_hover":         hover,
        "primary_pressed":       pressed,
        "primary_alpha":         rgba(_PRIMARY_ALPHA[theme]),
        "primary_a08":           rgba(0.08),
        "primary_a12":           rgba(0.12),
        "primary_a18":           rgba(0.18),
        "primary_a30":           rgba(0.30),
        "primary_border":        rgba(0.30),
        "primary_border_strong": rgba(_BORDER_STRONG[theme]),
        "list_border":           rgba(_LIST_BORDER[theme]),
        "list_item_hover":       rgba(0.08),
        "list_item_selected":    rgba(_LIST_SELECTED[theme]),
        "on_primary":            on_primary,
        "on_accent":             on_primary,
        "accent_soft":           mix(primary, surface, _ACCENT_SOFT_MIX[theme]),
        "secondary_text":        muted,
        "focus_ring":            muted,
    }


def build_accent_override(accent_id, theme_name: str, custom_hex: str = ""
                          ) -> dict:
    """产出可直接 ``dict.update`` 到 `theme.get_colors()` 结果上的覆盖层。

    - `accent_id` == "default" → 返回空 dict（内置值原样生效，零改动）
    - `accent_id` == "custom"  → 基准色取 ``custom_hex``；HEX 非法时
      回落内置（调用方拿到的仍是空 dict，行为等价于没设置）
    - 其余 → 取对应色板在该主题下的基准色
    """
    if accent_id == DEFAULT_ACCENT:
        return {}
    if accent_id == CUSTOM_ACCENT:
        base = normalize_hex(custom_hex)
        if not base:
            return {}
    else:
        spec = _ACCENT_BY_ID.get(accent_id)
        if spec is None:
            return {}
        base = spec.base_for(theme_name)
    return derive_accent_tokens(base, theme_name)
