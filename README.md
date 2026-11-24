# QQ Web Chat

连接 NapCat 的单账户 QQ 网页终端，支持私聊、群聊、图片、文件、引用、撤回和全局网页设置。

- 主页面：`/web`；低依赖兼容页面：`/web/nr`。
- Python aiohttp + SQLite 行级存储；浏览器使用同源 HttpOnly 会话 Cookie。
- 本项目不是多租户平台；共享网页密码意味着共享全部聊天与管理权限。

## 快速开始（Docker 一键部署）

一行命令，复制粘贴即可运行。需要 Linux amd64、Docker 与 Compose v2，通常用 root 执行（需要写 `/opt` 和 Docker 的权限）。

国内加速下载（已内置加速站）：

```sh
curl -fsSL https://hub.002230.xyz:44300/https://raw.githubusercontent.com/ljy2231/QQ-web-chat/main/deploy/install-cn.sh | bash
```

海外 / 直连 GHCR：

```sh
curl -fsSL https://raw.githubusercontent.com/ljy2231/QQ-web-chat/main/deploy/install.sh | bash
```

脚本会自动生成一次性初始化口令和 NapCat 连接令牌、创建数据目录、拉取镜像并启动容器，最后打印访问地址与首次设置链接。默认安装目录 `/opt/qq-webchat`，端口 `4001`，仅监听 `127.0.0.1`；重复执行会保留 `.env`、数据与令牌，并重新生成 Compose 文件。

打开脚本输出的链接，在网页中设置登录账号和密码即可（命令行不需要输入账号密码）。NapCat 需要使用 `.env` 中的 `NAPCAT_ACCESS_TOKEN`；脚本不会修改 NapCat。

默认只能从本机访问；公网使用请配置 HTTPS 反向代理，并覆盖监听地址和公开地址：

```sh
curl -fsSL https://raw.githubusercontent.com/ljy2231/QQ-web-chat/main/deploy/install.sh | \
  QQWEBCHAT_BIND=0.0.0.0 PUBLIC_BASE_URL=https://chat.example.com TRUSTED_PROXIES=127.0.0.1/32 bash
```

## 在线演示

纯静态界面演示，不需要后端，也不连接 QQ；演示密码 `admin`。

**https://ljy2231.github.io/QQ-Web-Chat-Demo/**

