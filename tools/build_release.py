# -*- coding: utf-8 -*-
"""组装发布包：主程序 zip + 插件附件清单（纯标准库，无 GUI 环境可跑）。

用法（在项目根执行）
====================================================================
    python -m PyInstaller --noconfirm --clean --distpath dist2 --workpath build2 FloatPulse.spec
    python tools/build_release.py

其他参数：
    --dry-run            只体检不写文件（打完包先跑一次看清单）
    --out <目录>         输出目录，默认「宣传页」
    --dist <目录>        产物目录，默认 dist2
    --with-knowledge     连 float_data/知识库.docx 一起打进包（默认**不打**）
    --no-plugin-assets   不复制插件附件

为什么要有这个脚本
====================================================================
手工压缩会漏三件事，且三件都是「发出去才发现」：

  1. ★**个人数据夹带**——产物目录在开发机上跑过一次之后，会留下
     `data/*.json`（碎片 / 笔记 / 任务 / 配置）、`app.log`、
     `temp_assets/`（剪贴板截图、被拖进来的文档），以及
     `float_data/知识库.docx`（个人知识库全文）。
     这些东西一旦随 Release 发出去就收不回来。
  2. **插件被预装**——用户可能只想用一两个插件，不该被迫接下全部；
     插件走独立附件，用户按需下载到 `plugin_store/`。
  3. **用户找不到 plugin_store/**——目录不存在时，下载了 .fpplug 也不知道放哪。
     故包内补齐 `plugin_store/` 与 `plugins/` 两个空目录 + 一份安装说明。

因此本脚本采用**白名单式收集**（只认 exe 与 `_internal/`），并对最终 zip 做
一轮事后校验：发现 `.fpplug`、`plugins/<子目录>`、`data/`、`temp_assets/`、
`*.log` 就**删掉输出并报错**，绝不留下一个坏包。

退出码：0 = 成功 / 1 = 体检或校验失败 / 2 = 用法错误（可直接进 CI）。
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

APP_NAME = "FloatPulse"
EXE_NAME = "FloatPulse.exe"
VERSION_FILE = os.path.join(ROOT, "v4", "src", "app_version.py")
GUIDE_FILE = os.path.join(ROOT, "shared", "插件安装说明.txt")
PLUGIN_STORE_DIR = os.path.join(ROOT, "plugin_store")
ISS_FILE = os.path.join(ROOT, "installer", "FloatPulse.iss")

# ISCC.exe（Inno Setup 6 命令行编译器）的常见安装位置，按序探测
ISCC_CANDIDATES = (
    r"D:\INNO setup\Inno Setup 6\ISCC.exe",
    r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    r"C:\Program Files\Inno Setup 6\ISCC.exe",
)

DIST_DEFAULT = "dist2"
OUT_DEFAULT = "宣传页"

# 产物目录顶层允许进包的名字（白名单；其余一律不进）
ALLOW_TOP = (EXE_NAME, "_internal", "float_data")

# 顶层就排除（即便在白名单目录内也再查一次）
DENY_DIR_NAMES = {"data", "temp_assets", "__pycache__"}
DENY_SUFFIXES = (".log", ".pyc")

# float_data/ 内只允许这些文件进包（其余是运行数据）
FLOAT_DATA_ALLOW = {"知识库.docx"}


def read_app_version() -> str:
    """从 app_version.py 里取 APP_VERSION（正则读，不 import，避免 sys.path 折腾）"""
    with open(VERSION_FILE, "r", encoding="utf-8") as f:
        text = f.read()
    m = re.search(r'^APP_VERSION\s*=\s*["\']([^"\']+)["\']', text, re.M)
    if not m:
        raise SystemExit(f"读不到 APP_VERSION：{VERSION_FILE}")
    return m.group(1)


def _is_denied(rel_parts, name: str) -> bool:
    if any(p in DENY_DIR_NAMES for p in rel_parts):
        return True
    if name.lower().endswith(DENY_SUFFIXES):
        return True
    return False


def resolve_app_root(dist_dir: str):
    """定位真正含 exe 的目录。

    PyInstaller onedir 产物是 ``<distpath>/FloatPulse/``（内层再放 exe 与
    ``_internal/``），所以 ``--dist dist2`` 与 ``--dist dist2/FloatPulse``
    两种写法都要能用；找不到 exe 返回 None（调用方报错）。
    """
    if os.path.isfile(os.path.join(dist_dir, EXE_NAME)):
        return dist_dir
    inner = os.path.join(dist_dir, APP_NAME)
    if os.path.isfile(os.path.join(inner, EXE_NAME)):
        return inner
    return None


def collect(dist_dir: str, with_knowledge: bool):
    """遍历产物目录 → (进包文件清单, 被排除清单)。

    返回 ``[(arcname, abs_path), ...]``，arcname 以 ``FloatPulse/`` 开头。
    """
    keep, skipped = [], []
    for top in sorted(os.listdir(dist_dir)):
        top_path = os.path.join(dist_dir, top)
        if top not in ALLOW_TOP:
            why = ("旧布局遗留（新版本应在 float_data/ 内）"
                   if top.endswith(".docx") else "顶层不在白名单")
            skipped.append((top, why))
            continue
        if os.path.isfile(top_path):
            keep.append((f"{APP_NAME}/{top}", top_path))
            continue
        # float_data/ 只挑知识库
        if top == "float_data":
            for dirpath, dirnames, filenames in os.walk(top_path):
                dirnames[:] = [d for d in dirnames if d not in DENY_DIR_NAMES]
                for fn in filenames:
                    abs_p = os.path.join(dirpath, fn)
                    rel = os.path.relpath(abs_p, top_path).replace("\\", "/")
                    if _is_denied(rel.split("/"), fn):
                        skipped.append((f"float_data/{rel}", "运行数据"))
                        continue
                    if rel not in FLOAT_DATA_ALLOW:
                        skipped.append((f"float_data/{rel}", "运行数据"))
                        continue
                    if not with_knowledge:
                        skipped.append((f"float_data/{rel}", "知识库未启用（--with-knowledge 才会打进包）"))
                        continue
                    keep.append((f"{APP_NAME}/float_data/{rel}", abs_p))
            continue
        # _internal/ 全量收，只排 __pycache__ 与 .pyc
        for dirpath, dirnames, filenames in os.walk(top_path):
            dirnames[:] = [d for d in dirnames if d not in DENY_DIR_NAMES]
            for fn in filenames:
                abs_p = os.path.join(dirpath, fn)
                rel = os.path.relpath(abs_p, dist_dir).replace("\\", "/")
                if _is_denied(rel.split("/"), fn):
                    skipped.append((rel, "缓存/日志"))
                    continue
                keep.append((f"{APP_NAME}/{rel}", abs_p))
    return keep, skipped


def read_plugin_meta(fpplug_path: str) -> dict:
    """从 .fpplug 里读 manifest，拿不到就返回空 dict（不抛）"""
    try:
        with zipfile.ZipFile(fpplug_path) as zf:
            for name in zf.namelist():
                if name.endswith("manifest.json"):
                    data = json.loads(zf.read(name).decode("utf-8"))
                    data["_pkg_top"] = name.split("/")[0]
                    return data
    except Exception:
        pass
    return {}


def read_plugin_summary(fpplug_path: str, pkg_top: str) -> str:
    """manifest 没写 description 时，退回包内「使用说明.md」的首段实质文字。

    插件包内普遍带一份使用说明（宿主卡片也会显示它的摘要），拿它当兜底
    比在清单里留一格空白要体面；都没有则返回空串。
    """
    try:
        with zipfile.ZipFile(fpplug_path) as zf:
            target = f"{pkg_top}/使用说明.md"
            if target not in zf.namelist():
                return ""
            text = zf.read(target).decode("utf-8", "ignore")
    except Exception:
        return ""
    for raw in text.splitlines():
        line = raw.strip().lstrip("#").strip()
        if not line or line.startswith(">") or line.startswith("|"):
            continue
        if line.startswith("-") or line.startswith("*"):
            line = line.lstrip("-* ").strip()
        if len(line) >= 8:
            return line
    return ""


def plugin_assets(out_dir: str, copy: bool) -> list:
    """收集插件附件（plugin_store/*.fpplug），可选复制到 <out>/plugin-assets/"""
    if not os.path.isdir(PLUGIN_STORE_DIR):
        return []
    items = []
    dest_dir = os.path.join(out_dir, "plugin-assets")
    if copy:
        os.makedirs(dest_dir, exist_ok=True)
    for fn in sorted(os.listdir(PLUGIN_STORE_DIR)):
        if not fn.lower().endswith(".fpplug"):
            continue
        src = os.path.join(PLUGIN_STORE_DIR, fn)
        meta = read_plugin_meta(src)
        caps = meta.get("capabilities") or []
        pkg_top = meta.get("_pkg_top") or fn[:-7]
        desc = (meta.get("description") or "").strip()
        if not desc:
            desc = read_plugin_summary(src, pkg_top)
        items.append({
            "file": fn,
            "path": src,
            "size": os.path.getsize(src),
            "id": meta.get("id") or pkg_top,
            "name": meta.get("name") or "(无 manifest)",
            "version": meta.get("version") or "?",
            "description": desc,
            "capabilities": list(caps),
            "page": (meta.get("page") or {}).get("title", ""),
            "hotkeys": [a.get("hotkey") for a in (meta.get("actions") or []) if a.get("hotkey")],
        })
        if copy:
            shutil.copy2(src, os.path.join(dest_dir, fn))
    return items


def write_plugin_manifest_md(items: list, out_dir: str, version: str, copy: bool) -> str:
    """生成一份插件清单 Markdown（贴进 Release 说明用）"""
    lines = [
        f"# FloatPulse v{version} 插件包清单",
        "",
        "插件**不随主程序打包**，按需下载。下载后把 `.fpplug` 放进程序目录的 "
        "`plugin_store\\` 文件夹，再到「插件中心 → 🏪 插件商店」点安装。",
        "",
        "| 插件包 | 名称 | 版本 | 体积 | 页面 / 热键 | 能力 | 说明 |",
        "|---|---|---|---|---|---|---|",
    ]
    cap_text = {"network": "🌐 网络", "write": "✍ 写入", "manage": "🛠 改删", "ai": "🧠 AI 总配置"}
    for it in items:
        entry = it["page"] or "—"
        if it["hotkeys"]:
            entry += " / " + " ".join(f"`{h}`" for h in it["hotkeys"])
        caps = " ".join(cap_text.get(c, c) for c in it["capabilities"]) or "只读"
        desc = it["description"]
        if len(desc) > 90:
            desc = desc[:88] + "……"
        lines.append(
            f"| `{it['file']}` | {it['name']} | {it['version']} | "
            f"{it['size'] / 1024:.1f} KB | {entry} | {caps} | {desc} |"
        )
    lines += [
        "",
        "> 程序本体不含插件；核心功能（悬浮球 / 碎片 / 任务 / 笔记 / 知识库 / 素材 / "
        "截图钉屏 / 软件网址导航）不受插件安装与否影响。",
        "",
    ]
    text = "\n".join(lines)
    if copy:
        os.makedirs(out_dir, exist_ok=True)
        dst = os.path.join(out_dir, "plugin-assets", "插件清单.md")
        with open(dst, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
    return text


def build_zip(zip_path: str, keep: list, guide_text: str, app_name: str) -> int:
    """写 zip：进包文件 + 两个空目录 + 安装说明。返回写入的文件条目数。"""
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        guide_arc = f"{app_name}/插件安装说明.txt"
        # 说明写成 UTF-8 BOM：Windows 记事本双击打开不乱码
        zf.writestr(guide_arc, b"\xef\xbb\xbf" + guide_text.encode("utf-8"))
        for arcname, abs_path in keep:
            zf.write(abs_path, arcname)
        # 两个空目录：带 MS-DOS 目录位，否则资源管理器不认它是文件夹
        for d in ("plugins", "plugin_store"):
            info = zipfile.ZipInfo(f"{app_name}/{d}/")
            info.external_attr = (0o40775 << 16) | 0x10
            zf.writestr(info, b"")
    return len(keep) + 3


def verify_zip(zip_path: str) -> list:
    """事后校验：返回问题列表（空 = 通过）"""
    problems = []
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
    if f"{APP_NAME}/{EXE_NAME}" not in names:
        problems.append(f"缺少 {EXE_NAME}")
    for d in ("plugins/", "plugin_store/"):
        if f"{APP_NAME}/{d}" not in names:
            problems.append(f"缺少空目录 {APP_NAME}/{d}")
    for n in names:
        low = n.lower()
        if low.endswith(".fpplug"):
            problems.append(f"包内出现 .fpplug（插件应走独立附件）：{n}")
        if low.startswith(f"{APP_NAME.lower()}/data/") or "/temp_assets/" in low or low.startswith(f"{APP_NAME.lower()}/temp_assets/"):
            problems.append(f"包内出现运行数据目录：{n}")
        if low.endswith(".log"):
            problems.append(f"包内出现日志：{n}")
        if low.startswith(f"{APP_NAME.lower()}/plugins/") and not low.endswith("plugins/"):
            problems.append(f"包内预装了插件：{n}")
    return problems


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def locate_iscc(explicit: str = "") -> str:
    """定位 ISCC.exe：显式指定 > 常见安装位置；找不到返回空串"""
    if explicit:
        return explicit if os.path.isfile(explicit) else ""
    for cand in ISCC_CANDIDATES:
        if os.path.isfile(cand):
            return cand
    return ""


def build_installer(iscc: str, version: str, out_dir: str) -> int:
    """调 Inno Setup 编译 setup.exe（版本经 /D 注入 iss，脚本内不写死）"""
    cmd = [iscc, f"/DAPP_VERSION={version}", f"/O{out_dir}", ISS_FILE]
    proc = subprocess.run(cmd, capture_output=True)
    # ISCC 控制台输出是 GBK（中文 Windows），按 GBK 解码，坏字节不炸
    log = (proc.stdout or b"").decode("gbk", "replace")
    tail = "\n".join(log.strip().splitlines()[-6:])
    print(tail)
    if proc.returncode != 0:
        print(f"[X] Inno 编译失败（rc={proc.returncode}），脚本：{ISS_FILE}")
        return 1
    exe = os.path.join(out_dir, f"{APP_NAME}-v{version}-setup.exe")
    if not os.path.isfile(exe):
        print(f"[X] 编译声称成功但找不到产物：{exe}")
        return 1
    print(f"[OK] {exe}")
    print(f"     体积 {human_mb(os.path.getsize(exe))} / SHA-256 {sha256_of(exe)}")
    return 0


def human_mb(n: int) -> str:
    return f"{n / 1048576:.1f} MB"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="组装 FloatPulse 发布包")
    ap.add_argument("--dist", default=DIST_DEFAULT, help="PyInstaller 产物目录")
    ap.add_argument("--out", default=OUT_DEFAULT, help="输出目录")
    ap.add_argument("--version", default=None, help="版本号（默认读 app_version.py）")
    ap.add_argument("--dry-run", action="store_true", help="只体检，不写任何文件")
    ap.add_argument("--with-knowledge", action="store_true",
                    help="连 float_data/知识库.docx 一起打进包（默认不打）")
    ap.add_argument("--no-plugin-assets", action="store_true", help="不复制插件附件")
    ap.add_argument("--installer", action="store_true",
                    help="附带编译 Inno Setup 安装包（FloatPulse-v<版本>-setup.exe）")
    ap.add_argument("--iscc", default="", help="ISCC.exe 路径（默认按常见位置探测）")
    args = ap.parse_args(argv)

    version = args.version or read_app_version()
    dist_dir = args.dist if os.path.isabs(args.dist) else os.path.join(ROOT, args.dist)
    out_dir = args.out if os.path.isabs(args.out) else os.path.join(ROOT, args.out)

    if not os.path.isdir(dist_dir):
        print(f"[X] 产物目录不存在：{dist_dir}")
        print("    先跑：python -m PyInstaller --noconfirm --clean "
              f"--distpath {args.dist} --workpath build2 FloatPulse.spec")
        return 1
    app_root = resolve_app_root(dist_dir)
    if app_root is None:
        print(f"[X] 找不到 {EXE_NAME}：{dist_dir}（也没有 {APP_NAME}/ 子目录）")
        return 1
    if app_root != dist_dir:
        print(f"[i] 产物根目录下钻一层：{app_root}")
    if not os.path.isfile(GUIDE_FILE):
        print(f"[X] 缺少安装说明模板：{GUIDE_FILE}")
        return 1

    with open(GUIDE_FILE, "r", encoding="utf-8-sig") as f:
        guide_text = f.read()

    keep, skipped = collect(app_root, args.with_knowledge)
    keep_bytes = sum(os.path.getsize(p) for _, p in keep)

    print("=" * 68)
    print(f"版本 v{version}   产物根目录 {app_root}")
    print(f"进包文件 {len(keep)} 个 / 未压缩 {human_mb(keep_bytes)}")
    print("-" * 68)
    print(f"排除 {len(skipped)} 项（前 12 条）：")
    for name, why in skipped[:12]:
        print(f"    - {name}   [{why}]")
    if len(skipped) > 12:
        print(f"    … 另有 {len(skipped) - 12} 项")

    plugins = plugin_assets(out_dir, copy=not args.dry_run and not args.no_plugin_assets)
    print("-" * 68)
    print(f"插件附件 {len(plugins)} 个（走独立下载，不进主包）：")
    for it in plugins:
        caps = ",".join(it["capabilities"]) or "只读"
        print(f"    - {it['file']:26s} {it['size'] / 1024:6.1f} KB  "
              f"{it['name']} v{it['version']}  [{caps}]")
    md = write_plugin_manifest_md(plugins, out_dir, version,
                                 copy=not args.dry_run and not args.no_plugin_assets)
    if args.dry_run:
        print("=" * 68)
        print("[DRY-RUN] 未写任何文件。清单预览：")
        print(md)
        return 0

    zip_name = f"{APP_NAME}-v{version}-win64.zip"
    zip_path = os.path.join(out_dir, zip_name)
    os.makedirs(out_dir, exist_ok=True)
    if os.path.exists(zip_path):
        os.remove(zip_path)

    n_entries = build_zip(zip_path, keep, guide_text, APP_NAME)
    problems = verify_zip(zip_path)
    if problems:
        os.remove(zip_path)
        print("=" * 68)
        print("[X] 校验未通过，已删除输出文件（不留坏包）：")
        for p in problems:
            print(f"    - {p}")
        return 1

    zsize = os.path.getsize(zip_path)
    digest = sha256_of(zip_path)
    print("=" * 68)
    print(f"[OK] {zip_path}")
    print(f"     条目 {n_entries} 个 / 压缩后 {human_mb(zsize)} / 解压后 {human_mb(keep_bytes)}")
    print(f"     SHA-256 {digest}")
    print(f"     插件附件目录 {os.path.join(out_dir, 'plugin-assets')}"
          f"（{len(plugins)} 个 .fpplug + 插件清单.md）")

    if args.installer:
        iscc = locate_iscc(args.iscc)
        if not iscc:
            print("[X] 找不到 ISCC.exe（Inno Setup 6），用 --iscc 指定路径")
            return 1
        print("-" * 68)
        if build_installer(iscc, version, out_dir) != 0:
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
