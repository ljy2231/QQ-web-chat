# 运维、迁移与回滚

## 单进程和数据目录

使用一份服务进程管理一个数据目录。不要启动多个worker共用数据库：会话、连接和任务管理仍为单进程模型，数据库文件锁会拒绝第二个writer。数据目录包含 SQLite、附件和锁文件，必须可写且只对服务用户开放。

首次升级从旧 SQLite `storage` 表或旧 JSON 导入行级表。迁移事务失败时保持旧源不变；不得删除 `initialized` 或用空库覆盖。不能确认类型的旧会话保留为 `legacy:` 只读归档，禁止发往猜测目标。保留全部导入历史；新消息时仍按最近200条保留规则裁剪。

迁移前必须备份程序、环境配置、服务单元、数据库和附件。上线前在独立目录测试同一份迁移文件，核对会话/消息数量及关键字段，不输出私聊内容。

## 备份

```sh
umask 077
python tools/backup.py /path/to/chat_storage.sqlite3 /private/backup/chat-20260101.sqlite3
```

该命令使用 SQLite backup API，检查完整性并输出备份哈希，不输出聊天。目标存在时拒绝覆盖。数据库可在线备份，但附件与数据库的严格一致快照需要停止写入；附件单独复制到权限0700的备份目录。不要仅复制活动数据库本体而忽略WAL。

## 恢复与回滚

1. 停止网页写入并停止本服务（无需操作NapCat本身）。确认没有其他实例。
2. 保存当前程序、配置、数据库及sidecar，避免回滚导致新消息消失。
3. 运行 `python tools/restore.py BACKUP NEW_DB --acknowledge-service-stopped`，只生成新路径，不直接覆盖活动库。
4. 确认绝对路径、权限、属主和备份版本，再执行人工原子切换。不要把旧JSON重新覆盖到已迁移后的新数据。
5. 启动后检查 `/healthz`、`/readyz`、数据库完整性及脱敏错误统计，人工验证文字、图片、附件和撤回。

SQLite schema3 的行级表是权威数据源。回退到只读取旧 `storage` JSON 的旧程序，会忽略升级后的新记录；不能简单把旧server.py复制回来后宣称无损回滚。真正回退旧格式需专门导出/兼容验证，或在无新写入的验证窗口内回到停服备份。

## systemd

示例在 `deploy/napcat-webchat.service`。先创建专用 `webchat` 系统用户，数据置于 `/opt/chat/data` 并赋予它所有权，代码仍由root拥有且服务用户不可改。把环境文件放在 `/etc/qq-webchat.env`，权限0600，由systemd加载。不要把凭据写进单元文件或提交仓库。

`TRUSTED_PROXIES` 必须按实际反代配置。若反代在同机回环，才能填写精确回环CIDR；如果反代在Docker网络，需核实真正源地址并覆盖转发头。禁止为了修403而设全网可信。

服务有内存、任务数和文件句柄上限，以及只写数据目录的隔离。限额要结合实际负载调整，不能盲目压到正常附件都无法处理。

## 容器

```sh
cp .env.example .env
# 编辑真实凭据、HTTPS地址与可信代理；不要提交.env。
docker compose up -d --build
```

默认UID/GID10001、只读根文件系统、独立命名数据卷、无额外Linux capability、健康检查。不要与现有正式站同时占用4001；镜像测试应随机映射宿主端口。命名数据卷的删除是永久数据破坏，不要使用 `down -v` 清理线上服务。

Linux x86_64 / Python3.12可使用 `requirements-linux-x86_64.lock` 的哈希锁；其他架构按 `requirements.txt` 安装并为该平台重新生成hash锁。CSS为仓库内静态构建产物，运行时无需Node或Tailwind CDN；重新构建用 `npm ci --ignore-scripts && npm run build:css`。

## 有界恢复与幂等窗口

inbox/outbound 有行数、UTF-8字节及磁盘余量准入预算；达到容量会先回收允许删除的已完成记录，未决unknown/partial/pending不会盲目清掉。压力下已完成幂等记录可能早于30天被回收，所以request_id去重不是永久承诺；不要在窗口外重新使用旧request_id重试未知操作。已接收操作的结果提交与历史publication保留必要空间，不能把“新任务拒绝”与“远端已成功后的记账”混为一谈。

用户清空会话会同时抑制尚未发布的本地历史intent，避免重启后把刻意清掉的历史恢复。远端QQ副本仍取决于平台撤回，不等于本地clear会清除QQ聊天。

## 告警与排障

至少告警：根分区/数据卷剩余空间、服务内存、HTTP429/503/507、数据库失败、pending inbox、outbound unknown/partial、QQonline与NapCat连接、附件过期/清理错误。`active`只说明进程存在，不代表QQ在线。`/healthz`是存活检查；就绪状态应按服务返回值区分数据库和消息通道。

先只读定位空间来源，不随意删除聊天、附件或其他服务目录。旧访问日志可能含token，应收紧权限并按留存制度处置；新应用日志只记录路由模板，不记录查询串。反代日志也需同步改为脱敏格式，仓库示例不能自动改变宿主配置。

升级重启会断开网页连接并使内存会话失效；停止期间上游未送达事件能否重放取决于上游，不能承诺零丢失或exactly-once。已接收并成功持久化的inbox才有本地恢复保证。
