# 审计修复与验收清单

本轮代码修复已完成，冻结版本通过93项Python测试、28项Node合成测试，生产快照迁移23项检查通过，且已部署验证HTTPS登录/会话/页面和schema3。A01–A20的代码与隔离验收覆盖见对应tests；不代表真实QQ完整收发或旧安卓真机验收。下表仍保留目标和无法代替用户完成的边界。

| 编号 | 审计项 | 目标实现/验收 |
|---|---|---|
| A01 | 64联系人初始化溢出 | 分批/有界背压；80联系人隔离真实WS及131联系人前端合成测试 |
| A02 | 注销WS/bridge继续有效 | 会话所有者绑定、过期与注销关闭；已建立连接回归 |
| A03 | token URL日志 | Cookie会话、SafeAccessLogger；反代独立脱敏，旧日志权限说明 |
| A04 | 全局ALL/引用绕前缀 | 管理员私聊显式ON/OFF、按目标保存，reply仍需前缀 |
| A05 | NR附件跨会话 | target/session/upload_id绑定、晚回调回归 |
| A06 | loading正文清屏 | 独立状态节点，正文及多图历史测试 |
| A07 | 慢媒体/断线丢事件 | 持久inbox、媒体独立worker、重启恢复和背压 |
| A08 | local/platform ID | 稳定内部ID、typed会话、平台映射、quote namespace与外部撤回 |
| A09 | 文件部分成功漏记 | parts持久记录及partial状态、未知不盲重试 |
| A10 | history/增量重复 | revision边界、串行发布、UI按ID去重/upsert |
| A11 | 在线状态错乱 | Web连接/NapCat socket/QQonline三态、离线恢复心跳 |
| A12 | 多设备设置覆盖ACL | revision CAS+changed fields、409可恢复反馈 |
| A13 | send背压超时 | 总deadline含send+echo，断线取消清理 |
| A14 | bridge4/8MiB冲突 | 统一内部桥接transport、字节/会话总限 |
| A15 | 裸数值会话冲突 | private/group键；孤儿legacy只读、迁移保留记录 |
| A16 | 昵称判断自己 | direction/sender_id，不信昵称 |
| A17 | 非法JSON500 | 小body、对象/schema校验、规范错误 |
| A18 | 坏嵌套迁移 | 严格校验、事务回滚、原库保留 |
| A19 | finally锁泄漏 | release放无await finally，取消分支回归 |
| A20 | 撤回附件/quote残留 | 本地字段与引用清理，说明远程下载不可销毁 |
| 运维 | root/资源/备份 | 专用用户、systemd保护、配额、健康/就绪、备份恢复 |
| 工程 | 单文件/无CI/外部CDN | 拆auth/storage/messaging/templates静态资源、CI与锁文件、本地CSS |
| 真机 | NR兼容 | ES5合成测试不等于Android5.1真机验收，需用户验证 |
| 许可 | LICENSE/提示音权利 | 原创代码和文档已采用MIT；第三方资源仍遵循各自权利与许可 |
