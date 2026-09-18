package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"strings"
	"time"
)

// maxReportEvents 单次上报携带的最近流水条数（控制请求体积）。
const maxReportEvents = 20

// reportLoop 定期把本机快照上报给中心 AgentSoc 的 /v1/fleet/report。
//
// 作用：让中心看板汇总「整体终端在线数量」。上报失败不影响本地代理转发，
// 只在连续失败时记日志；中心侧超过 offline_after 秒未收到上报即判定离线。
func (s *Stats) reportLoop(interval time.Duration, apiKey string) {
	if interval <= 0 {
		return
	}
	s.mu.Lock()
	s.reportEnabled = true
	s.reportInterval = int(interval.Seconds())
	s.mu.Unlock()

	client := &http.Client{Timeout: 8 * time.Second}
	failStreak := 0
	for {
		if url, ok := s.reportURL(); ok {
			if err := s.postReport(client, url, apiKey); err != nil {
				failStreak++
				s.setReportResult(false, failStreak+1)
				// 只记首错与每 10 次，避免日志刷屏
				if failStreak == 1 || failStreak%10 == 0 {
					log.Printf("终端上报失败（连续 %d 次）: %v", failStreak, err)
				}
			} else {
				if failStreak > 0 {
					log.Printf("终端上报已恢复（此前连续失败 %d 次）", failStreak)
				}
				failStreak = 0
				s.setReportResult(true, 0)
			}
		}
		time.Sleep(interval)
	}
}

// reportURL 拼出中心上报地址；未配置上游网关时返回 false。
func (s *Stats) reportURL() (string, bool) {
	s.mu.RLock()
	up := s.upstream
	s.mu.RUnlock()
	up = strings.TrimRight(strings.TrimSpace(up), "/")
	if up == "" {
		return "", false
	}
	if !strings.HasPrefix(up, "http://") && !strings.HasPrefix(up, "https://") {
		up = "http://" + up
	}
	return up + "/v1/fleet/report", true
}

// postReport 把当前快照 POST 到中心；中心返回非 2xx 视为失败。
func (s *Stats) postReport(client *http.Client, url, apiKey string) error {
	snap := s.Snapshot()
	if len(snap.Recent) > maxReportEvents {
		snap.Recent = snap.Recent[:maxReportEvents]
	}
	body, err := json.Marshal(snap)
	if err != nil {
		return err
	}
	req, err := http.NewRequest(http.MethodPost, url, bytes.NewReader(body))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	if apiKey != "" {
		req.Header.Set("X-API-Key", apiKey)
	}
	resp, err := client.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	_, _ = io.Copy(io.Discard, resp.Body)
	if resp.StatusCode >= 300 {
		return fmt.Errorf("中心返回 HTTP %d", resp.StatusCode)
	}
	return nil
}

// setReportResult 记录最近一次上报结果，并在首次调用时算出中心总览页地址。
func (s *Stats) setReportResult(ok bool, failStreak int) {
	// 先在锁外算出总览页地址：reportURL() 内部也要取锁，
	// sync.RWMutex 不可重入，持写锁时再 RLock 会死锁。
	s.mu.RLock()
	url := s.centerFleetURL
	s.mu.RUnlock()
	if url == "" {
		if u, ok2 := s.reportURL(); ok2 {
			url = strings.TrimSuffix(u, "/v1/fleet/report") + "/fleet"
		}
	}
	s.mu.Lock()
	s.reportOK = ok
	s.reportFail = failStreak
	if s.centerFleetURL == "" && url != "" {
		s.centerFleetURL = url
	}
	s.mu.Unlock()
}
