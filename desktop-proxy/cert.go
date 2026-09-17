package main

import (
	"crypto/rand"
	"crypto/rsa"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/pem"
	"fmt"
	"math/big"
	"net"
	"os"
	"path/filepath"
	"sync"
	"time"
)

// CertAuthority 动态签发 TLS 证书的本地 CA。
type CertAuthority struct {
	mu       sync.Mutex
	cert     *x509.Certificate
	key      *rsa.PrivateKey
	cache    map[string]*tls.Certificate
	dir      string
}

// NewCertAuthority 加载或生成自签根 CA。
func NewCertAuthority(dir string) (*CertAuthority, error) {
	if dir == "" {
		dir = "."
	}
	_ = os.MkdirAll(dir, 0700)

	ca := &CertAuthority{cache: map[string]*tls.Certificate{}, dir: dir}
	caPath := filepath.Join(dir, "ca.crt")
	keyPath := filepath.Join(dir, "ca.key")

	if _, err := os.Stat(caPath); err == nil {
		if _, err2 := os.Stat(keyPath); err2 == nil {
			if err := ca.load(caPath, keyPath); err == nil {
				return ca, nil
			}
		}
	}

	// 生成新 CA
	if err := ca.generate(caPath, keyPath); err != nil {
		return nil, err
	}
	return ca, nil
}

func (ca *CertAuthority) load(caPath, keyPath string) error {
	certPEM, err := os.ReadFile(caPath)
	if err != nil {
		return err
	}
	keyPEM, err := os.ReadFile(keyPath)
	if err != nil {
		return err
	}
	certBlock, _ := pem.Decode(certPEM)
	keyBlock, _ := pem.Decode(keyPEM)
	if certBlock == nil || keyBlock == nil {
		return fmt.Errorf("证书 PEM 解析失败")
	}
	cert, err := x509.ParseCertificate(certBlock.Bytes)
	if err != nil {
		return err
	}
	key, err := x509.ParsePKCS1PrivateKey(keyBlock.Bytes)
	if err != nil {
		return err
	}
	ca.cert = cert
	ca.key = key
	return nil
}

func (ca *CertAuthority) generate(caPath, keyPath string) error {
	key, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		return err
	}

	serial, _ := rand.Int(rand.Reader, new(big.Int).Lsh(big.NewInt(1), 128))
	tmpl := &x509.Certificate{
		SerialNumber:          serial,
		Subject:               pkix.Name{CommonName: "AgentSoc Local CA", Organization: []string{"AgentSoc"}},
		NotBefore:             time.Now().Add(-time.Hour),
		NotAfter:              time.Now().AddDate(10, 0, 0),
		KeyUsage:              x509.KeyUsageCertSign | x509.KeyUsageDigitalSignature,
		BasicConstraintsValid: true,
		IsCA:                  true,
	}

	der, err := x509.CreateCertificate(rand.Reader, tmpl, tmpl, &key.PublicKey, key)
	if err != nil {
		return err
	}

	certPEM := pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der})
	keyPEM := pem.EncodeToMemory(&pem.Block{Type: "RSA PRIVATE KEY", Bytes: x509.MarshalPKCS1PrivateKey(key)})

	if err := os.WriteFile(caPath, certPEM, 0600); err != nil {
		return err
	}
	if err := os.WriteFile(keyPath, keyPEM, 0600); err != nil {
		return err
	}

	ca.cert, _ = x509.ParseCertificate(der)
	ca.key = key
	return nil
}

// GetCertificate 为指定域名签发（或从缓存取）TLS 证书。
func (ca *CertAuthority) GetCertificate(host string) (*tls.Certificate, error) {
	ca.mu.Lock()
	defer ca.mu.Unlock()

	if c, ok := ca.cache[host]; ok {
		return c, nil
	}

	serial, _ := rand.Int(rand.Reader, new(big.Int).Lsh(big.NewInt(1), 128))
	tmpl := &x509.Certificate{
		SerialNumber: serial,
		Subject:      pkix.Name{CommonName: host},
		NotBefore:    time.Now().Add(-time.Hour),
		NotAfter:     time.Now().AddDate(1, 0, 0),
		KeyUsage:     x509.KeyUsageDigitalSignature | x509.KeyUsageKeyEncipherment,
		ExtKeyUsage:  []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth},
	}

	// SAN：域名 + 可能的 IP
	if ip := net.ParseIP(host); ip != nil {
		tmpl.IPAddresses = []net.IP{ip}
	} else {
		tmpl.DNSNames = []string{host}
	}

	der, err := x509.CreateCertificate(rand.Reader, tmpl, ca.cert, &ca.key.PublicKey, ca.key)
	if err != nil {
		return nil, err
	}

	certPEM := pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der})
	keyPEM := pem.EncodeToMemory(&pem.Block{Type: "RSA PRIVATE KEY", Bytes: x509.MarshalPKCS1PrivateKey(ca.key)})

	cert, err := tls.X509KeyPair(certPEM, keyPEM)
	if err != nil {
		return nil, err
	}
	ca.cache[host] = &cert
	return &cert, nil
}

// CARootPEM 返回根 CA 证书的 PEM（供安装到系统信任库）。
func (ca *CertAuthority) CARootPEM() []byte {
	return pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: ca.cert.Raw})
}
