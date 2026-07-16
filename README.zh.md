# 税务智能体平台

基于 FastAPI + RAG + Multi-Agent + 多租户的企业税务咨询平台，完整覆盖 Enterprise 面试题 Q1-Q9 知识点。

## 知识点映射

| 题号 | 知识点 | 模块 | 核心模式 |
|------|--------|------|----------|
| Q1 | Pydantic + 类型注解 | `models.py` | Pydantic 校验替代手工检查 → 自动 Swagger 文档 + 可测试性 |
| Q2 | 代码质量 / CI/CD | `.github/workflows/ci.yml`, `tests/` | Ruff lint + pytest CI 流水线 |
| Q3 | AI 辅助开发 | — | Vibe Coding 方法论（Figma MCP + Serena MCP + Playwright MCP） |
| Q4 | 异步 + 超时 | `api/dashboard.py`, `app.py` | `asyncio.gather` + `wait_for` 超时降级 + SSE 流式推送 |
| Q5 | RAG 检索增强 | `rag/`, `data/corpus/` | 语义分块 → **密集向量召回（本地 Ollama embedding，vector/hybrid）** → Cross-Encoder 精排 → 时效性过滤 → 按文档去重 |
| Q6 | 工具管理 + 依赖注入 | `api/`, `tools/registry.py` | RESTful 工具 CRUD + `Depends()` 注入 |
| Q7 | LLM 稳定性 | `llm/client.py` | 结构化输出 + tenacity 指数退避重试 + 熔断器 + 安全围栏 + token 计费 |
| Q8 | 多租户 | `middleware/tenant.py`、`app.py`、`tools/registry.py` | `contextvars` 上下文隔离 + 令牌桶限流 + **per-tenant RAG 语料/工具注册表隔离** + RLS 行级安全 |
| Q9 | 多智能体 | `agents/` | PSE（规划者/执行者/评估者）+ 工作流引擎 + 证据驱动评估 |

## 架构

```
请求 → TenantMiddleware (Q8: X-Tenant-ID → contextvars)
  → API 端点 (Q6: Depends 注入 RAG + Tools)
    → PSEWorkflow
      → PlannerAgent (Q7: LLMClient.structured_call 结构化输出)
      → SpecialistAgent (Q5: RAGPipeline 法规检索 + Q6: ToolRegistry 工具调用)
      → EvaluatorAgent (Q7: Guardrails 安全围栏 + Q9: 证据驱动独立验证)
    → PARTIAL/FAIL → 自动重试 (最多3次)
  → SSE 响应 (Q4: 实时流式输出)
```

## 快速开始

```bash
# 安装依赖
make install

# 启动开发服务器
make run

# 运行测试
make test

# 代码检查
make lint
```

## 知识库语料（RAG）

法规知识库存放于 `data/corpus/`，每篇为带 frontmatter 的 Markdown 文件（零依赖，正则解析）：

```markdown
---
doc_id: vat-2024
title: 增值税规定 2024 / VAT Regulations 2024
effective_date: 2024-01-01
source: State Taxation Administration
---

## 增值税税率变化 / VAT Rate Changes
软件服务适用 6% 增值税税率，自 2024 年 1 月起施行……
```

- **持久化**：启动或调用 `POST /rag/reload` 时从磁盘加载，重启不丢、可直接编辑增删。
- **热重载**：前端 RAG 页面的「Knowledge Base」面板提供 Reload 按钮，改完语料无需重启服务。
- **检索**：两阶段——先用语料密集向量召回（默认 `hybrid`：本地 Ollama `nomic-embed-text` 向量余弦 + 关键词重叠混合，强化中文召回；可切 `vector` 纯向量），再 Cross-Encoder 精排，按 `doc_id` 去重后返回 top-k，避免同一文档多 chunk 重复出现。embedding 走本地 Ollama，无需云端 API Key。
- **token 计费**：每次 LLM 调用累加 token 用量，`/agent/run` 与 `/agent/stream` 的返回含 `token_usage`（prompt/completion/total/calls/cost/provider），前端 Agent 页面展示。单价见下方环境变量，可在 `.env` 覆盖。

