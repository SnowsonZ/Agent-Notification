import json
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import os

from inbox import (
    desktop_registry_hit,
    display_rows,
    open_session,
    spawn_origin_from_parent,
    spawn_origin_from_tty,
)
from inbox_sources import collect_claude, collect_codex
from inbox_store import Store, effective_origin, receive
from migrations import (
    backfill_claude_tmp_origins,
    codex_dangling_turn_repair,
    codex_origin_backfill,
)


class ClaudeCliVisibilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = Store(self.root)  # 与 receive(root, ...) 同库

    def test_hook_session_with_cwd_becomes_visible_cli_row(self):
        receive(self.root, "claude", "cli-1", "SessionStart", project="/work/cli")
        row = self.store.rows()[0]
        self.assertEqual(row["project"], "/work/cli")
        self.assertEqual(row["locator"], {"kind": "cli", "cwd": "/work/cli"})
        self.assertFalse(row["hidden"])

    def test_hook_session_without_cwd_stays_hidden(self):
        receive(self.root, "claude", "cli-2", "SessionStart", project=None)
        self.assertEqual(self.store.rows(), [])
        key = self.store.ensure("claude", "cli-2")
        self.assertTrue(self.store.get.__self__)  # store 可用性占位
        with self.store.db() as db:
            row = db.execute(
                "SELECT hidden FROM sessions WHERE id=?", (key,)
            ).fetchone()
        self.assertEqual(row[0], 1)

    def test_lifecycle_events_flow_after_visibility(self):
        receive(self.root, "claude", "cli-1", "SessionStart", project="/work/cli")
        receive(self.root, "claude", "cli-1", "UserPromptSubmit", project="/work/cli")
        self.assertEqual(self.store.rows()[0]["state"], "running")
        receive(self.root, "claude", "cli-1", "Stop", project="/work/cli")
        row = self.store.rows()[0]
        self.assertEqual(row["state"], "idle")
        self.assertTrue(row["unread"])

    def test_receive_records_spawn_origin(self):
        receive(
            self.root,
            "claude",
            "cli-9",
            "SessionStart",
            project="/work/cli",
            origin="agent",
        )
        self.assertEqual(self.store.rows()[0]["origin"], "agent")
        receive(
            self.root, "claude", "cli-9", "Stop", project="/work/cli", origin="agent"
        )
        self.assertTrue(self.store.rows()[0]["unread"])
        # 未带 origin 的事件不改动已记录的启动方式。
        receive(self.root, "claude", "cli-9", "Stop", project="/work/cli")
        self.assertEqual(self.store.rows()[0]["origin"], "agent")

    def test_tty_spawn_origin_rule(self):
        self.assertEqual(spawn_origin_from_tty("??"), "agent")
        self.assertEqual(spawn_origin_from_tty(" ttys001\n"), "user")
        self.assertEqual(spawn_origin_from_tty(""), "user")

    def test_parent_spawn_origin_rule(self):
        # 终端会被子进程继承，tty 不够：直接父进程是终端 shell = 手敲，工具进程 = agent。
        self.assertEqual(spawn_origin_from_parent("zsh"), "user")
        self.assertEqual(spawn_origin_from_parent("bash\n"), "user")
        self.assertEqual(spawn_origin_from_parent("python3"), "agent")
        self.assertEqual(spawn_origin_from_parent("node"), "agent")
        self.assertEqual(spawn_origin_from_parent(""), "user")

    def test_declared_env_overrides_heuristics(self):
        # 声明优于推断：拉起方设置 SESSION_MANAGER_ORIGIN 即精确生效，不走启发式。
        from inbox import DECLARED_ORIGIN_ENV, claude_spawn_origin

        with patch.dict(os.environ, {DECLARED_ORIGIN_ENV: "agent"}):
            self.assertEqual(claude_spawn_origin("whatever"), "agent")
        with patch.dict(os.environ, {DECLARED_ORIGIN_ENV: "user"}):
            self.assertEqual(claude_spawn_origin("whatever"), "user")
        with patch.dict(os.environ, {DECLARED_ORIGIN_ENV: "bogus"}, clear=False):
            # 非法值忽略——落到启发式；进程 tty 不可控，仅验证不抛错且值合法。
            self.assertIn(claude_spawn_origin("whatever"), ("agent", "user"))

    def test_origin_rule_overrides_read_side(self):
        # 目录规则读时覆盖自动分类：行上 origin 不动，display/日报按规则算有效 origin。
        receive(
            self.root,
            "claude",
            "auto-agent",
            "Stop",
            project="/work/tool-dir",
            origin="agent",
        )
        receive(
            self.root,
            "claude",
            "auto-user",
            "Stop",
            project="/work/other",
            origin="user",
        )
        self.store.set_origin_rule("/work/tool-dir", "user")
        self.assertEqual(
            self.store.origin_rules(), [{"project": "/work/tool-dir", "origin": "user"}]
        )
        row = next(r for r in self.store.rows() if r["session_id"] == "auto-agent")
        self.assertEqual(row["origin"], "agent")  # 行上分类不动
        rows = display_rows(self.store, all_rows=True)
        self.assertEqual(
            sorted(r["session_id"] for r in rows), ["auto-agent", "auto-user"]
        )
        # 反向规则：目录标 agent 后即使行是 user 也被过滤（tool-dir 的 user 规则仍在）。
        self.store.set_origin_rule("/work/other", "agent")
        self.assertEqual(
            sorted(r["session_id"] for r in display_rows(self.store, all_rows=True)),
            ["auto-agent"],
        )
        # 删除规则恢复自动分类：agent 行回到默认隐藏。
        self.store.set_origin_rule("/work/other", None)
        self.store.set_origin_rule("/work/tool-dir", None)
        self.assertEqual(self.store.origin_rules(), [])
        self.assertEqual(
            [r["session_id"] for r in display_rows(self.store, all_rows=True)],
            ["auto-user"],
        )

    def test_claude_tmp_project_backfill(self):
        receive(
            self.root,
            "claude",
            "tmp-1",
            "SessionStart",
            project="/var/folders/xx/T/wb-gate-claude-a",
        )
        receive(
            self.root,
            "claude",
            "real-1",
            "SessionStart",
            project="/Users/snowson/work/real",
        )
        with self.store.db() as db:
            # 模拟迁移前状态：origin 均为默认 user。
            db.execute("UPDATE sessions SET origin='user'")
        backfill_claude_tmp_origins(self.store)
        origins = {r["session_id"]: r["origin"] for r in self.store.rows()}
        self.assertEqual(origins, {"tmp-1": "agent", "real-1": "user"})
        # 幂等：再次执行不改动。
        self.store.patch("claude", "real-1", origin="user")
        backfill_claude_tmp_origins(self.store)
        self.assertEqual(
            {r["session_id"]: r["origin"] for r in self.store.rows()}["tmp-1"], "agent"
        )

    def test_desktop_registry_hit_requires_recent_match(self):
        import os

        registry = self.root / "Library/Application Support/Claude/claude-code-sessions"
        registry.mkdir(parents=True)
        fresh = registry / "local_fresh.json"
        fresh.write_text(json.dumps({"cliSessionId": "sid-fresh"}))
        stale = registry / "local_stale.json"
        stale.write_text(json.dumps({"cliSessionId": "sid-stale"}))
        os.utime(stale, (time.time() - 3 * 86400, time.time() - 3 * 86400))
        self.assertTrue(desktop_registry_hit(self.root, "sid-fresh"))
        self.assertFalse(desktop_registry_hit(self.root, "sid-stale"))  # 过期登记不算
        self.assertFalse(
            desktop_registry_hit(self.root, "sid-other")
        )  # 未登记=无头 CLI


