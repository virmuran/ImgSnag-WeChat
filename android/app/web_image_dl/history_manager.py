"""
下载历史管理器 — SQLite持久化，记录每次解析下载的结果
数据库位置：~/.imgsnag_wechat/history.db（可传 `db_path=` 换地方）

⚠ 导入本模块**不该有任何副作用**：`history_manager` 那个全局单例改成
用到时才建（见文件末尾的 `__getattr__`）。否则安卓端只是想用一下
`HistoryManager` 类，就会顺手在应用目录里多建一个用不上的库；
测试更不该因为 import 一下就碰用户真实的 history.db。
"""
import os
import sqlite3
from datetime import datetime
from dataclasses import dataclass


DB_DIR = os.path.expanduser("~/.imgsnag_wechat")
DB_PATH = os.path.join(DB_DIR, "history.db")


def _ensure_dir(db_dir=None):
    os.makedirs(db_dir or DB_DIR, exist_ok=True)


@dataclass
class HistoryEntry:
    id: int = 0
    source_url: str = ""
    parsed_at: str = ""          # ISO datetime
    total_images: int = 0
    success_images: int = 0
    save_path: str = ""
    status: str = "done"         # done / partial / failed / scanned
    note: str = ""

    @property
    def parsed_at_dt(self):
        try:
            return datetime.fromisoformat(self.parsed_at)
        except (ValueError, TypeError):
            return datetime.min

    def status_text(self):
        return {"done": "已下载", "scanned": "已解析", "partial": "部分成功", "failed": "失败"}.get(
            self.status, self.status
        )


class HistoryManager:
    def __init__(self, db_path=DB_PATH):
        # 目录要按**实际使用的路径**建：传了自定义 db_path 却在默认位置建目录，
        # 表现是"库文件创建失败"（父目录不存在），报错还指向别处。
        self.db_path = db_path
        _ensure_dir(os.path.dirname(os.path.abspath(db_path)) or DB_DIR)
        self._init_db()

    def _connect(self):
        return sqlite3.connect(self.db_path)

    def _init_db(self):
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_url TEXT NOT NULL,
                    parsed_at TEXT NOT NULL,
                    total_images INTEGER DEFAULT 0,
                    success_images INTEGER DEFAULT 0,
                    save_path TEXT DEFAULT '',
                    status TEXT DEFAULT 'done',
                    note TEXT DEFAULT ''
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_history_parsed_at ON history(parsed_at DESC)"
            )
            conn.commit()

    def add(self, source_url, total_images=0, success_images=0, save_path="", status="done", note=""):
        """添加一条解析/下载记录（同一 URL 只保留一行，返回该行 id）。

        v1.8.1 修复：**重新解析不许抹掉下载记录**。以前是「先删后插」——
        已下载过的链接再解析一次，done 那行连同 save_path 被整个删掉，
        换成一条「已解析、0 下载」，看起来就像下载凭空消失了。
        现在改成：
        - 已有下载（done/partial 且有 save_path）→ 只刷新解析时间/图片数/备注，
          下载状态、张数、保存路径**原样保留**（图还在磁盘上，记录就该还在）
        - 没有下载记录（scanned/failed）→ 照旧整体覆盖
        """
        now = datetime.now().isoformat()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, status, save_path FROM history WHERE source_url = ?",
                (source_url,),
            ).fetchone()
            if row is None:
                cur = conn.execute(
                    "INSERT INTO history (source_url, parsed_at, total_images, success_images, save_path, status, note) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (source_url, now, total_images, success_images, save_path, status, note)
                )
                conn.commit()
                return cur.lastrowid
            entry_id, old_status, old_path = row
            if old_status in ("done", "partial") and old_path:
                conn.execute(
                    "UPDATE history SET parsed_at = ?, total_images = ?, note = ? WHERE id = ?",
                    (now, total_images, note, entry_id),
                )
            else:
                conn.execute(
                    "UPDATE history SET parsed_at = ?, total_images = ?, success_images = ?, "
                    "save_path = ?, status = ?, note = ? WHERE id = ?",
                    (now, total_images, success_images, save_path, status, note, entry_id),
                )
            conn.commit()
            return entry_id

    def update(self, entry_id, success_images=None, save_path=None, status=None, note=None):
        """更新已有记录的部分字段"""
        with self._connect() as conn:
            updates = []
            params = []
            if success_images is not None:
                updates.append("success_images = ?")
                params.append(success_images)
            if save_path is not None:
                updates.append("save_path = ?")
                params.append(save_path)
            if status is not None:
                updates.append("status = ?")
                params.append(status)
            if note is not None:
                updates.append("note = ?")
                params.append(note)
            if updates:
                params.append(entry_id)
                conn.execute(f"UPDATE history SET {', '.join(updates)} WHERE id = ?", params)
                conn.commit()

    def get_all(self, limit=200):
        """获取所有记录，按时间倒序"""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, source_url, parsed_at, total_images, success_images, save_path, status, note "
                "FROM history ORDER BY parsed_at DESC LIMIT ?",
                (limit,)
            ).fetchall()
        return [HistoryEntry(*r) for r in rows]

    def delete(self, entry_id):
        """删除一条记录"""
        with self._connect() as conn:
            conn.execute("DELETE FROM history WHERE id = ?", (entry_id,))
            conn.commit()

    def clear(self):
        """清空所有历史"""
        with self._connect() as conn:
            conn.execute("DELETE FROM history")
            conn.commit()

    def count(self):
        with self._connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM history").fetchone()[0]


# ============================================================================
#  全局单例（用到时才建）
# ============================================================================

_INSTANCE = None


def __getattr__(name):
    """PEP 562：`history_manager` 改成惰性创建。

    以前它在模块导入时就 `HistoryManager()`，于是任何"只是想用一下这个类"的地方
    都会真的去建库文件 —— 安卓端会多出一个用不上的目录，测试也会碰到用户的真实
    数据（本项目的规矩：测试不许写用户目录）。改成访问时才建，
    `from ... import history_manager` 这类写法照旧可用。
    """
    global _INSTANCE
    if name == 'history_manager':
        if _INSTANCE is None:
            _INSTANCE = HistoryManager()
        return _INSTANCE
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')
