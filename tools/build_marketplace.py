# -*- coding: utf-8 -*-
"""生成插件市场索引 marketplace/index.json（纯 stdlib，无 GUI 可跑）。

用法（项目根执行）：
    python tools/build_marketplace.py            # 扫 plugin_store/ 写索引
    python tools/build_marketplace.py --check    # 只校验不写（CI 可用）

索引里每个条目 = 商店目录里一个 .fpplug 的 manifest 摘要 + 下载所需
三要素（file / size / sha256）。应用端「在线市场」拉这份索引列出可装
清单，再经 latest Release 附件（release.yml 自动上传的 plugin-assets）
按文件名解析 asset id 下载——所以**索引只需在插件增改时重新生成提交**，
发版不用动它。

.manifest 缺 description 时退回包内「使用说明.md」首段（与
build_release.py 的插件清单同款兜底）。
"""

import argparse
import hashlib
import io
import json
import os
import sys
import zipfile
from datetime import date

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TOOLS_DIR)
STORE_DIR = os.path.join(ROOT, "plugin_store")
OUT_PATH = os.path.join(ROOT, "marketplace", "index.json")
SCHEMA = 1


def read_manifest(fpplug_path: str) -> dict:
    """读 zip 内 manifest.json；读不到返回空 dict（不抛）"""
    try:
        with zipfile.ZipFile(fpplug_path) as zf:
            for name in zf.namelist():
                if name.endswith("manifest.json"):
                    return json.loads(zf.read(name).decode("utf-8"))
    except Exception:                          # noqa: BLE001
        pass
    return {}


def read_summary(fpplug_path: str, pkg_top: str) -> str:
    """manifest 缺 description 时退回「使用说明.md」首段实质文字"""
    try:
        with zipfile.ZipFile(fpplug_path) as zf:
            target = f"{pkg_top}/使用说明.md"
            if target not in zf.namelist():
                return ""
            text = zf.read(target).decode("utf-8", "ignore")
    except Exception:                          # noqa: BLE001
        return ""
    for raw in text.splitlines():
        line = raw.strip().lstrip("#").strip()
        if not line or line.startswith((">", "|")):
            continue
        if line.startswith(("-", "*")):
            line = line.lstrip("-* ").strip()
        if len(line) >= 8:
            return line
    return ""


def build_items(store_dir: str) -> tuple:
    """扫描商店目录 → (条目列表, 问题列表)"""
    items, problems = [], []
    if not os.path.isdir(store_dir):
        return [], [f"商店目录不存在：{store_dir}"]
    for fn in sorted(os.listdir(store_dir)):
        if not fn.lower().endswith(".fpplug"):
            continue
        src = os.path.join(store_dir, fn)
        manifest = read_manifest(src)
        if not manifest:
            problems.append(f"{fn}: 读不到 manifest.json，已跳过")
            continue
        pid = manifest.get("id")
        if not pid:
            problems.append(f"{fn}: manifest 缺 id，已跳过")
            continue
        if fn != f"{pid}.fpplug":
            problems.append(f"{fn}: 文件名与 id {pid!r} 不一致（市场要求 <id>.fpplug）")
            continue
        with open(src, "rb") as f:
            sha = hashlib.sha256(f.read()).hexdigest()
        desc = (manifest.get("description") or "").strip()
        if not desc:
            desc = read_summary(src, fn[:-7])
        actions = manifest.get("actions") or []
        items.append({
            "id": pid,
            "name": manifest.get("name") or pid,
            "version": manifest.get("version") or "?",
            "file": fn,
            "size": os.path.getsize(src),
            "sha256": sha,
            "description": desc,
            "capabilities": list(manifest.get("capabilities") or []),
            "page": (manifest.get("page") or {}).get("title", ""),
            "hotkeys": [a.get("hotkey") for a in actions if a.get("hotkey")],
        })
    return items, problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="生成插件市场索引")
    ap.add_argument("--check", action="store_true",
                    help="只校验索引与商店一致，不写文件（退出码可进 CI）")
    args = ap.parse_args(argv)

    items, problems = build_items(STORE_DIR)
    for p in problems:
        print(f"[!] {p}")

    index = {
        "schema": SCHEMA,
        "updated": date.today().isoformat(),
        "plugins": items,
    }
    text = json.dumps(index, ensure_ascii=False, indent=1) + "\n"

    if args.check:
        if not os.path.isfile(OUT_PATH):
            print(f"[X] 索引不存在：{OUT_PATH}")
            return 1
        with io.open(OUT_PATH, "r", encoding="utf-8") as f:
            current = json.load(f)
        want = json.loads(text)
        if current.get("plugins") == want["plugins"]:
            print(f"[OK] 索引与商店一致（{len(items)} 个插件）")
            return 0
        print("[X] 索引与商店不一致——插件有增改，请重新跑 build_marketplace.py")
        return 1

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with io.open(OUT_PATH, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    print(f"[OK] 写出 {OUT_PATH}（{len(items)} 个插件）")
    for it in items:
        print(f"    - {it['file']:26s} v{it['version']:6s} "
              f"{it['size'] / 1024:6.1f} KB  {it['name']}")
    if problems:
        print(f"[!] 另有 {len(problems)} 条问题未入索引（见上）")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