## 多租户数据隔离（Q8）

租户隔离在数据层面是**真实分桶**（每个租户有独立的内存 RAG pipeline 与工具注册表），且已叠加**网关层 API Key 鉴权**：租户身份由 key 推导、客户端无法伪造 `X-Tenant-ID`。但仍属演示级——详见「安全与隐私须知」。

- **RAG 语料按租户隔离**：`get_rag_pipeline()` 依 `X-Tenant-ID` 为每个租户构建独立的内存 pipeline。若存在 `data/corpus/<tenant_id>/` 子目录（且含 `.md`），该租户加载自己的私有语料；否则回退到共享的 `data/corpus/` 根目录。示例：`acme-us` 有独立的 `data/corpus/acme-us/us-federal-cit-2024.md`（美国联邦税），而 `acme-cn`/`demo` 加载共享的中国税种语料。
- **工具注册表按租户隔离**：`get_tool_registry()` 为每个租户维护独立的 `ToolRegistry`（各自种子默认工具）。在某租户下 `POST /tools/` 注册的工具，其他租户不可见。
- **SQL 行级安全（RLS）**：`apply_tenant_filter()` 自动把 `AND tenant_id = :tenant_id` 追加到查询，供未来 DB 化的存储层强制隔离（已有单元测试覆盖）。
- **前端切换**：`X-Tenant-ID` 由 `web/src/api.ts` 从 `localStorage` 读取（默认 `acme-cn`）；「Tenant」页面提供租户切换器，切换后刷新即以新租户身份请求。

```bash
# acme-cn 加载共享中国税种语料（8 篇）
curl -H "X-Tenant-ID: acme-cn" http://localhost:8001/rag/documents
# acme-us 仅加载自己的美国联邦税语料（隔离验证）
curl -H "X-Tenant-ID: acme-us" http://localhost:8001/rag/documents
```

## 安全与隐私须知

本项目是面试题（Q1-Q9）**演示项目**，以下限制在投入生产前必须解决：

- **网关层租户 API Key 鉴权（已落地）**：租户身份现在**由 API Key 推导**，而非客户端声称。请求须带 `X-API-Key` 头、`Authorization: Bearer <key>`、或 SSE 的 `?api_key=` 参数；中间件用 key 反查租户作为**权威身份**，**忽略**客户端自带的 `X-Tenant-ID`，从而堵住"伪造租户 ID 越权读他人语料"的洞。无效/缺失 key 在 `TAX_AUTH_REQUIRED=true`（默认）时返回 401；`/health`、`/docs`、`/openapi.json`、`/redoc` 免鉴权。key 映射在 `TAX_TENANT_API_KEYS`（JSON 或 `tenant:key|...` 管道格式）。设置 `TAX_AUTH_REQUIRED=false` 可退回 legacy 的 `X-Tenant-ID` 信任模式（仅开发用，会重新开放越权风险）。
  - 演示用 dev key 见 `.env.example`（如 `acme-cn=tax_demo_acme_cn_8f3a`）。**生产须由服务端按租户下发独立 key，切勿把 key 写进前端 SPA**（本项目前端仅为演示内置了 dev key 常量）。
- **PII 外发至第三方 LLM**：用户的 `goal`（税务问题，可能含企业营收、个人收入等敏感信息）会原样发往配置的外部 LLM（`TAX_OPENAI_BASE_URL` 默认指向 deepseek 公网 API）。当前**无 PII 脱敏**。生产环境需要：签署数据处理协议（DPA）、使用私有化/本地模型、或在发送前做 PII 清洗。检索用的 embedding 走本地 Ollama，不外发。
- **日志含用户输入**：`agents` 曾在 INFO 级别记录完整 `goal` 与步骤描述（现已截断脱敏，仅保留长度/摘要）。`llm/client` 仍记录原始异常，若日志外发至第三方可能泄露调用细节。生产环境应进一步收紧日志级别与落盘/外发范围。
- **错误回显已收敛**：`/agent/run` 与 SSE 的异常现已返回**通用错误文案**（`workflow execution failed; see server logs for details`），内部异常细节仅保留在服务端日志，不再回显给客户端。
- **跨用户数据边界**：`/dashboard/{user_id}` 当前返回模拟数据，且**未按租户做行级隔离**；接入真实存储时必须套用 `apply_tenant_filter()`（已实现，仅对 DB 化存储生效）。
- **CORS**：当前未配置 `CORSMiddleware`，前端经 Vite dev proxy 同源访问；若跨域部署需显式设置 `allow_origins`，切忌 `*`。
- **密钥管理**：`.env` 已被 `.gitignore` 忽略，请勿提交真实密钥；参考 `.env.example` 复制后填写。

