package main

import (
	"encoding/json"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"sync"
	"time"
)

// Event 是一次请求的检测记录。
type Event struct {
	TS        int64    `json:"ts"`
	Method    string   `json:"method"`
	Path      string   `json:"path"`
	Status    int      `json:"status"`
	Action    string   `json:"action"` // block / pass / error
	Risk      string   `json:"risk"`
	Score     float64  `json:"score"`
	Rules     []string `json:"rules"`
	PolicyVer string   `json:"policy_ver"`
	Message   string   `json:"message"`
}

// Stats 是代理的本地运行状态（线程安全，供看板读取）。
type Stats struct {
	mu        sync.RWMutex
	hostname  string
	mode      string
	upstream  string
	startTime time.Time

	total   int
	blocked int
	passed  int
	errs    int

	recent []*Event // 环形缓冲，最多 maxRecent 条

	connOK      bool
	connLatency int64 // ms
	connLast    time.Time
	connErr     string
}

const maxRecent = 200

// NewStats 创建运行状态并开始后台健康检查。
func NewStats(cfg *Config) *Stats {
	host, err := os.Hostname()
	if err != nil {
		host = "unknown"
	}
	s := &Stats{
		hostname:  host,
		mode:      cfg.Mode,
		upstream:  cfg.Upstream,
		startTime: time.Now(),
	}
	go s.healthLoop()
	return s
}

// healthLoop 每 5 秒探活一次上游网关。
func (s *Stats) healthLoop() {
	ticker := time.NewTicker(5 * time.Second)
	defer ticker.Stop()
	s.check()
	for range ticker.C {
		s.check()
	}
}

