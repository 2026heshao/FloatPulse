# -*- coding: utf-8 -*-
"""
====================================================================
插件打包器  -  tools/pack_plugin.py
====================================================================
把一个插件文件夹打成可分发的 ``<插件id>.fpplug``（本质是 zip），
放进 ``plugin_store/`` 即可被 FloatPulse 插件商店识别安装。

用法::

    python tools/pack_plugin.py plugins/ai-assistant          # 打包到 plugin_store/
    python tools/pack_plugin.py plugins/ai-assistant --force  # 覆盖已存在的包
    python tools/pack_plugin.py plugins/ai-assistant --check-only   # 只校验不打包

为什么要有这个工具（而不是手工右键压缩）：
  1. **打包期就能发现装载期才会暴露的问题**——依赖不在白名单、热键格式非法、
     action id 重复、entry 指向的文件不存在。手工打包要等到用户装进程序、
     看到「加载失败」才知道，这个工具提前把问题挡在源头。
  2. **格式统一**——必须套一层 ``<插件id>/`` 目录（与内置包一致），
     且排除 ``__pycache__`` / ``*.pyc`` / 隐藏文件，手工压缩很容易带进去。
  3. **自校验**——打完立刻重新打开 zip，确认 manifest 可解析、entry 在包内。

设计约束：
  - 只依赖 Python 标准库 + ``v4/src/plugin_api``、``v4/src/plugin_loader``
    （两者都是纯逻辑层，**不 import PyQt6**，因此本工具无 GUI 环境可跑）
  - 校验复用契约层的唯一实现（``validate_manifest`` / ``is_allowed_requirement``
    / ``is_valid_hotkey``），不在这里另写一套规则
  - 退出码：0 = 成功，1 = 校验或打包失败，2 = 用法错误（便于 CI / 批处理）
====================================================================
"""

import argparse
import json
import os
import sys
import zipfile

# 仓库根 = tools/ 的上一级；v4 是 `src` 包的父目录
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
V4_DIR = os.path.join(ROOT, "v4")
if V4_DIR not in sys.path:
    sys.path.insert(0, V4_DIR)

from src.plugin_api import (                       # noqa: E402
    is_allowed_requirement, is_valid_hotkey,
)
from src.plugin_loader import (                    # noqa: E402
    MANIFEST_NAME, PLUGIN_PACKAGE_EXT, validate_manifest,
)

# 默认输出目录：<仓库根>/plugin_store（与运行时的商店目录一致）
DEFAULT_OUT_DIR = os.path.join(ROOT, "plugin_store")

# 打包时排除的目录名 / 文件名 / 后缀
SKIP_DIRS = frozenset({"__pycache__", ".git", ".idea", ".vscode",
                       ".pytest_cache", ".mypy_cache"})
SKIP_FILES = frozenset({".DS_Store", "Thumbs.db", "desktop.ini"})
SKIP_SUFFIXES = (".pyc", ".pyo", ".fpplug", ".zip", ".log")


def collect_files(plugin_dir: str) -> list:
    """收集要打进包里的文件（返回相对路径列表，按路径排序保证可复现）。

    排除：``__pycache__`` 等缓存目录、``*.pyc`` / ``*.log`` / 已有包文件、
    以及以 ``.`` 开头的隐藏文件。目录条目本身不入包（空目录会被丢弃）。
    """
    out = []
    for dirpath, dirnames, filenames in os.walk(plugin_dir):
        dirnames[:] = sorted(d for d in dirnames
                             if d not in SKIP_DIRS and not d.startswith("."))
        for name in sorted(filenames):
            if name in SKIP_FILES or name.startswith("."):
                continue
            if name.lower().endswith(SKIP_SUFFIXES):
                continue
            full = os.path.join(dirpath, name)
            out.append(os.path.relpath(full, plugin_dir).replace("\\", "/"))
    return sorted(out)


