# AgentSoc

> AI 助手安全防护 · L1-L4 纵深防御 MVP

定位为本地可运行、可落地的 AI 助手安全产品。

---

## 快速开始（5 分钟）

```bash
# 1. 安装依赖
make install

# 2. 启动服务（开发模式，代码热重载）
make dev

# 3. 健康检查
curl http://localhost:8000/health
# → {"status":"ok","service":"AgentSoc",...}

# 4. 打开可视化看板（6 个 demo 页互链）
#    http://127.0.0.1:8000/dashboard       总览看板
#    http://127.0.0.1:8000/demo            C1 prompt 注入拦截
#    http://127.0.0.1:8000/demo/tools      C2 工具调用拦截
#    http://127.0.0.1:8000/demo/policy     C3 策略中心 + 热更新
#    http://127.0.0.1:8000/demo/nl         C4 自然语言转策略
#    http://127.0.0.1:8000/demo/audit      D1 审计日志
```

### 跑一次完整攻击测试

```bash
# 端到端：9 类攻击 + 正常请求，打印演练报告
python examples/demo_attack_drill.py
# → 演练结果 9/9 符合预期

# 终端实时盯风险事件流（D2）
make dashboard
```

---

## 架构

4 层纵深防御：

| 层 | 职责 | 代码 |
|---|---|---|
| L1 | 归一化（URL / Unicode / HTML / Base64 / 零宽字符解码） | `app/detection/normalize.py` |
| L2 | 来源隔离（system / user / tool 标签） | `app/detection/isolate.py` |
| L3 | 检测分级（规则引擎 + 判别模型 + 决策融合） | `app/detection/{rules,classifier,fuse}.py` |
| L4 | 输出兜底（工具调用前 JSON / 白黑名单 / 高危语义校验） | `app/detection/egress.py`、`app/proxy/tool_hook.py` |

接入层：`app/proxy/openai_proxy.py` 提供 OpenAI 兼容的 `/v1/chat/completions`，
请求进来先过 L1-L3（拦输入），模型想调工具时再过 L4（拦动作）。

详细开发计划见 [docs/M1-开发计划.md](docs/M1-开发计划.md)，落地推广方案见 [docs/落地推广实施方案.md](docs/落地推广实施方案.md)。

---

## 策略配置中心（C3）

`policies/` 目录是**策略的单一事实来源**：目录下所有 `*.yaml` 都会被自动扫描并按顶层键归类。

| 顶层键 | 作用 |
|---|---|
| `rules` | L3 检测规则 |
| `blocked_tools` / `allowlist` / `dangerous_patterns` | L4 工具策略 |
| `tool_schemas` | 工具参数 JSON Schema |

- **热更新**：`PolicyWatcher` 监听目录，改动后**无需重启**即生效（`watchdog` 事件驱动 ≈ 毫秒级 + `POLICY_RELOAD_INTERVAL` 秒兜底轮询）。
- **fail-safe**：任一 YAML 解析失败时**整体保留上一版**并记录错误历史，不会打挂服务。
- **版本可追溯**：每次内容变化产生 `revision`（递增）+ `version`（sha256 前 12 位），每条检测结果带策略版本。

### 自然语言转策略（C4）

写一句人话，自动生成可热加载的策略 YAML：

```bash
# 试译（不落盘）
curl -X POST http://127.0.0.1:8000/v1/policy/nl/preview \
  -H 'Content-Type: application/json' \
  -d '{"text":"禁止 bash 删除 /etc 下的文件"}'

# 应用（落盘 + 触发热更新）
curl -X POST http://127.0.0.1:8000/v1/policy/nl/apply \
  -H 'Content-Type: application/json' \
  -d '{"text":"禁用 get_weather"}'
```

### 策略管理 API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/v1/policy` | 当前生效策略总览 |
| GET | `/v1/policy/version` | 轻量版本号，供客户端轮询感知变更 |
| GET | `/v1/policy/rules` | L3 规则清单，支持 `severity` / `tag` / `source` / `q` 过滤 |
| GET | `/v1/policy/tools` | L4 工具策略明细 |
| GET | `/v1/policy/files` | 策略文件指纹 |
| GET | `/v1/policy/history` | reload 历史（含失败回滚记录） |
| POST | `/v1/policy/reload` | 手动触发热更新 |
| POST | `/v1/policy/validate` | 预检一段策略 YAML |
| POST | `/v1/policy/nl/preview` | C4 试译自然语言 → YAML |
| POST | `/v1/policy/nl/apply` | C4 应用自然语言 → 落盘 + reload |
| GET | `/v1/policy/nl/examples` | C4 预置示例 |

---

## 审计与可观测性（D1/D2/D3）

每次检测 / 工具调用落库（`sessions` / `risk_events` / `audit_logs` 三表），带策略版本可溯源。

### 审计 API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/v1/audit/status` | 落库统计（队列 / 已写 / 丢弃） |
| GET | `/v1/audit/events` | 风险事件列表，支持 `layer` / `severity` / `action` 过滤 + 分页 |
| GET | `/v1/audit/sessions` | 会话列表 |
| GET | `/v1/audit/sessions/{id}` | 单会话全链路回放 |
| GET | `/v1/audit/sessions/{id}/timeline` | 按时间排序的完整 timeline（D3） |

### 终端日志展示（D2）

```bash
make dashboard        # 实时刷新风险事件流（Ctrl+C 退出）
make dashboard-once   # 打印一次快照
```

---

## 工具调用 Hook（C2）

模型决定调工具时，L4 在执行前拦截，回灌 `tool_result` 错误让模型解释。

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/v1/tools/guard` | 只体检不执行（严格模式，被拦即 403） |
| POST | `/v1/tools/execute` | dry-run 执行（零副作用） |
| GET | `/v1/tools/policy` | 工具策略明细 |

---

## 真实 Agent 接入

任何支持自定义 base URL 的 Agent，只需改一个地址即可透明接入（见 [examples/claude_code_demo.md](examples/claude_code_demo.md)）：

```bash
# Claude Code
export ANTHROPIC_BASE_URL="http://127.0.0.1:8000"

# Cursor / OpenAI 兼容客户端
#   Base URL: http://127.0.0.1:8000/v1
#   API Key:  sk-local（任意占位）
```

---

## 测试

```bash
make test          # 全量测试
make test-unit     # 单元测试
make test-int      # 集成测试
```

攻击样例集在 `tests/fixtures/attacks/attack_samples.json`（30+ 样例，覆盖 OWASP LLM Top 10），
由 `tests/integration/test_attack_coverage.py` 做全链路回归 + 拦截覆盖率统计。

---

## 状态

| 阶段 | 进度 |
|---|---|
| A 基建（仓库 / Docker / FastAPI / DB） | ✅ 4/4 |
| B 检测层（L1-L4） | ✅ 6/6 |
| C 接入与策略（Proxy / Hook / 策略中心 / NL 转策略） | ✅ 4/4 |
| D 审计与可观测性（落库 / CLI 展示 / 会话重放） | ✅ 3/3 |
| E 端到端验证（样例集 / 接入演练 / 文档） | ✅ 3/3 |

**M1 全部完成。** 测试 469 项通过。

---

## License

MIT
