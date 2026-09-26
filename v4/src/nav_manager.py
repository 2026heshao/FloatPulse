# -*- coding: utf-8 -*-
"""
====================================================================
网址导航管理模块  -  NavManager
====================================================================
独立封装网址导航的全部业务逻辑，与 UI 层解耦。

设计要点：
  1. 使用 nav.json 持久化网址导航数据（分组 + 站点）
  2. 兼容 PyInstaller 打包环境（路径由外部传入）
  3. 文件缺失自动初始化空数据；json 解析异常不崩溃
  4. 每个站点拥有自增且不复用的唯一 nav_id 主键
  5. 分组可增删改，站点可增删改拖拽排序
  6. URL 缺失 http/https 自动补全
  7. 所有增删改先操作内存，完毕统一调用 _save() 写盘
  8. UI 层只能通过本类公开方法操作，禁止直接读写 nav.json

数据结构：
  {
    "next_id": 1,
    "groups": [
      {
        "group_id": 1,
        "name": "常用",
        "sites": [
          {"nav_id": 1, "title": "百度", "url": "https://www.baidu.com"},
          ...
        ]
      },
      ...
    ]
  }

模块导出：
  - NavSite     : 站点数据类
  - NavGroup    : 分组数据类
  - NavManager  : 网址导航管理器
====================================================================
"""

import os
import json

from src.json_store import load_records


# ====================================================================
# 站点数据类
# ====================================================================
class NavSite:
    """单个网址站点的数据载体"""

    def __init__(self, nav_id, title, url):
        self.nav_id = nav_id
        self.title = str(title)
        self.url = self._normalize_url(str(url))

    @staticmethod
    def _normalize_url(url: str) -> str:
        """URL 缺失 http/https 自动补全"""
        url = url.strip()
        if url and not url.startswith(("http://", "https://")):
            url = "https://" + url
        return url

    def to_dict(self):
        return {
            "nav_id": self.nav_id,
            "title": self.title,
            "url": self.url,
        }

    @classmethod
    def from_dict(cls, d):
        return cls(
            nav_id=int(d.get("nav_id", 0) or 0),
            title=str(d.get("title", "")),
            url=str(d.get("url", "")),
        )


# ====================================================================
# 分组数据类
# ====================================================================
class NavGroup:
    """一个导航分组的数据载体"""

    def __init__(self, group_id, name, sites=None):
        self.group_id = group_id
        self.name = str(name)
        self.sites = sites if sites is not None else []

    def to_dict(self):
        return {
            "group_id": self.group_id,
            "name": self.name,
            "sites": [s.to_dict() for s in self.sites],
        }

    @classmethod
    def from_dict(cls, d):
        sites_data = d.get("sites", [])
        sites = [NavSite.from_dict(s) for s in sites_data if isinstance(s, dict)]
        return cls(
            group_id=int(d.get("group_id", 0) or 0),
            name=str(d.get("name", "")),
            sites=sites,
        )


