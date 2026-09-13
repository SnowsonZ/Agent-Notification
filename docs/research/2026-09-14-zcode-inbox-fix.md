# Zcode 完成任务未进入待处理列表

状态：已修复，真实队列和 UI 验证通过。

## 原因

旧 collector 把任务索引 unread_at 当作唯一待处理条件。本机当时 64 个未归档任务的该字段全部为空，收件箱因此也全部是已读。原 App 的蓝点和本收件箱的待处理语义不等价。

另一个问题是使用任务 updated_at 作为事件时间；该字段会在改名等操作后前移，可能压过真正完成时间，造成补回的结束事件被误判为过期。

## 新证据与修复

只读检查 `.zcode/cli/db/db.sqlite`，发现 turn_usage 含 session_id、turn_id、status、started_at、completed_at；本机观察到 completed/error/cancelled 状态，70 个 session ID 与任务索引精确匹配。没有读取 message/part 正文。

[collector](../../scripts/inbox_sources.py)现在根据最新轮次判定运行和完成。监控开始后的 completed/error 进入待处理，cancelled 不作为成功完成；改名不通知，重复刷新不重置已处理，原 App 蓝点清除不替用户确认。本机后端自动读取新脚本，无需重启原 App。

任务索引仍负责标题、工作区、归档与定位；last_unread_at 的持久标记作为补充。运行数据库不存在或格式不支持时降级到快照并明确显示来源状态，不靠 updated_at 猜完成。

对旧版收件箱逐会话迁移事件时钟，以免历史元数据时间屏蔽真实完成；迁移通过 metadata 唯一键只执行一次，revision 同步增加以阻止旧 UI 确认吞掉迁移后的新事件。

## 验证

- [新增六项回归](../../tests/test_zcode_inbox.py)：无原生未读仍收完成；确认后刷新/改名不重新点亮；下一轮重新进入；历史/子任务不过量导入；失败/取消区分；旧时钟迁移与原生已读不联动。
- 修复前对应回归出现 3 个失败；修复后全部 50 项检查通过。
- 实际只读采集识别 73 个索引任务、70 个有轮次数据的 session，补回 1 条 Zcode 待处理。
- 实际原生界面显示 Pi、Kimi 和 Zcode 待处理项，Zcode 条目状态为“本轮已结束”。未代用户点击已处理，也未修改 Zcode 数据库。