def check_plugin(plugin_dir: str) -> tuple:
    """打包前的全量校验。

    返回 ``(manifest | None, errors: list[str])``：
      - manifest 合法 → 归一化后的 dict；否则 None
      - errors 为空 = 可以打包；否则每条是一句人类可读的原因

    校验项（全部复用契约层实现，规则不重复）：
      manifest 可读 / 可解析 → 结构合法 → 依赖白名单 → 动作 id 唯一
      → 热键格式 → entry 文件存在 → 至少有一个文件可打包
    """
    errors = []

    manifest_path = os.path.join(plugin_dir, MANIFEST_NAME)
    if not os.path.isfile(manifest_path):
        return None, [f"插件目录里没有 {MANIFEST_NAME}：{plugin_dir}"]
    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except UnicodeDecodeError as exc:
        return None, [f"{MANIFEST_NAME} 不是 UTF-8 编码（{exc}）——"
                      f"注意别用记事本存成 GBK 或带 BOM"]
    except json.JSONDecodeError as exc:
        return None, [f"{MANIFEST_NAME} 不是合法 JSON：{exc}"]
    except OSError as exc:
        return None, [f"{MANIFEST_NAME} 读取失败：{exc}"]

    manifest, err = validate_manifest(raw)
    if manifest is None:
        return None, [f"manifest 校验失败：{err}"]

    # ---- 依赖白名单（plugins 只允许 PyQt6 + 标准库）----
    for req in manifest.get("requires", []):
        if not is_allowed_requirement(req):
            errors.append(
                f"依赖不在白名单（插件只能依赖 PyQt6 + Python 标准库）："
                f"{req}——联网请改用 capabilities=[\"network\"] + "
                f"ctx.http_post_json_async()")

    # ---- 动作 id 唯一 + 热键格式 ----
    seen_ids = set()
    for i, act in enumerate(manifest.get("actions", [])):
        aid = act.get("id", "")
        if aid in seen_ids:
            errors.append(f"actions[{i}].id 在同一插件内重复：{aid}")
        seen_ids.add(aid)
        hk = act.get("hotkey")
        if hk and not is_valid_hotkey(hk):
            errors.append(
                f"actions[{i}] 的热键格式非法：{hk!r}"
                f"（须形如 Ctrl+Alt+S：至少一个修饰键 + 一个主键）")

    # ---- 入口文件必须真的在目录里 ----
    entry = manifest["entry"]
    entry_path = os.path.join(plugin_dir, *entry.replace("\\", "/").split("/"))
    if not os.path.isfile(entry_path):
        errors.append(f"entry 指向的文件不存在：{entry}"
                      f"（{MANIFEST_NAME} 里写的是 {entry!r}）")

    # ---- 至少要有东西可打（entry + manifest 之外允许只有这两个）----
    if not collect_files(plugin_dir):
        errors.append("插件目录里没有可打包的文件")

    return manifest, errors


def _verify_package(pkg_path: str, plugin_id: str, entry: str) -> str:
    """自校验：重新打开刚写的包，确认结构正确。返回错误信息（"" = 通过）"""
    try:
        with zipfile.ZipFile(pkg_path) as zf:
            names = zf.namelist()
            member = f"{plugin_id}/{MANIFEST_NAME}"
            if member not in names:
                return f"自校验失败：包内缺少 {member}"
            data = json.loads(zf.read(member).decode("utf-8"))
            if data.get("id") != plugin_id:
                return (f"自校验失败：包内 manifest 的 id 是 "
                        f"{data.get('id')!r}，不是 {plugin_id!r}")
            entry_member = f"{plugin_id}/{entry.replace(chr(92), '/')}"
            if entry_member not in names:
                return f"自校验失败：包内缺少入口文件 {entry_member}"
    except (zipfile.BadZipFile, KeyError, ValueError, OSError) as exc:
        return f"自校验失败：{exc!r}"
    return ""


