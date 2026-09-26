# -*- coding: utf-8 -*-
"""
====================================================================
docx 文档管理模块  -  DocxManager
====================================================================
负责 docx 知识库的读取、可控写入、增量同步。

设计要点：
  1. 读取 docx 段落 → 返回 ParagraphInfo 列表（含 index/text/hash/preview）
  2. 可控写入（保留原段落样式）：
     - 修改段落文本：保留第一个 run 的样式，仅替换文本，清空其他 runs
     - 删除段落：p._element.getparent().remove(p._element)
     - 新增段落：在指定段落后插入（OxmlElement + addnext）
     - 追加段落：doc.add_paragraph()
  3. 增量同步：
     - 启动时算 mtime + sha1 与 docx_meta.json 对比
     - 变化 → 返回 changed=True（UI 弹窗询问用户）
     - 用户确认后调用 reload() 重新加载并重建段落哈希
  4. 段落哈希：sha1(text.strip())[:16]，用于跨修改追踪段落
  5. 备份：保存前自动备份 .bak
  6. 原子写入 meta.json（docx 本身用 python-docx 的 save）

段落过滤规则（沿用 load_knowledge_base）：
  - 去除首尾空格
  - 过滤空段落
  - 过滤字符长度 < 4 的无效段落
====================================================================
"""

import os
import json
import shutil
import hashlib

from docx import Document
from docx.text.paragraph import Paragraph
from docx.oxml.ns import qn


# ====================================================================
# 段落信息数据类
# ====================================================================
class ParagraphInfo:
    """段落信息（内存模型，不持有 docx 对象引用）"""

    def __init__(self, index, text, hash_value, preview):
        self.index = index            # 段落索引（在文档中的位置）
        self.text = text              # 段落文本
        self.hash = hash_value        # sha1(text.strip())[:16]
        self.preview = preview        # 前 30 字预览

    def to_dict(self):
        return {
            "index": self.index,
            "hash": self.hash,
            "preview": self.preview,
        }