源码：[QQ-Web-Chat-Demo](https://github.com/ljy2231/QQ-Web-Chat-Demo)

## 源码运行（Python）

需要 Python **3.12**、NapCat 或 任何支持WS的QQbot框架，以及生产环境 HTTPS 反向代理。

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
# Linux x86_64 / Python3.12 可改用带哈希锁：
# pip install --require-hashes -r requirements-linux-x86_64.lock
# 留空 WEB_PASSWORD 可改为首次打开网页设置账号密码；此时需设置一次性 SETUP_TOKEN。
export WEB_PASSWORD=''
export SETUP_TOKEN='一次性初始化口令'
export NAPCAT_ACCESS_TOKEN='请设置独立连接令牌'
export PUBLIC_BASE_URL='https://你的域名'
export CHAT_DATA_DIR='/你自己的私有数据目录'
# 只填实际反代源地址，示例不能不经核实直接照抄：
export TRUSTED_PROXIES='127.0.0.1/32'
export ADMIN_QQ_ID=''
python server.py
```

`PUBLIC_BASE_URL` 是 NapCat 获取附件时使用的本站公开地址，不可填别人的站点；不要依赖客户端提交的 Host 头生成文件地址。直接运行且未设置 `SETUP_TOKEN` 时，服务会在数据目录生成 `setup-token`，可用其中的口令打开 `/web?setup=...` 完成首次设置。反代必须覆盖转发头并支持 WebSocket，参见 [部署与运维](OPERATIONS.md)。不配置可信代理时不会信任其 X-Forwarded-Proto/For，可能以不安全传输拒绝认证，这是防伪造措施，不应通过“信任所有IP”绕过。

NapCat 反向 WebSocket 连接 `/ws` 或 `/`，连接令牌支持 Bearer 头及兼容的 `access_token` 参数。不要在访问日志保存令牌。普通根路径 GET 不是聊天主页。

## Docker 部署细节

- 安装目录：`/opt/qq-webchat`；配置在 `.env`（权限 600），聊天数据/附件在 `data/`。
- 端口与监听：`QQWEBCHAT_PORT`（默认 4001）、`QQWEBCHAT_BIND`（默认 `127.0.0.1`）。
- 其他覆盖：`QQWEBCHAT_DIR`、`QQWEBCHAT_IMAGE`、`QQWEBCHAT_MIRROR`、`QQWEBCHAT_SETUP_TOKEN`、`NAPCAT_ACCESS_TOKEN`、`PUBLIC_BASE_URL`、`TRUSTED_PROXIES`。
- 重复执行会保留 `.env`、数据和令牌，并重新生成 `compose.yaml`；不会覆盖已设置的网页账号密码。
- 首次设置链接只能使用一次；忘记账号密码的恢复方式见 [OPERATIONS.md](OPERATIONS.md)。
- 已构建镜像发布在 GitHub Container Registry（`main` 分支和 `v*` 标签由 Actions 自动构建）：

```sh
docker pull ghcr.io/ljy2231/qq-web-chat:latest
```

国内加速镜像使用前缀 `hub.002230.xyz:44300/`。手动部署可复制 `.env.example` 到 `.env` 后执行 `docker compose up -d --build`；镜像默认非root、只读根文件系统，数据必须挂载到 `/data`。

## 消息与权限规则

白黑名单控制新的QQ入站处理，不是网页用户权限表。私聊默认需 `=` 前缀，引用消息也适用。授权群的普通消息会转发。白名单用户可在自己的私聊发送 `=all` 或 `= all` 查询全量转发状态，用 `=all on` 开启、`=all off` 关闭。大小写不敏感，等号后可带空格；群聊不能使用此命令，也不会影响其他会话。`= CLEAR` 和 `= REBOOT` 仍限管理员私聊。管理员由 `ADMIN_QQ_ID` 指定；为空时管理命令禁用。

消息使用带类型的会话ID，内部消息ID与QQ平台ID分离；同号群聊和私聊不再混用。发送请求有唯一request_id，部分成功、失败和结果未知应独立展示；**未知结果不要盲目重试**，远端发送与本地提交无法成为一个数据库事务，项目不承诺exactly-once。

入站事件先持久化到inbox，再处理媒体和元数据；网络断开时只有已经成功落库的事件具有本地恢复保证。上游未送达、重启期间的事件能否重放取决于上游。

## 数据与升级

新库按行保存会话、消息、平台ID映射及inbox/outbound状态。首次升级可事务导入旧 `storage` JSON快照，或同目录中的旧 `chat_data.json`、`settings.json`。原源数据不自动删除；无法确定类型的孤儿历史保留为只读归档，不猜测发送目标。

导入保留已有记录，新增时沿用每会话最近200条规则。附件不是永久归档；上传上限50MiB，图片抓取另有限制，附件过期后链接失效。知道随机附件链接的人可访问，因此不要把它当作账号级ACL。撤回不能销毁对方已经下载的副本。

发出的文字默认按 `正文:…\nIP:…\n时间` 附带客户端IP；如不需要可设置 `DISCLOSE_CLIENT_IP=false`，并告知使用者。会话有到期、登录限速和注销撤销，服务重启仍会使内存登录失效。不要上传真实密码、QQ配置、数据库或附件到仓库。

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

项目原创代码和文档采用 [MIT License](LICENSE)，Copyright © 2026 LJY2231。第三方依赖与资源仍遵循各自的许可或使用条款；本项目的 MIT 许可不代替提示音等第三方资源的授权。随机壁纸和QQ头像依赖第三方网络服务，可能不可用。
