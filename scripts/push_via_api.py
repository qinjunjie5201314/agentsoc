# -*- coding: utf-8 -*-
"""当 github.com 不可直连时，用 GitHub REST API 把本地改动提交到远端。

背景：本机到 github.com:443 不通（git push 失败），但 api.github.com 可直连，
因此改用 Git Data API 走 HTTP 提交，效果等价于一次 git commit + push。

用法：
    python push_via_api.py --token <PAT>                 # 真实提交
    python push_via_api.py --token <PAT> --dry-run       # 只预览，不改动远端
    python push_via_api.py --token <PAT> -m "提交说明"

流程（Git Data API）：
    1. 取 refs/heads/main 当前 commit sha
    2. 取该 commit 的 base tree sha
    3. 为待提交文件逐个创建 blob
    4. 以 base_tree 为基础创建新 tree
    5. 创建 commit（parent = 当前 commit）
    6. 更新 refs/heads/main 指向新 commit

只会提交「本地与远端不同」的文件，且自动跳过缓存/编译产物。
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import os
import sys

try:
    import urllib.request as urlreq
    import urllib.error as urlerr
except ImportError:  # pragma: no cover
    urlreq = None

OWNER = "qinjunjie5201314"
REPO = "agentsoc"
BRANCH = "main"
API = "https://api.github.com"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SKIP_DIRS = {".git", ".workbuddy", "__pycache__", ".venv", "venv", "certs",
             "output", ".pytest_cache", ".ruff_cache", "node_modules"}
SKIP_EXT = {".exe", ".pem", ".key", ".crt", ".db", ".zip", ".log", ".pyc", ".dat"}
SKIP_NAMES = {"config.json", ".env"}


def local_files() -> dict[str, bytes]:
    """扫描本地待提交文件（相对路径 -> 字节内容）。"""
    out: dict[str, bytes] = {}
    for dp, dns, fns in os.walk(ROOT):
        dns[:] = [d for d in dns if d not in SKIP_DIRS]
        for fn in fns:
            if os.path.splitext(fn)[1].lower() in SKIP_EXT or fn in SKIP_NAMES:
                continue
            full = os.path.join(dp, fn)
            rel = os.path.relpath(full, ROOT).replace("\\", "/")
            try:
                out[rel] = io.open(full, "rb").read()
            except Exception:
                continue
    return out


def http(method: str, path: str, token: str, payload=None):
    url = API + path
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urlreq.Request(url, data=data, method=method)
    req.add_header("Authorization", "token " + token)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "agentsoc-push")
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urlreq.urlopen(req, timeout=60) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body) if body else {}
    except urlerr.HTTPError as e:
        body = e.read().decode("utf-8", "ignore")
        print("HTTP %d %s %s" % (e.code, method, path))
        print(body[:600])
        raise SystemExit(1)


def remote_blobs(token: str) -> dict[str, str]:
    """取远端 main 的完整文件树：路径 -> blob sha。"""
    ref = http("GET", "/repos/%s/%s/git/ref/heads/%s" % (OWNER, REPO, BRANCH), token)
    head_sha = ref["object"]["sha"]
    commit = http("GET", "/repos/%s/%s/git/commits/%s" % (OWNER, REPO, head_sha), token)
    tree = http("GET", "/repos/%s/%s/git/trees/%s?recursive=1" % (OWNER, REPO, commit["tree"]["sha"]), token)
    out = {}
    for item in tree.get("tree", []):
        if item["type"] == "blob":
            out[item["path"]] = item["sha"]
    return head_sha, out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--token", default=os.environ.get("GITHUB_TOKEN", ""))
    ap.add_argument("-m", "--message", default="")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not args.token:
        print("缺少 token：用 --token <PAT> 或设置环境变量 GITHUB_TOKEN")
        return 1

    local = local_files()
    head_sha, remote = remote_blobs(args.token)

    # GitHub 的 blob sha 就是 git 的对象 sha，可直接本地计算比对，省去逐文件下载
    import hashlib
    changed = []
    for rel, data in sorted(local.items()):
        sha = hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()
        if remote.get(rel) != sha:
            changed.append(rel)

    gone = sorted([p for p in remote if p not in local])

    print("本地文件 %d · 远端文件 %d" % (len(local), len(remote)))
    print("待提交：新增/修改 %d 个，远端已删 %d 个" % (len(changed), len(gone)))
    for p in changed:
        mark = "+" if p not in remote else "~"
        print("  %s %s" % (mark, p))
    for p in gone:
        print("  - %s" % p)

    if not changed and not gone:
        print("\n无差异，无需提交")
        return 0
    if args.dry_run:
        print("\n[dry-run] 未改动远端")
        return 0

    msg = args.message or "feat: 多终端总览看板(Fleet) + 本机看板接入中心状态栏 + 上游降级"
    print("\n创建 blob ...")
    tree_items = []
    for rel in changed:
        data = local[rel]
        blob = http("POST", "/repos/%s/%s/git/blobs" % (OWNER, REPO), args.token,
                    {"content": base64.b64encode(data).decode("ascii"), "encoding": "base64"})
        tree_items.append({"path": rel, "mode": "100644", "type": "blob", "sha": blob["sha"]})
        print("  blob %s (%d bytes)" % (rel, len(data)))

    for p in gone:
        tree_items.append({"path": p, "mode": "100644", "type": "blob", "sha": None})

    print("创建 tree ...")
    head_commit = http("GET", "/repos/%s/%s/git/commits/%s" % (OWNER, REPO, head_sha), args.token)
    tree = http("POST", "/repos/%s/%s/git/trees" % (OWNER, REPO), args.token,
                {"base_tree": head_commit["tree"]["sha"], "tree": tree_items})
    print("  tree %s" % tree["sha"])

    print("创建 commit ...")
    commit = http("POST", "/repos/%s/%s/git/commits" % (OWNER, REPO), args.token,
                  {"message": msg, "tree": tree["sha"], "parents": [head_sha]})
    print("  commit %s" % commit["sha"])

    print("更新 refs/heads/%s ..." % BRANCH)
    http("PATCH", "/repos/%s/%s/git/refs/heads/%s" % (OWNER, REPO, BRANCH), args.token,
         {"sha": commit["sha"]})
    print("\n完成：https://github.com/%s/%s/commit/%s" % (OWNER, REPO, commit["sha"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
