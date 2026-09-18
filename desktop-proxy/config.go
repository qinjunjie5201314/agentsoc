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
	// Dashboard 看板监听地址（仅 localhost；留空关闭看板）
	Dashboard string `json:"dashboard,omitempty"`
	// AgentName 终端显示别名（可选；留空则中心显示主机名）
	AgentName string `json:"agent_name,omitempty"`
	// ReportIntervalSec 向中心上报心跳的间隔秒数（默认 60；负数关闭上报）
	ReportIntervalSec int `json:"report_interval_sec,omitempty"`
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
		Dashboard:     "127.0.0.1:8890",
		// 默认每 60s 向中心上报一次心跳（中心超过 180s 未收到判离线）
		ReportIntervalSec: 60,
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
		if fileCfg.Dashboard != "" {
			cfg.Dashboard = fileCfg.Dashboard
		}
		if fileCfg.AgentName != "" {
			cfg.AgentName = fileCfg.AgentName
		}
		// 0 = 未配置（沿用默认 60s）；负数 = 显式关闭上报
		if fileCfg.ReportIntervalSec != 0 {
			cfg.ReportIntervalSec = fileCfg.ReportIntervalSec
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
