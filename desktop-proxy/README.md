# AgentSoc 桌面代理客户端（显式代理模式）

> 本机常驻的 OpenAI 兼容代理，把 AI 工具的请求转发到集团 AgentSoc 网关。
> 工具只需改一次 base_url，即可接入安全防护（L1-L4 检测 + 审计 + 策略）。

## 一、这是什么

集团推广阶段，90% 终端是 Windows，每个员工用的 AI 工具五花八门（Cursor、Claude Code、自研脚本……）。
逐个工具改配置不现实。本客户端在每台终端上跑一个**本地代理**，统一收敛流量。

**显式代理模式**（当前实现）：工具把 base_url 指向本机 `http://127.0.0.1:8899`，代理转发到集团网关。
- ✅ 合规零风险（不改 hosts、不发证书、不做 MITM）
- ✅ 工具侧只需改一次 base_url

## 二、使用

### 1. 编译

```bash
go build -o agentsoc-proxy.exe .
```

### 2. 配置

复制 `config.example.json` 为 `config.json`，改上游网关地址：

```json
{
  "listen": "127.0.0.1:8899",
  "upstream": "http://172.17.0.200:8000"
}
```

或直接用命令行参数：

```bash
agentsoc-proxy.exe -upstream http://172.17.0.200:8000
```

### 3. 工具接入

把 AI 工具的 base_url 改为：

```
http://127.0.0.1:8899
```

（各工具改法不同，Cursor 在设置里改 Base URL，Claude Code 设环境变量 `ANTHROPIC_BASE_URL=http://127.0.0.1:8899`）

## 三、工作原理

```
工具 ──请求──> 127.0.0.1:8899 (本地代理) ──转发──> AgentSoc 网关 ──检测──> 内网模型网关
                                                    │
                                                    └─ 危险 → 400 拦截（审计留痕）
```

- 代理透传 OpenAI 兼容的 `/v1/chat/completions`（流式 + 非流式）
- 不解析、不修改请求内容（透传），检测在 AgentSoc 网关完成

## 四、后续演进（手段②完整形态）

当前是「显式代理」，后续升级到「透明代理」（终端零改动）：

1. 改 hosts 把 `api.openai.com` 等域名指向 `127.0.0.1`
2. 监听 443，自签根证书装系统信任库（TLS MITM）
3. 拦截并解密 HTTPS 流量，转发到集团网关

> 透明代理涉及合规（解密员工流量），需集团安全/合规团队书面授权后方可推广。

## 五、分发（集团域控）

- 单文件 `agentsoc-proxy.exe`，通过集团软件分发系统（如 SCCM/域控）静默下发
- 配合统一配置中心下发 `config.json`（上游地址、端口）
- 可注册为 Windows 服务开机自启

## 六、项目结构

```
desktop-proxy/
├── main.go          # 入口
├── config.go        # 配置加载
├── proxy.go         # 代理核心（转发 + 流式透传）
├── config.example.json
└── README.md
```