## API 端点

| 方法 | 路径 | 标签 | 说明 |
|------|------|------|------|
| GET | `/dashboard/{user_id}` | Dashboard | Q4: 异步并发微服务调用 + 超时降级 |
| POST | `/tools/` | AI Tools | Q6: 注册 Agent 工具 |
| GET | `/tools/` | AI Tools | Q6: 列出可用工具 |
| POST | `/agent/run?goal=...` | Agent | Q9: 运行 PSE 工作流（返回含 token_usage） |
| GET | `/agent/stream?goal=...` | Agent | Q4/Q9: SSE 实时 Agent 执行流（result 事件含 token_usage） |
| GET | `/rag/query?question=...` | RAG | Q5: 两阶段检索 + 重排 + 去重 |
| GET | `/rag/documents` | RAG | 列出知识库全部文档及生效/废止状态 |
| POST | `/rag/reload` | RAG | 热重载语料（改完 `data/corpus` 后无需重启服务） |
| GET | `/llm/status` | LLM | Q7: 熔断器 + 安全围栏状态 |
| GET | `/tenant/me` | Tenant | Q8: 当前租户上下文 |
| GET | `/health` | System | 健康检查 |

## curl 测试

```bash
# Q8: 多租户 — 必须携带 X-Tenant-ID 头
curl -H "X-Tenant-ID: acme-cn" http://localhost:8001/tenant/me

# Q4: 异步仪表盘
curl -H "X-Tenant-ID: acme-cn" http://localhost:8001/dashboard/1

# Q6: 注册工具
curl -X POST http://localhost:8001/tools/ \
  -H "X-Tenant-ID: acme-cn" \
  -H "Content-Type: application/json" \
  -d '{"tool_name":"tax_calculator","description":"计算增值税/企业所得税","endpoint_url":"http://tax-service/calculate"}'

# Q9: 运行 PSE Agent 工作流
curl -H "X-Tenant-ID: acme-cn" "http://localhost:8001/agent/run?goal=软件增值税税率"

# Q5: RAG 检索
curl -H "X-Tenant-ID: acme-cn" "http://localhost:8001/rag/query?question=软件增值税税率"

# Q5: 列出知识库文档（含生效状态）
curl -H "X-Tenant-ID: acme-cn" http://localhost:8001/rag/documents

# Q5: 热重载语料
curl -X POST -H "X-Tenant-ID: acme-cn" http://localhost:8001/rag/reload

# Q4/Q9: SSE 实时流
curl -N -H "X-Tenant-ID: acme-cn" "http://localhost:8001/agent/stream?goal=企业所得税扣除"
```

## 项目结构