def pack_plugin(plugin_dir: str, out_dir: str = DEFAULT_OUT_DIR,
                force: bool = False, check_only: bool = False) -> tuple:
    """打包一个插件目录。

    返回 ``(ok: bool, message: str, info: dict)``：
      - info 始终含 ``errors``（校验问题列表）与 ``manifest``（解析成功时）
      - 成功打包时 info 另含 ``package``（输出路径）/ ``files``（文件数）
        / ``size``（字节）/ ``check_only``（True = 只校验未打包）
    """
    plugin_dir = os.path.abspath(plugin_dir)
    info = {"errors": [], "manifest": None}

    if not os.path.isdir(plugin_dir):
        return False, f"插件目录不存在：{plugin_dir}", info

    manifest, errors = check_plugin(plugin_dir)
    info["errors"] = list(errors)
    info["manifest"] = manifest
    if errors:
        return False, "校验未通过：\n  - " + "\n  - ".join(errors), info
    if manifest is None:                      # 理论上到不了这里，防御性
        return False, "校验未通过：manifest 无法解析", info

    plugin_id = manifest["id"]
    info["plugin_id"] = plugin_id
    info["check_only"] = bool(check_only)
    if check_only:
        return True, f"校验通过（未打包）：{plugin_id} v{manifest['version']}", info

    files = collect_files(plugin_dir)
    os.makedirs(out_dir, exist_ok=True)
    pkg_path = os.path.join(out_dir, f"{plugin_id}{PLUGIN_PACKAGE_EXT}")
    if os.path.exists(pkg_path) and not force:
        return (False,
                f"目标包已存在：{pkg_path}\n"
                f"  加 --force 覆盖，或先把旧包移走", info)

    try:
        with zipfile.ZipFile(pkg_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for rel in files:
                # 套一层 <插件id>/ 前缀：与内置包结构一致，
                # 解压到 plugins/<id>/ 时天然隔离
                zf.write(os.path.join(plugin_dir, *rel.split("/")),
                         arcname=f"{plugin_id}/{rel}")
    except OSError as exc:
        return False, f"写包失败：{pkg_path}（{exc}）", info

    problem = _verify_package(pkg_path, plugin_id, manifest["entry"])
    if problem:
        # 自校验不过 = 包不可用，删掉避免用户拿去装
        try:
            os.remove(pkg_path)
        except OSError:
            pass
        return False, problem, info

    info.update(package=pkg_path, files=len(files),
                size=os.path.getsize(pkg_path))
    return (True,
            f"已打包：{pkg_path}\n"
            f"  插件：{manifest['name']} v{manifest['version']}（{plugin_id}）\n"
            f"  文件：{len(files)} 个，{info['size']} 字节", info)


def main(argv=None) -> int:
    """命令行入口。返回退出码：0 成功 / 1 失败 / 2 用法错误"""
    parser = argparse.ArgumentParser(
        prog="pack_plugin.py",
        description="把插件文件夹打成 .fpplug 插件包（放进 plugin_store/ 即可安装）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例：\n"
               "  python tools/pack_plugin.py plugins/ai-assistant\n"
               "  python tools/pack_plugin.py plugins/ai-assistant --force\n"
               "  python tools/pack_plugin.py plugins/ai-assistant --check-only\n")
    parser.add_argument("plugin_dir", help="插件目录（内含 manifest.json）")
    parser.add_argument("-o", "--out", default=DEFAULT_OUT_DIR,
                        help=f"输出目录（默认 {DEFAULT_OUT_DIR}）")
    parser.add_argument("--force", action="store_true",
                        help="覆盖已存在的同名包")
    parser.add_argument("--check-only", action="store_true",
                        help="只做校验，不生成包（CI / 提交前自检）")
    args = parser.parse_args(argv)

    ok, message, info = pack_plugin(args.plugin_dir, args.out,
                                    force=args.force,
                                    check_only=args.check_only)
    print(message)
    if ok and not args.check_only:
        print("  下一步：启动 FloatPulse → 插件中心 → 🏪 插件商店 → 安装")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
