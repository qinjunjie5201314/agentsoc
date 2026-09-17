package main

import (
	"encoding/json"
	"os"
)

type Config struct {
	Listen   string `json:"listen"`
	Upstream string `json:"upstream"`
	APIKey   string `json:"api_key,omitempty"`
	LogLevel string `json:"log_level,omitempty"`
}

func DefaultConfig() *Config {
	return &Config{
		Listen:   "127.0.0.1:8899",
		Upstream: "",
		LogLevel: "info",
	}
}

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
	}

	if listenArg != "" && listenArg != "127.0.0.1:8899" {
		cfg.Listen = listenArg
	}
	if upstreamArg != "" {
		cfg.Upstream = upstreamArg
	}

	return cfg, nil
}
