# 开发指南

从已准备好的开发数据库运行服务、验证改动并维护迁移。先读 [README](../README.md)、[架构](architecture.md) 和 [产品状态](status.md)。以下命令以仓库根目录为起点，不依赖个人绝对路径或固定 Docker 容器名。

## 开发前提

| 依赖 | 要求与核对位置 |
| --- | --- |
| Python | 3.12，范围由 [pyproject.toml](../backend/pyproject.toml) 定义 |
| uv | 用于按 [uv.lock](../backend/uv.lock) 安装与运行后端 |
| Node.js / npm | 需支持前端测试脚本的 `--experimental-strip-types`；检查 `node --version` 与 `node --help` |
| PostgreSQL | 具备下节基础 schema 和授权开发数据，版本及扩展需由数据库维护方确认 |
| 模型运行依赖 | Claude Agent SDK 锁定依赖及对应 CLI 运行条件；仅在启用模型时验收 |

React、Vite、TypeScript 版本由 [package.json](../frontend/package.json) 和 [package-lock.json](../frontend/package-lock.json) 管理。不要用本机曾安装的包版本代替锁文件。

## 数据库前提与迁移

本仓库提供产品增量迁移，依赖已有 `regimen_catalog`、`knowledge`、`catalog_bridge`、`clinical`、`agent`、`integration`、`ops` 基础 schema。**没有从空库初始化全部基础结构与来源数据的完整流程。**

新成员需要从项目资料维护方获得：基础 DDL 与版本说明、非敏感方案/证据及布局种子、授权的开发账号和连接方式。迁移前对照 [数据模型](data-model.md) 检查结构；不得用正式患者数据库的整库副本作为默认开发种子。

启动 API 不执行迁移、回填、数据导入或知识发布。迁移由维护人员显式执行：

```sh
cd backend
uv run python -m chemo_agent_product.entrypoints.migrate \
  --env-file .env \
  --target-database YOUR_DEVELOPMENT_DATABASE \
  --backup-file /path/to/authorized/nonempty-backup.dump
```

命令中的目标库和备份路径必须替换为实际授权环境；迁移器检查连接中的库名、非空备份和已执行 SQL 校验和。记录位于 `ops.product_migration`，脚本位于 [backend/migrations](../backend/migrations/)。

已有 SQL 不修改，通过新的迁移向前修正。代码回退优先使用可审阅的反向提交；数据库回退需独立评估已有业务数据，不删除历史修订或方案版本。

## 本地配置与启动

后端安装与配置：

```sh
cd backend
uv sync --extra dev
cp -n .env.example .env
```

本地 `.env` 被 Git 忽略。数据库 URL 填入经授权的开发连接；模型默认关闭，真实密钥不写入配置模板、源码或前端。`cp -n` 保留已有本地配置。

| 配置组 | 变量 | 用途 |
| --- | --- | --- |
| 运行与数据库 | `CHEMO_PRODUCT_ENVIRONMENT`、`CHEMO_PRODUCT_DATABASE_URL` | local/test 环境和主数据连接 |
| 兼容读取 | `CHEMO_PRODUCT_RUNTIME_TEST_DATABASE_URL` | 可选的独立只读测试上下文来源，不能与目录连接相同 |
| 宿主身份 | `CHEMO_PRODUCT_LAUNCH_SIGNING_KEY`、`CHEMO_PRODUCT_TRUSTED_HOST_ORIGINS` | 本地测试签名和可信来源 |
| 生命周期 | `CHEMO_PRODUCT_CONTEXT_TTL_SECONDS`、Worker 配置 | 上下文有效期、轮询与租约 |
| 模型 | `CHEMO_PRODUCT_MODEL_*`、`CHEMO_PRODUCT_REVIEWER_MODEL_NAME` | 启用、认证、协议地址、模型与执行限制 |
| 医院 | `CHEMO_PRODUCT_HOSPITAL_ADAPTER_CONFIG`、`CHEMO_PRODUCT_HOSPITAL_DELIVERY_CONFIG` | 指向已核对的本地读取/交付配置 |

完整字段与默认值见 [config.py](../backend/src/chemo_agent_product/core/config.py)、[.env.example](../backend/.env.example)，外部接入步骤见 [集成合同](integrations.md)。患者功能还需数据库迁移标记、签名及当前医院/人员授权；只有数据库连接不能直接进入患者流程。

