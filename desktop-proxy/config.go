package main

import (
	"encoding/json"
	"os"
)

// Config 是桌面代理的配置。
type Config struct {
	// Listen 本地监听地址（显式代理模式：工具 base_url 指向这里）
	Listen string `json:"listen"`
	// Upstream 上游 AgentSoc 网关地址（http://ip:port）
	Upstream string `json:"upstream"`
	// APIKey 可选，上游网关的管理 key
	APIKey string `json:"api_key,omitempty"`
	// LogLevel 日志级别
	LogLevel string `json:"log_level,omitempty"`
	// Mode 运行模式：explicit（显式代理）/ transparent（透明 MITM）
	Mode string `json:"mode,omitempty"`
	// TargetDomains 透明模式下要拦截的 AI 域名（写入 hosts）
	TargetDomains []string `json:"target_domains,omitempty"`
	// CADir 自签根证书存放目录
	CADir string `json:"ca_dir,omitempty"`
}

// DefaultConfig 返回默认配置。
func DefaultConfig() *Config {
	return &Config{
		Listen:        "127.0.0.1:8899",
		Upstream:      "",
		LogLevel:      "info",
		Mode:          "explicit",
		TargetDomains: []string{"new-api.gaojihealth.cn", "api.openai.com", "api.anthropic.com"},
		CADir:         "certs",
	}
}

// LoadConfig 加载配置：优先用命令行参数覆盖，否则读配置文件，最后用默认值。
func LoadConfig(path, listenArg, upstreamArg string) (*Config, error) {
	cfg := DefaultConfig()

	if data, err := os.ReadFile(path); err == nil {
		var fileCfg Config
		if err := json.Unmarshal(data, &fileCfg); err != nil {
			return nil, err
		}
		if fileCfg.Listen != "" {
			cfg.Listen = fileCfg.Listen
		}
		if fileCfg.Upstream != "" {
			cfg.Upstream = fileCfg.Upstream
		}
		if fileCfg.APIKey != "" {
			cfg.APIKey = fileCfg.APIKey
		}
		if fileCfg.LogLevel != "" {
			cfg.LogLevel = fileCfg.LogLevel
		}
		if fileCfg.Mode != "" {
			cfg.Mode = fileCfg.Mode
		}
		if len(fileCfg.TargetDomains) > 0 {
			cfg.TargetDomains = fileCfg.TargetDomains
		}
		if fileCfg.CADir != "" {
			cfg.CADir = fileCfg.CADir
		}
	}

	if listenArg != "" && listenArg != "127.0.0.1:8899" {
		cfg.Listen = listenArg
	}
	if upstreamArg != "" {
		cfg.Upstream = upstreamArg
	}

	return cfg, nil
}
