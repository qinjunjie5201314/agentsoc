package main

import (
	"fmt"
	"log"
	"os"
	"os/exec"
	"path/filepath"
)

// runSetup 一键安装：写 config → 注册服务（带 -config 参数）→ 启动服务。
// 前提：exe 已在安装目录（由安装包 Inno Setup 负责放置）。
func runSetup(upstream, mode string) error {
	// 1. 拿到当前 exe 路径
	exe, err := os.Executable()
	if err != nil {
		return err
	}
	exe, _ = filepath.Abs(exe)
	dir := filepath.Dir(exe)
	cfgPath := filepath.Join(dir, "config.json")

	// 2. 写入 config.json（在 exe 同目录）
	cfg := fmt.Sprintf(`{
  "listen": "127.0.0.1:8899",
  "mode": %q,
  "upstream": %q,
  "target_domains": ["new-api.gaojihealth.cn", "api.openai.com", "api.anthropic.com"],
  "ca_dir": "certs"
}
`, mode, upstream)
	if err := os.WriteFile(cfgPath, []byte(cfg), 0644); err != nil {
		return fmt.Errorf("写入配置失败: %w", err)
	}
	log.Printf("已写入配置 %s", cfgPath)

	// 3. 注册服务 —— 关键：binPath 带 -config 指向绝对路径
	binPath := fmt.Sprintf(`"%s" -config "%s"`, exe, cfgPath)
	regCmd := exec.Command("sc", "create", serviceName,
		"binPath=", binPath,
		"start=", "auto",
		"DisplayName=", "AgentSoc Desktop Proxy",
	)
	if out, err := regCmd.CombinedOutput(); err != nil {
		return fmt.Errorf("注册服务失败: %v: %s", err, string(out))
	}
	log.Printf("服务已注册: %s", serviceName)

	// 4. 启动服务
	startCmd := exec.Command("sc", "start", serviceName)
	if out, err := startCmd.CombinedOutput(); err != nil {
		return fmt.Errorf("启动服务失败: %v: %s", err, string(out))
	}
	log.Printf("服务已启动: %s", serviceName)

	return nil
}
