// Package main 是 AgentSoc 桌面代理客户端。
// 支持两种模式：
//   - explicit：显式代理，监听 HTTP 端口，工具把 base_url 指向这里
//   - transparent：透明 MITM，监听 443，改 hosts + 自签证书，工具零改动
// 支持以 Windows 服务方式运行（install/uninstall 子命令）。
// 内置本地看板（默认 http://127.0.0.1:8890），展示联通状态/主机名/命中规则。
package main

import (
	"flag"
	"fmt"
	"log"
	"os"
	"os/exec"
	"path/filepath"

	"golang.org/x/sys/windows/svc"
)

var version = "0.4.0"

func main() {
	var (
		listenAddr    = flag.String("listen", "127.0.0.1:8899", "本地监听地址（显式模式）")
		upstream      = flag.String("upstream", "", "上游 AgentSoc 网关地址")
		cfgFile       = flag.String("config", "config.json", "配置文件路径")
		mode          = flag.String("mode", "", "运行模式：explicit / transparent")
		install       = flag.Bool("install", false, "注册为 Windows 服务")
		uninstall     = flag.Bool("uninstall", false, "卸载 Windows 服务")
		setupUpstream = flag.String("setup", "", "一键安装：复制到程序目录 + 写配置 + 注册并启动服务（参数为上游网关地址）")
		showVer       = flag.Bool("version", false, "显示版本")
		dashboard     = flag.String("dashboard", "127.0.0.1:8890", "看板监听地址（留空关闭）")
	)
	flag.Parse()

	if *showVer {
		fmt.Println("AgentSoc Desktop Proxy", version)
		return
	}

	// 服务安装/卸载
	if *install {
		if err := installService(); err != nil {
			log.Fatalf("安装服务失败（需管理员权限）: %v", err)
		}
		return
	}
	if *uninstall {
		if err := uninstallService(); err != nil {
			log.Fatalf("卸载服务失败（需管理员权限）: %v", err)
		}
		return
	}

	// 一键安装
	if *setupUpstream != "" {
		up := *setupUpstream
		m := "explicit"
		if *mode != "" {
			m = *mode
		}
		if err := runSetup(up, m); err != nil {
			log.Fatalf("一键安装失败（需管理员权限）: %v", err)
		}
		fmt.Println("✅ 安装完成！AgentSoc 桌面代理已作为 Windows 服务运行。")
		fmt.Println("   看板地址: http://127.0.0.1:8890")
		return
	}

	cfg, err := LoadConfig(*cfgFile, *listenAddr, *upstream)
	if err != nil {
		log.Fatalf("加载配置失败: %v", err)
	}
	if *mode != "" {
		cfg.Mode = *mode
	}
	if *dashboard != "" {
		cfg.Dashboard = *dashboard
	}

	if cfg.Upstream == "" {
		fmt.Fprintln(os.Stderr, "错误：未配置上游网关地址。")
		fmt.Fprintln(os.Stderr, "  用法: agentsoc-proxy -upstream http://172.17.0.200:8000")
		os.Exit(1)
	}

	log.Printf("AgentSoc 桌面代理 · 版本 %s · 模式 %s", version, cfg.Mode)
	log.Printf("  上游网关: %s", cfg.Upstream)

	// 判断是否以 Windows 服务方式运行
	if isWindowsService() {
		if err := runAsService(cfg); err != nil {
			log.Fatalf("服务运行失败: %v", err)
		}
		return
	}

	// 前台运行
	runForeground(cfg)
}

func isWindowsService() bool {
	b, err := svc.IsWindowsService()
	return err == nil && b
}

func runForeground(cfg *Config) {
	var err error
	switch cfg.Mode {
	case "transparent":
		err = runTransparent(cfg)
	case "explicit", "":
		err = runExplicit(cfg)
	default:
		log.Fatalf("未知模式: %s（支持 explicit / transparent）", cfg.Mode)
	}
	if err != nil {
		log.Fatalf("代理运行失败: %v", err)
	}
}

func runExplicit(cfg *Config) error {
	stats := NewStats(cfg)
	if cfg.Dashboard != "" {
		go stats.ServeDashboard(cfg.Dashboard)
	}
	proxy, err := NewProxy(cfg)
	if err != nil {
		return fmt.Errorf("初始化代理失败: %w", err)
	}
	proxy.stats = stats
	log.Printf("  本地监听: %s", cfg.Listen)
	log.Printf("  工具接入: 把 base_url 改为 http://%s", cfg.Listen)
	return proxy.Run()
}

func runTransparent(cfg *Config) error {
	stats := NewStats(cfg)
	if cfg.Dashboard != "" {
		go stats.ServeDashboard(cfg.Dashboard)
	}
	ca, err := NewCertAuthority(cfg.CADir)
	if err != nil {
		return fmt.Errorf("初始化 CA 失败: %w", err)
	}

	if err := installRootCA(filepath.Join(cfg.CADir, "ca.crt")); err != nil {
		log.Printf("警告：安装根证书失败（可能已安装）: %v", err)
	} else {
		log.Printf("根证书已安装到系统信任库")
	}

	if err := AddHostsEntries(cfg.TargetDomains); err != nil {
		return fmt.Errorf("写入 hosts 失败（需要管理员权限）: %w", err)
	}
	log.Printf("hosts 已写入: %v", cfg.TargetDomains)

	mitm, err := NewMITMProxy(cfg, ca)
	if err != nil {
		return fmt.Errorf("初始化 MITM 代理失败: %w", err)
	}
	mitm.proxy.stats = stats
	log.Printf("透明代理已启动，拦截域名: %v", cfg.TargetDomains)
	return mitm.Run()
}

// installRootCA 用 certutil 把根证书安装到系统信任库。
func installRootCA(caPath string) error {
	if _, err := os.Stat(caPath); err != nil {
		return err
	}
	cmd := exec.Command("certutil", "-addstore", "-f", "Root", caPath)
	out, err := cmd.CombinedOutput()
	if err != nil {
		return fmt.Errorf("%v: %s", err, string(out))
	}
	return nil
}