# ====================================================================
# 网址导航管理器
# ====================================================================
class NavManager:
    """
    网址导航管理器。

    对外提供分组和站点的增删改查、拖拽排序接口，
    内部维护内存数据，修改完毕统一调用 _save() 写入磁盘。
    UI 层禁止直接读写 nav.json 文件。
    """

    def __init__(self, json_path: str):
        self._json_path = json_path
        self._groups = []
        self._next_id = 1
        self._load()

    # ---------------- 持久化 ----------------
    def _load(self):
        """从磁盘加载导航数据（骨架见 json_store.load_records）。文件缺失或损坏 → 初始化空数据"""
        self._groups, self._next_id = load_records(
            self._json_path, "groups", NavGroup.from_dict,
            # nav_id 在嵌套的 sites 里，next_id 需遍历全部站点后取值
            min_next_id=lambda gs: max(
                [0] + [s.nav_id + 1 for g in gs for s in g.sites]),
        )

    def _save(self):
        """统一保存：原子写入"""
        data = {
            "groups": [g.to_dict() for g in self._groups],
            "next_id": self._next_id,
        }
        try:
            os.makedirs(os.path.dirname(self._json_path), exist_ok=True)
            tmp_path = self._json_path + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self._json_path)
        except OSError:
            pass

    # ---------------- 分组操作 ----------------
    def add_group(self, name: str) -> int:
        """新建分组，返回 group_id"""
        gid = self._next_id
        self._next_id += 1
        self._groups.append(NavGroup(group_id=gid, name=name))
        self._save()
        return gid

    def rename_group(self, group_id: int, name: str) -> bool:
        """重命名分组"""
        for g in self._groups:
            if g.group_id == group_id:
                g.name = name
                self._save()
                return True
        return False

    def delete_group(self, group_id: int) -> bool:
        """删除分组及其所有站点"""
        before = len(self._groups)
        self._groups = [g for g in self._groups if g.group_id != group_id]
        if len(self._groups) < before:
            self._save()
            return True
        return False

    def move_group(self, from_index: int, to_index: int):
        """拖拽移动分组顺序"""
        if 0 <= from_index < len(self._groups) and 0 <= to_index < len(self._groups):
            g = self._groups.pop(from_index)
            self._groups.insert(to_index, g)
            self._save()

    def get_groups(self):
        """返回全部分组"""
        return list(self._groups)

    def get_group(self, group_id: int):
        """按 group_id 获取分组，不存在返回 None"""
        for g in self._groups:
            if g.group_id == group_id:
                return g
        return None

    # ---------------- 站点操作 ----------------
    def add_site(self, group_id: int, title: str, url: str) -> int:
        """在指定分组中添加站点，返回 nav_id。URL 自动补全 http/https"""
        group = self.get_group(group_id)
        if group is None:
            return -1
        nav_id = self._next_id
        self._next_id += 1
        group.sites.append(NavSite(nav_id=nav_id, title=title, url=url))
        self._save()
        return nav_id

    def update_site(self, group_id: int, nav_id: int, title: str, url: str) -> bool:
        """更新站点信息"""
        group = self.get_group(group_id)
        if group is None:
            return False
        for s in group.sites:
            if s.nav_id == nav_id:
                s.title = title
                s.url = NavSite._normalize_url(url)
                self._save()
                return True
        return False

    def delete_site(self, group_id: int, nav_id: int) -> bool:
        """删除站点"""
        group = self.get_group(group_id)
        if group is None:
            return False
        before = len(group.sites)
        group.sites = [s for s in group.sites if s.nav_id != nav_id]
        if len(group.sites) < before:
            self._save()
            return True
        return False

    def move_site(self, group_id: int, from_index: int, to_index: int):
        """拖拽移动站点顺序"""
        group = self.get_group(group_id)
        if group is None:
            return
        if 0 <= from_index < len(group.sites) and 0 <= to_index < len(group.sites):
            s = group.sites.pop(from_index)
            group.sites.insert(to_index, s)
            self._save()

    def reorder_sites_flat(self, ordered_ids) -> bool:
        """按给定的 nav_id 先后顺序重排全部站点，并落盘。

        用途：网址导航面板是「平铺展示所有站点」的视图，用户拖拽行以后
        需要把新的先后顺序写回数据层，否则一次 refresh() 就会复位。

        实现约定：**保持各分组的站点数量（槽位）不变**，把新顺序依次回填。
        例如原分组 A 有 2 个、B 有 3 个，新顺序 [b1,a1,a2,b2,b3] 会回填成
        A=[b1,a1]、B=[a2,b2,b3]，这样跨分组拖拽也不会丢站点。

        返回 True 表示已落盘；顺序不完整（与当前站点数不匹配）时返回 False，
        不做任何改动，避免因数据变化导致站点丢失。
        """
        slots = [len(g.sites) for g in self._groups]
        total = sum(slots)
        if total == 0:
            return False
        id_map = {}
        for g in self._groups:
            for s in g.sites:
                id_map[s.nav_id] = s
        ordered = [id_map[i] for i in ordered_ids if i in id_map]
        if len(ordered) != total:
            return False
        pos = 0
        for g, n in zip(self._groups, slots):
            g.sites = ordered[pos:pos + n]
            pos += n
        self._save()
        return True

    def get_all_sites(self):
        """返回所有分组的所有站点（扁平列表，用于小卡片展示）"""
        result = []
        for g in self._groups:
            result.append((g, list(g.sites)))
        return result

    def find_site_by_url(self, url: str):
        """按 URL 查找站点，返回 (group, site) 或 (None, None)"""
        normalized = NavSite._normalize_url(url)
        for g in self._groups:
            for s in g.sites:
                if s.url == normalized:
                    return g, s
        return None, None

    # ---------------- 简化接口（无分组，内部自动使用默认分组） ----------------
    _DEFAULT_GROUP_NAME = "默认"

    def _ensure_default_group(self) -> int:
        """确保默认分组存在，返回 group_id"""
        for g in self._groups:
            if g.name == self._DEFAULT_GROUP_NAME:
                return g.group_id
        return self.add_group(self._DEFAULT_GROUP_NAME)

    def add_site_simple(self, title: str, url: str) -> int:
        """简化添加站点（自动放入默认分组），返回 nav_id"""
        gid = self._ensure_default_group()
        return self.add_site(gid, title, url)

    def get_all_sites_flat(self):
        """返回所有站点的扁平列表（不分分组），用于简化 UI 展示"""
        result = []
        for g in self._groups:
            for s in g.sites:
                result.append(s)
        return result

    def find_site_by_id(self, nav_id: int):
        """按 nav_id 查找站点，返回 (group, site) 或 (None, None)"""
        for g in self._groups:
            for s in g.sites:
                if s.nav_id == nav_id:
                    return g, s
        return None, None

    def update_site_simple(self, nav_id: int, title: str, url: str) -> bool:
        """简化更新站点（自动找到所在分组）"""
        g, s = self.find_site_by_id(nav_id)
        if g and s:
            return self.update_site(g.group_id, nav_id, title, url)
        return False

    def delete_site_simple(self, nav_id: int) -> bool:
        """简化删除站点（自动找到所在分组）"""
        g, s = self.find_site_by_id(nav_id)
        if g:
            return self.delete_site(g.group_id, nav_id)
        return False
