# 集成合同

本文面向宿主工作站、院方接口和模型接入人员。说明当前代码合同及配置位置；真实联通、生产开放和未完成项统一见 [产品状态](status.md)。

## 接入资料与来源

| 基线资料 | 用途 | 仓库中的实现或说明 |
| --- | --- | --- |
| 温州化疗智能体项目接口文档 v1.0.1，三医管理字段补充修订 | 患者、就诊、医师、医院读取及交付字段合同 | hospital、hospital_contracts 与配置模板 |
| 阶段 0 临床匹配规则 V2.2 | 等级、展示区域、独立提示和 X 依据 | matching 与 [业务流程](workflows.md) |
| 已确认的患者主流程与结构化表单设计 | 提前准备、固定版本、实例、修订及独立确认 | Workflow、Worker、editor 与 [数据模型](data-model.md) |

原始业务资料和完整基础建库资料未全部随本仓库分发。接入前应从项目资料维护方取得授权版本，并核对正文必填字段与示例差异；个人文件路径不作为工程依赖入口。早期调查来源可查 [历史索引](archive/README.md)。

## 宿主工作站与身份

宿主加载 [chemo-widget.js](../frontend/public/chemo-widget.js)，通过 `createChemoWidget({serviceOrigin, getAccessToken, onError})` 创建挂件。`getAccessToken` 从可信后端取得凭据；网页不保存签名密钥。

进入或切换患者时调用 `widget.updatePatient`，传入患者、就诊、操作人员、科室和会话上下文。挂件发送 `AUTO_PREPARE`，维护递增患者代次，关闭时仍可查询准备状态。完整输入见 [LaunchInput](../backend/src/chemo_agent_product/modules/patient_regimen/schemas.py)。

iframe 使用 `CHEMO_READY` / `CHEMO_CONTEXT`、版本 `1` 交换上下文。宿主与页面检查来源窗口和可信 origin；页面只在内存维护 access token，不放入 URL 或 localStorage。`context_id` 只标识一次上下文，不是访问凭据。

当前身份实现为服务端 HMAC 签名的 local/test 合同，角色包括 `DOCTOR`、`KNOWLEDGE_REVIEWER`、`OPERATOR`。生产医院 SSO、签发方、令牌验证和角色映射尚待接入；当前 `staging` / `production` 不接受本地测试凭据。

相关配置：`CHEMO_PRODUCT_LAUNCH_SIGNING_KEY`（至少 32 字符）、`CHEMO_PRODUCT_TRUSTED_HOST_ORIGINS`、上下文 TTL。身份范围由后端校验医院、患者、就诊和操作者，不能只由宿主页面声称。

## 患者与公共 API

普通 FastAPI 应用由 [应用入口](../backend/src/chemo_agent_product/entrypoints/api.py) 装配，HTTP 合同分别由 [患者上下文](../backend/src/chemo_agent_product/modules/clinical_context/api.py)、[候选](../backend/src/chemo_agent_product/modules/recommendation/api.py)、[患者方案](../backend/src/chemo_agent_product/modules/patient_regimen/api.py)、[Agent](../backend/src/chemo_agent_product/agent_runtime/api.py)、[公共目录](../backend/src/chemo_agent_product/modules/regimen/api.py) 和 [管理](../backend/src/chemo_agent_product/modules/management/api.py) 路由定义。启动应用后可通过 `/openapi.json` 或 `/docs` 查看字段合同；路由存在不表示生产权限已经开放。

| 类别 | 主要方法与路径 | 用途与权限 |
| --- | --- | --- |
| 活性与能力 | `GET /health/live`、`GET /api/v1/status` | 进程活性与组件状态，活性不代表外部服务联通 |
| 宿主合同 | `GET /api/v1/host-contract` | 可信 origin 配置 |
| 公共目录 | `GET /api/v1/regimens`、`GET /api/v1/regimens/{regimen_id}/versions/{version_id}` | local/test 公共目录与固定版本读取 |
| 证据 | 固定版本下的 `/evidence`、`GET /api/v1/evidence/{evidence_id}` | 关联依据与来源读取 |
| 患者进入 | `POST /api/v1/launch-context` | DOCTOR，绑定患者并启动准备 |
| 准备与刷新 | `GET /api/v1/contexts/{context_id}/preparation-status`、`POST /api/v1/contexts/{context_id}/refresh` | 当前授权患者范围；刷新校验期望代次 |
| 候选 | context 下 `/candidates/{candidate_id}`、`/evidence`、`POST .../select` | 固定候选的读取与明确选用 |
| 查看与比较动作 | `POST /api/v1/contexts/{context_id}/actions` | VIEW/COMPARE，记录动作，不选用方案 |
| 实例与历史 | context 下 `/instances/{instance_id}`、`/revisions/{revision_id}` | 已选实例与只读历史 |
| 保存与确认 | `POST .../instances/{instance_id}/revisions`、`POST .../confirm` | 幂等、行版本和确切修订校验 |
| 交付准备 | `GET .../instances/{instance_id}/hospital-readiness` | 本地准备检查，不产生院方校验回执 |
| 模型运行 | context 下 `POST /agent-runs`、`GET /agent-runs`、`GET /agent-runs/{run_id}` | 当前患者及指定修订的运行与产物 |
| 管理概览 | `GET /api/v1/knowledge/overview`、`GET /api/v1/operations/overview` | 分别要求 KNOWLEDGE_REVIEWER / OPERATOR |

受控患者命令要求 `Authorization: Bearer ...` 和 `Idempotency-Key`；读取也需患者授权。保存和确认输入还包含期望行版本，确认包含修订哈希。详情见 [患者方案输入合同](../backend/src/chemo_agent_product/modules/patient_regimen/schemas.py)。

