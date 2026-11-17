# QQ Web Chat

连接 NapCat 的单账户 QQ 网页终端，支持私聊、群聊、图片、文件、引用、撤回和全局网页设置。

- 主页面：`/web`；低依赖兼容页面：`/web/nr`。
- Python aiohttp + SQLite 行级存储；浏览器使用同源 HttpOnly 会话 Cookie。
- 本项目不是多租户平台；共享网页密码意味着共享全部聊天与管理权限。

## 快速运行

需要 Python **3.12**、已由你维护的 NapCat，以及生产环境 HTTPS 反向代理。

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
# Linux x86_64 / Python3.12 可改用带哈希锁：
# pip install --require-hashes -r requirements-linux-x86_64.lock
export WEB_PASSWORD='请设置自己的强密码'
export NAPCAT_ACCESS_TOKEN='请设置独立连接令牌'
export PUBLIC_BASE_URL='https://你的域名'
export CHAT_DATA_DIR='/你自己的私有数据目录'
# 只填实际反代源地址，示例不能不经核实直接照抄：
export TRUSTED_PROXIES='127.0.0.1/32'
export ADMIN_QQ_ID=''
python server.py
```

`PUBLIC_BASE_URL` 是 NapCat 获取附件时使用的本站公开地址，不可填别人的站点；不要依赖客户端提交的 Host 头生成文件地址。反代必须覆盖转发头并支持 WebSocket，参见 [部署与运维](OPERATIONS.md)。不配置可信代理时不会信任其 X-Forwarded-Proto/For，可能以不安全传输拒绝认证，这是防伪造措施，不应通过“信任所有IP”绕过。

NapCat 反向 WebSocket 连接 `/ws` 或 `/`，连接令牌支持 Bearer 头及兼容的 `access_token` 参数。不要在访问日志保存令牌。普通根路径 GET 不是聊天主页。

容器可复制 `.env.example` 到 `.env` 后填写，再执行 `docker compose up -d --build`。默认仅在宿主回环地址公开4001、非root运行、只读根文件系统，数据使用命名卷 `webchat-data`。不要用 `down -v` 清理线上数据。完整部署说明和恢复流程见 [OPERATIONS.md](OPERATIONS.md)，安全边界见 [SECURITY.md](SECURITY.md)。

## 消息与权限规则

白黑名单控制新的QQ入站处理，不是网页用户权限表。私聊默认需 `=` 前缀，引用消息也适用。授权群的普通消息会转发。`= ALL ON/OFF` 只允许管理员私聊使用，并只控制当前目标，不再让群成员翻转其他人的全局转发规则。管理员由 `ADMIN_QQ_ID` 指定；为空时管理命令禁用。

消息使用带类型的会话ID，内部消息ID与QQ平台ID分离；同号群聊和私聊不再混用。发送请求有唯一request_id，部分成功、失败和结果未知应独立展示；**未知结果不要盲目重试**，远端发送与本地提交无法成为一个数据库事务，项目不承诺exactly-once。

入站事件先持久化到inbox，再处理媒体和元数据；网络断开时只有已经成功落库的事件具有本地恢复保证。上游未送达、重启期间的事件能否重放取决于上游。

## 数据与升级

新库按行保存会话、消息、平台ID映射及inbox/outbound状态。首次升级可事务导入旧 `storage` JSON快照，或同目录中的旧 `chat_data.json`、`settings.json`。原源数据不自动删除；无法确定类型的孤儿历史保留为只读归档，不猜测发送目标。

导入保留已有记录，新增时沿用每会话最近200条规则。附件不是永久归档；上传上限50MiB，图片抓取另有限制，附件过期后链接失效。知道随机附件链接的人可访问，因此不要把它当作账号级ACL。撤回不能销毁对方已经下载的副本。

不再默认把客户端IP附加到QQ正文；如确实需要，明确设置 `DISCLOSE_CLIENT_IP=true` 并告知使用者。会话有到期、登录限速和注销撤销，服务重启仍会使内存登录失效。不要上传真实密码、QQ配置、数据库或附件到仓库。

## 开发与验证

```sh
python -m unittest discover -s tests -p 'test_*.py' -v
node tests/test_frontend.js
npm ci --ignore-scripts
npm run build:css
python tools/check_public_tree.py
```

CSS预编译并本地提供，不再在运行时加载Tailwind CDN脚本。Node只用于开发构建与合成测试，Python服务运行无需Node。测试中的QQ服务为mock，不能替代真实QQ、浏览器、TLS和Android设备验收。

NR保持低依赖脚本和基本聊天能力，但仍需要WebSocket、XHR、FormData、Cookie及TLS支持。请在目标旧设备上确认；不声称适用于所有Android5.1浏览器。

## 许可与第三方服务

尚未指定开源许可证，保留作者权利，不应把公开可见等同于自由再分发授权。提示音等资源的来源及分发权利需维护者确认。随机壁纸和QQ头像依赖第三方网络服务，可能不可用。