后端启动：

```sh
uv run uvicorn chemo_agent_product.entrypoints.api:app --host 127.0.0.1 --port 8011
```

另开终端，从仓库根目录启动前端：

```sh
cd frontend
npm ci
npm run dev
```

默认访问 `http://127.0.0.1:5173/`。`/catalog` 是公共目录，`/` 等待可信工作站上下文；手工填写 context UUID 不会取得合法患者身份。知识与运行页面分别需要对应角色。

前端默认将 `/api` 代理到 `http://127.0.0.1:8011`。独立工作目录可设置 `CHEMO_API_ORIGIN` 指向其后端，配置见 [vite.config.ts](../frontend/vite.config.ts)；修改 origin 同时核对宿主可信来源和凭据范围。

## 环境与数据隔离

| 环境 | 允许的数据与执行 | 边界 |
| --- | --- | --- |
| 产品主线开发 | 授权目录/证据、明确开发上下文 | 普通 API 尚未开放生产身份及医院写入 |
| 自动化测试 | 显式隔离库、合成夹具、回滚事务 | 禁止将工作流测试连接指向正式产品库 |
| 独立验证版本 | 独立工作目录/分支、样例库、本地医院和私有回执 | 操作手册与验证结果由该版本维护，不自动合并到主线 |
| staging / production | 需要正式身份和院方协议接入 | 当前不接受本地测试凭据，也未完成生产流程开放 |

当前本机产品库和独立样例库名称不同，仍共用数据库服务及账号，属于库级隔离；账号权限和服务级隔离仍待处理。历史模拟初始化采用整库复制，不能据此假定任意来源库只含非敏感数据。实际环境快照见 [产品状态](status.md)。

密钥、患者资料、备份、日志及私有回执不进入 Git。`.gitignore` 只防止通常的文件跟踪，不是权限或脱敏机制；提交前检查本次变更内容，不分享整个私有运行目录。

## 验证

后端静态检查与测试，从 `backend/` 执行：

```sh
uv run ruff check src tests
uv run pytest -q
```

完整数据库测试需要在当前 Shell 明确配置 `CHEMO_WORKFLOW_TEST_DSN` 和 `CHEMO_TEST_DATABASE_URL`。前者使用名含 `test` 的隔离数据库并在测试事务中回滚；后者为目录读取合同准备对应数据库。连接由维护方提供，不在文档中填写实际凭据。

未配置连接时对应测试会跳过，报告必须保留 passed/skipped/failed。执行前核对目标库、基线结构和授权数据，不能只通过改库名绕过隔离。

前端，从 `frontend/` 执行：

```sh
npm test
npm run build
```

[browser_acceptance_harness.py](../backend/tests/e2e/browser_acceptance_harness.py) 是隔离事务中的浏览器验收宿主，要求明确测试库。它使用读取替身和回滚验证，不能计为真实医院或模型联通。真实模型另行检查鉴权、工具调用、结构化结果、引用、超时和实际计费。

仅修改文档时检查相对链接、代码/表名、图示语法和状态一致性，不因文档变化重跑会写患者数据的流程。

## 故障定位

| 现象 | 优先检查 |
| --- | --- |
| 页面等待工作站上下文 | 是否从可信宿主进入，消息来源、origin 和 token 是否正确 |
| 数据库不可用 | 开发连接、账号、网络及基础结构；检查 `/api/v1/status` |
| `WORKFLOW_NOT_CONFIGURED` | 数据库、迁移标记及签名配置，查看应用启动状态 |
| `HOSPITAL_AUTH_NOT_CONFIGURED` | 环境是否拒绝测试身份，生产身份适配是否已完成 |
| `MODEL_NOT_CONFIGURED` | 模型开关、密钥和名称是否齐全；Base URL 协议是否兼容 |
| 模型 401/403 | 服务地域、密钥类型、有效性和接口地址，不尝试其他项目凭据 |
| 候选或计算缺失 | 准备状态、来源事实、知识审核、适用关系与公式门禁 |
| 保存冲突 | 当前实例行版本与基准修订，保留本地编辑后合并 |
| 医院写请求超时 | 原记录的回查状态；不重复创建新批次 |

`/health/live` 只证明进程活着，`/api/v1/status` 和授权运行概览才提供组件状态；二者均不能替代外部联通验收。
