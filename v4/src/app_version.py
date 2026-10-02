# -*- coding: utf-8 -*-
"""程序与插件契约的版本常量（唯一真相源）。

- ``APP_VERSION``        : 程序版本，三段式，与 CHANGELOG / Release tag 对齐。
- ``PLUGIN_API_VERSION`` : 插件契约版本，**只在破坏性变更时 +1**。

插件在 manifest 里可声明两个可选字段（详见 docs/插件开发说明）：
  - ``api_version``     : 插件要求的契约版本（缺省 1）。高于宿主 → 拒载。
  - ``min_app_version`` : 插件要求的最低程序版本（缺省 ""，即不限制）。

这样 API 演进时，老插件会以明确的「版本不匹配」失败阶段暴露出来（带修复建议），
而不是留到运行期变成「动作创建失败」这类摸不着头脑的表层错误。

本模块**刻意不 import PyQt6**（与 ``plugin_api.py`` 同款约束），
让纯逻辑测试与工具脚本无 GUI 也能引用。
"""

# 程序版本：三段式点分数字（major.minor.patch）
# 改这里时**必须同步** CHANGELOG.md 与 Release tag
APP_VERSION = "4.8.0"

# 插件契约版本。破坏性变更（改 PluginContext 签名、删方法、
# 改 manifest 字段语义等）才 +1；新增可选能力不 +1。
PLUGIN_API_VERSION = 1


def parse_version(text) -> tuple:
    """把 ``"4.6.0"`` 解析成 ``(4, 6, 0)``；非法格式返回 ``None``。

    只接受「点分的纯数字段」，段数不限（1~4 段均可），便于未来扩展。
    拒绝 ``""`` / ``"4.6"`` 之外的奇怪写法交给调用方决定策略——
    这里只回：能解析就回元组，不能就回 None。
    """
    if not isinstance(text, str):
        return None
    parts = text.strip().split(".")
    if not parts or len(parts) > 4:
        return None
    out = []
    for p in parts:
        p = p.strip()
        if not p.isdigit():
            return None
        out.append(int(p))
    return tuple(out)


def is_version_ge(host: str, required: str) -> bool:
    """宿主版本 ``host`` 是否 **>= ** 插件要求的最低版本 ``required``。

    任一侧解析失败时返回 True（放行）——版本字段是可选护栏，
    不该因为写了个奇怪的值就把用户插件加载路径整体拦死；
    真正格式非法的 ``min_app_version`` 在 validate_manifest 里已单独拒载。
    """
    h = parse_version(host)
    r = parse_version(required)
    if h is None or r is None:
        return True
    # 补齐到等长再比较，使 (4, 6) 与 (4, 6, 0) 等价
    n = max(len(h), len(r))
    return h + (0,) * (n - len(h)) >= r + (0,) * (n - len(r))
