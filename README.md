# Sub App

[![CI](https://github.com/MinG-98/sub-app/actions/workflows/ci.yml/badge.svg)](https://github.com/MinG-98/sub-app/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](pyproject.toml)

基于 FastAPI + Vue 3 的轻量节点订阅管理系统，用于集中维护代理节点、为不同用户分配节点、采集节点状态与流量，并生成 V2Ray/Clash 兼容的订阅内容。

代码开源以供参考；线上实例仅供本人个人使用，不开放注册，具体地址不在此公开。

## 目录

- [功能](#功能)
- [观测与拓扑](#观测与拓扑)
- [运维脚本与定时任务](#运维脚本与定时任务)
- [支持的订阅格式](#支持的订阅格式)
- [技术栈](#技术栈)
- [项目结构](#项目结构)
- [配置](#配置)
- [本地运行](#本地运行)
- [主要接口](#主要接口)
- [开发规范](#开发规范)
- [安全注意事项](#安全注意事项)
- [License](#license)

## 功能

- 管理代理节点：批量粘贴、启用/停用、排序、编辑和删除。
- 支持解析 `VLESS`、`VMess`、`Trojan`、`Hysteria2/Hy2` 和 `Shadowsocks` URI。
- 管理订阅用户：UID、备注、节点分配、启用/停用和订阅令牌轮换。
- 设备识别：根据订阅拉取请求记录设备指纹、User-Agent、IP、拉取次数和最近活动时间。
- 单独停用或删除设备，支持设备数量上限。
- 生成 Base64/V2Ray 原始订阅，以及 Clash/Mihomo YAML 配置。
- 仪表盘统计节点、用户、设备、24 小时拉取次数、活跃设备和最近拉取记录。
- 通过哪吒 API 采集节点在线状态、心跳、速度和 30 天内的节点流量历史。
- 为已适配节点维护用户级凭据状态；首批适配 Hysteria2 和 VLESS，旧凭据支持短暂兼容窗口。
- 通过独立采集脚本读取代理核心的用户级流量，并将哪吒整机流量与用户配额统计分开。
- 为设备生成可撤销、可轮换的专属订阅访问标识，同时保留旧版 UA/IP 设备记录用于审计。
- 使用 SQLite 持久化数据；公开 `/healthz` 只提供最小存活状态，详细健康信息通过需要管理员登录的 `/api/admin/healthz` 查看。

## 观测与拓扑

- `/api/admin/overview/topology` 从当前分配关系生成管理拓扑，保留用户、节点和服务器三层关系。
- `/api/admin/latency` 与 `/api/admin/latency/probe` 提供受控的真实探测：控制面、节点入口 TCP 和代理出口分别记录；探测临时文件使用 root-only 权限，凭据不会写入状态文件或 API 响应。
- 仪表盘桌面端使用横向流量/活跃度柱状图；拓扑中的用户名在图外显示并稳定分色。`mobile-enhancements` 资源负责跨视口的品牌标记和紧凑布局。
- `/healthz` 只返回 `ok` 和时间戳，避免向未认证访问者暴露数据库、采集器、协调器和 Agent 详情；管理员登录后通过 `/api/admin/healthz` 获取完整状态。

## 运维脚本与定时任务

仪表盘和健康检查展示的采集/协调状态并非由应用自动刷新，而是由以下独立脚本写入 `/var/lib/sub-app/*.json` 状态文件，需要通过 cron 或 systemd timer 周期执行：

| 脚本 | 作用 | 关键环境变量（均有默认值，可省略） |
| --- | --- | --- |
| `scripts/nezha_collector.py` | 单次采集哪吒整机状态与流量 | `NEZHA_BASE_URL`、`NEZHA_API_TOKEN`（或写入 `NEZHA_ENV_FILE` 指向的文件，默认为 `/etc/sub-app/nezha.env`） |
| `scripts/proxy_collector.py` | 单次采集 Hysteria2/VLESS 用户级流量计数器 | `SUB_APP_VLESS_STATS_BINARY`（默认 `/usr/local/libexec/sub-app-vless-stats`）、`SUB_APP_PROXY_STATUS` |
| `scripts/reconciler.py` | 将用户分配与本机代理适配器配置做最终一致性协调（新增/轮换/吊销凭据的重试循环） | `SUB_APP_PROXY_ADAPTERS_ENV`（默认 `/etc/sub-app/proxy-adapters.env`）、`SUB_APP_RECONCILER_STATUS` |
| `scripts/latency_probe.py` | 触发一次真实延迟探测（控制面/节点入口/代理出口） | `SUB_APP_LATENCY_STATUS`、`SUB_APP_LATENCY_LOCK`、`SUB_APP_HYSTERIA_BIN`、`SUB_APP_SING_BOX_PROBE_BIN` |

延迟探测通常由仪表盘按需触发（`POST /api/admin/latency/probe`），无需单独定时；其余三个脚本建议用 systemd timer 定期执行，例如：

仪表盘触发探测时，`/api/admin/latency/probe` 用当前运行 FastAPI 进程的解释器去启动 `scripts/latency_probe.py`（即 `sys.executable`），所以正常情况下不需要单独配置。仅当你想让它用另一个解释器（比如手动跑的场景与服务进程的 venv 不一致）时才需要设置 `SUB_APP_PYTHON` 覆盖。

```ini
# /etc/systemd/system/sub-app-nezha-collector.service
[Service]
Type=oneshot
User=sub-app
EnvironmentFile=/etc/sub-app/app.env
ExecStart=/opt/sub-app/.venv/bin/python /opt/sub-app/scripts/nezha_collector.py

# /etc/systemd/system/sub-app-nezha-collector.timer
[Timer]
OnBootSec=1min
OnUnitActiveSec=5min

[Install]
WantedBy=timers.target
```

`proxy_collector.py` 和 `reconciler.py` 可按相同方式配置各自的 `.service`/`.timer`，运行间隔按节点数量和流量精度需求调整。

`scripts/pilot_hysteria_client.py` 与 `scripts/pilot_vless_client.py` 是一次性人工诊断脚本，用于在排查某个节点握手问题时手动运行，不应加入定时任务。

节点 Agent（`agent/` 目录，独立于以上中心侧脚本）的部署方式见 [agent/README.md](agent/README.md)。

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
- Vue 3 前端（线上使用构建后的静态 JS/CSS 资源）
- httpx（哪吒 API 和本地采集器）

## 项目结构

```text
.
├── app/
│   ├── converter.py   # 节点 URI 解析与订阅格式转换
│   ├── credentials.py # 用户节点凭据的派生、加密和脱敏输出
│   ├── proxy_adapters.py # Hysteria2/VLESS 代理适配器
│   ├── traffic.py     # 哪吒采集和流量增量归一化
│   ├── agent_api.py   # 节点 Agent 接入
│   ├── main.py        # FastAPI 应用、管理 API 和订阅接口
│   └── models.py      # SQLAlchemy 数据模型
├── scripts/           # 哪吒、代理核心和节点 Agent 的采集/同步脚本
├── static/
│   ├── index.html     # 管理界面入口
│   └── assets/        # 前端构建产物
├── .github/
│   ├── workflows/ci.yml          # lint、格式检查和测试
│   └── pull_request_template.md
├── requirements.txt       # 运行依赖
├── requirements-dev.txt   # 运行依赖 + ruff/black/pytest
├── pyproject.toml         # ruff/black 配置
├── .pre-commit-config.yaml
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
python -m pip install -r requirements.txt

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
| `GET /healthz` | 公开最小存活检查，不返回内部组件详情 |
| `GET /api/admin/healthz` | 管理员登录后查看数据库、采集器、协调器、Agent 和适配器详情 |
| `POST /api/admin/login` | 管理员登录 |
| `/api/admin/nodes` | 节点增删改查 |
| `/api/admin/nodes/{id}/traffic` | 节点流量曲线和时间范围汇总 |
| `/api/admin/collector/status` | 哪吒、代理流量和节点 Agent 采集状态 |
| `/api/admin/friends` | 订阅用户增删改查 |
| `/api/admin/devices` | 设备记录管理 |
| `GET /api/admin/stats` | 统计和最近拉取记录 |
| `GET /sub/{token}` | 生成订阅内容，使用 `target` 选择格式 |

除公开最小存活检查和订阅接口外，管理接口需要登录 Cookie。订阅响应显式设置 `private, no-store`；订阅令牌本身等同于访问凭据，应按密码处理。

## 开发规范

```bash
python -m pip install -r requirements-dev.txt
pre-commit install   # 可选：提交前自动跑 ruff/black 和基础检查
```

- 代码风格由 [black](https://black.readthedocs.io/) 统一格式化，静态检查用 [ruff](https://docs.astral.sh/ruff/)；配置见 `pyproject.toml`。
- 提交前建议本地跑一遍：

  ```bash
  ruff check .
  black --check .
  pytest -q
  ```

- GitHub Actions（`.github/workflows/ci.yml`）在每次 push/PR 到 `master` 时执行同样的检查，PR 请保持 CI 全绿再合并。
- PR 请使用 `.github/pull_request_template.md` 中的 Summary/Test plan 结构描述改动。

## 安全注意事项

- 不要提交 `.env`、`data.db`、订阅令牌、节点 URI、密码、私钥或其他运行时凭据。
- 哪吒 Token、代理适配器密钥和节点配置应通过 VPS 上的 root-only 环境文件注入，不写入仓库、数据库或页面。
- 管理密码和 `SUB_APP_SECRET` 应使用随机且互不重复的值，并通过服务管理器或安全的环境文件注入。
- 生产环境必须使用 HTTPS；应用设置的会话 Cookie 启用了 `HttpOnly`、`Secure` 和 `SameSite=Lax`。
- 限制管理后台的公网访问范围，并定期轮换暴露过的管理员密码、会话密钥和订阅令牌。
- SQLite 数据库包含用户、设备、IP、User-Agent 和拉取记录，应设置合适的文件权限并纳入独立备份。

## License

本项目采用 [MIT License](LICENSE)。