# ====================================================================
# docx 管理器
# ====================================================================
class DocxManager:
    """
    docx 文档管理器。

    对外提供：
      - load()                    读取段落，返回 (paragraphs, error_msg)
      - check_external_modification()  检测外部修改
      - reload()                  重新加载并更新 meta
      - get_paragraphs()          获取当前段落列表
      - update_paragraph_text()   修改段落文本
      - delete_paragraph()        删除段落
      - insert_paragraph_after()  在某段后插入
      - append_paragraph()        末尾追加
      - save()                    保存到 docx + 更新 meta
    """

    # 段落最小有效长度（沿用原 load_knowledge_base 规则）
    MIN_PARAGRAPH_LENGTH = 4

    def __init__(self, docx_path: str, meta_path: str):
        self._docx_path = docx_path      # 知识库.docx 路径
        self._meta_path = meta_path      # docx_meta.json 路径
        self._doc = None                 # python-docx Document 对象
        self._paragraphs = []            # ParagraphInfo 列表
        self._raw_paragraphs = []        # python-docx Paragraph 对象列表（过滤后）

    # ---------------- 哈希与指纹 ----------------
    @staticmethod
    def _hash_text(text: str) -> str:
        """段落哈希：sha1(去除首尾空格的文本)[:16]"""
        return hashlib.sha1(text.strip().encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _make_preview(text: str, max_len: int = 30) -> str:
        """生成段落预览"""
        s = text.strip().replace("\n", " ").replace("\r", " ")
        if len(s) > max_len:
            return s[:max_len] + "..."
        return s

    def _file_fingerprint(self):
        """
        计算 docx 文件指纹：mtime + 内容 sha1
        返回 (mtime, sha1)；文件不存在返回 (None, None)
        """
        if not os.path.exists(self._docx_path):
            return None, None
        try:
            mtime = os.path.getmtime(self._docx_path)
            h = hashlib.sha1()
            with open(self._docx_path, "rb") as f:
                for chunk in iter(lambda: f.read(8192), b""):
                    h.update(chunk)
            return mtime, h.hexdigest()
        except (OSError, IOError):
            return None, None

    # ---------------- meta 读写 ----------------
    def _load_meta(self) -> dict:
        """加载 docx_meta.json"""
        if not os.path.exists(self._meta_path):
            return {}
        try:
            with open(self._meta_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return {}
            return data
        except Exception:
            return {}

    def _save_meta(self, mtime, sha1, paragraph_hashes):
        """原子写入 docx_meta.json"""
        data = {
            "last_mtime": mtime,
            "last_sha1": sha1,
            "paragraph_hashes": paragraph_hashes,
        }
        try:
            os.makedirs(os.path.dirname(self._meta_path), exist_ok=True)
            tmp_path = self._meta_path + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
            os.replace(tmp_path, self._meta_path)
        except OSError:
            pass

    def _update_meta(self):
        """根据当前 docx 文件状态更新 meta"""
        mtime, sha1 = self._file_fingerprint()
        paragraph_hashes = [p.to_dict() for p in self._paragraphs]
        self._save_meta(mtime, sha1, paragraph_hashes)

    # ---------------- 读取 ----------------
    def load(self):
        """
        读取 docx 段落，初始化内存模型。

        返回:
            (paragraphs: list[ParagraphInfo], error_msg: str | None)
            - 成功: (段落列表, None)
            - 失败: ([], 错误信息)
        """
        if not os.path.exists(self._docx_path):
            return [], f"找不到文件：\n{self._docx_path}\n\n请确认「知识库.docx」与本程序位于同一文件夹。"

        try:
            self._doc = Document(self._docx_path)
        except Exception as e:
            return [], (
                f"读取文档失败：{e}\n\n"
                "可能原因：\n"
                "  1. 文件不是有效的 .docx 格式（不支持旧版 .doc）；\n"
                "  2. 文件已被损坏或被其他程序占用。"
            )

        self._paragraphs = []
        self._raw_paragraphs = []
        for idx, para in enumerate(self._doc.paragraphs):
            text = para.text.strip()
            if not text:
                continue
            if len(text) < self.MIN_PARAGRAPH_LENGTH:
                continue
            info = ParagraphInfo(
                index=idx,
                text=text,
                hash_value=self._hash_text(text),
                preview=self._make_preview(text),
            )
            self._paragraphs.append(info)
            self._raw_paragraphs.append(para)

        # 首次加载或文件变化时更新 meta
        self._update_meta()
        return list(self._paragraphs), None

    def get_paragraphs(self) -> list:
        """返回当前段落列表副本"""
        return list(self._paragraphs)

    def get_paragraph_text(self, index: int) -> str:
        """按索引获取段落文本，越界返回空串"""
        if 0 <= index < len(self._paragraphs):
            return self._paragraphs[index].text
        return ""

    def get_cards(self) -> list:
        """返回纯文本段落列表（兼容 FloatingBall 的 cards 接口）"""
        return [p.text for p in self._paragraphs]

    # ---------------- 增量同步 ----------------
    def _file_mtime(self):
        """仅获取 docx 的 mtime（轻量，不读文件内容）；文件不存在返回 None"""
        if not os.path.exists(self._docx_path):
            return None
        try:
            return os.path.getmtime(self._docx_path)
        except (OSError, IOError):
            return None

    def check_external_modification(self) -> bool:
        """
        检查 docx 是否被外部修改。
        返回 True 表示检测到外部修改。

        规则（mtime 优先，避免高频调用时整文件 sha1）：
          - 文件不存在 → False
          - 无 meta（首次启动）→ False（不算修改）
          - mtime 与上次记录一致 → False（直接判定未修改，跳过 sha1）
          - mtime 变化 → 再算 sha1 确认内容是否真的变化
        """
        mtime = self._file_mtime()
        if mtime is None:
            return False
        meta = self._load_meta()
        if not meta:
            return False  # 首次无 meta
        last_mtime = meta.get("last_mtime")
        if last_mtime is not None and abs(float(last_mtime) - mtime) < 1e-6:
            return False  # mtime 一致，跳过 sha1
        _, sha1 = self._file_fingerprint()
        if sha1 is None:
            return False
        last_sha1 = meta.get("last_sha1")
        if last_sha1 is None:
            return False
        return last_sha1 != sha1

    def reload(self):
        """
        重新加载 docx 并更新 meta。
        保留段落的"哈希 → 文本"映射用于追踪（虽然索引会变，但哈希可作为辅助标识）。
        """
        return self.load()

    # ---------------- 可控写入 ----------------
    def _get_raw_paragraph(self, index: int):
        """按过滤后索引获取 python-docx Paragraph 对象"""
        if 0 <= index < len(self._raw_paragraphs):
            return self._raw_paragraphs[index]
        return None

    def update_paragraph_text(self, index: int, new_text: str) -> bool:
        """
        修改段落文本（保留第一个 run 的样式）。
        - 保留 para.runs[0] 的格式，仅替换文本
        - 清空其他 runs 的文本
        - 无 runs 时调用 add_run
        修改后需调用 save() 持久化。
        """
        para = self._get_raw_paragraph(index)
        if para is None:
            return False
        new_text = new_text.strip()
        if not new_text or len(new_text) < self.MIN_PARAGRAPH_LENGTH:
            return False

        try:
            if para.runs:
                para.runs[0].text = new_text
                for run in para.runs[1:]:
                    run.text = ""
            else:
                para.add_run(new_text)
            # 同步内存模型
            self._paragraphs[index].text = new_text
            self._paragraphs[index].hash = self._hash_text(new_text)
            self._paragraphs[index].preview = self._make_preview(new_text)
            return True
        except Exception:
            return False

    def delete_paragraph(self, index: int) -> bool:
        """
        删除段落。
        从 docx 中移除段落元素，并同步内存列表。
        删除后索引会重新排列（后续段落 index 减 1）。
        """
        para = self._get_raw_paragraph(index)
        if para is None:
            return False
        try:
            para._element.getparent().remove(para._element)
            # 同步内存模型
            del self._paragraphs[index]
            del self._raw_paragraphs[index]
            # 重建索引
            for i, info in enumerate(self._paragraphs):
                info.index = i
            return True
        except Exception:
            return False

    def insert_paragraph_after(self, index: int, text: str) -> int:
        """
        在指定段落后插入新段落。
        返回新段落的索引；失败返回 -1。
        """
        para = self._get_raw_paragraph(index)
        if para is None:
            return -1
        text = text.strip()
        if not text or len(text) < self.MIN_PARAGRAPH_LENGTH:
            return -1
        try:
            # 创建新段落元素并插入到当前段落之后
            new_p = para._element.makeelement(qn('w:p'), {})
            para._element.addnext(new_p)
            new_para = Paragraph(new_p, para._parent)
            new_para.add_run(text)
            # 同步内存模型：在 index+1 处插入
            insert_pos = index + 1
            new_info = ParagraphInfo(
                index=insert_pos,
                text=text,
                hash_value=self._hash_text(text),
                preview=self._make_preview(text),
            )
            self._paragraphs.insert(insert_pos, new_info)
            self._raw_paragraphs.insert(insert_pos, new_para)
            # 重建后续索引
            for i in range(insert_pos, len(self._paragraphs)):
                self._paragraphs[i].index = i
            return insert_pos
        except Exception:
            return -1

    def append_paragraph(self, text: str) -> int:
        """
        在文档末尾追加段落。
        返回新段落的索引；失败返回 -1。
        """
        text = text.strip()
        if not text or len(text) < self.MIN_PARAGRAPH_LENGTH:
            return -1
        if self._doc is None:
            return -1
        try:
            new_para = self._doc.add_paragraph(text)
            new_index = len(self._paragraphs)
            new_info = ParagraphInfo(
                index=new_index,
                text=text,
                hash_value=self._hash_text(text),
                preview=self._make_preview(text),
            )
            self._paragraphs.append(new_info)
            self._raw_paragraphs.append(new_para)
            return new_index
        except Exception:
            return -1

    # ---------------- 保存 ----------------
    def save(self) -> bool:
        """
        保存到 docx 文件并更新 meta。
        保存前自动备份 .bak（若原文件存在）。
        返回是否保存成功。
        """
        if self._doc is None:
            return False
        try:
            # 备份原文件
            if os.path.exists(self._docx_path):
                try:
                    shutil.copy2(self._docx_path, self._docx_path + ".bak")
                except OSError:
                    pass
            # python-docx 保存
            self._doc.save(self._docx_path)
            # 更新 meta（含新 mtime + sha1 + 段落哈希）
            self._update_meta()
            return True
        except Exception:
            return False

    def restore_backup(self) -> bool:
        """
        从 .bak 恢复 docx（保存失败的兜底）。
        恢复后需调用 load() 重新加载内存模型。
        """
        bak_path = self._docx_path + ".bak"
        if not os.path.exists(bak_path):
            return False
        try:
            shutil.copy2(bak_path, self._docx_path)
            return True
        except OSError:
            return False
