# Runner Beacon

一个轻量的 GitHub Actions self-hosted runner 监控台。它通过 GitHub API 展示 Runner 的在线、空闲、执行中和离线状态，并按 Label 聚合可用容量。

## 功能

- 支持组织、仓库和企业三个 Runner 作用域
- Runner 状态、操作系统、忙闲状态和全部 Labels
- 公开只读页面无需登录即可查看 Runner、Labels 和状态
- 支持单选或多选 Labels 筛选，多选时匹配包含全部所选 Labels 的 Runner
- 支持可持久化的 Dark/Light 主题和统一的自定义下拉控件
- Token 在网页中配置，只提交到后端；使用 `APP_SECRET` 加密后保存
- Runner 持续离线时通过钉钉自定义机器人告警，恢复在线可选通知
- 在管理页面按 Runner 名称或 Label 配置 @ 人员，无需修改仓库配置
- 连接设置受管理密码保护，支持自动刷新和 GitHub API 配额显示
- 支持 GitHub.com 和 GitHub Enterprise Server
- 响应式深色运维控制台、Docker Compose 一键部署

界面固定使用 [阿里巴巴普惠体 3.0](https://www.alibabafonts.com/#/font)。Docker 构建时会从阿里巴巴官方字体域下载并校验四个 WOFF2 字重；源码方式运行时，浏览器首次请求字体会触发服务端下载到 `DATA_DIR/fonts`，之后从本地缓存提供。字体二进制不提交到 Git，来源、用途及分发须遵守官方[法律声明](https://www.yuque.com/yiguang-wkqc2/hgpff0/nus9wiinq4aeiegy)。

## Docker Compose 部署

1. 复制环境变量示例并修改密钥：

   ```bash
   cp .env.example .env
   ```

2. 启动服务：

   ```bash
   docker compose up -d --build
   ```

3. 打开 `http://localhost:8000` 即可查看公开状态页。点击右上角设置按钮，使用 `.env` 中的 `ADMIN_PASSWORD` 登录后填写 GitHub Token 和作用域。

配置存储在 Docker 命名卷 `runner-beacon-data` 中。修改 `APP_SECRET` 后，已经保存的 Token 将无法解密，需要在网页中重新填写。

## 钉钉离线告警

在钉钉群中添加“自定义机器人”，建议启用“加签”，然后在右上角管理设置的“钉钉离线告警”区域填写 Webhook 和 Secret。配置保存在本服务的数据卷中，Webhook、Secret、手机号与 GitHub Token 一样使用 `APP_SECRET` 加密。管理员重新打开设置时会完整回显 Webhook，Secret 仍保持隐藏。

人员映射直接在网页管理页面配置：

- `Runner 名称`：与 GitHub 返回的 Runner 机器名精确匹配
- `Label`：与 Runner 的任一 Label 精确匹配
- `@ 手机号`：填写人员钉钉账号绑定的手机号，多个号码用逗号分隔
- 匹配顺序：Runner 名称规则优先，其次合并所有命中的 Label 规则，均未命中时使用默认人员

服务启动或告警配置变更时，当前 Runner 状态会作为新的基线，不会对已经离线的所有机器集中补发告警。之后检测到 `在线 → 离线`，并持续超过设置的时间，才发送一次离线通知；恢复在线时可发送恢复通知。告警状态保存在数据卷中的 SQLite 数据库，因此重启容器不会重复告警。

“发送测试通知”使用已经保存的配置；新填写或修改 Webhook 后，请先保存再测试。本服务只调用 GitHub 的 Runner 状态读取接口，不会触发 GitHub Actions，也不会向 Runner 下发任务。

> 公开接口会展示 Runner 机器名和 Labels。请仅部署到允许查看这些信息的网络。Compose 提供的默认密码仅用于首次本地体验；对外部署前务必设置随机的 `ADMIN_PASSWORD` 和 `APP_SECRET`，经 HTTPS 反向代理访问时同时设置 `COOKIE_SECURE=true`。

## Token 权限

根据 [GitHub 官方 API 文档](https://docs.github.com/en/rest/actions/self-hosted-runners)，不同作用域需要以下只读权限：

- 组织：fine-grained token 需要组织的 **Self-hosted runners: Read**；classic token 需要 `admin:org`（涉及私有仓库时还需要 `repo`）
- 仓库：fine-grained token 需要仓库的 **Administration: Read**；classic token 需要 `repo`，且账号需有仓库管理权限
- 企业：需要 classic token 的 `manage_runners:enterprise`；该接口目前不支持 fine-grained token

作用域填写方式：

- 组织：填写组织名，例如 `acme`
- 仓库：填写 `owner/repository`，例如 `acme/api`
- 企业：填写 enterprise slug，例如 `acme-enterprise`

GHES 的 API 地址通常是 `https://github.example.com/api/v3`。

## 本地开发

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
set ADMIN_PASSWORD=admin
set APP_SECRET=development-secret
set DATA_DIR=./data
uvicorn app.main:app --reload
```

运行测试：

```bash
pytest -q
```

## API

- `GET /healthz`：容器健康检查（无需登录）
- `GET /api/session`：当前登录状态
- `GET/PUT /api/settings`：读取或保存脱敏配置
- `POST /api/alerts/test`：发送钉钉测试通知（需管理员登录）
- `GET /api/runners`：公开的 Runner、Label 与状态快照
- `GET /api/runners?force=true`：管理员登录时绕过短期缓存立即刷新；匿名访问仍使用缓存
