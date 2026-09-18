package main

import (
	"encoding/json"
	"bytes"
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
	agentName string
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
	// 中心上报状态（供看板展示「是否已接入中心总览」）
	reportEnabled   bool
	reportOK        bool
	reportFail      int
	reportInterval  int // 秒
	centerFleetURL  string
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
		agentName: cfg.AgentName,
		mode:      cfg.Mode,
		upstream:  cfg.Upstream,
		startTime: time.Now(),
	}
	go s.healthLoop()
	// 向中心上报心跳（供多终端总览看板统计在线数量）；间隔 <=0 则关闭
	if cfg.ReportIntervalSec > 0 {
		go s.reportLoop(time.Duration(cfg.ReportIntervalSec)*time.Second, cfg.APIKey)
	}
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
	AgentID    string `json:"agent_id"`
	AgentName  string `json:"agent_name,omitempty"`
	Mode       string `json:"mode"`
	Upstream   string `json:"upstream"`
	Version    string `json:"version"`
	StartTime  int64  `json:"start_time"`
	Now        int64  `json:"now"`
	UptimeSec  int64  `json:"uptime_sec"`
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
	Report struct {
		Enabled     bool   `json:"enabled"`
		OK          bool   `json:"ok"`
		FailStreak  int    `json:"fail_streak"`
		IntervalSec int    `json:"interval_sec"`
		CenterURL   string `json:"center_url,omitempty"`
	} `json:"report"`}

// Snapshot 生成当前状态快照（recent 按时间倒序）。
func (s *Stats) Snapshot() *Snapshot {
	s.mu.RLock()
	defer s.mu.RUnlock()
	ss := &Snapshot{
		Hostname:  s.hostname,
		AgentID:   s.hostname,
		AgentName: s.agentName,
		Mode:      s.mode,
		Upstream:  s.upstream,
		Version:   version,
		StartTime: s.startTime.UnixMilli(),
		Now:       time.Now().UnixMilli(),
		UptimeSec: int64(time.Since(s.startTime).Seconds()),
	}
	ss.Connectivity.OK = s.connOK
	ss.Connectivity.LatencyMs = s.connLatency
	ss.Connectivity.LastCheck = s.connLast.UnixMilli()
	ss.Connectivity.Error = s.connErr
	ss.Stats.Total = s.total
	ss.Stats.Blocked = s.blocked
	ss.Stats.Passed = s.passed
	ss.Stats.Errors = s.errs
	ss.Report.Enabled = s.reportEnabled
	ss.Report.OK = s.reportOK
	ss.Report.FailStreak = s.reportFail
	ss.Report.IntervalSec = s.reportInterval
	ss.Report.CenterURL = s.centerFleetURL
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
		w.Write(injectFleetBar(loadDashboardHTML()))
	})
	log.Printf("看板已启动: http://%s/", addr)
	if err := http.ListenAndServe(addr, mux); err != nil {
		log.Printf("看板启动失败: %v", err)
	}
}

// fleetBarScript 注入到看板页面末尾：展示「是否已接入中心总览」并给出跳转入口。
// 之所以运行时注入而不是改 dashboard.html：该文件在部分环境会被加密，不便直接编辑。
const fleetBarScript = `
<style>
.fleetbar{display:flex;align-items:center;gap:10px;flex-wrap:wrap;background:#fff;
  border:1px solid #e6eaf1;border-radius:12px;padding:10px 16px;margin:0 0 16px;
  font-size:13px;box-shadow:0 1px 3px rgba(20,30,50,.08)}
.fb-dot{width:10px;height:10px;border-radius:50%;background:#c3cad6;flex:none}
.fleetbar.ok .fb-dot{background:#1f9d55;box-shadow:0 0 0 3px rgba(31,157,85,.15)}
.fleetbar.bad .fb-dot{background:#d9352b;box-shadow:0 0 0 3px rgba(217,53,43,.15)}
.fb-txt{color:#6b7686}
.fleetbar.bad .fb-txt{color:#d9352b;font-weight:600}
.fb-link{margin-left:auto;color:#2563eb;text-decoration:none;font-weight:600;font-size:13px}
.fb-link:hover{text-decoration:underline}
</style>
<script>
(function(){
  var wrap=document.querySelector(".wrap")||document.body;
  var bar=document.createElement("div");
  bar.id="fleetbar";bar.className="fleetbar";
  bar.innerHTML='<span class="fb-dot"></span><span class="fb-txt">正在检测中心连接…</span>'
    + '<a class="fb-link" target="_blank" rel="noopener">查看全部终端 →</a>';
  var hdr=wrap.querySelector("header");
  if(hdr&&hdr.nextSibling){wrap.insertBefore(bar,hdr.nextSibling);}else{wrap.appendChild(bar);}
  var link=bar.querySelector(".fb-link");
  function refresh(){
    fetch("/api",{cache:"no-store"}).then(function(r){return r.json();}).then(function(d){
      var rp=d.report||{};
      bar.className="fleetbar "+(rp.enabled?(rp.ok?"ok":"bad"):"off");
      var t;
      if(!rp.enabled){t="未接入中心：本机上报已关闭（只显示本机看板）";}
      else if(rp.ok){t="已接入中心 · 每 "+(rp.interval_sec||60)+"s 上报 · 本机已出现在总览中";}
      else{t="中心上报失败 "+(rp.fail_streak||0)+" 次 · 本机暂未出现在总览";}
      bar.querySelector(".fb-txt").textContent=t;
      if(rp.center_url){link.href=rp.center_url;link.style.display="";}
      else{link.style.display="none";}
    }).catch(function(){});
  }
  refresh();setInterval(refresh,5000);
})();
</script>
`

// injectFleetBar 把状态栏脚本插到 </body> 之前；找不到就直接追加。
func injectFleetBar(html []byte) []byte {
	script := []byte(fleetBarScript)
	idx := bytes.LastIndex(html, []byte("</body>"))
	if idx < 0 {
		return append(append([]byte{}, html...), script...)
	}
	out := make([]byte, 0, len(html)+len(script))
	out = append(out, html[:idx]...)
	out = append(out, script...)
	out = append(out, html[idx:]...)
	return out
}