## 医院读取适配

[读取配置模板](../backend/config/hospital-read.example.json) 定义八个操作：

| 操作 | 数据职责 |
| --- | --- |
| `Q_GetPatientClinicalData` | 肿瘤临床资料与结构化事实 |
| `Q_GetPatientEncounter` | 患者、就诊及人员上下文 |
| `Q_GetClinicalRecord` | 病历文本记录 |
| `Q_GetClinicalReport` | 检验、病理、影像与检查报告 |
| `Q_GetDrugOrdersHistory` | 历史药品医嘱 |
| `Q_GetOrderDictionary` | 院内药品、途径、频次、单位及科室字典 |
| `Q_GetMedicalStaffInfo` | 医务人员信息 |
| `Q_GetPatientOperationHistory` | 实际手术记录 |

配置通过 `CHEMO_PRODUCT_HOSPITAL_ADAPTER_CONFIG` 指向本地文件。模板中的医院编码、URL、患者/就诊定位和事实映射默认为空，必须依据院方协议补齐。事实映射还需明确值、单位、状态、采集时间、来源定位、文本记录和分页规则。

凭据通过配置的 `credential_environment_variable` 引用环境变量；模板使用 `CHEMO_PRODUCT_HOSPITAL_AUTHORIZATION`，不把认证内容写入 JSON。真实请求/响应哈希、时间、业务码和传输状态由调用观察器留痕。

主管、主治、副主任、主任医师分别保留，不能用一个角色代填另一个角色。接口例子中缺失的必填字段不能作为省略正文合同的依据。

## 医院交付合同

[交付配置模板](../backend/config/hospital-delivery.example.json) 通过 `CHEMO_PRODUCT_HOSPITAL_DELIVERY_CONFIG` 加载。请求与回执类型在 [hospital_contracts.py](../backend/src/chemo_agent_product/integrations/hospital/contracts.py)，驱动在 [hospital_delivery.py](../backend/src/chemo_agent_product/integrations/hospital/delivery.py)。

| 操作 | 业务语义 |
| --- | --- |
| `B_ValidateChemoOrders` | 院方医嘱预校验 |
| `B_ImportChemoOrders` | 导入已确认的确切方案与医嘱 |
| `Q_GetRegimenHandoverStatus` | 回查受理批次的逐条处理结果 |
| `B_CancelRegimenHandover` | 撤销既有交付 |
| `B_ArchiveRegimenRecord` | 归档方案记录 |
| `Q_GetRegimenArchiveStatus` | 查询归档文书与签名结果 |

数量、单位、版本、治疗起始时间、操作者与院内编码均需满足正文合同。预校验、受理、全部成功、部分成功、归档和签名是独立结果；医院代码映射及真实人员必须来自审核配置。

驱动校验请求/响应、调用医师与记录归属。写请求超时标为结果未知，按原记录回查，不自动重发新批次。当前尚无普通产品 API 的生产提交入口或通用全类别医嘱编译，配置文件存在不能补齐这些业务能力。

## 模型与只读 MCP

模型适配使用 Claude Agent SDK 和 Anthropic Messages 协议。后端模型开关、Key、Base URL、主模型、Reviewer 模型、超时、轮数和预算都在 [配置模板](../backend/.env.example) 中；实际值仅写本地后端配置。OpenAI Chat Completions 地址不能直接作为当前适配器的 Base URL。

未指定 Reviewer 模型时复用主模型名称，但独立创建运行、输入与提示词。SDK 预算字段是调用限制配置，兼容服务的真实计费另行核实。

当前注册内置 SDK MCP 服务 `clinical`，工具名为 `mcp__clinical__<name>`：

| 工具 | 允许读取的内容 |
| --- | --- |
| `read_snapshot` | 本次绑定的患者快照及来源 |
| `read_candidates` | 程序固定的候选顺序、等级与标签 |
| `read_plan` | 本次候选中的固定表单 |
| `read_evidence` | 本次知识清单允许的指定证据 |
| `read_rules` | 固定规则结果与知识清单 |
| `read_calculations` | 受控参考计算或保存修订中的计算记录 |
| `read_revision` | 本次 Reviewer 绑定的确切修订、字段与药物行 |
| `search_knowledge` | 本次绑定证据中的文字检索 |

工具不提供任意 SQL、Shell、文件读写或医院写权限。输出须符合 `agent-output.v1` 并通过引用范围校验；病历文本、证据原文与工具结果中的指令不获得操作权限。

产品运行提示词为 [recommendation.v1.md](../backend/src/chemo_agent_product/agent_runtime/prompts/recommendation.v1.md) 和 [reviewer.v1.md](../backend/src/chemo_agent_product/agent_runtime/prompts/reviewer.v1.md)。它们与工程 AI 使用的 [AGENTS.md](../AGENTS.md) 分属不同用途。

## 知识检索与扩展位置

当前 `search_knowledge` 对本次证据的 `verbatim_excerpt` 做不区分大小写的子串匹配，最多返回 20 条。没有 Embedding 配置、向量索引或外部向量知识库适配器；Agent 选项为 `skills=[]`、`plugins=[]`，没有启用产品 Skill。

后续向量检索可在该工具合同后增加检索适配层，但以下内容仍属于待设计和开发范围：文档切分与版本、向量化、索引更新、权限与审核过滤、召回排序、原文定位，以及结果如何固定到本次知识清单。扩展检索范围需要明确调整现有范围合同和引用校验，不能直接让模型读取任意知识或其他患者数据。

MCP 负责提供可调用的数据工具，Skill 可描述使用步骤和输出约束；Skill 本身不代替向量存储、权限或临床审核。独立验证索引与产品索引也需明确隔离。
