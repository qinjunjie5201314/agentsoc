package main

import (
	"fmt"
	"log"
	"os"
	"os/exec"
	"path/filepath"

	"golang.org/x/sys/windows/svc"
)

const serviceName = "AgentSocProxy"

// installService 用 sc create 把当前 exe 注册为 Windows 服务（开机自启）。
func installService() error {
	exe, err := os.Executable()
	if err != nil {
		return err
	}
	abs, _ := filepath.Abs(exe)

	// 从当前工作目录找 config.json（服务运行时工作目录是 System32，需显式指定）
	cmd := exec.Command("sc", "create", serviceName,
		"binPath=", fmt.Sprintf(`"%s" -config "%s"`, abs, configPathForService()),
		"start=", "auto",
		"DisplayName=", "AgentSoc Desktop Proxy",
	)
	out, err := cmd.CombinedOutput()
	if err != nil {
		return fmt.Errorf("%v: %s", err, string(out))
	}
	fmt.Println("服务已注册:", serviceName)
	fmt.Println(string(out))
	return nil
}

// uninstallService 删除服务。
func uninstallService() error {
	cmd := exec.Command("sc", "delete", serviceName)
	out, err := cmd.CombinedOutput()
	if err != nil {
		return fmt.Errorf("%v: %s", err, string(out))
	}
	fmt.Println("服务已删除:", serviceName)
	return nil
}

// configPathForService 返回服务运行时使用的 config.json 绝对路径。
// 服务的工作目录是 C:/Windows/System32，所以 config.json 应放在 exe 同目录。
func configPathForService() string {
	exe, _ := os.Executable()
	dir := filepath.Dir(exe)
	return filepath.Join(dir, "config.json")
}

// runAsService 以 Windows 服务方式运行。
func runAsService(cfg *Config) error {
	return svc.Run(serviceName, &proxyService{cfg: cfg})
}

// proxyService 实现 svc.Handler 接口。
type proxyService struct {
	cfg *Config
}

func (p *proxyService) Execute(args []string, r <-chan svc.ChangeRequest, changes chan<- svc.Status) (bool, uint32) {
	const cmdsAccepted = svc.AcceptStop | svc.AcceptShutdown
	changes <- svc.Status{State: svc.StartPending}

	// 在后台启动代理
	errCh := make(chan error, 1)
	go func() {
		var err error
		switch p.cfg.Mode {
		case "transparent":
			err = runTransparent(p.cfg)
		default:
			err = runExplicit(p.cfg)
		}
		errCh <- err
	}()

	changes <- svc.Status{State: svc.Running, Accepts: cmdsAccepted}

	for {
		select {
		case c := <-r:
			switch c.Cmd {
			case svc.Interrogate:
				changes <- c.CurrentStatus
			case svc.Stop, svc.Shutdown:
				changes <- svc.Status{State: svc.StopPending}
				log.Printf("收到停止信号，正在关闭...")
				cleanup(p.cfg)
				return false, 0
			default:
				log.Printf("未处理的服务控制: %d", c.Cmd)
			}
		case err := <-errCh:
			log.Printf("代理退出: %v", err)
			cleanup(p.cfg)
			return false, 1
		}
	}
}

// cleanup 清理透明模式写入的 hosts 条目。
func cleanup(cfg *Config) {
	if cfg.Mode == "transparent" {
		if err := RemoveHostsEntries(); err != nil {
			log.Printf("清理 hosts 失败: %v", err)
		} else {
			log.Printf("已清理 hosts 条目")
		}
	}
}
