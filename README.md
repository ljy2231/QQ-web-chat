# QQ Web Chat

一个连接 NapCat 的 QQ 网页聊天服务，支持私聊、群聊、图片、文件、引用、撤回及网页设置。主页面为 `/web`，旧设备兼容页面为 `/web/nr`。

## 部署

需要 Python 3.12 和 NapCat。项目不会附带密码、QQ 白名单、聊天数据或 NapCat 配置。请先设置足够强且互不相同的密码和 NapCat 连接令牌，并确保 NapCat 与服务端使用相同令牌。

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
export WEB_PASSWORD='请在此处设置自己的强密码'
export NAPCAT_ACCESS_TOKEN='请在此处设置自己的连接令牌'
export PUBLIC_BASE_URL='https://你的域名'
# 可选：设置有权执行管理命令的 QQ 号
export ADMIN_QQ_ID=''
python server.py
```

`PUBLIC_BASE_URL` 必须是 NapCat 可以访问到的本站地址，文件直发使用此地址生成下载链接；不要以其他人的网址替代。反向代理应支持 WebSocket，并启用 HTTPS。访问 `https://你的地址/web`，然后在设置中配置白名单。NapCat 的 WebSocket 接入 `/ws` 或 `/`，使用 `access_token` 查询参数或 Bearer 授权头传递 `NAPCAT_ACCESS_TOKEN`。注意保护反向代理访问日志，避免记录令牌。

也可复制 `.env.example` 为 `.env`、填写配置后执行 `docker compose up -d --build`。Compose 默认只在宿主机回环地址暴露 4001，由外部反向代理提供 HTTPS；数据保存在 `./data`。Python 直接启动时默认在脚本目录保存数据，可使用 `CHAT_DATA_DIR` 指定目录。`.env`、数据库、消息记录及上传文件均不应提交到 Git。

## 数据与隐私

首次运行会将同一数据目录中存在的 `chat_data.json` 和 `settings.json` 导入 SQLite；之后 SQLite 为权威数据源。每个会话当前保留最近 200 条记录。图片和文件默认有清理策略，文件发送成功后本地副本也可能在清理时过期，不应将聊天历史当作永久附件备份。

文件发送需要随机文件链接，知道链接的人可访问文件。应用会在发出的文字中附加客户端 IP。网站登录 token 位于浏览器存储，服务重启会使登录失效；目前未实现登录限速与 token 自动过期，公开部署应另行配置访问控制、限速、备份和监控。不要把密码、token、聊天记录或真实设置发布到公开仓库。

依赖第三方图源、QQ 头像服务及 Tailwind CDN。NR 页面的特定旧设备兼容性取决于浏览器、TLS 和 Cookie 支持，需在目标设备上验证。