class DesktopOriginOverrideTests(unittest.TestCase):
    """Desktop 登记表成员是用户在 Desktop 界面驱动的：origin 权威为 user，
    每次 collect_claude 刷新自愈 hook 无头判定的误伤（Electron 内嵌无终端）。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.store = Store(self.home / "state")
        self.registry = (
            self.home / "Library/Application Support/Claude/claude-code-sessions"
        )
        self.registry.mkdir(parents=True)

    def desktop_entry(self, sid):
        (self.registry / f"local_{sid}.json").write_text(
            json.dumps(
                {
                    "cliSessionId": sid,
                    "sessionId": "local_" + sid,
                    "title": "桌面会话",
                    "cwd": "/Users/snowson/work",
                    "lastActivityAt": round(time.time() * 1000),
                }
            )
        )

    def test_desktop_session_self_heals_to_user(self):
        receive(
            self.home / "state",
            "claude",
            "desk-1",
            "SessionStart",
            project="/Users/snowson/work",
            origin="agent",
        )  # hook 无头误判
        self.desktop_entry("desk-1")
        collect_claude(self.store, self.home)
        self.assertEqual(self.store.rows()[0]["origin"], "user")
        self.assertEqual(self.store.rows()[0]["locator"]["kind"], "url")

    def test_headless_session_not_in_registry_stays_agent(self):
        receive(
            self.home / "state",
            "claude",
            "head-1",
            "Stop",
            project="/var/folders/x/T/wb-gate-claude-z",
            origin="agent",
        )
        collect_claude(self.store, self.home)
        self.assertEqual(self.store.rows()[0]["origin"], "agent")


class ClaudeDesktopIdentityPollutionTests(unittest.TestCase):
    """P1 回归（2026-09-21 评审）：目录级读取失败只降级 health，不抹掉正常会话
    的定位；歧义判定只看该 sid 的候选数，同 sid 多候选仍拒绝猜测。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.store = Store(self.home / "state")
        self.registry = (
            self.home / "Library/Application Support/Claude/claude-code-sessions"
        )
        self.registry.mkdir(parents=True)

    def write_entry(self, sid, filename=None):
        (self.registry / (filename or f"local_{sid}.json")).write_text(
            json.dumps(
                {
                    "cliSessionId": sid,
                    "sessionId": "local_" + sid,
                    "title": "桌面会话",
                    "cwd": "/Users/snowson/work",
                    "lastActivityAt": round(time.time() * 1000),
                }
            )
        )

    def test_unrelated_corrupt_file_keeps_valid_locator(self):
        self.write_entry("ok-1")
        (self.registry / "local_broken.json").write_text(
            '{"cliSessionId": "other", "sessi'
        )
        health = collect_claude(self.store, self.home)
        self.assertEqual(self.store.rows()[0]["locator"]["kind"], "url")
        self.assertEqual(health["status"], "degraded")

    def test_unrelated_oversized_file_keeps_valid_locator(self):
        self.write_entry("ok-2")
        (self.registry / "local_huge.json").write_text("x" * 4_000_001)
        health = collect_claude(self.store, self.home)
        self.assertEqual(self.store.rows()[0]["locator"]["kind"], "url")
        self.assertEqual(health["status"], "degraded")

    def test_duplicate_candidates_for_same_sid_stay_unavailable(self):
        self.write_entry("dup-1", filename="local_copy_a.json")
        self.write_entry("dup-1", filename="local_copy_b.json")
        collect_claude(self.store, self.home)
        locator = self.store.rows()[0]["locator"]
        self.assertEqual(locator["kind"], "unavailable")
        self.assertIn("ambiguous", locator["reason"])


