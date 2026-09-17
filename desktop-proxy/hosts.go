package main

import (
	"bufio"
	"fmt"
	"os"
	"strings"
)

const hostsPath = `C:/Windows/System32/drivers/etc/hosts`

// HostsMarker 标记 AgentSoc 管理的 hosts 条目，便于精确清理。
const hostsMarker = "# agentsoc-managed"

// AddHostsEntries 把目标域名映射到 127.0.0.1，写入 hosts 文件。
// 幂等：已存在（含标记）的条目不重复添加。
func AddHostsEntries(domains []string) error {
	existing, err := os.ReadFile(hostsPath)
	if err != nil {
		return fmt.Errorf("读取 hosts 失败（需要管理员权限）: %w", err)
	}

	lines := strings.Split(string(existing), "\n")
	have := map[string]bool{}
	for _, ln := range lines {
		trimmed := strings.TrimSpace(ln)
		if trimmed == "" || strings.HasPrefix(trimmed, "#") {
			continue
		}
		// 提取域名（第二列）
		fields := strings.Fields(trimmed)
		if len(fields) >= 2 {
			have[strings.ToLower(fields[1])] = true
		}
	}

	var toAdd []string
	for _, d := range domains {
		d = strings.ToLower(strings.TrimSpace(d))
		if d == "" || have[d] {
			continue
		}
		toAdd = append(toAdd, d)
		have[d] = true
	}
	if len(toAdd) == 0 {
		return nil
	}

	// 追加
	f, err := os.OpenFile(hostsPath, os.O_APPEND|os.O_WRONLY, 0644)
	if err != nil {
		return fmt.Errorf("打开 hosts 失败: %w", err)
	}
	defer f.Close()

	w := bufio.NewWriter(f)
	w.WriteString("\n" + hostsMarker + "\n")
	for _, d := range toAdd {
		w.WriteString(fmt.Sprintf("127.0.0.1 %s\n", d))
	}
	w.WriteString(hostsMarker + "\n")
	if err := w.Flush(); err != nil {
		return err
	}
	return nil
}

// RemoveHostsEntries 清理 AgentSoc 之前写入的 hosts 条目（按标记定位）。
func RemoveHostsEntries() error {
	data, err := os.ReadFile(hostsPath)
	if err != nil {
		return err
	}
	lines := strings.Split(string(data), "\n")
	var out []string
	inManaged := false
	for _, ln := range lines {
		trimmed := strings.TrimSpace(ln)
		if trimmed == hostsMarker {
			inManaged = !inManaged
			continue
		}
		if inManaged {
			continue
		}
		out = append(out, ln)
	}
	return os.WriteFile(hostsPath, []byte(strings.Join(out, "\n")), 0644)
}
