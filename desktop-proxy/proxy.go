package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"net/url"
	"strings"
	"time"
)

type Proxy struct {
	cfg      *Config
	upstream *url.URL
	client   *http.Client
	stats    *Stats // 可选的本地运行状态（看板用）
}

func NewProxy(cfg *Config) (*Proxy, error) {
	u, err := url.Parse(cfg.Upstream)
	if err != nil {
		return nil, fmt.Errorf("解析上游地址失败: %w", err)
	}
	if u.Scheme == "" {
		u.Scheme = "http"
	}
	return &Proxy{
		cfg:      cfg,
		upstream: u,
		stats:    nil,
		client: &http.Client{
			Timeout: 0,
			Transport: &http.Transport{
				MaxIdleConns:        100,
				MaxIdleConnsPerHost: 20,
				IdleConnTimeout:     90 * time.Second,
			},
		},
	}, nil
}

func (p *Proxy) Run() error {
	mux := http.NewServeMux()
	mux.HandleFunc("/", p.handle)
	srv := &http.Server{Addr: p.cfg.Listen, Handler: mux}
	log.Printf("正在监听 %s ...", p.cfg.Listen)
	return srv.ListenAndServe()
}

func (p *Proxy) handle(w http.ResponseWriter, r *http.Request) {
	start := time.Now()
	path := r.URL.Path

	target := *p.upstream
	target.Path = strings.TrimSuffix(target.Path, "/") + path
	target.RawQuery = r.URL.RawQuery

	log.Printf("[proxy] %s %s -> %s", r.Method, path, target.String())

	body, err := io.ReadAll(r.Body)
	if err != nil {
		http.Error(w, "读取请求体失败", http.StatusBadRequest)
		return
	}
	r.Body.Close()

	isStream := detectStream(body)

	req, err := http.NewRequest(r.Method, target.String(), bytes.NewReader(body))
	if err != nil {
		http.Error(w, "构造转发请求失败", http.StatusInternalServerError)
		return
	}
	req.Header = r.Header.Clone()
	req.Host = target.Host

	resp, err := p.client.Do(req)
	if err != nil {
		log.Printf("[proxy] 上游请求失败: %v", err)
		http.Error(w, fmt.Sprintf("上游网关不可达: %v", err), http.StatusBadGateway)
		return
	}
	defer resp.Body.Close()

	// 拦截/错误响应：读全量 body 以解析命中规则信息，写入看板
	if resp.StatusCode >= 400 && !isStream {
		b, _ := io.ReadAll(resp.Body)
		if p.stats != nil {
			p.stats.RecordResponse(r, resp, b)
		}
		for k, vv := range resp.Header {
			for _, v := range vv {
				w.Header().Add(k, v)
			}
		}
		w.WriteHeader(resp.StatusCode)
		w.Write(b)
		return
	}

	// 正常响应：仅记录概要（不缓冲大响应体）
	if p.stats != nil {
		p.stats.RecordResponse(r, resp, nil)
	}

	for k, vv := range resp.Header {
		for _, v := range vv {
			w.Header().Add(k, v)
		}
	}
	w.WriteHeader(resp.StatusCode)

	if isStream {
		p.streamCopy(w, resp)
	} else {
		io.Copy(w, resp.Body)
	}

	log.Printf("[proxy] %s %s -> %d (%.1fms)", r.Method, path, resp.StatusCode,
		float64(time.Since(start).Microseconds())/1000.0)
}

func (p *Proxy) streamCopy(w http.ResponseWriter, resp *http.Response) {
	flusher, ok := w.(http.Flusher)
	if !ok {
		io.Copy(w, resp.Body)
		return
	}
	buf := make([]byte, 4096)
	for {
		n, err := resp.Body.Read(buf)
		if n > 0 {
			if _, werr := w.Write(buf[:n]); werr != nil {
				return
			}
			flusher.Flush()
		}
		if err != nil {
			if err != io.EOF {
				log.Printf("[proxy] 流式读取结束: %v", err)
			}
			return
		}
	}
}

func detectStream(body []byte) bool {
	var m map[string]any
	if err := json.Unmarshal(body, &m); err != nil {
		return false
	}
	if v, ok := m["stream"].(bool); ok {
		return v
	}
	return false
}