class ManualOriginOverrideTests(unittest.TestCase):
    """评审 R5：单条手动改判独立存储，采集器重写 origin 字段不冲掉人工意图；
    display_rows 回填有效来源供下游（Swift 通知过滤）使用。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.store = Store(self.home / "state")
        self.registry = (
            self.home / "Library/Application Support/Claude/claude-code-sessions"
        )
        self.registry.mkdir(parents=True)
        (self.registry / "local_desk-1.json").write_text(
            json.dumps(
                {
                    "cliSessionId": "desk-1",
                    "sessionId": "local_desk-1",
                    "title": "桌面会话",
                    "cwd": "/Users/snowson/work",
                    "lastActivityAt": round(time.time() * 1000),
                }
            )
        )

    def test_manual_override_survives_desktop_refresh(self):
        collect_claude(self.store, self.home)  # 自动分类 → user
        row = self.store.rows()[0]
        self.store.set_origin_override(row["id"], "agent")
        collect_claude(self.store, self.home)  # 下一轮：origin 字段被权威覆盖回 user
        row = self.store.rows()[0]
        self.assertEqual(row["origin"], "user")
        self.assertEqual(
            effective_origin(
                self.store.origin_rule_index(), row, self.store.origin_overrides()
            ),
            "agent",
        )

    def test_display_rows_stamps_effective_origin(self):
        collect_claude(self.store, self.home)
        row = self.store.rows()[0]
        self.store.set_origin_override(row["id"], "agent")
        listed = display_rows(self.store, True, include_agents=True)
        self.assertEqual(listed[0]["origin"], "agent")


class ClaudeCliTitleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.store = Store(self.home / "state")
        # collect_claude 需要 Desktop 元数据根存在才会走到迁移/标题逻辑。
        (self.home / "Library/Application Support/Claude/claude-code-sessions").mkdir(
            parents=True
        )

    def test_title_from_first_user_message_with_preview_fallback_order(self):
        sid = "11111111-2222-3333-4444-555555555555"
        transcript = self.home / ".claude/projects/proj" / f"{sid}.jsonl"
        transcript.parent.mkdir(parents=True)
        lines = [
            json.dumps(
                {
                    "type": "user",
                    "isMeta": True,
                    "message": {"content": "注入的上下文不应当标题"},
                    "sessionId": sid,
                }
            ),
            json.dumps(
                {
                    "type": "user",
                    "message": {"content": "<command>跳过命令行</command>"},
                    "sessionId": sid,
                }
            ),
            json.dumps(
                {
                    "type": "user",
                    "message": {
                        "content": [
                            {"type": "text", "text": "真实的  首条提问\n换行也保留"}
                        ]
                    },
                    "sessionId": sid,
                }
            ),
        ]
        transcript.write_text("\n".join(lines) + "\n")
        self.store.patch("claude", sid, locator={"kind": "cli", "cwd": "/work/cli"})
        self.store.patch("claude", sid, title=None)
        health = collect_claude(self.store, self.home)
        self.assertEqual(health["cli_titled"], 1)
        row = self.store.rows()[0]
        self.assertEqual(row["title"], "真实的 首条提问 换行也保留")

    def test_missing_transcript_cached_negative(self):
        sid = "22222222-2222-3333-4444-555555555555"
        self.store.patch("claude", sid, locator={"kind": "cli", "cwd": "/work/cli"})
        # 已结束仍缺转写才永久记忆；未结束的会话转写可能尚未落盘，不缓存。
        receive(self.home / "state", "claude", sid, "SessionEnd")
        health = collect_claude(self.store, self.home)
        self.assertEqual(health["cli_titled"], 0)
        self.assertTrue(self.store.meta("claude-cli-title-missing:" + sid))

    def test_titles_collected_when_desktop_registry_missing(self):
        # 评审 R6：纯 CLI 安装（Desktop 登记目录不存在）时，collect_claude 曾以
        # unavailable 整段早返回，CLI 行的标题补全被跳过；现在登记目录只是可选源。
        sid = "33333333-2222-3333-4444-555555555555"
        shutil.rmtree(self.home / "Library/Application Support/Claude")
        transcript = self.home / ".claude/projects/proj" / f"{sid}.jsonl"
        transcript.parent.mkdir(parents=True)
        transcript.write_text(
            json.dumps(
                {
                    "type": "user",
                    "message": {"content": "纯 CLI 会话标题"},
                    "sessionId": sid,
                }
            )
            + "\n"
        )
        self.store.patch("claude", sid, locator={"kind": "cli", "cwd": "/work/cli"})
        self.store.patch("claude", sid, title=None)
        health = collect_claude(self.store, self.home)
        self.assertEqual(health["status"], "ok")
        self.assertEqual(health["cli_titled"], 1)
        self.assertEqual(self.store.rows()[0]["title"], "纯 CLI 会话标题")

    def test_backfilled_unknown_row_cached_negative(self):
        # unknown 是无 hooks 时代回填的历史死行（行由回填分支建立、无事件），转写不会再
        # 出现——允许永久缓存，避免每轮全量扫描对存量行空转 glob（2026-09-21 评审）。
        sid = "66666666-6666-6666-6666-666666666666"
        self.store.patch("claude", sid, locator={"kind": "cli", "cwd": "/work/legacy"})
        health = collect_claude(self.store, self.home)
        self.assertEqual(health["cli_titled"], 0)
        self.assertTrue(self.store.meta("claude-cli-title-missing:" + sid))

    def test_turn_state_rows_stay_live_without_cache(self):
        # failed/interrupted 是回合态（会话可继续），不缓存、下轮重试。
        sid = "77777777-7777-7777-7777-777777777777"
        self.store.patch("claude", sid, locator={"kind": "cli", "cwd": "/work/cli"})
        receive(self.home / "state", "claude", sid, "StopFailure")
        collect_claude(self.store, self.home)
        self.assertFalse(self.store.meta("claude-cli-title-missing:" + sid))

    def test_stale_negative_cache_migration_clears_when_transcript_exists(self):
        # 旧版负缓存无条件永久记忆（codex 评审 P2：升级路径上仍锁死标题）。
        # 迁移清掉「转写现已存在」的陈旧标记，确实缺失的保留。
        from migrations import run as run_migrations

        stale = "88888888-8888-8888-8888-888888888888"
        keep = "99999999-9999-9999-9999-999999999999"
        transcript = self.home / ".claude/projects/proj" / f"{stale}.jsonl"
        transcript.parent.mkdir(parents=True)
        transcript.write_text(
            json.dumps(
                {
                    "type": "user",
                    "message": {"content": "迁移后补到的标题"},
                    "sessionId": stale,
                }
            )
            + "\n"
        )
        for sid in (stale, keep):
            self.store.patch("claude", sid, locator={"kind": "cli", "cwd": "/work/old"})
            self.store.set_meta("claude-cli-title-missing:" + sid, True)
        run_migrations(self.store, self.home)
        self.assertFalse(self.store.meta("claude-cli-title-missing:" + stale))
        self.assertTrue(self.store.meta("claude-cli-title-missing:" + keep))
        collect_claude(self.store, self.home)
        row = next(r for r in self.store.rows() if r["session_id"] == stale)
        self.assertEqual(row["title"], "迁移后补到的标题")

    def test_titled_row_gets_locator_backfilled(self):
        # codex 二次评审 P2：已有真标题的行同样要回填 locator（导航的 lsof fd 匹配
        # 依赖转写路径）；标题 continue 前置曾让这类行永远跳过回填。
        sid = "aaaaaaaa-1111-2222-3333-444444444444"
        transcript = self.home / ".claude/projects/proj" / f"{sid}.jsonl"
        transcript.parent.mkdir(parents=True)
        transcript.write_text(
            json.dumps(
                {
                    "type": "user",
                    "message": {"content": "首条提问"},
                    "sessionId": sid,
                }
            )
            + "\n"
        )
        self.store.patch("claude", sid, locator={"kind": "cli", "cwd": "/work/cli"})
        self.store.patch("claude", sid, title="已是真标题")  # 无 locator.file
        receive(self.home / "state", "claude", sid, "SessionEnd")
        collect_claude(self.store, self.home)
        row = next(r for r in self.store.rows() if r["session_id"] == sid)
        self.assertEqual(row["title"], "已是真标题")  # 不被覆盖
        self.assertEqual(row["locator"].get("file"), str(transcript))

    def test_titled_row_gets_stale_locator_corrected(self):
        # locator 过期的真标题行同样要纠正为真实路径（导航会拿过期路径做 fd 匹配）。
        sid = "aaaaaaaa-5555-6666-7777-888888888888"
        transcript = self.home / ".claude/projects/proj" / f"{sid}.jsonl"
        transcript.parent.mkdir(parents=True)
        transcript.write_text(
            json.dumps(
                {
                    "type": "user",
                    "message": {"content": "首条提问"},
                    "sessionId": sid,
                }
            )
            + "\n"
        )
        self.store.patch(
            "claude",
            sid,
            locator={
                "kind": "cli",
                "cwd": "/work/cli",
                "file": str(self.home / ".claude/projects/gone/old.jsonl"),
            },
        )
        self.store.patch("claude", sid, title="已是真标题")
        receive(self.home / "state", "claude", sid, "SessionEnd")
        collect_claude(self.store, self.home)
        row = next(r for r in self.store.rows() if r["session_id"] == sid)
        self.assertEqual(row["locator"].get("file"), str(transcript))

    def test_live_row_retries_after_transcript_lands(self):
        # 2026-09-20 实测回归：转写在进程存活数秒后才落盘，早于落盘的取标题
        # 曾被永久负缓存锁死在兜底格式（node 时代之后再无 cli 行取到真标题）。
        # 真实拓扑：hook 建行必带事件态（SessionStart→idle），活会话不缓存。
        sid = "33333333-3333-3333-4444-555555555555"
        self.store.patch("claude", sid, locator={"kind": "cli", "cwd": "/work/cli"})
        receive(self.home / "state", "claude", sid, "SessionStart")
        health = collect_claude(self.store, self.home)
        self.assertEqual(health["cli_titled"], 0)
        self.assertFalse(self.store.meta("claude-cli-title-missing:" + sid))
        transcript = self.home / ".claude/projects/proj" / f"{sid}.jsonl"
        transcript.parent.mkdir(parents=True)
        transcript.write_text(
            json.dumps(
                {
                    "type": "user",
                    "message": {"content": "落盘后补到的首条提问"},
                    "sessionId": sid,
                }
            )
            + "\n"
        )
        health = collect_claude(self.store, self.home)
        self.assertEqual(health["cli_titled"], 1)
        row = next(r for r in self.store.rows() if r["session_id"] == sid)
        self.assertEqual(row["title"], "落盘后补到的首条提问")

    def test_locator_file_path_preferred_for_title(self):
        # hook 带来的转写路径优先于按 sid 全目录查找（文件名与 sid 无关也能取到）。
        sid = "44444444-4444-4444-4444-555555555555"
        transcript = self.home / ".claude/projects/proj/renamed.jsonl"
        transcript.parent.mkdir(parents=True)
        transcript.write_text(
            json.dumps(
                {
                    "type": "user",
                    "message": {"content": "经 locator 路径取到的标题"},
                    "sessionId": sid,
                }
            )
            + "\n"
        )
        self.store.patch(
            "claude",
            sid,
            locator={"kind": "cli", "cwd": "/work/cli", "file": str(transcript)},
        )
        receive(self.home / "state", "claude", sid, "SessionEnd")
        health = collect_claude(self.store, self.home)
        self.assertEqual(health["cli_titled"], 1)
        row = next(r for r in self.store.rows() if r["session_id"] == sid)
        self.assertEqual(row["title"], "经 locator 路径取到的标题")

    def test_stale_locator_falls_back_to_glob(self):
        # 2026-09-21 评审修复：locator 路径失效但按 sid 可找到时必须回退 glob，
        # 否则已结束会话会被永久负缓存重新锁死在兜底格式；且 locator 要纠正为真实路径。
        sid = "55555555-5555-5555-5555-555555555555"
        transcript = self.home / ".claude/projects/proj" / f"{sid}.jsonl"
        transcript.parent.mkdir(parents=True)
        transcript.write_text(
            json.dumps(
                {
                    "type": "user",
                    "message": {"content": "locator 过期后按 sid 找回的标题"},
                    "sessionId": sid,
                }
            )
            + "\n"
        )
        self.store.patch(
            "claude",
            sid,
            locator={
                "kind": "cli",
                "cwd": "/work/cli",
                "file": str(self.home / ".claude/projects/gone/old.jsonl"),
            },
        )
        receive(self.home / "state", "claude", sid, "SessionEnd")
        health = collect_claude(self.store, self.home)
        self.assertEqual(health["cli_titled"], 1)
        row = next(r for r in self.store.rows() if r["session_id"] == sid)
        self.assertEqual(row["title"], "locator 过期后按 sid 找回的标题")
        self.assertEqual(row["locator"]["file"], str(transcript))
        self.assertFalse(self.store.meta("claude-cli-title-missing:" + sid))


class CodexCliInclusionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.store = Store(self.home / "state")
        (self.home / ".codex/sessions/2026").mkdir(parents=True)
        (self.home / ".codex").mkdir(exist_ok=True)
        # 预置 CLI 基线为一小时前：此刻之后的完成应抬升待查看。
        self.store.set_meta("codex-cli:baseline", time.time() - 3600)

    def rollout(self, sid, originator, events, meta_extra=None):
        path = self.home / ".codex/sessions/2026" / f"rollout-{sid}.jsonl"
        payload = {
            "id": sid,
            "originator": originator,
            "cwd": "/work/codexcli",
        }
        payload.update(meta_extra or {})
        body = [json.dumps({"type": "session_meta", "payload": payload})]
        for kind, stamp in events:
            body.append(
                json.dumps(
                    {
                        "type": "event_msg",
                        "payload": {
                            "type": kind,
                            "event_id": f"{sid}:{kind}:{stamp}",
                            "turn_id": "turn-1",
                        },
                        "timestamp": stamp,
                    }
                )
            )
        path.write_text("\n".join(body) + "\n")

    def test_cli_originator_session_included_with_resume_locator(self):
        done = time.time() - 60
        self.rollout(
            "cli-codex-1",
            "codex_cli_rs",
            [("task_started", done - 30), ("task_complete", done)],
        )
        health = collect_codex(self.store, self.home)
        self.assertEqual(health["status"], "ok")
        row = self.store.rows()[0]
        self.assertEqual(row["session_id"], "cli-codex-1")
        self.assertEqual(row["state"], "idle")
        self.assertTrue(row["unread"])  # 基线后的完成 → 待查看
        self.assertEqual(row["locator"]["kind"], "cli")
        self.assertEqual(row["locator"]["cwd"], "/work/codexcli")
        self.assertTrue(row["locator"]["file"].endswith("rollout-cli-codex-1.jsonl"))
        self.assertTrue(display_rows(self.store, all_rows=True)[0]["open_available"])

    def _turns(self, count=1):
        done = time.time() - 60
        events = []
        for i in range(count):
            events.append(("task_started", done - 120 + i))
            events.append(("task_complete", done - 60 + i))
        return events

    def test_string_source_vscode_still_user_with_url(self):
        # codex 复审 P1：source 也可能是普通字符串（实测 267/616 个，如 "vscode"），
        # 类型不设防会让采集/迁移抛 AttributeError。
        self.rollout(
            "vscode-sess-1",
            "Codex Desktop",
            self._turns(),
            meta_extra={"source": "vscode"},
        )
        collect_codex(self.store, self.home)
        row = self.store.rows()[0]
        self.assertEqual(row["origin"], "user")
        self.assertEqual(row["locator"]["kind"], "url")

    def test_guardian_subagent_hidden_without_jump(self):
        # 图 1 回归：guardian 审查子会话按 originator 误判人工 + 生成 Desktop 不识别
        # 的 codex:// 深链（不在 session_index，跳转必死）。
        sid = "guardian-1"
        self.rollout(
            sid,
            "Codex Desktop",
            self._turns(),
            meta_extra={
                "source": {"subagent": {"other": "guardian"}},
                "parent_thread_id": "thread-parent",
                "thread_source": "guardian_review",
            },
        )
        collect_codex(self.store, self.home)
        row = self.store.rows()[0]
        self.assertEqual(row["origin"], "agent")
        self.assertEqual(row["locator"], {})
        self.assertFalse(display_rows(self.store, all_rows=True))
        audit = display_rows(self.store, all_rows=True, include_agents=True)
        self.assertEqual(audit[0]["session_id"], sid)
        self.assertFalse(audit[0]["open_available"])

    def test_thread_spawn_unindexed_loses_jump(self):
        self.rollout(
            "spawn-1",
            "Codex Desktop",
            self._turns(),
            meta_extra={
                "source": {"subagent": {"thread_spawn": {"depth": 1}}},
            },
        )
        collect_codex(self.store, self.home)
        row = self.store.rows()[0]
        self.assertEqual(row["origin"], "agent")
        self.assertEqual(row["locator"], {})

    def test_indexed_thread_spawn_keeps_url(self):
        # codex 复审 P1：2 条 thread_spawn 已进 session_index 且可正常打开——
        # 身份归 agent，可导航性保留。
        sid = "spawn-idx-1"
        index = self.home / ".codex/session_index.jsonl"
        index.write_text(
            json.dumps(
                {
                    "id": sid,
                    "thread_name": "已索引的子代理任务",
                    "updated_at": "2026-09-21T00:00:00Z",
                }
            )
            + "\n"
        )
        self.rollout(
            sid,
            "Codex Desktop",
            self._turns(),
            meta_extra={"source": {"subagent": {"thread_spawn": {"depth": 1}}}},
        )
        collect_codex(self.store, self.home)
        row = self.store.rows()[0]
        self.assertEqual(row["origin"], "agent")
        self.assertEqual(row["locator"]["kind"], "url")
        self.assertIn(sid, row["locator"]["url"])
        self.assertEqual(row["title"], "已索引的子代理任务")

    def test_cli_subagent_keeps_cli_locator(self):
        # CLI 变体（codex_exec 等）即使带 subagent 标记也保留 cli 定位（可恢复）。
        sid = "exec-sub-1"
        self.rollout(
            sid,
            "codex_exec",
            self._turns(),
            meta_extra={"source": {"subagent": {"other": "guardian"}}},
        )
        collect_codex(self.store, self.home)
        row = self.store.rows()[0]
        self.assertEqual(row["origin"], "agent")
        self.assertEqual(row["locator"]["kind"], "cli")

    def test_legacy_thread_source_marker_recognized(self):
        # 旧版（<0.155）只有 thread_source="guardian_review"/"subagent"，无 source 字典。
        self.rollout(
            "legacy-guard-1",
            "Codex Desktop",
            self._turns(),
            meta_extra={"thread_source": "guardian_review"},
        )
        collect_codex(self.store, self.home)
        row = self.store.rows()[0]
        self.assertEqual(row["origin"], "agent")
        self.assertEqual(row["locator"], {})

    def test_subagent_migration_fixes_and_scopes(self):
        from migrations import run as run_migrations

        self.rollout(
            "mig-guard-1",
            "Codex Desktop",
            self._turns(),
            meta_extra={"source": {"subagent": {"other": "guardian"}}},
        )
        self.rollout("mig-normal-1", "Codex Desktop", self._turns())
        collect_codex(self.store, self.home)
        # 迁移前：guardian 行是 user + 死链
        guard = next(r for r in self.store.rows() if r["session_id"] == "mig-guard-1")
        self.assertEqual(guard["origin"], "agent")  # 采集器新逻辑已按 agent 落库
        # 幂等：跑两遍，普通行不被触碰
        run_migrations(self.store, self.home)
        run_migrations(self.store, self.home)
        normal = next(r for r in self.store.rows() if r["session_id"] == "mig-normal-1")
        self.assertEqual(normal["origin"], "user")
        self.assertEqual(normal["locator"]["kind"], "url")

    def test_subagent_migration_uses_payload_id_not_filename(self):
        # 复合文件名（<父>_<分身>）下迁移必须以 payload.id 为身份，不为文件名尾建行。
        from migrations import run as run_migrations

        parent = "aaaaaaaa-7777-4111-8111-111111111111"
        tail = "aaaaaaaa-8888-4222-8222-222222222222"
        body = [
            json.dumps(
                {
                    "type": "session_meta",
                    "payload": {
                        "id": parent,
                        "originator": "Codex Desktop",
                        "cwd": "/work/x",
                        "source": {"subagent": {"other": "guardian"}},
                    },
                }
            )
        ]
        path = (
            self.home
            / ".codex/sessions/2026"
            / f"rollout-2026-09-21T01-00-00-{parent}_{tail}.jsonl"
        )
        path.write_text("\n".join(body) + "\n")
        self.store.patch(
            "codex", parent, locator={"kind": "url", "url": f"codex://threads/{parent}"}
        )
        run_migrations(self.store, self.home)
        rows = {
            r["session_id"]: r for r in self.store.rows() if r["provider"] == "codex"
        }
        self.assertIn(parent, rows)
        self.assertNotIn(tail, rows)  # 不为文件名尾建行
        self.assertEqual(rows[parent]["origin"], "agent")
        self.assertEqual(rows[parent]["locator"], {})

    def test_cli_composite_filename_backfill_uses_tail_no_parent_ghost(self):
        # codex 复审 P2：CLI 分叉合同是独立子会话行（文件名尾身份），backfill 若按
        # Desktop 规则取 payload.id 会为父 id 建幽灵行（unknown + 空 locator）。
        from migrations import run as run_migrations

        parent = "aaaaaaaa-9999-4111-8111-111111111111"
        child = "aaaaaaaa-aaaa-4222-8222-222222222222"
        done = time.time() - 60
        body = [
            json.dumps(
                {
                    "type": "session_meta",
                    "payload": {
                        "id": parent,
                        "originator": "codex_exec",
                        "cwd": "/work/cli-fork",
                    },
                }
            ),
            json.dumps(
                {
                    "type": "event_msg",
                    "payload": {"type": "task_started", "turn_id": "t1"},
                    "timestamp": done - 30,
                }
            ),
            json.dumps(
                {
                    "type": "event_msg",
                    "payload": {"type": "task_complete", "turn_id": "t1"},
                    "timestamp": done,
                }
            ),
        ]
        path = (
            self.home
            / ".codex/sessions/2026"
            / f"rollout-2026-09-21T02-00-00-{parent}_{child}.jsonl"
        )
        path.write_text("\n".join(body) + "\n")
        collect_codex(self.store, self.home)
        run_migrations(self.store, self.home)
        rows = {
            r["session_id"]: r for r in self.store.rows() if r["provider"] == "codex"
        }
        self.assertNotIn(parent, rows)  # 不产生父幽灵行
        self.assertIn(child, rows)  # 子会话行完整：状态 + cli 定位
        self.assertEqual(rows[child]["state"], "idle")
        self.assertEqual(rows[child]["locator"]["kind"], "cli")

    def test_desktop_resume_fork_attributed_to_parent_thread(self):
        # 2026-09-21 实测回归（用户报"会话找不到"+"跳转报错"）：Desktop 分叉文件
        # （<父id>_<分身id> 文件名）只写祖先 meta、自身 id 仅在文件名尾。回合归属
        # 父线程——session_index 只登记父线程、codex:// 跳转不识别分身 id；一行
        # 一对话，父行被分叉回合延续（不落 interrupted 终态）。
        parent = "11111111-1111-4111-8111-111111111111"
        fork = "22222222-2222-4222-8222-222222222222"
        path = (
            self.home
            / ".codex/sessions/2026"
            / f"rollout-2026-09-21T00-51-18-{parent}_{fork}.jsonl"
        )
        body = [
            json.dumps(
                {
                    "type": "session_meta",
                    "payload": {
                        "id": parent,
                        "originator": "Codex Desktop",
                        "cwd": "/work/resume",
                    },
                }
            ),
            json.dumps(
                {
                    "type": "event_msg",
                    "payload": {"type": "task_started", "turn_id": "t1"},
                    "timestamp": time.time() - 120,
                }
            ),
            json.dumps(
                {
                    "type": "event_msg",
                    "payload": {"type": "task_complete", "turn_id": "t1"},
                    "timestamp": time.time() - 60,
                }
            ),
        ]
        path.write_text("\n".join(body) + "\n")
        collect_codex(self.store, self.home)
        rows = self.store.rows()
        row = next(r for r in rows if r["session_id"] == parent)
        self.assertEqual(row["state"], "idle")
        self.assertEqual(row["locator"]["kind"], "url")
        self.assertIn(parent, row["locator"]["url"])
        self.assertEqual(row["project"], "/work/resume")
        # 分身 id 不建独立行（Desktop 侧栏与跳转均不识别）。
        self.assertFalse(any(r["session_id"] == fork for r in rows))

    def test_historical_cli_completion_before_baseline_stays_silent(self):
        self.rollout(
            "cli-codex-old",
            "codex_exec",
            [
                ("task_started", time.time() - 7200),
                ("task_complete", time.time() - 7100),
            ],
        )
        collect_codex(self.store, self.home)
        row = self.store.rows()[0]
        self.assertFalse(row["unread"])

    def test_desktop_originator_still_uses_url_locator(self):
        sid = "dddddddd-1111-2222-3333-444444444444"
        self.rollout(sid, "Codex Desktop", [("task_complete", time.time() - 60)])
        collect_codex(self.store, self.home)
        row = self.store.rows()[0]
        self.assertEqual(row["locator"]["kind"], "url")

    def test_workbench_originator_marked_agent_and_hidden_from_display(self):
        # 多 agent 工具拉起的会话仍走完整事件链（含待查看抬升），但默认不出现在收件箱。
        self.rollout(
            "wb-1", "coding-agent-workbench", [("task_complete", time.time() - 60)]
        )
        collect_codex(self.store, self.home)
        row = self.store.rows()[0]
        self.assertEqual(row["origin"], "agent")
        self.assertTrue(row["unread"])
        self.assertEqual(display_rows(self.store, all_rows=True), [])
        self.assertEqual(
            len(display_rows(self.store, all_rows=True, include_agents=True)), 1
        )

    def test_interactive_tui_originators_marked_user(self):
        for index, originator in enumerate(("codex-tui", "codex_cli_rs")):
            self.rollout(
                f"tui-{index}", originator, [("task_complete", time.time() - 60)]
            )
        collect_codex(self.store, self.home)
        origins = {row["session_id"]: row["origin"] for row in self.store.rows()}
        self.assertEqual(origins, {"tui-0": "user", "tui-1": "user"})
        # 存量回填迁移已收敛到 migrations.run（随 refresh 执行）；此处直接验证幂等契约。
        codex_origin_backfill(self.store, self.home)
        self.assertTrue(self.store.meta("codex-origin:backfill-v1"))
        codex_origin_backfill(self.store, self.home)  # 幂等：再次执行不改动
        self.assertEqual(
            {row["session_id"]: row["origin"] for row in self.store.rows()},
            {"tui-0": "user", "tui-1": "user"},
        )

    def test_origin_backfill_tags_existing_rows(self):
        # 一次性迁移：先有库后加白名单判定的场景，回填按 rollout 首行补 origin。
        self.rollout(
            "legacy-exec", "codex_exec", [("task_complete", time.time() - 3600)]
        )
        collect_codex(self.store, self.home)
        row = self.store.rows()[0]
        self.assertEqual(row["origin"], "agent")


class CodexDanglingTurnTests(unittest.TestCase):
    """codex 悬置回合收口（2026-09-20）：进程被硬杀的回合在 rollout 里没有终态事件，
    running 永久滞留（真机存量 6 条，最早 2026-07-24），「进行中」视图放大了暴露。
    三种拓扑：①mtime 超 24h 兜底收口；②重开标记（thread_settings_applied）即时收口；
    ③活回合（文件持续流式写行）保持 running 不误伤。另含一次性存量迁移。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.store = Store(self.home / "state")
        (self.home / ".codex/sessions/2026").mkdir(parents=True)
        self.store.set_meta("codex-cli:baseline", time.time() - 3600)

    def rollout(self, sid, lines):
        path = self.home / ".codex/sessions/2026" / f"rollout-{sid}.jsonl"
        body = [
            json.dumps(
                {
                    "type": "session_meta",
                    "payload": {
                        "id": sid,
                        "originator": "codex_cli_rs",
                        "cwd": "/work",
                    },
                }
            )
        ]
        for record in lines:
            body.append(json.dumps(record))
        path.write_text("\n".join(body) + "\n")
        return path

    @staticmethod
    def turn(kind, turn_id, stamp):
        return {
            "type": "event_msg",
            "payload": {"type": kind, "turn_id": turn_id},
            "timestamp": stamp,
        }

    @staticmethod
    def marker(stamp):
        return {
            "type": "event_msg",
            "payload": {"type": "thread_settings_applied"},
            "timestamp": stamp,
        }

    def test_unterminated_turn_with_stale_mtime_repaired(self):
        # 拓扑①：悬置开始 + 文件 25h 无新行（活回合会持续流式写 token_count 行）。
        path = self.rollout(
            "stale-1", [self.turn("task_started", "t1", time.time() - 26 * 3600)]
        )
        os.utime(path, (time.time() - 25 * 3600,) * 2)
        collect_codex(self.store, self.home)
        row = self.store.rows()[0]
        self.assertEqual(row["state"], "interrupted")
        self.assertFalse(row["unread"])  # 修复不抬升待查看、不声明成功

    def test_reopen_marker_closes_dangling_turn(self):
        # 拓扑②：悬置开始 + 此后出现重开标记（真机 019e8e32：7 月悬置、9 月 20 重开）。
        self.rollout(
            "reopen-1",
            [
                self.turn("task_started", "t1", time.time() - 7200),
                self.marker(time.time() - 60),
            ],
        )
        collect_codex(self.store, self.home)
        self.assertEqual(self.store.rows()[0]["state"], "interrupted")

    def test_live_turn_keeps_running(self):
        # 拓扑③：悬置开始但文件新鲜（活回合流式写行中），保持 running 不误伤。
        self.rollout("live-1", [self.turn("task_started", "t1", time.time() - 60)])
        collect_codex(self.store, self.home)
        self.assertEqual(self.store.rows()[0]["state"], "running")

    def test_next_turn_closes_previous_dangling_turn(self):
        # 采集器内联规则：新回合开始即证明旧悬置回合已死（同线程不并发两回合），
        # 合成 turn_aborted 收口旧回合后再开始新回合。
        self.rollout(
            "next-1",
            [
                self.turn("task_started", "t1", time.time() - 7200),
                self.turn("task_started", "t2", time.time() - 60),
                self.turn("task_complete", "t2", time.time() - 30),
            ],
        )
        collect_codex(self.store, self.home)
        row = self.store.rows()[0]
        self.assertEqual(row["state"], "idle")

        from codex_rollout_events import RolloutReader

        reader = RolloutReader(
            self.home / ".codex/sessions/2026" / "rollout-next-1.jsonl",
            "next-1",
            originator="codex_cli_rs",
        )
        events = reader.poll()
        self.assertEqual(
            [event["event"] for event in events],
            ["task_started", "turn_aborted", "task_started", "task_complete"],
        )

    def test_migration_repairs_rows_whose_marker_was_already_consumed(self):
        # 一次性迁移：旧采集器已读过重开标记但没处理（游标在标记之后），内联规则
        # 不会再触发；迁移按文件内容直接修复存量行，幂等由守护键保证。
        path = self.rollout(
            "legacy-1",
            [
                self.turn("task_started", "t1", time.time() - 7200),
                self.marker(time.time() - 60),
            ],
        )
        self.store.patch(
            "codex", "legacy-1", locator={"kind": "cli", "cwd": "", "file": str(path)}
        )
        self.store.event(
            "codex",
            "legacy-1",
            event_id="legacy-1:t1:task_started",
            timestamp=time.time() - 7200,
            state="running",
        )
        codex_dangling_turn_repair(self.store, self.home)
        self.assertEqual(self.store.rows()[0]["state"], "interrupted")
        # 幂等：守护键命中后再次执行不再改动。
        self.store.event(
            "codex",
            "legacy-1",
            event_id="legacy-1:t2:task_started",
            timestamp=time.time() - 30,
            state="running",
        )
        codex_dangling_turn_repair(self.store, self.home)
        self.assertEqual(self.store.rows()[0]["state"], "running")

    def test_migration_skips_live_dangling_turn(self):
        # 迁移不凭静默误伤：无重开标记且文件新鲜（可能正在跑）的悬置行不动。
        self.rollout("legacy-live", [self.turn("task_started", "t1", time.time() - 60)])
        self.store.event(
            "codex",
            "legacy-live",
            event_id="legacy-live:t1:task_started",
            timestamp=time.time() - 60,
            state="running",
        )
        codex_dangling_turn_repair(self.store, self.home)
        self.assertEqual(self.store.rows()[0]["state"], "running")


class CliOpenTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.store = Store(self.home / "state")
        self.demo = self.home / "work" / "demo"
        self.demo.mkdir(parents=True)

    def cli_row(self, provider):
        self.store.patch(
            provider,
            "sess-cli",
            project=str(self.demo),
            locator={"kind": "cli", "cwd": str(self.demo)},
        )
        return self.store.rows()[0]

    def test_open_focuses_live_process_by_command_match(self):
        # 经包装器恢复的会话进程命令行携带会话 ID：直接聚焦原标签，不再新开。
        row = self.cli_row("claude")
        with (
            patch("inbox.ttys_for_command", return_value=["ttys011"]) as ttys,
            patch("inbox.iterm_select_tty", return_value=True) as focus,
            patch("inbox.launch_agent") as launch,
            patch("sys.stdout"),
        ):
            code = open_session(self.store, row["id"], row["revision"])
        self.assertEqual(code, 0)
        ttys.assert_called_once_with(("--resume", "sess-cli"))
        focus.assert_called_once_with("ttys011")
        launch.assert_not_called()

    def test_open_focuses_by_open_session_file_fd(self):
        # 用户自行启动的会话（命令行无会话 ID）：运行中持有转写/rollout fd，按 fd 定位。
        row = self.cli_row("codex")
        self.store.patch(
            "codex",
            "sess-cli",
            locator={
                "kind": "cli",
                "cwd": str(self.demo),
                "file": "/sessions/rollout-x.jsonl",
            },
        )
        row = self.store.rows()[0]
        with (
            patch("inbox.ttys_for_command", return_value=[]),
            patch("inbox.ttys_for_open_file", return_value=["ttys009"]) as filettys,
            patch("inbox.iterm_select_tty", return_value=True) as focus,
            patch("inbox.launch_agent") as launch,
            patch("sys.stdout"),
        ):
            code = open_session(self.store, row["id"], row["revision"])
        self.assertEqual(code, 0)
        filettys.assert_called_once_with("/sessions/rollout-x.jsonl")
        focus.assert_called_once_with("ttys009")
        launch.assert_not_called()

    def test_claude_resume_dispatch(self):
        row = self.cli_row("claude")
        with patch("inbox.launch_agent") as launch, patch("sys.stdout"):
            code = open_session(self.store, row["id"], row["revision"])
        self.assertEqual(code, 0)
        self.assertEqual(launch.call_args[0], ("claude", str(self.demo)))
        self.assertEqual(launch.call_args[1], {"args": ("--resume", "sess-cli")})

    def test_codex_resume_dispatch(self):
        row = self.cli_row("codex")
        with patch("inbox.launch_agent") as launch, patch("sys.stdout"):
            code = open_session(self.store, row["id"], row["revision"])
        self.assertEqual(code, 0)
        self.assertEqual(launch.call_args[0], ("codex", str(self.demo)))
        self.assertEqual(launch.call_args[1], {"args": ("resume", "sess-cli")})


