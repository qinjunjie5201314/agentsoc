# AgentSoc 桌面代理客户端

> 本机常驻代理，把 AI 工具的请求转发到集团 AgentSoc 网关，接入安全防护（L1-L4 检测 + 审计 + 策略）。
> 支持两种模式：**显式代理**（合规零风险）和 **透明代理**（终端零改动，需合规授权）。

## 一、这是什么

集团推广阶段，90% 终端是 Windows，每个员工用的 AI 工具五花八门（Cursor、自研脚本、SDK……）。
逐个工具改配置不现实。本客户端在每台终端上跑一个**本地代理**，统一收敛流量。

| 模式 | 原理 | 终端改动 | 合规要求 |
|---|---|---|---|
| 显式代理 | 监听 HTTP 端口，工具把 base_url 指向它 | 改一次 base_url | 无 |
| 透明代理 | 改 hosts + 自签证书 + 监听 443（TLS MITM） | 零改动 | 需授权 |

## 二、使用

### 1. 编译

```bash
go build -o agentsoc-proxy.exe .
```

### 2. 配置

复制 `config.example.json` 为 `config.json`：

```json
{
  "listen": "127.0.0.1:8899",
  "upstream": "http://172.17.0.200:8000",
  "mode": "explicit",
  "target_domains": ["new-api.gaojihealth.cn", "api.openai.com"],
  "ca_dir": "certs"
}
```

### 3. 显式代理模式（默认，合规零风险）

```bash
agentsoc-proxy.exe -upstream http://172.17.0.200:8000
# 工具把 base_url 改为 http://127.0.0.1:8899
```

### 4. 透明代理模式（终端零改动，需管理员权限 + 合规授权）

```bash
# 需以管理员身份运行
agentsoc-proxy.exe -mode transparent -upstream http://172.17.0.200:8000
```

启动后自动：生成自签 CA → 安装根证书 → 写入 hosts → 监听 443 解密转发。
工具无需任何配置，流量自动流经代理。

## 三、工作原理

```
工具 ──请求──> 本地代理 ──转发──> AgentSoc 网关 ──检测──> 内网模型网关
                                          │
                                          └─ 危险 → 400 拦截（审计留痕）
```

- 显式模式：HTTP 透传，代理不解析内容
- 透明模式：TLS MITM 解密后透传（用 tls.Config.GetCertificate 回调按 SNI 动态签发证书）

## 四、透明代理合规须知

透明代理会解密终端上发往目标 AI 域名的 HTTPS 流量，涉及员工隐私。
**必须获得集团安全/合规团队书面授权后方可部署。** 详见 `透明代理合规授权申请.md`。

## 五、分发（集团域控）

- 单文件 `agentsoc-proxy.exe`，通过集团软件分发系统静默下发
- 配合统一配置中心下发 `config.json`
- 透明模式需以管理员权限运行，可注册为 Windows 服务开机自启

### 注册为 Windows 服务（开机自启）

```bash
# 注册服务（需管理员权限）
agentsoc-proxy.exe -install

# 卸载服务
agentsoc-proxy.exe -uninstall
```

服务注册后：
- 服务名 `AgentSocProxy`，开机自动启动
- 以服务运行时从 exe 同目录读 `config.json`
- 停止服务时自动清理透明模式写入的 hosts 条目

### 域控静默下发流程

1. 分发 `agentsoc-proxy.exe` + `config.json` 到目标目录（如 `C:\Program Files\AgentSoc\`）
2. 以管理员权限执行 `agentsoc-proxy.exe -install` 注册服务
3. 服务开机自启，终端零感知

## 六、项目结构

```
desktop-proxy/
├── main.go          # 入口（两种模式 + 服务注册）
├── config.go        # 配置加载
├── proxy.go         # 代理核心（转发 + 流式透传）
├── hosts.go         # hosts 自动管理
├── cert.go          # 自签 CA + 动态签发
├── mitm.go          # 443 透明 MITM 代理
├── service.go       # Windows 服务注册
├── config.example.json
├── 透明代理合规授权申请.md
├── 集团推广-桌面透明代理方案.md
└── README.md
```
