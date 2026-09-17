package main

import (
	"crypto/tls"
	"fmt"
	"log"
	"net"
	"net/http"
)

// MITMProxy 透明代理：监听 443，TLS 中间人解密后转发。
// 用 tls.Config.GetCertificate 回调自动解析 SNI 并动态签发证书。
type MITMProxy struct {
	cfg   *Config
	ca    *CertAuthority
	proxy *Proxy
}

// NewMITMProxy 创建 MITM 代理。
func NewMITMProxy(cfg *Config, ca *CertAuthority) (*MITMProxy, error) {
	p, err := NewProxy(cfg)
	if err != nil {
		return nil, err
	}
	return &MITMProxy{cfg: cfg, ca: ca, proxy: p}, nil
}

// Run 监听 443，TLS MITM 解密后转发。
func (m *MITMProxy) Run() error {
	addr := ":443"
	ln, err := net.Listen("tcp", addr)
	if err != nil {
		return fmt.Errorf("监听 %s 失败（需管理员权限，且端口未被占用）: %w", addr, err)
	}

	// 关键：GetCertificate 回调在 TLS 握手时根据 SNI 动态签发证书
	tlsConfig := &tls.Config{
		GetCertificate: func(chi *tls.ClientHelloInfo) (*tls.Certificate, error) {
			log.Printf("[mitm] 拦截域名: %s", chi.ServerName)
			return m.ca.GetCertificate(chi.ServerName)
		},
	}

	tlsLn := tls.NewListener(ln, tlsConfig)
	log.Printf("MITM 透明代理监听 %s ...", addr)

	// 解密后的 HTTP 交给 handler，复用 proxy 的转发逻辑
	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		m.proxy.handle(w, r)
	})

	return http.Serve(tlsLn, handler)
}