class ClaudeNativeSpawnOriginTests(unittest.TestCase):
    """原生二进制（ucomm=版本号）直接派生 hook 的回归拓扑。

    2026-09-20 实测：claude 2.1.277 原生安装器版本不经 sh 包装直接 exec 派生
    hook，旧判定把 hook 父进程（claude 本体，ucomm "2.1.277"）当启动方，
    终端会话全被误判 agent（store 自 09-19 升级起无一条 user 的 cli 行）。
    """

    def ps_table(self, table):
        from subprocess import CompletedProcess

        def fake(cmd, **kwargs):
            # cmd 形如 ["ps", "-o", <fields>, "-p", <pid>]
            return CompletedProcess(cmd, 0, stdout=table.get((cmd[-1], cmd[2]), ""))

        return fake

    def origin(self, table, sid="regress-1"):
        from inbox import claude_spawn_origin

        with (
            patch("inbox.subprocess.run", side_effect=self.ps_table(table)),
            patch("inbox.os.getppid", return_value=111),
            patch.dict(os.environ, {}, clear=False),
        ):
            os.environ.pop("SESSION_MANAGER_ORIGIN", None)
            return claude_spawn_origin(sid)

    def test_native_binary_in_terminal_is_user(self):
        self.assertEqual(
            self.origin(
                {
                    ("111", "tty=,ucomm=,ppid="): "ttys001 2.1.277 110\n",
                    ("110", "ucomm="): "zsh\n",
                }
            ),
            "user",
        )

    def test_native_binary_launched_by_tool_is_agent(self):
        self.assertEqual(
            self.origin(
                {
                    ("111", "tty=,ucomm=,ppid="): "ttys001 2.1.277 110\n",
                    ("110", "ucomm="): "node\n",
                }
            ),
            "agent",
        )

    def test_native_binary_headless_without_registry_is_agent(self):
        # 无头形态不触发第二次 ps：表里没有 (110, ucomm=) 也能走完。
        with patch("inbox.desktop_registry_hit", return_value=False):
            self.assertEqual(
                self.origin({("111", "tty=,ucomm=,ppid="): "?? claude 110\n"}),
                "agent",
            )

    def test_native_binary_headless_registry_hit_is_user(self):
        with patch("inbox.desktop_registry_hit", return_value=True):
            self.assertEqual(
                self.origin({("111", "tty=,ucomm=,ppid="): "?? 2.1.277 110\n"}),
                "user",
            )

    def test_legacy_shell_wrapper_still_user(self):
        # 旧 node 版经 sh 包装：hook 父进程即 sh，不再上移一层。
        self.assertEqual(
            self.origin({("111", "tty=,ucomm=,ppid="): "ttys001 sh 110\n"}), "user"
        )

    def test_garbage_ps_output_keeps_user(self):
        self.assertEqual(self.origin({("111", "tty=,ucomm=,ppid="): "\n"}), "user")


if __name__ == "__main__":
    unittest.main()