```
tax-agent-platform/
├── data/corpus/            # Q5: 可编辑的税务语料（Markdown + frontmatter，持久化）
│   └── *.md
├── src/tax_agent_platform/
│   ├── app.py                 # 主 FastAPI 应用（集成中心）
│   ├── config.py              # Q1: pydantic-settings 配置（含 token 单价）
│   ├── models.py              # Q1/Q6/Q7/Q8/Q9: Pydantic 模型
│   ├── api/
│   │   ├── __init__.py        # Q6: 工具管理路由
│   │   └── dashboard.py       # Q4: 异步仪表盘路由
│   ├── agents/
│   │   └── __init__.py        # Q9: PSE 框架 + 工作流引擎 + token 计费
│   ├── rag/
│   │   ├── __init__.py        # Q5: 分块 + 检索 + 重排 + 语料加载 + 密集向量库
│   │   └── embeddings.py      # Q5: 本地 Ollama embedding（含离线兜底）
│   ├── llm/
│   │   ├── __init__.py
│   │   └── client.py          # Q7: 结构化输出 + 重试 + 熔断器 + 安全围栏 + 计费
│   ├── middleware/
│   │   ├── __init__.py
│   │   └── tenant.py          # Q8: contextvars + 限流 + RLS
│   └── tools/
│       ├── __init__.py
│       └── registry.py        # Q6: 工具注册 + 依赖注入
├── tests/
│   ├── test_core.py           # Q2: 单元测试（模型 / 护栏 / RLS）
│   ├── test_circuit_breaker.py# Q7: 熔断器（含护栏不误触发）
│   ├── test_tenant_isolation.py # Q8: per-tenant 隔离
│   └── test_rag_vector.py     # Q5: 语义向量召回
├── .github/workflows/
│   └── ci.yml                 # Q2: CI 流水线
├── web/                       # 前端（Vite + React + TypeScript）
│   └── src/                   #   api.ts / pages / App.tsx
├── .env.example               # 环境变量样例（复制为 .env 填写密钥）
├── Makefile
└── pyproject.toml
```

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `TAX_OPENAI_API_KEY` | `""` | LLM API 密钥 |
| `TAX_OPENAI_BASE_URL` | `https://api.deepseek.com/v1` | LLM API 地址 |
| `TAX_OPENAI_MODEL` | `deepseek-chat` | LLM 模型名称 |
| `TAX_AGNES_API_KEY` | `""` | agnes 提供方 API 密钥 |
| `TAX_AGNES_BASE_URL` | `https://apihub.agnes-ai.com/v1` | agnes 提供方 API 地址 |
| `TAX_AGNES_MODEL` | `agnes-2.0-flash` | agnes 模型名称 |
| `TAX_LLM_PROVIDER` | `agnes` | 默认 LLM 提供方（`agnes` / `deepseek`） |
| `TAX_MAX_RETRIES` | `3` | PSE 工作流最大重试次数 |
| `TAX_CIRCUIT_BREAKER_THRESHOLD` | `5` | 熔断器触发阈值（连续失败次数） |
| `TAX_LLM_TIMEOUT` | `30.0` | LLM 请求超时（秒） |
| `TAX_RAG_TOP_K` | `20` | RAG 粗召回 top-k |
| `TAX_RAG_RERANK_K` | `5` | RAG 精排 top-k |
| `TAX_RAG_MODE` | `hybrid` | RAG 召回模式：`hybrid`（向量+关键词混合，中文语料推荐）/ `vector`（纯密集向量） |
| `TAX_EMBEDDING_MODEL` | `nomic-embed-text` | 本地 embedding 模型（需 `ollama pull`），可换 `snowflake-arctic-embed2` 提升召回 |
| `TAX_EMBEDDING_URL` | `http://localhost:11434/api/embed` | Ollama embedding 接口地址 |
| `TAX_EMBEDDING_TIMEOUT` | `10.0` | embedding 请求超时（秒） |
| `TAX_AGNES_PRICE_PROMPT` | `0.004` | agnes 输入单价（CNY / 1K tokens） |
| `TAX_AGNES_PRICE_COMPLETION` | `0.012` | agnes 输出单价（CNY / 1K tokens） |
| `TAX_DEEPSEEK_PRICE_PROMPT` | `0.001` | deepseek 输入单价 |
| `TAX_DEEPSEEK_PRICE_COMPLETION` | `0.002` | deepseek 输出单价 |
| `TAX_HOST` | `0.0.0.0` | 服务监听地址 |
| `TAX_PORT` | `8001` | 服务端口 |
| `TAX_AUTH_REQUIRED` | `true` | 网关鉴权开关；`false` 退回 legacy 的 `X-Tenant-ID` 信任模式（仅开发，重新开放越权风险） |
| `TAX_TENANT_API_KEYS` | `{}` | 租户→API Key 映射，JSON `{"acme-cn":"...","acme-us":"..."}` 或管道 `acme-cn:k1\|acme-us:k2` |

> 复制 `.env.example` 为 `.env` 后填写密钥；`.env` 已被 `.gitignore` 忽略，请勿提交真实密钥。
