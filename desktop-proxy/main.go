// Package main 是 AgentSoc 桌面代理客户端（显式代理模式）。
package main

import (
	"flag"
	"fmt"
	"log"
	"os"
)

var version = "0.1.0"

func main() {
	var (
		listenAddr = flag.String("listen", "127.0.0.1:8899", "本地监听地址")
		upstream   = flag.String("upstream", "", "上游 AgentSoc 网关地址")
		cfgFile    = flag.String("config", "config.json", "配置文件路径")
		showVer    = flag.Bool("version", false, "显示版本")
	)
	flag.Parse()

	if *showVer {
		fmt.Println("AgentSoc Desktop Proxy", version)
		return
	}

	cfg, err := LoadConfig(*cfgFile, *listenAddr, *upstream)
	if err != nil {
		log.Fatalf("加载配置失败: %v", err)
	}

	if cfg.Upstream == "" {
		fmt.Fprintln(os.Stderr, "错误：未配置上游网关地址。")
		fmt.Fprintln(os.Stderr, "  用法: agentsoc-proxy -upstream http://172.17.0.200:8000")
		os.Exit(1)
	}

	proxy, err := NewProxy(cfg)
	if err != nil {
		log.Fatalf("初始化代理失败: %v", err)
	}

	log.Printf("AgentSoc 桌面代理启动 · 版本 %s", version)
	log.Printf("  本地监听: %s", cfg.Listen)
	log.Printf("  上游网关: %s", cfg.Upstream)
	log.Printf("  工具接入: 把 base_url 改为 http://%s", cfg.Listen)

	if err := proxy.Run(); err != nil {
		log.Fatalf("代理运行失败: %v", err)
	}
}
