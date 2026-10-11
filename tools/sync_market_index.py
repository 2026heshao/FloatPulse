# -*- coding: utf-8 -*-
"""
====================================================================
市场索引再生器  -  tools/sync_market_index.py
====================================================================
从插件源码包（plugins/*/）自动再生 ``marketplace/index.json``——
索引里的 sha256 / size / version 不再靠手抄。

为什么必须有这个工具（2026-10-10 根因修复）：
  索引此前是手工钉死的（10-02 维护后插件源码又演进了 4 个版本），
  而 Release 附件每次发版从源码现打——附件哈希 ≠ 索引哈希，在线市场
  下载「AI 助手」等全部插件被完整性校验丢弃（「sha256 不符，已丢弃，
  请稍后重试」）。手工同步哈希这件事已经被证明守不住，收口成自动化：
  打包 → 算哈希 → 写索引，一条命令；CI 再加脱同步红灯。

用法::

    python tools/sync_market_index.py             # 再生索引（写入文件）
    python tools/sync_market_index.py --check     # 只校验不写（CI 用）

配套约束（pack_plugin 侧已收口）：
  打包是**确定性**的（成员时间戳固定 1980-01-01 + compresslevel=9），
  同一份插件源码任何一次重打包字节相同——索引哈希才能长期成立。

退出码：0 = 成功/一致，1 = 失败/脱同步，2 = 用法错误
====================================================================
"""

import argparse
import json
import os
import sys
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
V4_DIR = os.path.join(ROOT, "v4")
TOOLS_DIR = os.path.join(ROOT, "tools")
if V4_DIR not in sys.path:
    sys.path.insert(0, V4_DIR)

from pack_plugin import pack_plugin                # noqa: E402
from src.plugin_loader import MANIFEST_NAME        # noqa: E402

INDEX_PATH = os.path.join(ROOT, "marketplace", "index.json")
PLUGINS_DIR = os.path.join(ROOT, "plugins")
STORE_DIR = os.path.join(ROOT, "plugin_store")


def _load_manifest(plugin_dir: str):
    """读插件 manifest（index 字段的唯一来源）；坏目录返回 None。"""
    path = os.path.join(plugin_dir, MANIFEST_NAME)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (ValueError, OSError):
        return None


def build_entries() -> tuple:
    """逐个插件打包 → 生成索引条目。

    返回 ``(entries, problems)``：entries 按插件 id 排序；problems 是
    跳过原因（坏 manifest / 打包失败等，不打断其余插件）。
    打包走 pack_plugin（--force 覆盖旧包），产物同时服务本地商店与
    索引哈希——保证「索引钉的哈希 == plugin_store 里的包 == Release 附件」
    三者同源。
    """
    entries, problems = [], []
    if not os.path.isdir(PLUGINS_DIR):
        return [], [f"插件目录不存在：{PLUGINS_DIR}"]
    for name in sorted(os.listdir(PLUGINS_DIR)):
        plugin_dir = os.path.join(PLUGINS_DIR, name)
        manifest = _load_manifest(plugin_dir)
        if not isinstance(manifest, dict):
            problems.append(f"跳过 {name}：{MANIFEST_NAME} 缺失或不可解析")
            continue
        ok, message, info = pack_plugin(plugin_dir, STORE_DIR, force=True)
        if not ok or not info.get("package"):
            problems.append(f"跳过 {manifest.get('id', name)}：打包失败——{message}")
            continue
        with open(info["package"], "rb") as f:
            import hashlib
            sha = hashlib.sha256(f.read()).hexdigest()
        hotkeys = [a.get("hotkey") for a in manifest.get("actions", [])
                   if isinstance(a, dict) and a.get("hotkey")]
        page = manifest.get("page")
        entries.append({
            "id": manifest["id"],
            "name": str(manifest.get("name") or manifest["id"]),
            "version": str(manifest.get("version") or "?"),
            "file": f"{manifest['id']}.fpplug",
            "size": int(info["size"]),
            "sha256": sha,
            "description": str(manifest.get("description") or ""),
            "capabilities": list(manifest.get("capabilities") or []),
            "page": str(page.get("title")) if isinstance(page, dict) else "",
            "hotkeys": hotkeys,
        })
    entries.sort(key=lambda e: e["id"])
    return entries, problems


def render_index(entries) -> dict:
    """再生完整 index 结构（schema 沿用 1，updated 取今天）。"""
    return {
        "schema": 1,
        "updated": date.today().isoformat(),
        "plugins": entries,
    }


def load_committed_index():
    """读现有 index.json；不存在/坏 JSON 返回 None。"""
    if not os.path.isfile(INDEX_PATH):
        return None
    try:
        with open(INDEX_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (ValueError, OSError):
        return None


def index_matches(entries, committed) -> tuple:
    """再生条目与已提交索引是否一致（**忽略 updated 日期**——它随再生
    日期漂移，不该让 CI 红在日期上）。返回 ``(ok, 原因列表)``。"""
    if not isinstance(committed, dict):
        return False, ["index.json 缺失或不是对象"]
    old = committed.get("plugins")
    if old != entries:
        reasons = []
        old_by_id = {e.get("id"): e for e in old if isinstance(e, dict)}
        for e in entries:
            o = old_by_id.get(e["id"])
            if o is None:
                reasons.append(f"{e['id']}: 索引缺条目")
                continue
            for key in ("version", "size", "sha256", "file"):
                if o.get(key) != e[key]:
                    reasons.append(
                        f"{e['id']}.{key}: 索引 {o.get(key)!r} ≠ 实际 {e[key]!r}")
        for oid in old_by_id:
            if oid not in {e["id"] for e in entries}:
                reasons.append(f"{oid}: 索引多出的条目（源码已删除？）")
        return False, reasons or ["plugins 数组内容不一致"]
    return True, []


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="sync_market_index.py",
        description="从插件源码再生 marketplace/index.json（哈希/版本/体积全自动）")
    parser.add_argument("--check", action="store_true",
                        help="只校验索引与实际包是否一致，不写文件（CI 用）")
    args = parser.parse_args(argv)

    entries, problems = build_entries()
    for p in problems:
        print(f"  ⚠ {p}")
    if not entries:
        print("没有生成任何索引条目，中止")
        return 1

    if args.check:
        committed = load_committed_index()
        ok, reasons = index_matches(entries, committed)
        if ok:
            print(f"[OK] index.json 与 {len(entries)} 个插件包一致（哈希/版本/体积）")
            return 0
        print("[FAIL] index.json 与实际插件包脱同步：")
        for r in reasons:
            print(f"  - {r}")
        print("修复：python tools/sync_market_index.py && git add marketplace/index.json")
        return 1

    index = render_index(entries)
    os.makedirs(os.path.dirname(INDEX_PATH), exist_ok=True)
    with open(INDEX_PATH, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=1)
        f.write("\n")
    print(f"[OK] 已再生 {INDEX_PATH}（{len(entries)} 个插件，"
          f"updated={index['updated']}）")
    for e in entries:
        print(f"  - {e['id']} v{e['version']}  {e['size']} B  "
              f"{e['sha256'][:12]}…")
    print("  下一步：git add marketplace/index.json plugin_store/ 一并提交")
    return 0


if __name__ == "__main__":
    sys.exit(main())