// check 探测上游 /v1/info 联通性。
func (s *Stats) check() {
	s.mu.RLock()
	up := s.upstream
	s.mu.RUnlock()
	if up == "" {
		s.mu.Lock()
		s.connOK = false
		s.connErr = "未配置上游网关"
		s.connLast = time.Now()
		s.mu.Unlock()
		return
	}
	u := up
	if u[len(u)-1] != '/' {
		u += "/"
	}
	u += "v1/info"
	start := time.Now()
	client := &http.Client{Timeout: 3 * time.Second}
	resp, err := client.Get(u)
	elapsed := time.Since(start).Milliseconds()
	s.mu.Lock()
	s.connLast = time.Now()
	if err != nil {
		s.connOK = false
		s.connErr = err.Error()
		s.mu.Unlock()
		return
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 500 {
		s.connOK = false
		s.connErr = http.StatusText(resp.StatusCode)
	} else if resp.StatusCode == 401 || resp.StatusCode == 403 {
		s.connOK = true
		s.connErr = "网关需鉴权（已联通）"
	} else {
		s.connOK = true
		s.connErr = ""
	}
	s.connLatency = elapsed
	s.mu.Unlock()
}

// RecordResponse 记录一次转发结果。body 为响应体（仅 4xx/5xx 时用于解析拦截信息）。
func (s *Stats) RecordResponse(r *http.Request, resp *http.Response, body []byte) {
	ev := &Event{
		TS:     time.Now().UnixMilli(),
		Method: r.Method,
		Path:   r.URL.Path,
		Status: resp.StatusCode,
	}
	if resp.StatusCode >= 400 {
		if a := parseAgentsentry(body); a != nil {
			ev.Action = "block"
			ev.Risk = a.risk
			ev.Score = a.score
			ev.Rules = a.rules
			ev.PolicyVer = a.policyVer
			ev.Message = a.message
		} else {
			ev.Action = "error"
			ev.Message = http.StatusText(resp.StatusCode)
		}
	} else {
		ev.Action = "pass"
	}

	s.mu.Lock()
	s.total++
	switch ev.Action {
	case "block":
		s.blocked++
	case "error":
		s.errs++
	default:
		s.passed++
	}
	s.recent = append(s.recent, ev)
	if len(s.recent) > maxRecent {
		s.recent = s.recent[len(s.recent)-maxRecent:]
	}
	s.mu.Unlock()
}

type agentsentryInfo struct {
	risk      string
	score     float64
	rules     []string
	policyVer string
	message   string
}

// parseAgentsentry 从网关的 400 拦截体里抽取命中规则信息。
func parseAgentsentry(body []byte) *agentsentryInfo {
	if len(body) == 0 {
		return nil
	}
	var doc struct {
		Agentsentry struct {
			RiskLevel          string  `json:"risk_level"`
			Action             string  `json:"action"`
			ClassifierMaxScore float64 `json:"classifier_max_score"`
			Policy             struct {
				Version string `json:"version"`
			} `json:"policy"`
			RuleHits []struct {
				RuleID string `json:"rule_id"`
			} `json:"rule_hits"`
			Reasons []string `json:"reasons"`
		} `json:"agentsentry"`
	}
	if err := json.Unmarshal(body, &doc); err != nil {
		return nil
	}
	if doc.Agentsentry.Action == "" {
		return nil
	}
	info := &agentsentryInfo{
		risk:      doc.Agentsentry.RiskLevel,
		score:     doc.Agentsentry.ClassifierMaxScore,
		policyVer: doc.Agentsentry.Policy.Version,
	}
	for _, h := range doc.Agentsentry.RuleHits {
		info.rules = append(info.rules, h.RuleID)
	}
	for i, rs := range doc.Agentsentry.Reasons {
		if i > 0 {
			info.message += "; "
		}
		info.message += rs
	}
	return info
}

// Snapshot 是 /api 返回的 JSON 结构。
type Snapshot struct {
	Hostname   string `json:"hostname"`
	Mode       string `json:"mode"`
	Upstream   string `json:"upstream"`
	Version    string `json:"version"`
	StartTime  int64  `json:"start_time"`
	Now        int64  `json:"now"`
	Connectivity struct {
		OK        bool  `json:"ok"`
		LatencyMs int64 `json:"latency_ms"`
		LastCheck int64 `json:"last_check"`
		Error     string `json:"error"`
	} `json:"connectivity"`
	Stats struct {
		Total   int `json:"total"`
		Blocked int `json:"blocked"`
		Passed  int `json:"passed"`
		Errors  int `json:"errors"`
	} `json:"stats"`
	Recent []*Event `json:"recent"`
}

// Snapshot 生成当前状态快照（recent 按时间倒序）。
func (s *Stats) Snapshot() *Snapshot {
	s.mu.RLock()
	defer s.mu.RUnlock()
	ss := &Snapshot{
		Hostname:  s.hostname,
		Mode:      s.mode,
		Upstream:  s.upstream,
		Version:   version,
		StartTime: s.startTime.UnixMilli(),
		Now:       time.Now().UnixMilli(),
	}
	ss.Connectivity.OK = s.connOK
	ss.Connectivity.LatencyMs = s.connLatency
	ss.Connectivity.LastCheck = s.connLast.UnixMilli()
	ss.Connectivity.Error = s.connErr
	ss.Stats.Total = s.total
	ss.Stats.Blocked = s.blocked
	ss.Stats.Passed = s.passed
	ss.Stats.Errors = s.errs
	n := len(s.recent)
	ss.Recent = make([]*Event, n)
	for i := 0; i < n; i++ {
		ss.Recent[i] = s.recent[n-1-i]
	}
	return ss
}

// loadDashboardHTML 从 exe 同目录读取 dashboard.html（安装包会随附该文件）。
func loadDashboardHTML() []byte {
	if exe, err := os.Executable(); err == nil {
		p := filepath.Join(filepath.Dir(exe), "dashboard.html")
		if b, err := os.ReadFile(p); err == nil {
			return b
		}
	}
	return []byte("<!doctype html><html lang=\"zh-CN\"><body style=\"font-family:sans-serif;padding:24px\">" +
		"<h2>AgentSoc 桌面代理看板</h2><p>未找到 dashboard.html，请确认安装包完整（dashboard.html 需与 exe 同目录）。</p></body></html>")
}

// ServeDashboard 在 addr 上启动看板 HTTP 服务（应仅绑定 localhost）。
func (s *Stats) ServeDashboard(addr string) {
	mux := http.NewServeMux()
	mux.HandleFunc("/api", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json; charset=utf-8")
		w.Header().Set("Cache-Control", "no-store")
		json.NewEncoder(w).Encode(s.Snapshot())
	})
	mux.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		w.Write(loadDashboardHTML())
	})
	log.Printf("看板已启动: http://%s/", addr)
	if err := http.ListenAndServe(addr, mux); err != nil {
		log.Printf("看板启动失败: %v", err)
	}
}
