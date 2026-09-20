"""Local inbox state. Acknowledgements use CAS.

事件幂等依赖调用方传入确定性 event_id（文件采集器按 会话:轮次:事件 构造）：
events 表按它去重，防止游标重置后重扫双计。hook 事件传入的是随机 uuid，
只作流水记录、没有去重作用（hooks 本身不重投）。"""

import hashlib
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

DEFAULT_ROOT = Path.home() / ".local/state/session-manager"


def identity(provider, session_id):
    return hashlib.sha256(json.dumps([provider, session_id]).encode()).hexdigest()[:24]


class Store:
    def __init__(self, root=DEFAULT_ROOT):
        self.root = root
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = root / "inbox.sqlite"
        self._connection = None
        self._depth = 0
        with self.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS sessions (
                  id TEXT PRIMARY KEY, provider TEXT, session_id TEXT, title TEXT,
                  project TEXT DEFAULT '', state TEXT DEFAULT 'unknown', unread INTEGER DEFAULT 0,
                  event_at REAL DEFAULT 0, revision INTEGER DEFAULT 0, attention_token TEXT,
                  locator TEXT DEFAULT '{}', hidden INTEGER DEFAULT 0);
                CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT);
            """)
            db.execute(
                "INSERT OR IGNORE INTO metadata VALUES (?,?)",
                ("started_at", json.dumps(time.time())),
            )
            existing = {
                row["name"] for row in db.execute("PRAGMA table_info(sessions)")
            }
            if "activity_at" not in existing:
                db.execute("ALTER TABLE sessions ADD COLUMN activity_at REAL DEFAULT 0")
            # 会话时长口径：attention_at=最近一次通知抬升，acknowledged_at=已处理时刻。
            for column in ("attention_at", "acknowledged_at"):
                if column not in existing:
                    db.execute(
                        f"ALTER TABLE sessions ADD COLUMN {column} REAL DEFAULT 0"
                    )
            # 启动方式：user=人工（默认），agent=其它工具拉起（不通知、不进待查看、日报默认不计）。
            if "origin" not in existing:
                db.execute("ALTER TABLE sessions ADD COLUMN origin TEXT DEFAULT 'user'")

    @contextmanager
    def db(self):
        """顶层调用复用同一连接：Store 每方法各开一连接的模式在 3 秒轮询下放大到
        每轮上千次连接开关。嵌套调用（少见）回退一次性连接，避免内外层事务互相提交。"""
        if self._connection is None:
            self._connection = sqlite3.connect(self.path, timeout=3)
            self._connection.row_factory = sqlite3.Row
        reuse = self._depth == 0
        connection = self._connection
        if not reuse:
            connection = sqlite3.connect(self.path, timeout=3)
            connection.row_factory = sqlite3.Row
        self._depth += 1
        try:
            with connection:
                yield connection
        finally:
            self._depth -= 1
            if not reuse:
                connection.close()

    def close(self):
        """关闭缓存连接；不显式调用也安全（进程退出时由系统回收）。"""
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def meta(self, key, default=None):
        with self.db() as db:
            row = db.execute(
                "SELECT value FROM metadata WHERE key=?", (key,)
            ).fetchone()
            return json.loads(row[0]) if row else default

    def set_meta(self, key, value):
        with self.db() as db:
            db.execute(
                "INSERT OR REPLACE INTO metadata VALUES (?,?)", (key, json.dumps(value))
            )

    @staticmethod
    def _insert_default_row(db, provider, sid):
        """默认行插入（幂等）。并发首次发现同一会话时由主键 + OR IGNORE 做原子仲裁：
        输家静默跳过插入，随后的 SELECT 读到赢家已写入的行继续正常比对——裸 INSERT
        在此交错下会抛 IntegrityError（进程级实测 30 轮 28 轮命中，hook 事件静默丢失）。"""
        db.execute(
            "INSERT OR IGNORE INTO sessions (id,provider,session_id,title) VALUES (?,?,?,?)",
            (identity(provider, sid), provider, sid, provider + " · " + sid[:12]),
        )

    def ensure(self, provider, sid):
        key = identity(provider, sid)
        with self.db() as db:
            self._insert_default_row(db, provider, sid)
        return key

    _PATCH_COLUMNS = "title, project, locator, hidden, origin, activity_at"

    def patch(
        self,
        provider,
        sid,
        *,
        title=None,
        project=None,
        locator=None,
        hidden=None,
        activity_at=None,
        origin=None,
    ):
        """单连接合并写：与现值逐字段比对，只有实际变化的才落库——稳态轮询下绝大多数
        行的标题/项目/定位长期不变，跳过后写放大降一个数量级（值不变的 patch 不产生
        任何写事务）。行不存在时按 ensure 的默认值插入后继续走同一套比对。"""
        key = identity(provider, sid)
        with self.db() as db:
            row = db.execute(
                f"SELECT {self._PATCH_COLUMNS} FROM sessions WHERE id=?", (key,)
            ).fetchone()
            if row is None:
                self._insert_default_row(db, provider, sid)
                row = db.execute(
                    f"SELECT {self._PATCH_COLUMNS} FROM sessions WHERE id=?", (key,)
                ).fetchone()
            fields = {
                k: v
                for k, v in [
                    ("title", title),
                    ("project", project),
                    ("locator", json.dumps(locator) if locator is not None else None),
                    ("hidden", int(hidden) if hidden is not None else None),
                    ("origin", origin),
                ]
                if v is not None and v != row[k]
            }
            if fields:
                db.execute(
                    "UPDATE sessions SET "
                    + ",".join(k + "=?" for k in fields)
                    + " WHERE id=?",
                    [*fields.values(), key],
                )
            if isinstance(activity_at, (int, float)) and activity_at > row["activity_at"]:
                db.execute(
                    "UPDATE sessions SET activity_at=MAX(activity_at,?) WHERE id=?",
                    (activity_at, key),
                )
        return key

    def event(
        self, provider, sid, *, event_id, timestamp, state, attention=None, token=None
    ):
        """先查 events 账本再写：确定性 event_id 命中去重的重复事件（采集器每轮重放的
        大头）只读不写，也不再为它碰 sessions 行（含已被清理的行——重复事件不复活空行，
        与旧版 ensure 先行的行为差异仅此一处）。"""
        key = identity(provider, sid)
        with self.db() as db:
            if db.execute("SELECT 1 FROM events WHERE id=?", (event_id,)).fetchone():
                return False
            self._insert_default_row(db, provider, sid)
            if not db.execute(
                "INSERT OR IGNORE INTO events VALUES (?)", (event_id,)
            ).rowcount:
                return False
            row = db.execute("SELECT * FROM sessions WHERE id=?", (key,)).fetchone()
            if timestamp < row["event_at"]:
                return False
            unread = row["unread"]
            attention_token = row["attention_token"]
            attention_at = None
            if attention is False:
                unread = 0
            elif attention is True and (token or event_id) != attention_token:
                unread = 1
                attention_token = token or event_id
                attention_at = timestamp
            columns = "state=?,unread=?,event_at=?,activity_at=MAX(activity_at,?),revision=revision+1,attention_token=?"
            values = [state, unread, timestamp, timestamp, attention_token]
            if attention_at is not None:
                columns += ",attention_at=?"
                values.append(attention_at)
            db.execute(f"UPDATE sessions SET {columns} WHERE id=?", [*values, key])
        return True

    def origin_rules(self):
        """目录级 origin 覆盖规则（用户手动改判沉淀）：[{project, origin}]，读取时优先生效。"""
        return self.meta("origin-rules", []) or []

    def set_origin_rule(self, project, origin):
        """origin=None 表示删除该目录的规则。"""
        rules = [r for r in self.origin_rules() if r.get("project") != project]
        if origin is not None:
            rules.append({"project": project, "origin": origin})
        self.set_meta("origin-rules", rules)

    def origin_rule_index(self):
        """{project: origin}：origin_rules 的读取侧形态（收件箱 display 与日报共用）。"""
        return {r.get("project"): r.get("origin") for r in self.origin_rules()}

    def acknowledge(self, key, revision):
        with self.db() as db:
            return bool(
                db.execute(
                    "UPDATE sessions SET unread=0,acknowledged_at=?,revision=revision+1 WHERE id=? AND revision=?",
                    (time.time(), key, revision),
                ).rowcount
            )

    def acknowledge_batch(self, items):
        """批量按各自 (id, revision) CAS 确认；revision 不匹配的项跳过并保留未读。"""
        now = time.time()
        acked = []
        with self.db() as db:
            for key, revision in items:
                if db.execute(
                    "UPDATE sessions SET unread=0,acknowledged_at=?,revision=revision+1 WHERE id=? AND revision=?",
                    (now, key, revision),
                ).rowcount:
                    acked.append(key)
        return acked

    def rows(self, unread_only=False):
        order = (
            "CASE state WHEN 'waiting' THEN 0 WHEN 'failed' THEN 1 ELSE 2 END, "
            if unread_only
            else ""
        )
        order += "MAX(activity_at,event_at) DESC, id"
        with self.db() as db:
            rows = db.execute(
                "SELECT * FROM sessions WHERE hidden=0 "
                + ("AND unread=1 " if unread_only else "")
                + "ORDER BY "
                + order
            ).fetchall()
        return [
            {
                **dict(row),
                "locator": json.loads(row["locator"]),
                "unread": bool(row["unread"]),
            }
            for row in rows
        ]

    def get(self, key):
        with self.db() as db:
            row = db.execute(
                "SELECT * FROM sessions WHERE id=? AND hidden=0", (key,)
            ).fetchone()
        if row is None:
            raise ValueError("session missing or archived")
        return {**dict(row), "locator": json.loads(row["locator"])}


def effective_origin(rules, row):
    """行级有效启动方式：目录规则（用户改判沉淀）优先于行内自动分类，缺省人工。
    收件箱 display 与日报 excluded 共用本实现，两处口径不会漂移；
    无行（来源未入箱）按人工保留。"""
    if row is None:
        return "user"
    return rules.get(row.get("project") or "") or row.get("origin") or "user"


EVENTS = {
    "SessionStart": ("idle", False),
    "session_start": ("idle", False),
    "UserPromptSubmit": ("running", False),
    "TurnStarted": ("running", False),
    "agent_start": ("running", False),
    "Stop": ("idle", True),
    "agent_settled": ("idle", True),
    "StopFailure": ("failed", True),
    "PermissionRequest": ("waiting", True),
    "ui_prompt_start": ("waiting", True),
    "PermissionResult": ("running", False),
    "ui_prompt_end": ("running", False),
    "Interrupt": ("interrupted", False),
    "SessionEnd": ("closed", None),
    "session_shutdown": ("closed", None),
}


def receive(
    root,
    provider,
    sid,
    event,
    *,
    run_id=None,
    title=None,
    project=None,
    transcript=None,
    idle=True,
    origin=None,
):
    from uuid import uuid4

    store = Store(root)
    title = title[:300] if isinstance(title, str) and title else None
    project = project[:2048] if isinstance(project, str) else None
    if provider == "claude":
        key = store.ensure(provider, sid)
        with store.db() as db:
            row = db.execute(
                "SELECT locator, project FROM sessions WHERE id=?", (key,)
            ).fetchone()
        if row and row["locator"] == "{}":
            # CLI 直启的 claude 会话（hook 带 cwd）：给出可恢复定位并可见；
            # Desktop 内嵌会话由采集器在刷新时改写为 claude:// 深链。
            cwd = row["project"] or project
            if cwd:
                locator = {"kind": "cli", "cwd": cwd}
                if isinstance(transcript, str) and transcript:
                    locator["file"] = transcript
                with store.db() as db:
                    db.execute(
                        "UPDATE sessions SET hidden=0, locator=? WHERE id=? AND locator='{}'",
                        (json.dumps(locator), key),
                    )
            else:
                with store.db() as db:
                    db.execute(
                        "UPDATE sessions SET hidden=1 WHERE id=? AND locator='{}'",
                        (key,),
                    )
    locator = (
        {"kind": "managed", "run_id": run_id, "session_id": sid} if run_id else None
    )
    store.patch(
        provider,
        sid,
        title=title,
        project=project,
        locator=locator,
        activity_at=time.time() if event == "session_info_changed" else None,
        origin=origin,
    )
    if event not in EVENTS or (event == "agent_settled" and not idle):
        return
    state, attention = EVENTS[event]
    store.event(
        provider,
        sid,
        event_id=uuid4().hex,
        timestamp=time.time(),
        state=state,
        attention=attention,
    )
