# Sub App

基于 FastAPI + Vue 3 的轻量节点订阅管理系统，用于集中维护代理节点、为不同用户分配节点，并生成 V2Ray/Clash 兼容的订阅内容。

当前仓库是私有仓库，面向个人部署与运维使用。线上实例地址为 `https://sub.m1n6.uk`。

## 功能

- 管理代理节点：批量粘贴、启用/停用、排序、编辑和删除。
- 支持解析 `VLESS`、`VMess`、`Trojan`、`Hysteria2/Hy2` 和 `Shadowsocks` URI。
- 管理订阅用户：UID、备注、节点分配、启用/停用和订阅令牌轮换。
- 设备识别：根据订阅拉取请求记录设备指纹、User-Agent、IP、拉取次数和最近活动时间。
- 单独停用或删除设备，支持设备数量上限。
- 生成 Base64/V2Ray 原始订阅，以及 Clash/Mihomo YAML 配置。
- 仪表盘统计节点、用户、设备、24 小时拉取次数、活跃设备和最近拉取记录。
- 使用 SQLite 持久化数据，提供 `/healthz` 健康检查接口。

## 支持的订阅格式

订阅接口由 `target` 参数选择输出格式：

- `v2ray`、`base64`：将原始节点 URI 编码为 Base64。
- `raw`：按行输出原始节点 URI。
- `clash`、`clashmeta`、`mihomo`：输出包含代理、自动测速组和节点选择组的 YAML。

实际订阅地址由系统为每个用户生成，不要把包含用户令牌的完整订阅链接提交到 Git 或公开分享。

## 技术栈

- Python 3.10+
- FastAPI / Uvicorn
- SQLAlchemy + SQLite
- itsdangerous 会话签名
- PyYAML
- Vue 3 和 Tailwind CSS CDN（前端为单页静态文件，无独立构建步骤）

## 项目结构

```text
.
├── app/
│   ├── converter.py   # 节点 URI 解析与订阅格式转换
│   ├── main.py        # FastAPI 应用、管理 API 和订阅接口
│   └── models.py      # SQLAlchemy 数据模型
├── static/
│   └── index.html     # Vue 3 管理界面
├── .gitignore
└── README.md
```

运行时的 `.env`、SQLite 数据库、虚拟环境和缓存目录不属于源代码，均由 `.gitignore` 排除。

## 配置

启动前必须设置以下环境变量：

| 变量 | 作用 |
| --- | --- |
| `SUB_APP_ADMIN_PASSWORD` | 管理后台登录密码 |
| `SUB_APP_SECRET` | Cookie 会话签名密钥，应使用随机长字符串 |
| `SUB_APP_DB` | SQLite 数据库路径；不设置时默认为项目根目录下的 `data.db` |
| `SUB_APP_PUBLIC_BASE` | 生成订阅链接时使用的公网基地址 |

示例（请替换所有占位值，不要把真实值写入仓库）：

```bash
export SUB_APP_ADMIN_PASSWORD='change-this-password'
export SUB_APP_SECRET='generate-a-long-random-secret'
export SUB_APP_DB='/var/lib/sub-app/data.db'
export SUB_APP_PUBLIC_BASE='https://your-domain.example'
```

## 本地运行

```bash
git clone https://github.com/MinG-98/sub-app.git
cd sub-app

python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install fastapi 'uvicorn[standard]' sqlalchemy itsdangerous pyyaml

export SUB_APP_ADMIN_PASSWORD='change-this-password'
export SUB_APP_SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
export SUB_APP_DB="$PWD/data.db"
export SUB_APP_PUBLIC_BASE='https://your-domain.example'

uvicorn app.main:app --host 127.0.0.1 --port 8080
```

数据库表会在应用首次启动时自动创建。正式部署时建议让 Caddy、Nginx 或其他反向代理负责 HTTPS，并将 Uvicorn 仅绑定在回环地址或受控内网地址。

## 主要接口

| 接口 | 说明 |
| --- | --- |
| `GET /` | 管理后台页面 |
| `GET /healthz` | 健康检查 |
| `POST /api/admin/login` | 管理员登录 |
| `/api/admin/nodes` | 节点增删改查 |
| `/api/admin/friends` | 订阅用户增删改查 |
| `/api/admin/devices` | 设备记录管理 |
| `GET /api/admin/stats` | 统计和最近拉取记录 |
| `GET /sub/{token}` | 生成订阅内容，使用 `target` 选择格式 |

除健康检查和订阅接口外，管理接口需要登录 Cookie。订阅令牌本身等同于访问凭据，应按密码处理。

## 安全注意事项

- 不要提交 `.env`、`data.db`、订阅令牌、节点 URI、密码、私钥或其他运行时凭据。
- 管理密码和 `SUB_APP_SECRET` 应使用随机且互不重复的值，并通过服务管理器或安全的环境文件注入。
- 生产环境必须使用 HTTPS；应用设置的会话 Cookie 启用了 `HttpOnly`、`Secure` 和 `SameSite=Lax`。
- 限制管理后台的公网访问范围，并定期轮换暴露过的管理员密码、会话密钥和订阅令牌。
- SQLite 数据库包含用户、设备、IP、User-Agent 和拉取记录，应设置合适的文件权限并纳入独立备份。

## License

本项目采用 [MIT License](LICENSE)。
