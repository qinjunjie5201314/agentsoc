# -*- coding: utf-8 -*-
"""AgentSoc 中心侧 · 多终端总览看板（Fleet）部署补丁。

在服务器项目目录（如 /root/agentsoc）下执行：

    python deploy_fleet_server.py              # 只落地代码
    python deploy_fleet_server.py --rebuild    # 落地并重建 api 容器

设计：
  - 只做增量：新增 app/fleet/ 四个文件 + 在 main.py / config.py 精准插入，
    不整体覆盖已有文件（避免冲掉服务器上已有的其他补丁）；
  - 幂等：已打过的补丁自动跳过，可重复执行；
  - 换行一律用 NL = chr(10) 拼接，避免转义序列被写坏；
  - 锚点缺失时只告警不修改，绝不改坏现有代码。
"""
from __future__ import print_function

import base64
import os
import sys

NL = chr(10)
ROOT = os.path.dirname(os.path.abspath(__file__))

FILES = {
    "app/fleet/__init__.py": (
        "IiIiRmxlZXQg5aSa57uI56uv5oC76KeIIOKAlOKAlCDnu4jnq6/lv4Pot7Pms6jlhozooaggKyDm"
        "n6Xor6LmjqXlj6MgKyDnnIvmnb/pobXjgIIKCue7hOaIkO+8mgogIC0gOmNsYXNzOmBGbGVldFJl"
        "Z2lzdHJ5YCAgICAgICAgICAg6L+b56iL57qn5YaF5a2Y6KGo77yI57uI56uv5LiK5oql6amx5Yqo"
        "77yM5ZCr5Zyo57q/5Yik5a6a77yJCiAgLSA6ZnVuYzpgY3JlYXRlX2ZsZWV0X3JvdXRlcmAgICAg"
        "ICAvdjEvZmxlZXQvcmVwb3J077yI5LiK5oql77yJKyAvdjEvZmxlZXQvYWdlbnRz77yI5p+l6K+i"
        "77yJCiAgLSA6ZnVuYzpgY3JlYXRlX2ZsZWV0X3BhZ2Vfcm91dGVyYCAvZmxlZXQg5oC76KeI6aG1"
        "ICsgL2ZsZWV0L2FnZW50L3tpZH0g6K+m5oOF6aG1CiIiIgpmcm9tIF9fZnV0dXJlX18gaW1wb3J0"
        "IGFubm90YXRpb25zCgpmcm9tIGFwcC5mbGVldC5wYWdlIGltcG9ydCBjcmVhdGVfZmxlZXRfcGFn"
        "ZV9yb3V0ZXIKZnJvbSBhcHAuZmxlZXQucmVnaXN0cnkgaW1wb3J0IEZsZWV0UmVnaXN0cnkKZnJv"
        "bSBhcHAuZmxlZXQucm91dGVzIGltcG9ydCBjcmVhdGVfZmxlZXRfcm91dGVyCgpfX2FsbF9fID0g"
        "WwogICAgIkZsZWV0UmVnaXN0cnkiLAogICAgImNyZWF0ZV9mbGVldF9yb3V0ZXIiLAogICAgImNy"
        "ZWF0ZV9mbGVldF9wYWdlX3JvdXRlciIsCl0K"
    ),
    "app/fleet/registry.py": (
        "IiIiRmxlZXQg57uI56uv5rOo5YaM6KGoIOKAlOKAlCDnlLHmoYzpnaLku6PnkIblv4Pot7PkuIrm"
        "iqXpqbHliqjnmoTov5vnqIvnuqflhoXlrZjooajjgIIKCuiuvuiuoeimgeeCue+8mgogIC0g57uI"
        "56uv77yIZGVza3RvcC1wcm94ee+8ieavjyBOIOenkuaKiuW/q+eFpyBQT1NUIOWIsCBgYC92MS9m"
        "bGVldC9yZXBvcnRgYO+8mwogIC0g5pys6KGo5LulIGBgYWdlbnRfaWRgYO+8iOm7mOiupOS4u+ac"
        "uuWQje+8ieS4uumUru+8jOiusOW9leacgOi/keS4gOasoeW/q+eFpyArIOS4iuaKpeaXtumXtO+8"
        "mwogIC0g5Zyo57q/5Yik5a6a77yaYGBub3cgLSBsYXN0X3NlZW4gPD0gb2ZmbGluZV9hZnRlcmBg"
        "77yI6buY6K6kIDE4MHMgPSAzIHggNjBzIOS4iuaKpemXtOmalO+8ie+8mwogIC0g57qv5YaF5a2Y"
        "ICsg57q/56iL6ZSB77ya6L+b56iL6YeN5ZCv5Y2z5riF56m677yI5ZCO57ut5Y+v5YiHIFNRTGl0"
        "ZSDlgZrljoblj7Lotovlir/vvInvvJsKICAtIOWvueWkluWPque7meinhuWbvu+8iHN1bW1hcnkg"
        "LyBsaXN0IC8gZ2V077yJ77yM5LiN5pq06Zyy5YaF6YOo54q25oCB5a+56LGh44CCCiIiIgpmcm9t"
        "IF9fZnV0dXJlX18gaW1wb3J0IGFubm90YXRpb25zCgppbXBvcnQgdGhyZWFkaW5nCmltcG9ydCB0"
        "aW1lCmZyb20gZGF0YWNsYXNzZXMgaW1wb3J0IGRhdGFjbGFzcywgZmllbGQKZnJvbSB0eXBpbmcg"
        "aW1wb3J0IEFueQoKREVGQVVMVF9PRkZMSU5FX0FGVEVSID0gMTgwLjAKREVGQVVMVF9SRVBPUlRf"
        "SU5URVJWQUwgPSA2MC4wCk1BWF9FVkVOVFMgPSAyMApNQVhfQUdFTlRTID0gMjAwMApTVEFMRV9Q"
        "VVJHRV9TRUMgPSA3ICogMjQgKiAzNjAwLjAKCgpAZGF0YWNsYXNzCmNsYXNzIEFnZW50U3RhdGU6"
        "CiAgICAiIiLljZXlj7Dnu4jnq6/nmoTmnIDov5HnirbmgIHjgIIiIiIKCiAgICBhZ2VudF9pZDog"
        "c3RyCiAgICBmaXJzdF9zZWVuOiBmbG9hdAogICAgbGFzdF9zZWVuOiBmbG9hdAogICAgcmVwb3J0"
        "X2NvdW50OiBpbnQgPSAwCiAgICBzb3VyY2VfaXA6IHN0ciA9ICIiCiAgICBzbmFwc2hvdDogZGlj"
        "dFtzdHIsIEFueV0gPSBmaWVsZChkZWZhdWx0X2ZhY3Rvcnk9ZGljdCkKCgpjbGFzcyBGbGVldFJl"
        "Z2lzdHJ5OgogICAgIiIi57uI56uv5b+D6Lez5rOo5YaM6KGo77yI57q/56iL5a6J5YWo77yJ44CC"
        "IiIiCgogICAgZGVmIF9faW5pdF9fKAogICAgICAgIHNlbGYsCiAgICAgICAgb2ZmbGluZV9hZnRl"
        "cjogZmxvYXQgPSBERUZBVUxUX09GRkxJTkVfQUZURVIsCiAgICAgICAgcmVwb3J0X2ludGVydmFs"
        "OiBmbG9hdCA9IERFRkFVTFRfUkVQT1JUX0lOVEVSVkFMLAogICAgICAgIG1heF9ldmVudHM6IGlu"
        "dCA9IE1BWF9FVkVOVFMsCiAgICAgICAgbWF4X2FnZW50czogaW50ID0gTUFYX0FHRU5UUywKICAg"
        "ICkgLT4gTm9uZToKICAgICAgICBzZWxmLl9sb2NrID0gdGhyZWFkaW5nLlJMb2NrKCkKICAgICAg"
        "ICBzZWxmLl9hZ2VudHM6IGRpY3Rbc3RyLCBBZ2VudFN0YXRlXSA9IHt9CiAgICAgICAgc2VsZi5v"
        "ZmZsaW5lX2FmdGVyID0gZmxvYXQob2ZmbGluZV9hZnRlcikKICAgICAgICBzZWxmLnJlcG9ydF9p"
        "bnRlcnZhbCA9IGZsb2F0KHJlcG9ydF9pbnRlcnZhbCkKICAgICAgICBzZWxmLm1heF9ldmVudHMg"
        "PSBpbnQobWF4X2V2ZW50cykKICAgICAgICBzZWxmLm1heF9hZ2VudHMgPSBpbnQobWF4X2FnZW50"
        "cykKCiAgICAjIC0tLS0tLS0tLS0g5YaZ5YWlIC0tLS0tLS0tLS0KCiAgICBkZWYgcmVwb3J0KHNl"
        "bGYsIHNuYXBzaG90OiBkaWN0W3N0ciwgQW55XSwgc291cmNlX2lwOiBzdHIgPSAiIikgLT4gQWdl"
        "bnRTdGF0ZToKICAgICAgICAiIiLorrDlvZXkuIDmrKHnu4jnq6/kuIrmiqXvvIzov5Tlm57mm7Tm"
        "lrDlkI7nmoTlhoXpg6jnirbmgIHjgIIiIiIKICAgICAgICBhZ2VudF9pZCA9IHNlbGYuX2FnZW50"
        "X2lkKHNuYXBzaG90KQogICAgICAgIG5vdyA9IHRpbWUudGltZSgpCiAgICAgICAgd2l0aCBzZWxm"
        "Ll9sb2NrOgogICAgICAgICAgICBzdCA9IHNlbGYuX2FnZW50cy5nZXQoYWdlbnRfaWQpCiAgICAg"
        "ICAgICAgIGlmIHN0IGlzIE5vbmU6CiAgICAgICAgICAgICAgICBpZiBsZW4oc2VsZi5fYWdlbnRz"
        "KSA+PSBzZWxmLm1heF9hZ2VudHM6CiAgICAgICAgICAgICAgICAgICAgc2VsZi5fZXZpY3RfbG9j"
        "a2VkKCkKICAgICAgICAgICAgICAgIHN0ID0gQWdlbnRTdGF0ZShhZ2VudF9pZD1hZ2VudF9pZCwg"
        "Zmlyc3Rfc2Vlbj1ub3csIGxhc3Rfc2Vlbj1ub3cpCiAgICAgICAgICAgICAgICBzZWxmLl9hZ2Vu"
        "dHNbYWdlbnRfaWRdID0gc3QKICAgICAgICAgICAgc3QubGFzdF9zZWVuID0gbm93CiAgICAgICAg"
        "ICAgIHN0LnJlcG9ydF9jb3VudCArPSAxCiAgICAgICAgICAgIGlmIHNvdXJjZV9pcDoKICAgICAg"
        "ICAgICAgICAgIHN0LnNvdXJjZV9pcCA9IHNvdXJjZV9pcAogICAgICAgICAgICBzdC5zbmFwc2hv"
        "dCA9IHNlbGYuX3RyaW0oc25hcHNob3QpCiAgICAgICAgcmV0dXJuIHN0CgogICAgIyAtLS0tLS0t"
        "LS0tIOWPquivu+inhuWbviAtLS0tLS0tLS0tCgogICAgZGVmIHN1bW1hcnkoc2VsZikgLT4gZGlj"
        "dFtzdHIsIEFueV06CiAgICAgICAgIiIi5oC76YePIC8g5Zyo57q/IC8g56a757q/IOaxh+aAu++8"
        "iOeci+adv+mhtumDqOWkp+aVsOWtl+eUqO+8ieOAgiIiIgogICAgICAgIG5vdyA9IHRpbWUudGlt"
        "ZSgpCiAgICAgICAgd2l0aCBzZWxmLl9sb2NrOgogICAgICAgICAgICB0b3RhbCA9IGxlbihzZWxm"
        "Ll9hZ2VudHMpCiAgICAgICAgICAgIG9ubGluZSA9IHNlbGYuY291bnRfb25saW5lX2xvY2tlZChu"
        "b3cpCiAgICAgICAgcmV0dXJuIHsKICAgICAgICAgICAgInRvdGFsIjogdG90YWwsCiAgICAgICAg"
        "ICAgICJvbmxpbmUiOiBvbmxpbmUsCiAgICAgICAgICAgICJvZmZsaW5lIjogdG90YWwgLSBvbmxp"
        "bmUsCiAgICAgICAgICAgICJvZmZsaW5lX2FmdGVyIjogc2VsZi5vZmZsaW5lX2FmdGVyLAogICAg"
        "ICAgICAgICAicmVwb3J0X2ludGVydmFsIjogc2VsZi5yZXBvcnRfaW50ZXJ2YWwsCiAgICAgICAg"
        "ICAgICJub3ciOiBub3csCiAgICAgICAgfQoKICAgIGRlZiBsaXN0X2FnZW50cyhzZWxmKSAtPiBs"
        "aXN0W2RpY3Rbc3RyLCBBbnldXToKICAgICAgICAiIiLnu4jnq6/liJfooajvvJrlnKjnur/kvJjl"
        "hYjvvIzlhbbmrKHmjInmnIDov5HkuIrmiqXml7bpl7TlgJLluo/jgIIiIiIKICAgICAgICBub3cg"
        "PSB0aW1lLnRpbWUoKQogICAgICAgIHdpdGggc2VsZi5fbG9jazoKICAgICAgICAgICAgdmlld3Mg"
        "PSBbc2VsZi5fdmlldyhzdCwgbm93KSBmb3Igc3QgaW4gc2VsZi5fYWdlbnRzLnZhbHVlcygpXQog"
        "ICAgICAgIHZpZXdzLnNvcnQoa2V5PWxhbWJkYSB2OiAobm90IHZbIm9ubGluZSJdLCAtZmxvYXQo"
        "dlsibGFzdF9zZWVuIl0gb3IgMCkpKQogICAgICAgIHJldHVybiB2aWV3cwoKICAgIGRlZiBnZXQo"
        "c2VsZiwgYWdlbnRfaWQ6IHN0cikgLT4gZGljdFtzdHIsIEFueV0gfCBOb25lOgogICAgICAgICIi"
        "IuWNleWPsOe7iOerr+ivpuaDhe+8iOWQq+acgOi/keivt+axgua1geawtO+8ieOAguS4jeWtmOWc"
        "qOi/lOWbniBOb25l44CCIiIiCiAgICAgICAgbm93ID0gdGltZS50aW1lKCkKICAgICAgICB3aXRo"
        "IHNlbGYuX2xvY2s6CiAgICAgICAgICAgIHN0ID0gc2VsZi5fYWdlbnRzLmdldChhZ2VudF9pZCkK"
        "ICAgICAgICAgICAgaWYgc3QgaXMgTm9uZToKICAgICAgICAgICAgICAgIHJldHVybiBOb25lCiAg"
        "ICAgICAgICAgIHZpZXcgPSBzZWxmLl92aWV3KHN0LCBub3cpCiAgICAgICAgICAgIHJlY2VudCA9"
        "IChzdC5zbmFwc2hvdCBvciB7fSkuZ2V0KCJyZWNlbnQiKSBvciBbXQogICAgICAgICAgICB2aWV3"
        "WyJyZWNlbnQiXSA9IGxpc3QocmVjZW50KQogICAgICAgIHJldHVybiB2aWV3CgogICAgZGVmIGNv"
        "dW50X29ubGluZV9sb2NrZWQoc2VsZiwgbm93OiBmbG9hdCB8IE5vbmUgPSBOb25lKSAtPiBpbnQ6"
        "CiAgICAgICAgbm93ID0gdGltZS50aW1lKCkgaWYgbm93IGlzIE5vbmUgZWxzZSBub3cKICAgICAg"
        "ICByZXR1cm4gc3VtKDEgZm9yIHN0IGluIHNlbGYuX2FnZW50cy52YWx1ZXMoKSBpZiBub3cgLSBz"
        "dC5sYXN0X3NlZW4gPD0gc2VsZi5vZmZsaW5lX2FmdGVyKQoKICAgICMgLS0tLS0tLS0tLSDnu7Tm"
        "iqQgLS0tLS0tLS0tLQoKICAgIGRlZiBwdXJnZV9zdGFsZShzZWxmLCBtYXhfYWdlOiBmbG9hdCA9"
        "IFNUQUxFX1BVUkdFX1NFQykgLT4gaW50OgogICAgICAgICIiIua4heeQhumVv+acn+acquS4iuaK"
        "peeahOe7iOerr++8jOmBv+WFjeWGheWtmOaXoOmZkOWinumVv+OAgiIiIgogICAgICAgIHdpdGgg"
        "c2VsZi5fbG9jazoKICAgICAgICAgICAgbm93ID0gdGltZS50aW1lKCkKICAgICAgICAgICAgc3Rh"
        "bGUgPSBbayBmb3Igaywgc3QgaW4gc2VsZi5fYWdlbnRzLml0ZW1zKCkgaWYgbm93IC0gc3QubGFz"
        "dF9zZWVuID4gbWF4X2FnZV0KICAgICAgICAgICAgZm9yIGsgaW4gc3RhbGU6CiAgICAgICAgICAg"
        "ICAgICBzZWxmLl9hZ2VudHMucG9wKGssIE5vbmUpCiAgICAgICAgcmV0dXJuIGxlbihzdGFsZSkK"
        "CiAgICBkZWYgY2xlYXIoc2VsZikgLT4gTm9uZToKICAgICAgICB3aXRoIHNlbGYuX2xvY2s6CiAg"
        "ICAgICAgICAgIHNlbGYuX2FnZW50cy5jbGVhcigpCgogICAgIyAtLS0tLS0tLS0tIOWGhemDqCAt"
        "LS0tLS0tLS0tCgogICAgZGVmIF9ldmljdF9sb2NrZWQoc2VsZikgLT4gaW50OgogICAgICAgICIi"
        "IuihqOa7oeaXtui4ouaOieacgOS5heacquS4iuaKpeeahCAxMCXjgIIiIiIKICAgICAgICBpdGVt"
        "cyA9IHNvcnRlZChzZWxmLl9hZ2VudHMuaXRlbXMoKSwga2V5PWxhbWJkYSBrdjoga3ZbMV0ubGFz"
        "dF9zZWVuKQogICAgICAgIGRyb3AgPSBtYXgoMSwgbGVuKGl0ZW1zKSAvLyAxMCkKICAgICAgICBm"
        "b3Iga2V5LCBfIGluIGl0ZW1zWzpkcm9wXToKICAgICAgICAgICAgc2VsZi5fYWdlbnRzLnBvcChr"
        "ZXksIE5vbmUpCiAgICAgICAgcmV0dXJuIGRyb3AKCiAgICBkZWYgX3ZpZXcoc2VsZiwgc3Q6IEFn"
        "ZW50U3RhdGUsIG5vdzogZmxvYXQpIC0+IGRpY3Rbc3RyLCBBbnldOgogICAgICAgIHNuYXAgPSBz"
        "dC5zbmFwc2hvdCBvciB7fQogICAgICAgIHNpbGVudCA9IG1heCgwLjAsIG5vdyAtIHN0Lmxhc3Rf"
        "c2VlbikKICAgICAgICBjb25uID0gc25hcC5nZXQoImNvbm5lY3Rpdml0eSIpIG9yIHt9CiAgICAg"
        "ICAgc3RhdHMgPSBzbmFwLmdldCgic3RhdHMiKSBvciB7fQogICAgICAgIHJldHVybiB7CiAgICAg"
        "ICAgICAgICJhZ2VudF9pZCI6IHN0LmFnZW50X2lkLAogICAgICAgICAgICAiYWdlbnRfbmFtZSI6"
        "IHNuYXAuZ2V0KCJhZ2VudF9uYW1lIikgb3IgIiIsCiAgICAgICAgICAgICJob3N0bmFtZSI6IHNu"
        "YXAuZ2V0KCJob3N0bmFtZSIpIG9yIHN0LmFnZW50X2lkLAogICAgICAgICAgICAib25saW5lIjog"
        "c2lsZW50IDw9IHNlbGYub2ZmbGluZV9hZnRlciwKICAgICAgICAgICAgImxhc3Rfc2VlbiI6IHN0"
        "Lmxhc3Rfc2VlbiwKICAgICAgICAgICAgInNpbGVudF9zZWMiOiByb3VuZChzaWxlbnQsIDEpLAog"
        "ICAgICAgICAgICAiZmlyc3Rfc2VlbiI6IHN0LmZpcnN0X3NlZW4sCiAgICAgICAgICAgICJyZXBv"
        "cnRfY291bnQiOiBzdC5yZXBvcnRfY291bnQsCiAgICAgICAgICAgICJzb3VyY2VfaXAiOiBzdC5z"
        "b3VyY2VfaXAsCiAgICAgICAgICAgICJ2ZXJzaW9uIjogc25hcC5nZXQoInZlcnNpb24iKSBvciAi"
        "IiwKICAgICAgICAgICAgIm1vZGUiOiBzbmFwLmdldCgibW9kZSIpIG9yICIiLAogICAgICAgICAg"
        "ICAidXBzdHJlYW0iOiBzbmFwLmdldCgidXBzdHJlYW0iKSBvciAiIiwKICAgICAgICAgICAgInN0"
        "YXJ0X3RpbWUiOiBzbmFwLmdldCgic3RhcnRfdGltZSIpIG9yIDAsCiAgICAgICAgICAgICJ1cHRp"
        "bWVfc2VjIjogc25hcC5nZXQoInVwdGltZV9zZWMiKSBvciAwLAogICAgICAgICAgICAiY29ubmVj"
        "dGl2aXR5IjogewogICAgICAgICAgICAgICAgIm9rIjogYm9vbChjb25uLmdldCgib2siKSksCiAg"
        "ICAgICAgICAgICAgICAibGF0ZW5jeV9tcyI6IGNvbm4uZ2V0KCJsYXRlbmN5X21zIikgb3IgMCwK"
        "ICAgICAgICAgICAgICAgICJlcnJvciI6IGNvbm4uZ2V0KCJlcnJvciIpIG9yICIiLAogICAgICAg"
        "ICAgICB9LAogICAgICAgICAgICAic3RhdHMiOiB7CiAgICAgICAgICAgICAgICAidG90YWwiOiBz"
        "dGF0cy5nZXQoInRvdGFsIikgb3IgMCwKICAgICAgICAgICAgICAgICJibG9ja2VkIjogc3RhdHMu"
        "Z2V0KCJibG9ja2VkIikgb3IgMCwKICAgICAgICAgICAgICAgICJwYXNzZWQiOiBzdGF0cy5nZXQo"
        "InBhc3NlZCIpIG9yIDAsCiAgICAgICAgICAgICAgICAiZXJyb3JzIjogc3RhdHMuZ2V0KCJlcnJv"
        "cnMiKSBvciAwLAogICAgICAgICAgICB9LAogICAgICAgIH0KCiAgICBkZWYgX3RyaW0oc2VsZiwg"
        "c25hcHNob3Q6IGRpY3Rbc3RyLCBBbnldKSAtPiBkaWN0W3N0ciwgQW55XToKICAgICAgICAiIiLl"
        "j6rnlZnmnIDov5EgbWF4X2V2ZW50cyDmnaHmtYHmsLTvvIzmjqfliLblhoXlrZjljaDnlKjjgIIi"
        "IiIKICAgICAgICBzbmFwID0gZGljdChzbmFwc2hvdCkKICAgICAgICByZWNlbnQgPSBzbmFwLmdl"
        "dCgicmVjZW50IikKICAgICAgICBpZiBpc2luc3RhbmNlKHJlY2VudCwgbGlzdCkgYW5kIGxlbihy"
        "ZWNlbnQpID4gc2VsZi5tYXhfZXZlbnRzOgogICAgICAgICAgICBzbmFwWyJyZWNlbnQiXSA9IHJl"
        "Y2VudFs6IHNlbGYubWF4X2V2ZW50c10KICAgICAgICByZXR1cm4gc25hcAoKICAgIEBzdGF0aWNt"
        "ZXRob2QKICAgIGRlZiBfYWdlbnRfaWQoc25hcHNob3Q6IGRpY3Rbc3RyLCBBbnldKSAtPiBzdHI6"
        "CiAgICAgICAgIiIi57uI56uv5ZSv5LiA5qCH6K+G77ya5LyY5YWI5pi+5byPIGFnZW50X2lk77yM"
        "5YW25qyh5Li75py65ZCN44CCIiIiCiAgICAgICAgZm9yIGtleSBpbiAoImFnZW50X2lkIiwgImhv"
        "c3RuYW1lIik6CiAgICAgICAgICAgIHZhbCA9IHNuYXBzaG90LmdldChrZXkpCiAgICAgICAgICAg"
        "IGlmIHZhbDoKICAgICAgICAgICAgICAgIHJldHVybiBzdHIodmFsKS5zdHJpcCgpCiAgICAgICAg"
        "cmV0dXJuICJ1bmtub3duIgoKCl9fYWxsX18gPSBbIkFnZW50U3RhdGUiLCAiRmxlZXRSZWdpc3Ry"
        "eSIsICJERUZBVUxUX09GRkxJTkVfQUZURVIiLCAiREVGQVVMVF9SRVBPUlRfSU5URVJWQUwiXQo="
    ),
    "app/fleet/routes.py": (
        "IiIiRmxlZXQg6Lev55SxIOKAlOKAlCDnu4jnq6/lv4Pot7PkuIrmiqUgKyDmgLvop4jmn6Xor6Lj"
        "gIIKCue7iOerr++8iGRlc2t0b3AtcHJveHnvvInkvqflhpnlhaXvvJoKICAgIFBPU1QgL3YxL2Zs"
        "ZWV0L3JlcG9ydCAgICAgICAg5q+PIDYwcyDkuIrmiqXkuIDmrKHov5DooYzlv6vnhacKCueci+ad"
        "v+S+p+WPquivu++8mgogICAgR0VUICAvdjEvZmxlZXQvc3VtbWFyeSAgICAgICDmgLvmlbAgLyDl"
        "nKjnur8gLyDnprvnur8KICAgIEdFVCAgL3YxL2ZsZWV0L2FnZW50cyAgICAgICAg57uI56uv5YiX"
        "6KGo77yI5Zyo57q/5LyY5YWI77yJCiAgICBHRVQgIC92MS9mbGVldC9hZ2VudHMve2lkfSAgIOWN"
        "leWPsOe7iOerr+ivpuaDhe+8iOWQq+acgOi/keivt+axgua1geawtO+8iQoK6Ym05p2D6K+05piO"
        "77ya57uI56uv5LiK5oql6LWw5Lit5b+D57uf5LiA55qEIGBgWC1BUEktS2V5YGDvvIjorr7kuoYg"
        "QVBJX0tFWSDmiY3lvLrliLbvvInvvJsK5Y+q6K+75p+l6K+i5LiO55yL5p2/6aG15ZCM5Li65YaF"
        "572R6L+Q57u06KeG5Zu+77yM5rK/55So546w5pyJIGRlbW8g6aG1562W55Wl5LiN6aKd5aSW6Ym0"
        "5p2D44CCCiIiIgpmcm9tIF9fZnV0dXJlX18gaW1wb3J0IGFubm90YXRpb25zCgpmcm9tIHR5cGlu"
        "ZyBpbXBvcnQgQW55Cgpmcm9tIGZhc3RhcGkgaW1wb3J0IEFQSVJvdXRlciwgQm9keSwgRGVwZW5k"
        "cywgSFRUUEV4Y2VwdGlvbiwgUmVxdWVzdAoKZnJvbSBhcHAuZmxlZXQucmVnaXN0cnkgaW1wb3J0"
        "IEZsZWV0UmVnaXN0cnkKZnJvbSBhcHAuc2VjdXJpdHkgaW1wb3J0IHJlcXVpcmVfYXBpX2tleQoK"
        "CmRlZiBjcmVhdGVfZmxlZXRfcm91dGVyKHJlZ2lzdHJ5OiBGbGVldFJlZ2lzdHJ5KSAtPiBBUElS"
        "b3V0ZXI6CiAgICAiIiLmnoTpgKAgZmxlZXQg6Lev55Sx77ybcmVnaXN0cnkg5Li66L+b56iL57qn"
        "5Y2V5L6L77yM6Leo6K+35rGC5YWx5Lqr44CCIiIiCiAgICByb3V0ZXIgPSBBUElSb3V0ZXIodGFn"
        "cz1bImZsZWV0Il0pCgogICAgQHJvdXRlci5wb3N0KCIvdjEvZmxlZXQvcmVwb3J0IiwgZGVwZW5k"
        "ZW5jaWVzPVtEZXBlbmRzKHJlcXVpcmVfYXBpX2tleSldKQogICAgYXN5bmMgZGVmIHJlcG9ydChy"
        "ZXF1ZXN0OiBSZXF1ZXN0LCBwYXlsb2FkOiBkaWN0W3N0ciwgQW55XSA9IEJvZHkoLi4uKSkgLT4g"
        "ZGljdFtzdHIsIEFueV06CiAgICAgICAgIiIi5qGM6Z2i5Luj55CG5b+D6Lez5LiK5oql77yI57uI"
        "56uv5q+PIDYwcyDosIPnlKjkuIDmrKHvvInjgIIiIiIKICAgICAgICBjbGllbnQgPSByZXF1ZXN0"
        "LmNsaWVudC5ob3N0IGlmIHJlcXVlc3QuY2xpZW50IGVsc2UgIiIKICAgICAgICBmd2QgPSByZXF1"
        "ZXN0LmhlYWRlcnMuZ2V0KCJ4LWZvcndhcmRlZC1mb3IiLCAiIikKICAgICAgICBzb3VyY2VfaXAg"
        "PSAoZndkLnNwbGl0KCIsIilbMF0uc3RyaXAoKSBpZiBmd2QgZWxzZSAiIikgb3IgY2xpZW50CiAg"
        "ICAgICAgc3QgPSByZWdpc3RyeS5yZXBvcnQocGF5bG9hZCwgc291cmNlX2lwPXNvdXJjZV9pcCkK"
        "ICAgICAgICByZXR1cm4gewogICAgICAgICAgICAib2siOiBUcnVlLAogICAgICAgICAgICAiYWdl"
        "bnRfaWQiOiBzdC5hZ2VudF9pZCwKICAgICAgICAgICAgInJlcG9ydF9jb3VudCI6IHN0LnJlcG9y"
        "dF9jb3VudCwKICAgICAgICAgICAgIm9mZmxpbmVfYWZ0ZXIiOiByZWdpc3RyeS5vZmZsaW5lX2Fm"
        "dGVyLAogICAgICAgIH0KCiAgICBAcm91dGVyLmdldCgiL3YxL2ZsZWV0L3N1bW1hcnkiKQogICAg"
        "YXN5bmMgZGVmIHN1bW1hcnkoKSAtPiBkaWN0W3N0ciwgQW55XToKICAgICAgICAiIiLnu4jnq6/l"
        "nKjnur8gLyDnprvnur/msYfmgLvjgIIiIiIKICAgICAgICByZXR1cm4gcmVnaXN0cnkuc3VtbWFy"
        "eSgpCgogICAgQHJvdXRlci5nZXQoIi92MS9mbGVldC9hZ2VudHMiKQogICAgYXN5bmMgZGVmIGxp"
        "c3RfYWdlbnRzKCkgLT4gZGljdFtzdHIsIEFueV06CiAgICAgICAgIiIi57uI56uv5YiX6KGo77yI"
        "5ZCr5rGH5oC777yM5LiA5qyh5ouJ6b2Q57uZ55yL5p2/55So77yJ44CCIiIiCiAgICAgICAgcmV0"
        "dXJuIHsic3VtbWFyeSI6IHJlZ2lzdHJ5LnN1bW1hcnkoKSwgImFnZW50cyI6IHJlZ2lzdHJ5Lmxp"
        "c3RfYWdlbnRzKCl9CgogICAgQHJvdXRlci5nZXQoIi92MS9mbGVldC9hZ2VudHMve2FnZW50X2lk"
        "fSIpCiAgICBhc3luYyBkZWYgYWdlbnRfZGV0YWlsKGFnZW50X2lkOiBzdHIpIC0+IGRpY3Rbc3Ry"
        "LCBBbnldOgogICAgICAgICIiIuWNleWPsOe7iOerr+ivpuaDheOAgiIiIgogICAgICAgIGRldGFp"
        "bCA9IHJlZ2lzdHJ5LmdldChhZ2VudF9pZCkKICAgICAgICBpZiBkZXRhaWwgaXMgTm9uZToKICAg"
        "ICAgICAgICAgcmFpc2UgSFRUUEV4Y2VwdGlvbihzdGF0dXNfY29kZT00MDQsIGRldGFpbD1mIue7"
        "iOerr+S4jeWtmOWcqDoge2FnZW50X2lkfSIpCiAgICAgICAgcmV0dXJuIGRldGFpbAoKICAgIHJl"
        "dHVybiByb3V0ZXIKCgpfX2FsbF9fID0gWyJjcmVhdGVfZmxlZXRfcm91dGVyIl0K"
    ),
    "app/fleet/page.py": (
        "IiIiRmxlZXQg55yL5p2/6aG16Z2iIOKAlOKAlCDnu4jnq6/mgLvop4ggKyDljZXnu4jnq6/or6bm"
        "g4XjgIIKCuS4pOe6p+inhuWbvu+8mgogIC0gYGAvZmxlZXRgYCAgICAgICAgICAgICAg5oC76KeI"
        "77ya5oC75pWwIC8g5Zyo57q/IC8g56a757q/ICsg57uI56uv5YiX6KGo77yI54K55Ye76L+b6K+m"
        "5oOF77yJCiAgLSBgYC9mbGVldC9hZ2VudC97aWR9YGAgICDor6bmg4XvvJrljZXlj7Dnu4jnq6/n"
        "moTogZTpgJrnirbmgIHjgIHnu5/orqHkuI7or7fmsYLmtYHmsLQKCumhtemdouS4uue6r+mdmeaA"
        "gSBIVE1MICsgZmV0Y2gg6L2u6K+i77yM5pWw5o2u5p2l6IeqIGBgL3YxL2ZsZWV0LypgYCDlj6ro"
        "r7vmjqXlj6PjgIIK5Y+q55uR5ZCs5YaF572R77yM5LiN5a+55aSW5pq06Zyy77yM5LiO546w5pyJ"
        "IGRlbW8g6aG15ZCM5LiA562W55Wl44CCCiIiIgpmcm9tIF9fZnV0dXJlX18gaW1wb3J0IGFubm90"
        "YXRpb25zCgpmcm9tIGZhc3RhcGkgaW1wb3J0IEFQSVJvdXRlcgpmcm9tIGZhc3RhcGkucmVzcG9u"
        "c2VzIGltcG9ydCBIVE1MUmVzcG9uc2UKCl9TSEFSRURfQ1NTID0gIiIiCiAgOnJvb3R7CiAgICAt"
        "LWJnOiNmNGY2ZmI7IC0tY2FyZDojZmZmZmZmOyAtLWluazojMWYyNzMzOyAtLXN1YjojNmI3Njg2"
        "OwogICAgLS1saW5lOiNlNmVhZjE7IC0tYnJhbmQ6IzI1NjNlYjsgLS1vazojMTZhMzRhOyAtLW9r"
        "LWJnOiNlOGY3ZWM7CiAgICAtLWJhZDojZGMyNjI2OyAtLWJhZC1iZzojZmRlY2VjOyAtLXdhcm46"
        "I2Q5NzcwNjsgLS13YXJuLWJnOiNmZGYzZTM7CiAgICAtLXNoYWRvdzowIDFweCAycHggcmdiYSgx"
        "NiwyNCw0MCwuMDQpLCAwIDhweCAyNHB4IHJnYmEoMTYsMjQsNDAsLjA1KTsKICB9CiAgKntib3gt"
        "c2l6aW5nOmJvcmRlci1ib3h9CiAgaHRtbCxib2R5e21hcmdpbjowO3BhZGRpbmc6MDtiYWNrZ3Jv"
        "dW5kOnZhcigtLWJnKTtjb2xvcjp2YXIoLS1pbmspOwogICAgZm9udDoxNHB4LzEuNTUgLWFwcGxl"
        "LXN5c3RlbSxCbGlua01hY1N5c3RlbUZvbnQsIlNlZ29lIFVJIiwiUGluZ0ZhbmcgU0MiLCJNaWNy"
        "b3NvZnQgWWFIZWkiLHNhbnMtc2VyaWZ9CiAgLndyYXB7bWF4LXdpZHRoOjExODBweDttYXJnaW46"
        "MCBhdXRvO3BhZGRpbmc6MjhweCAyMnB4IDU2cHh9CiAgaGVhZGVye2Rpc3BsYXk6ZmxleDthbGln"
        "bi1pdGVtczpjZW50ZXI7Z2FwOjE0cHg7ZmxleC13cmFwOndyYXA7bWFyZ2luLWJvdHRvbToyMnB4"
        "fQogIGgxe21hcmdpbjowO2ZvbnQtc2l6ZToyMHB4O2ZvbnQtd2VpZ2h0OjYwMDtsZXR0ZXItc3Bh"
        "Y2luZzotLjAxZW19CiAgLnN1Yntjb2xvcjp2YXIoLS1zdWIpO2ZvbnQtc2l6ZToxM3B4fQogIC5n"
        "cm93e2ZsZXg6MX0KICAuc3RhbXB7Y29sb3I6dmFyKC0tc3ViKTtmb250LXNpemU6MTJweH0KICAu"
        "Y2FyZHtiYWNrZ3JvdW5kOnZhcigtLWNhcmQpO2JvcmRlcjoxcHggc29saWQgdmFyKC0tbGluZSk7"
        "Ym9yZGVyLXJhZGl1czoxNHB4O2JveC1zaGFkb3c6dmFyKC0tc2hhZG93KX0KICAua3Bpc3tkaXNw"
        "bGF5OmdyaWQ7Z3JpZC10ZW1wbGF0ZS1jb2x1bW5zOnJlcGVhdChhdXRvLWZpdCxtaW5tYXgoMTgw"
        "cHgsMWZyKSk7Z2FwOjE0cHg7bWFyZ2luLWJvdHRvbToxOHB4fQogIC5rcGl7cGFkZGluZzoxOHB4"
        "IDIwcHg7ZGlzcGxheTpmbGV4O2ZsZXgtZGlyZWN0aW9uOmNvbHVtbjtnYXA6NHB4fQogIC5rcGkg"
        "Lm51bXtmb250LXNpemU6MzBweDtmb250LXdlaWdodDo2MDA7bGluZS1oZWlnaHQ6MS4xO2xldHRl"
        "ci1zcGFjaW5nOi0uMDJlbX0KICAua3BpIC5sYmx7Y29sb3I6dmFyKC0tc3ViKTtmb250LXNpemU6"
        "MTJweH0KICAua3BpLm9rIC5udW17Y29sb3I6dmFyKC0tb2spfSAua3BpLmJhZCAubnVte2NvbG9y"
        "OnZhcigtLWJhZCl9CiAgLmtwaS5oaXQgLm51bXtjb2xvcjp2YXIoLS13YXJuKX0KICAuY2FyZC1o"
        "ZHtkaXNwbGF5OmZsZXg7YWxpZ24taXRlbXM6Y2VudGVyO2dhcDoxMnB4O3BhZGRpbmc6MTZweCAy"
        "MHB4O2JvcmRlci1ib3R0b206MXB4IHNvbGlkIHZhcigtLWxpbmUpO2ZsZXgtd3JhcDp3cmFwfQog"
        "IC5jYXJkLWhkIGgye21hcmdpbjowO2ZvbnQtc2l6ZToxNHB4O2ZvbnQtd2VpZ2h0OjYwMH0KICAu"
        "aGludHtjb2xvcjp2YXIoLS1zdWIpO2ZvbnQtc2l6ZToxMnB4O21hcmdpbi1sZWZ0OmF1dG99CiAg"
        "aW5wdXRbdHlwZT1zZWFyY2hde2JvcmRlcjoxcHggc29saWQgdmFyKC0tbGluZSk7Ym9yZGVyLXJh"
        "ZGl1czo4cHg7cGFkZGluZzo3cHggMTFweDtmb250LXNpemU6MTNweDtvdXRsaW5lOm5vbmU7bWlu"
        "LXdpZHRoOjIwMHB4O2JhY2tncm91bmQ6I2ZiZmNmZX0KICBpbnB1dFt0eXBlPXNlYXJjaF06Zm9j"
        "dXN7Ym9yZGVyLWNvbG9yOiNiOWNkZjc7YmFja2dyb3VuZDojZmZmfQogIHRhYmxle3dpZHRoOjEw"
        "MCU7Ym9yZGVyLWNvbGxhcHNlOmNvbGxhcHNlO2ZvbnQtc2l6ZToxM3B4fQogIHRoLHRke3RleHQt"
        "YWxpZ246bGVmdDtwYWRkaW5nOjExcHggMTRweDtib3JkZXItYm90dG9tOjFweCBzb2xpZCB2YXIo"
        "LS1saW5lKTt3aGl0ZS1zcGFjZTpub3dyYXB9CiAgdGh7Y29sb3I6dmFyKC0tc3ViKTtmb250LXdl"
        "aWdodDo1MDA7Zm9udC1zaXplOjEycHg7YmFja2dyb3VuZDojZmFmYmZlfQogIHRib2R5IHRye2N1"
        "cnNvcjpwb2ludGVyO3RyYW5zaXRpb246YmFja2dyb3VuZCAuMTJzfQogIHRib2R5IHRyOmhvdmVy"
        "e2JhY2tncm91bmQ6I2Y3ZjlmZX0KICB0Ym9keSB0ci5yb3ctb2Zme2JhY2tncm91bmQ6I2ZmZmFm"
        "YX0KICB0Ym9keSB0ci5yb3ctb2ZmIC5ob3N0e2NvbG9yOnZhcigtLXN1Yil9CiAgLmhvc3R7Y29s"
        "b3I6dmFyKC0tYnJhbmQpO2ZvbnQtd2VpZ2h0OjUwMH0KICAubW9ub3tmb250LWZhbWlseTp1aS1t"
        "b25vc3BhY2UsU0ZNb25vLVJlZ3VsYXIsQ29uc29sYXMsbW9ub3NwYWNlO2NvbG9yOnZhcigtLXN1"
        "Yik7Zm9udC1zaXplOjEycHh9CiAgLmRvdHtkaXNwbGF5OmlubGluZS1ibG9jazt3aWR0aDo4cHg7"
        "aGVpZ2h0OjhweDtib3JkZXItcmFkaXVzOjUwJTttYXJnaW4tcmlnaHQ6NnB4O3ZlcnRpY2FsLWFs"
        "aWduOm1pZGRsZX0KICAuZG90Lm9ue2JhY2tncm91bmQ6dmFyKC0tb2spO2JveC1zaGFkb3c6MCAw"
        "IDAgM3B4IHJnYmEoMjIsMTYzLDc0LC4xMil9CiAgLmRvdC5vZmZ7YmFja2dyb3VuZDojYzNjOWQ0"
        "O2JveC1zaGFkb3c6MCAwIDAgM3B4IHJnYmEoMTk1LDIwMSwyMTIsLjE4KX0KICAuYmFkZ2V7ZGlz"
        "cGxheTppbmxpbmUtZmxleDthbGlnbi1pdGVtczpjZW50ZXI7Z2FwOjZweDtwYWRkaW5nOjNweCAx"
        "MHB4O2JvcmRlci1yYWRpdXM6OTk5cHg7Zm9udC1zaXplOjEycHg7Zm9udC13ZWlnaHQ6NTAwfQog"
        "IC5iYWRnZS5vbntiYWNrZ3JvdW5kOnZhcigtLW9rLWJnKTtjb2xvcjojMTU4MDNkfSAuYmFkZ2Uu"
        "b2Zme2JhY2tncm91bmQ6dmFyKC0tYmFkLWJnKTtjb2xvcjojYjkxYzFjfQogIC5lbXB0eXtwYWRk"
        "aW5nOjM0cHggMjBweDt0ZXh0LWFsaWduOmNlbnRlcjtjb2xvcjp2YXIoLS1zdWIpO2ZvbnQtc2l6"
        "ZToxM3B4fQogIC5ncmlkMntkaXNwbGF5OmdyaWQ7Z3JpZC10ZW1wbGF0ZS1jb2x1bW5zOnJlcGVh"
        "dChhdXRvLWZpdCxtaW5tYXgoMzIwcHgsMWZyKSk7Z2FwOjE2cHg7bWFyZ2luLWJvdHRvbToxNnB4"
        "fQogIC5ncmlkNHtkaXNwbGF5OmdyaWQ7Z3JpZC10ZW1wbGF0ZS1jb2x1bW5zOnJlcGVhdChhdXRv"
        "LWZpdCxtaW5tYXgoMTcwcHgsMWZyKSk7Z2FwOjE0cHg7bWFyZ2luLWJvdHRvbToxNnB4fQogIC5m"
        "aWVsZHtkaXNwbGF5OmZsZXg7anVzdGlmeS1jb250ZW50OnNwYWNlLWJldHdlZW47Z2FwOjEycHg7"
        "cGFkZGluZzo5cHggMDtib3JkZXItYm90dG9tOjFweCBkYXNoZWQgdmFyKC0tbGluZSk7Zm9udC1z"
        "aXplOjEzcHh9CiAgLmZpZWxkOmxhc3QtY2hpbGR7Ym9yZGVyLWJvdHRvbTpub25lfQogIC5maWVs"
        "ZCAua3tjb2xvcjp2YXIoLS1zdWIpfQogIC5maWVsZCAudntmb250LXdlaWdodDo1MDB9CiAgLnBh"
        "ZHtwYWRkaW5nOjE2cHggMjBweH0KICAucGFkLXR7cGFkZGluZzoxNnB4IDIwcHggNHB4fQogIC5i"
        "aWd7ZGlzcGxheTpmbGV4O2FsaWduLWl0ZW1zOmNlbnRlcjtnYXA6MTJweDtwYWRkaW5nOjE4cHgg"
        "MjBweH0KICAuYmlnIC50eHR7ZGlzcGxheTpmbGV4O2ZsZXgtZGlyZWN0aW9uOmNvbHVtbn0KICAu"
        "YmlnIC50eHQgYntmb250LXNpemU6MTdweDtmb250LXdlaWdodDo2MDB9CiAgLmJpZyAudHh0IHNw"
        "YW57Y29sb3I6dmFyKC0tc3ViKTtmb250LXNpemU6MTJweH0KICAucGlsbC1sZ3t3aWR0aDoxMnB4"
        "O2hlaWdodDoxMnB4O2JvcmRlci1yYWRpdXM6NTAlO2JhY2tncm91bmQ6dmFyKC0tb2spO2JveC1z"
        "aGFkb3c6MCAwIDAgNXB4IHJnYmEoMjIsMTYzLDc0LC4xMil9CiAgLnBpbGwtbGcub2Zme2JhY2tn"
        "cm91bmQ6I2MzYzlkNDtib3gtc2hhZG93OjAgMCAwIDVweCByZ2JhKDE5NSwyMDEsMjEyLC4xOCl9"
        "CiAgLmJhbm5lcnttYXJnaW4tYm90dG9tOjE2cHg7cGFkZGluZzoxMnB4IDE2cHg7Ym9yZGVyLXJh"
        "ZGl1czoxMnB4O2JhY2tncm91bmQ6dmFyKC0tYmFkLWJnKTtjb2xvcjojYjkxYzFjO2ZvbnQtc2l6"
        "ZToxM3B4O2JvcmRlcjoxcHggc29saWQgI2Y3ZDRkNH0KICAuYmFubmVyLndhcm57YmFja2dyb3Vu"
        "ZDp2YXIoLS13YXJuLWJnKTtjb2xvcjojYjQ1MzA5O2JvcmRlci1jb2xvcjojZjZlMGJkfQogIC5i"
        "YWNre2NvbG9yOnZhcigtLWJyYW5kKTt0ZXh0LWRlY29yYXRpb246bm9uZTtmb250LXNpemU6MTNw"
        "eH0KICAuYmFjazpob3Zlcnt0ZXh0LWRlY29yYXRpb246dW5kZXJsaW5lfQogIC50YWd7Zm9udC1z"
        "aXplOjExcHg7cGFkZGluZzoycHggN3B4O2JvcmRlci1yYWRpdXM6NnB4O2JhY2tncm91bmQ6I2Yx"
        "ZjVmOTtjb2xvcjojNDc1NTY5O2ZvbnQtZmFtaWx5OnVpLW1vbm9zcGFjZSxDb25zb2xhcyxtb25v"
        "c3BhY2V9CiAgLnRhZy5ibG9ja3tiYWNrZ3JvdW5kOnZhcigtLWJhZC1iZyk7Y29sb3I6I2I5MWMx"
        "Y30gLnRhZy5wYXNze2JhY2tncm91bmQ6dmFyKC0tb2stYmcpO2NvbG9yOiMxNTgwM2R9CiAgLnRh"
        "Zy5lcnJvcntiYWNrZ3JvdW5kOnZhcigtLXdhcm4tYmcpO2NvbG9yOiNiNDUzMDl9CiAgZm9vdGVy"
        "e21hcmdpbi10b3A6MjZweDtjb2xvcjp2YXIoLS1zdWIpO2ZvbnQtc2l6ZToxMnB4O3RleHQtYWxp"
        "Z246Y2VudGVyfQoiIiIKCgpkZWYgX25hdigpIC0+IHN0cjoKICAgIHJldHVybiAiIiIKICA8aGVh"
        "ZGVyPgogICAgPGgxPkFnZW50U29jIMK3IOe7iOerr+aAu+iniDwvaDE+CiAgICA8c3BhbiBjbGFz"
        "cz0ic3ViIiBpZD0ic3ViIj7moYzpnaLku6PnkIblnKjnur/mg4XlhrUgwrcg57uI56uv5Li75Yqo"
        "5LiK5oqlPC9zcGFuPgogICAgPHNwYW4gY2xhc3M9Imdyb3ciPjwvc3Bhbj4KICAgIDxzcGFuIGNs"
        "YXNzPSJzdGFtcCIgaWQ9InN0YW1wIj7mraPlnKjliqDovb3igKY8L3NwYW4+CiAgPC9oZWFkZXI+"
        "CiIiIgoKCl9PVkVSVklFV19CT0RZID0gIiIiCiAgPHNlY3Rpb24gY2xhc3M9ImtwaXMiPgogICAg"
        "PGRpdiBjbGFzcz0iY2FyZCBrcGkiPjxkaXYgY2xhc3M9Im51bSIgaWQ9ImstdG90YWwiPjA8L2Rp"
        "dj48ZGl2IGNsYXNzPSJsYmwiPue7iOerr+aAu+aVsDwvZGl2PjwvZGl2PgogICAgPGRpdiBjbGFz"
        "cz0iY2FyZCBrcGkgb2siPjxkaXYgY2xhc3M9Im51bSIgaWQ9Imstb25saW5lIj4wPC9kaXY+PGRp"
        "diBjbGFzcz0ibGJsIj7lnKjnur88L2Rpdj48L2Rpdj4KICAgIDxkaXYgY2xhc3M9ImNhcmQga3Bp"
        "IGJhZCI+PGRpdiBjbGFzcz0ibnVtIiBpZD0iay1vZmZsaW5lIj4wPC9kaXY+PGRpdiBjbGFzcz0i"
        "bGJsIj7nprvnur88L2Rpdj48L2Rpdj4KICAgIDxkaXYgY2xhc3M9ImNhcmQga3BpIGhpdCI+PGRp"
        "diBjbGFzcz0ibnVtIiBpZD0iay1ibG9ja2VkIj4wPC9kaXY+PGRpdiBjbGFzcz0ibGJsIj7ntK/o"
        "rqHlkb3kuK3op4TliJnmi6bmiKo8L2Rpdj48L2Rpdj4KICA8L3NlY3Rpb24+CgogIDxzZWN0aW9u"
        "IGNsYXNzPSJjYXJkIj4KICAgIDxkaXYgY2xhc3M9ImNhcmQtaGQiPgogICAgICA8aDI+57uI56uv"
        "5YiX6KGoPC9oMj4KICAgICAgPGlucHV0IHR5cGU9InNlYXJjaCIgaWQ9InEiIHBsYWNlaG9sZGVy"
        "PSLmkJzntKLkuLvmnLrlkI0gLyDliKvlkI0gLyBJUCIgLz4KICAgICAgPHNwYW4gY2xhc3M9Imhp"
        "bnQiPueCueWHu+afkOihjOi/m+WFpeivpee7iOerr+ivpuaDheeci+advzwvc3Bhbj4KICAgIDwv"
        "ZGl2PgogICAgPGRpdiBzdHlsZT0ib3ZlcmZsb3cteDphdXRvIj4KICAgICAgPHRhYmxlPgogICAg"
        "ICAgIDx0aGVhZD48dHI+CiAgICAgICAgICA8dGg+54q25oCBPC90aD48dGg+5Li75py65ZCNPC90"
        "aD48dGg+5Yir5ZCNPC90aD48dGg+5qih5byPPC90aD48dGg+54mI5pysPC90aD4KICAgICAgICAg"
        "IDx0aD7nvZHlhbPlu7bov588L3RoPjx0aD7mgLvor7fmsYI8L3RoPjx0aD7lkb3kuK3mi6bmiKo8"
        "L3RoPjx0aD7mnIDlkI7kuIrmiqU8L3RoPjx0aD7mnaXmupAgSVA8L3RoPgogICAgICAgIDwvdHI+"
        "PC90aGVhZD4KICAgICAgICA8dGJvZHkgaWQ9InJvd3MiPjwvdGJvZHk+CiAgICAgIDwvdGFibGU+"
        "CiAgICA8L2Rpdj4KICAgIDxkaXYgaWQ9ImVtcHR5IiBjbGFzcz0iZW1wdHkiIHN0eWxlPSJkaXNw"
        "bGF5Om5vbmUiPgogICAgICDmmoLml6Dnu4jnq6/kuIrmiqXjgILor7fnoa7orqTnu4jnq6/lt7Lp"
        "hY3nva4gdXBzdHJlYW0g572R5YWz5bm26YeN5ZCv5Luj55CG5pyN5Yqh44CCCiAgICA8L2Rpdj4K"
        "ICA8L3NlY3Rpb24+CgogIDxmb290ZXI+55yL5p2/5LuF5YaF572R5Y+v6K6/6ZeuIMK3IOaVsOaN"
        "ruS4uue7iOerr+S4iuaKpeeahOWGheWtmOW/q+eFp++8jOi/m+eoi+mHjeWQr+WNs+a4heepujwv"
        "Zm9vdGVyPgoiIiIKCl9PVkVSVklFV19KUyA9ICIiIgo8c2NyaXB0PgooZnVuY3Rpb24oKXsKICB2"
        "YXIgJCA9IGZ1bmN0aW9uKGlkKXsgcmV0dXJuIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKGlkKTsg"
        "fTsKICB2YXIgYWdlbnRzID0gW107CiAgdmFyIE9GRkxJTkVfQUZURVIgPSAxODA7CgogIGZ1bmN0"
        "aW9uIGVzYyhzKXsKICAgIHJldHVybiBTdHJpbmcocyA9PSBudWxsID8gJycgOiBzKS5yZXBsYWNl"
        "KC9bJjw+Il0vZywgZnVuY3Rpb24oYyl7CiAgICAgIHJldHVybiAoeycmJzonJmFtcDsnLCc8Jzon"
        "Jmx0OycsJz4nOicmZ3Q7JywnIic6JyZxdW90Oyd9KVtjXTsKICAgIH0pOwogIH0KICBmdW5jdGlv"
        "biBhZ28oc2VjKXsKICAgIHNlYyA9IE1hdGgubWF4KDAsIE1hdGgucm91bmQoc2VjIHx8IDApKTsK"
        "ICAgIGlmIChzZWMgPCA2MCkgcmV0dXJuIHNlYyArICcg56eS5YmNJzsKICAgIGlmIChzZWMgPCAz"
        "NjAwKSByZXR1cm4gTWF0aC5mbG9vcihzZWMgLyA2MCkgKyAnIOWIhumSn+WJjSc7CiAgICBpZiAo"
        "c2VjIDwgODY0MDApIHJldHVybiBNYXRoLmZsb29yKHNlYyAvIDM2MDApICsgJyDlsI/ml7bliY0n"
        "OwogICAgcmV0dXJuIE1hdGguZmxvb3Ioc2VjIC8gODY0MDApICsgJyDlpKnliY0nOwogIH0KICBm"
        "dW5jdGlvbiB1cHRpbWUoc2VjKXsKICAgIHNlYyA9IE1hdGgubWF4KDAsIE1hdGgucm91bmQoc2Vj"
        "IHx8IDApKTsKICAgIGlmIChzZWMgPCAzNjAwKSByZXR1cm4gTWF0aC5mbG9vcihzZWMgLyA2MCkg"
        "KyAnIOWIhumSnyc7CiAgICBpZiAoc2VjIDwgODY0MDApIHJldHVybiBNYXRoLmZsb29yKHNlYyAv"
        "IDM2MDApICsgJyDlsI/ml7YgJyArIE1hdGguZmxvb3IoKHNlYyAlIDM2MDApIC8gNjApICsgJyDl"
        "iIYnOwogICAgcmV0dXJuIE1hdGguZmxvb3Ioc2VjIC8gODY0MDApICsgJyDlpKkgJyArIE1hdGgu"
        "Zmxvb3IoKHNlYyAlIDg2NDAwKSAvIDM2MDApICsgJyDlsI/ml7YnOwogIH0KCiAgZnVuY3Rpb24g"
        "cmVuZGVyKCl7CiAgICB2YXIgcSA9ICgkKCdxJykudmFsdWUgfHwgJycpLnRyaW0oKS50b0xvd2Vy"
        "Q2FzZSgpOwogICAgdmFyIGxpc3QgPSBhZ2VudHMuZmlsdGVyKGZ1bmN0aW9uKGEpewogICAgICBp"
        "ZiAoIXEpIHJldHVybiB0cnVlOwogICAgICByZXR1cm4gW2EuaG9zdG5hbWUsIGEuYWdlbnRfbmFt"
        "ZSwgYS5hZ2VudF9pZCwgYS5zb3VyY2VfaXBdLnNvbWUoZnVuY3Rpb24odil7CiAgICAgICAgcmV0"
        "dXJuIFN0cmluZyh2ID09IG51bGwgPyAnJyA6IHYpLnRvTG93ZXJDYXNlKCkuaW5kZXhPZihxKSA+"
        "PSAwOwogICAgICB9KTsKICAgIH0pOwogICAgdmFyIGh0bWwgPSBsaXN0Lm1hcChmdW5jdGlvbihh"
        "KXsKICAgICAgdmFyIGNvbm4gPSBhLmNvbm5lY3Rpdml0eSB8fCB7fTsKICAgICAgdmFyIHN0ID0g"
        "YS5zdGF0cyB8fCB7fTsKICAgICAgdmFyIGxhdCA9IGNvbm4ub2sgPyAoY29ubi5sYXRlbmN5X21z"
        "ICsgJyBtcycpIDogJ+KAlCc7CiAgICAgIHZhciBkb3RDbHMgPSBhLm9ubGluZSA/ICdvbicgOiAn"
        "b2ZmJzsKICAgICAgcmV0dXJuICc8dHIgY2xhc3M9IicgKyAoYS5vbmxpbmUgPyAnJyA6ICdyb3ct"
        "b2ZmJykgKyAnIiBkYXRhLWlkPSInICsgZXNjKGEuYWdlbnRfaWQpICsgJyI+JwogICAgICAgICsg"
        "Jzx0ZD48c3BhbiBjbGFzcz0iZG90ICcgKyBkb3RDbHMgKyAnIj48L3NwYW4+JyArIChhLm9ubGlu"
        "ZSA/ICflnKjnur8nIDogJ+emu+e6vycpICsgJzwvdGQ+JwogICAgICAgICsgJzx0ZCBjbGFzcz0i"
        "aG9zdCI+JyArIGVzYyhhLmhvc3RuYW1lKSArICc8L3RkPicKICAgICAgICArICc8dGQ+JyArIGVz"
        "YyhhLmFnZW50X25hbWUgfHwgJ+KAlCcpICsgJzwvdGQ+JwogICAgICAgICsgJzx0ZD4nICsgZXNj"
        "KGEubW9kZSB8fCAn4oCUJykgKyAnPC90ZD4nCiAgICAgICAgKyAnPHRkPicgKyBlc2MoYS52ZXJz"
        "aW9uIHx8ICfigJQnKSArICc8L3RkPicKICAgICAgICArICc8dGQ+JyArIGxhdCArICc8L3RkPicK"
        "ICAgICAgICArICc8dGQ+JyArIChzdC50b3RhbCB8fCAwKSArICc8L3RkPicKICAgICAgICArICc8"
        "dGQgc3R5bGU9ImNvbG9yOicgKyAoKHN0LmJsb2NrZWQgfHwgMCkgPiAwID8gJyNkOTc3MDYnIDog"
        "J2luaGVyaXQnKSArICciPicgKyAoc3QuYmxvY2tlZCB8fCAwKSArICc8L3RkPicKICAgICAgICAr"
        "ICc8dGQ+JyArIGFnbyhhLnNpbGVudF9zZWMpICsgJzwvdGQ+JwogICAgICAgICsgJzx0ZCBjbGFz"
        "cz0ibW9ubyI+JyArIGVzYyhhLnNvdXJjZV9pcCB8fCAn4oCUJykgKyAnPC90ZD4nCiAgICAgICAg"
        "KyAnPC90cj4nOwogICAgfSkuam9pbignJyk7CiAgICAkKCdyb3dzJykuaW5uZXJIVE1MID0gaHRt"
        "bDsKICAgICQoJ2VtcHR5Jykuc3R5bGUuZGlzcGxheSA9IGxpc3QubGVuZ3RoID8gJ25vbmUnIDog"
        "J2Jsb2NrJzsKICAgIEFycmF5LnByb3RvdHlwZS5mb3JFYWNoLmNhbGwoJCgncm93cycpLnF1ZXJ5"
        "U2VsZWN0b3JBbGwoJ3RyJyksIGZ1bmN0aW9uKHRyKXsKICAgICAgdHIuYWRkRXZlbnRMaXN0ZW5l"
        "cignY2xpY2snLCBmdW5jdGlvbigpewogICAgICAgIGxvY2F0aW9uLmhyZWYgPSAnL2ZsZWV0L2Fn"
        "ZW50LycgKyBlbmNvZGVVUklDb21wb25lbnQodHIuZ2V0QXR0cmlidXRlKCdkYXRhLWlkJykpOwog"
        "ICAgICB9KTsKICAgIH0pOwogIH0KCiAgZnVuY3Rpb24gbG9hZCgpewogICAgZmV0Y2goJy92MS9m"
        "bGVldC9hZ2VudHMnLCB7IGNhY2hlOiAnbm8tc3RvcmUnIH0pLnRoZW4oZnVuY3Rpb24ocil7IHJl"
        "dHVybiByLmpzb24oKTsgfSkudGhlbihmdW5jdGlvbihkKXsKICAgICAgYWdlbnRzID0gZC5hZ2Vu"
        "dHMgfHwgW107CiAgICAgIHZhciBzID0gZC5zdW1tYXJ5IHx8IHt9OwogICAgICB2YXIgYmxvY2tl"
        "ZCA9IDA7CiAgICAgIGFnZW50cy5mb3JFYWNoKGZ1bmN0aW9uKGEpeyBibG9ja2VkICs9IChhLnN0"
        "YXRzICYmIGEuc3RhdHMuYmxvY2tlZCkgfHwgMDsgfSk7CiAgICAgICQoJ2stdG90YWwnKS50ZXh0"
        "Q29udGVudCA9IHMudG90YWwgPT0gbnVsbCA/IGFnZW50cy5sZW5ndGggOiBzLnRvdGFsOwogICAg"
        "ICAkKCdrLW9ubGluZScpLnRleHRDb250ZW50ID0gcy5vbmxpbmUgPT0gbnVsbCA/IDAgOiBzLm9u"
        "bGluZTsKICAgICAgJCgnay1vZmZsaW5lJykudGV4dENvbnRlbnQgPSBzLm9mZmxpbmUgPT0gbnVs"
        "bCA/IDAgOiBzLm9mZmxpbmU7CiAgICAgICQoJ2stYmxvY2tlZCcpLnRleHRDb250ZW50ID0gYmxv"
        "Y2tlZDsKICAgICAgT0ZGTElORV9BRlRFUiA9IHMub2ZmbGluZV9hZnRlciB8fCBPRkZMSU5FX0FG"
        "VEVSOwogICAgICAkKCdzdWInKS50ZXh0Q29udGVudCA9ICfmoYzpnaLku6PnkIblnKjnur/mg4Xl"
        "hrUgwrcg5q+PICcgKyAocy5yZXBvcnRfaW50ZXJ2YWwgfHwgNjApICsgJyDnp5LkuIrmiqUgwrcg"
        "6LaF6L+HICcKICAgICAgICArIE1hdGgucm91bmQoT0ZGTElORV9BRlRFUiAvIDYwKSArICcg5YiG"
        "6ZKf5pyq5LiK5oql5Yik56a757q/JzsKICAgICAgJCgnc3RhbXAnKS50ZXh0Q29udGVudCA9ICfm"
        "m7TmlrDkuo4gJyArIG5ldyBEYXRlKCkudG9Mb2NhbGVUaW1lU3RyaW5nKCd6aC1DTicpOwogICAg"
        "ICByZW5kZXIoKTsKICAgIH0pLmNhdGNoKGZ1bmN0aW9uKCl7CiAgICAgICQoJ3N0YW1wJykudGV4"
        "dENvbnRlbnQgPSAn6K+75Y+W5aSx6LSl77yM6YeN6K+V5Lit4oCmJzsKICAgIH0pOwogIH0KCiAg"
        "JCgncScpLmFkZEV2ZW50TGlzdGVuZXIoJ2lucHV0JywgcmVuZGVyKTsKICBsb2FkKCk7CiAgc2V0"
        "SW50ZXJ2YWwobG9hZCwgMzAwMCk7Cn0pKCk7Cjwvc2NyaXB0PgoiIiIKCgpfREVUQUlMX0JPRFkg"
        "PSAiIiIKICA8ZGl2IGlkPSJiYW5uZXIiIGNsYXNzPSJiYW5uZXIiIHN0eWxlPSJkaXNwbGF5Om5v"
        "bmUiPjwvZGl2PgoKICA8ZGl2IGNsYXNzPSJncmlkMiI+CiAgICA8c2VjdGlvbiBjbGFzcz0iY2Fy"
        "ZCI+CiAgICAgIDxkaXYgY2xhc3M9ImNhcmQtaGQiPjxoMj7ku6PnkIbogZTpgJrnirbmgIE8L2gy"
        "PjwvZGl2PgogICAgICA8ZGl2IGNsYXNzPSJiaWciPgogICAgICAgIDxzcGFuIGNsYXNzPSJwaWxs"
        "LWxnIiBpZD0iY29ubi1kb3QiPjwvc3Bhbj4KICAgICAgICA8c3BhbiBjbGFzcz0idHh0Ij4KICAg"
        "ICAgICAgIDxiIGlkPSJjb25uLXRleHQiPuivu+WPluS4reKApjwvYj4KICAgICAgICAgIDxzcGFu"
        "IGlkPSJjb25uLXN1YiI+4oCUPC9zcGFuPgogICAgICAgIDwvc3Bhbj4KICAgICAgPC9kaXY+CiAg"
        "ICAgIDxkaXYgY2xhc3M9InBhZC10Ij4KICAgICAgICA8ZGl2IGNsYXNzPSJmaWVsZCI+PHNwYW4g"
        "Y2xhc3M9ImsiPuS4iuaKpee9keWFszwvc3Bhbj48c3BhbiBjbGFzcz0idiBtb25vIiBpZD0iZi11"
        "cHN0cmVhbSI+4oCUPC9zcGFuPjwvZGl2PgogICAgICAgIDxkaXYgY2xhc3M9ImZpZWxkIj48c3Bh"
        "biBjbGFzcz0iayI+5pyA6L+R5LiK5oqlPC9zcGFuPjxzcGFuIGNsYXNzPSJ2IiBpZD0iZi1sYXN0"
        "Ij7igJQ8L3NwYW4+PC9kaXY+CiAgICAgICAgPGRpdiBjbGFzcz0iZmllbGQiPjxzcGFuIGNsYXNz"
        "PSJrIj7kuIrmiqXmrKHmlbA8L3NwYW4+PHNwYW4gY2xhc3M9InYiIGlkPSJmLWNvdW50Ij7igJQ8"
        "L3NwYW4+PC9kaXY+CiAgICAgICAgPGRpdiBjbGFzcz0iZmllbGQiPjxzcGFuIGNsYXNzPSJrIj7o"
        "r7TmmI48L3NwYW4+PHNwYW4gY2xhc3M9InYiIGlkPSJmLWVyciI+4oCUPC9zcGFuPjwvZGl2Pgog"
        "ICAgICA8L2Rpdj4KICAgIDwvc2VjdGlvbj4KCiAgICA8c2VjdGlvbiBjbGFzcz0iY2FyZCI+CiAg"
        "ICAgIDxkaXYgY2xhc3M9ImNhcmQtaGQiPjxoMj7nu4jnq6/kv6Hmga88L2gyPjwvZGl2PgogICAg"
        "ICA8ZGl2IGNsYXNzPSJwYWQtdCI+CiAgICAgICAgPGRpdiBjbGFzcz0iZmllbGQiPjxzcGFuIGNs"
        "YXNzPSJrIj7kuLvmnLrlkI3np7A8L3NwYW4+PHNwYW4gY2xhc3M9InYiIGlkPSJmLWhvc3QiPuKA"
        "lDwvc3Bhbj48L2Rpdj4KICAgICAgICA8ZGl2IGNsYXNzPSJmaWVsZCI+PHNwYW4gY2xhc3M9Imsi"
        "Pue7iOerr+WIq+WQjTwvc3Bhbj48c3BhbiBjbGFzcz0idiIgaWQ9ImYtbmFtZSI+4oCUPC9zcGFu"
        "PjwvZGl2PgogICAgICAgIDxkaXYgY2xhc3M9ImZpZWxkIj48c3BhbiBjbGFzcz0iayI+5p2l5rqQ"
        "IElQPC9zcGFuPjxzcGFuIGNsYXNzPSJ2IG1vbm8iIGlkPSJmLWlwIj7igJQ8L3NwYW4+PC9kaXY+"
        "CiAgICAgICAgPGRpdiBjbGFzcz0iZmllbGQiPjxzcGFuIGNsYXNzPSJrIj7ov5DooYzmqKHlvI88"
        "L3NwYW4+PHNwYW4gY2xhc3M9InYiIGlkPSJmLW1vZGUiPuKAlDwvc3Bhbj48L2Rpdj4KICAgICAg"
        "ICA8ZGl2IGNsYXNzPSJmaWVsZCI+PHNwYW4gY2xhc3M9ImsiPuS7o+eQhueJiOacrDwvc3Bhbj48"
        "c3BhbiBjbGFzcz0idiIgaWQ9ImYtdmVyIj7igJQ8L3NwYW4+PC9kaXY+CiAgICAgICAgPGRpdiBj"
        "bGFzcz0iZmllbGQiPjxzcGFuIGNsYXNzPSJrIj7lt7Lov5DooYw8L3NwYW4+PHNwYW4gY2xhc3M9"
        "InYiIGlkPSJmLXVwdGltZSI+4oCUPC9zcGFuPjwvZGl2PgogICAgICA8L2Rpdj4KICAgIDwvc2Vj"
        "dGlvbj4KICA8L2Rpdj4KCiAgPHNlY3Rpb24gY2xhc3M9ImdyaWQ0Ij4KICAgIDxkaXYgY2xhc3M9"
        "ImNhcmQga3BpIj48ZGl2IGNsYXNzPSJudW0iIGlkPSJzLXRvdGFsIj4wPC9kaXY+PGRpdiBjbGFz"
        "cz0ibGJsIj7mgLvor7fmsYI8L2Rpdj48L2Rpdj4KICAgIDxkaXYgY2xhc3M9ImNhcmQga3BpIGhp"
        "dCI+PGRpdiBjbGFzcz0ibnVtIiBpZD0icy1ibG9ja2VkIj4wPC9kaXY+PGRpdiBjbGFzcz0ibGJs"
        "Ij7lkb3kuK3op4TliJnmi6bmiKo8L2Rpdj48L2Rpdj4KICAgIDxkaXYgY2xhc3M9ImNhcmQga3Bp"
        "IG9rIj48ZGl2IGNsYXNzPSJudW0iIGlkPSJzLXBhc3NlZCI+MDwvZGl2PjxkaXYgY2xhc3M9Imxi"
        "bCI+5pS+6KGM6YCa6L+HPC9kaXY+PC9kaXY+CiAgICA8ZGl2IGNsYXNzPSJjYXJkIGtwaSBiYWQi"
        "PjxkaXYgY2xhc3M9Im51bSIgaWQ9InMtZXJycyI+MDwvZGl2PjxkaXYgY2xhc3M9ImxibCI+572R"
        "5YWz6ZSZ6K+vPC9kaXY+PC9kaXY+CiAgPC9zZWN0aW9uPgoKICA8c2VjdGlvbiBjbGFzcz0iY2Fy"
        "ZCI+CiAgICA8ZGl2IGNsYXNzPSJjYXJkLWhkIj4KICAgICAgPGgyPuWRveS4reinhOWImSAvIOiv"
        "t+axgua1geawtDwvaDI+CiAgICAgIDxzcGFuIGNsYXNzPSJoaW50Ij7or6Xnu4jnq6/mnIDov5Hk"
        "uIrmiqXnmoTmo4DmtYvorrDlvZXvvIjmnIDlpJogMjAg5p2h77yJPC9zcGFuPgogICAgPC9kaXY+"
        "CiAgICA8ZGl2IHN0eWxlPSJvdmVyZmxvdy14OmF1dG8iPgogICAgICA8dGFibGU+CiAgICAgICAg"
        "PHRoZWFkPjx0cj4KICAgICAgICAgIDx0aD7ml7bpl7Q8L3RoPjx0aD7liqjkvZw8L3RoPjx0aD7m"
        "lrnms5U8L3RoPjx0aD7ot6/lvoQ8L3RoPjx0aD7po47pmak8L3RoPgogICAgICAgICAgPHRoPuiv"
        "hOWIhjwvdGg+PHRoPuWRveS4reinhOWImTwvdGg+PHRoPuetlueVpTwvdGg+PHRoPuivtOaYjjwv"
        "dGg+CiAgICAgICAgPC90cj48L3RoZWFkPgogICAgICAgIDx0Ym9keSBpZD0iZmxvd3MiPjwvdGJv"
        "ZHk+CiAgICAgIDwvdGFibGU+CiAgICA8L2Rpdj4KICAgIDxkaXYgaWQ9ImZsb3dzLWVtcHR5IiBj"
        "bGFzcz0iZW1wdHkiIHN0eWxlPSJkaXNwbGF5Om5vbmUiPuivpee7iOerr+aaguaXoOivt+axguiu"
        "sOW9lTwvZGl2PgogIDwvc2VjdGlvbj4KCiAgPGZvb3Rlcj7mlbDmja7mnaXoh6ror6Xnu4jnq6/m"
        "nIDov5HkuIDmrKHlv4Pot7PkuIrmiqXnmoTlv6vnhac8L2Zvb3Rlcj4KIiIiCgpfREVUQUlMX0pT"
        "ID0gIiIiCjxzY3JpcHQ+CihmdW5jdGlvbigpewogIHZhciAkID0gZnVuY3Rpb24oaWQpeyByZXR1"
        "cm4gZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoaWQpOyB9OwogIHZhciBhZ2VudElkID0gZGVjb2Rl"
        "VVJJQ29tcG9uZW50KGxvY2F0aW9uLnBhdGhuYW1lLnNwbGl0KCcvJykucG9wKCkgfHwgJycpOwoK"
        "ICBmdW5jdGlvbiBlc2Mocyl7CiAgICByZXR1cm4gU3RyaW5nKHMgPT0gbnVsbCA/ICcnIDogcyku"
        "cmVwbGFjZSgvWyY8PiJdL2csIGZ1bmN0aW9uKGMpewogICAgICByZXR1cm4gKHsnJic6JyZhbXA7"
        "JywnPCc6JyZsdDsnLCc+JzonJmd0OycsJyInOicmcXVvdDsnfSlbY107CiAgICB9KTsKICB9CiAg"
        "ZnVuY3Rpb24gYWdvKHNlYyl7CiAgICBzZWMgPSBNYXRoLm1heCgwLCBNYXRoLnJvdW5kKHNlYyB8"
        "fCAwKSk7CiAgICBpZiAoc2VjIDwgNjApIHJldHVybiBzZWMgKyAnIOenkuWJjSc7CiAgICBpZiAo"
        "c2VjIDwgMzYwMCkgcmV0dXJuIE1hdGguZmxvb3Ioc2VjIC8gNjApICsgJyDliIbpkp/liY0nOwog"
        "ICAgaWYgKHNlYyA8IDg2NDAwKSByZXR1cm4gTWF0aC5mbG9vcihzZWMgLyAzNjAwKSArICcg5bCP"
        "5pe25YmNJzsKICAgIHJldHVybiBNYXRoLmZsb29yKHNlYyAvIDg2NDAwKSArICcg5aSp5YmNJzsK"
        "ICB9CiAgZnVuY3Rpb24gdXB0aW1lKHNlYyl7CiAgICBzZWMgPSBNYXRoLm1heCgwLCBNYXRoLnJv"
        "dW5kKHNlYyB8fCAwKSk7CiAgICBpZiAoc2VjIDwgMzYwMCkgcmV0dXJuIE1hdGguZmxvb3Ioc2Vj"
        "IC8gNjApICsgJyDliIbpkp8nOwogICAgaWYgKHNlYyA8IDg2NDAwKSByZXR1cm4gTWF0aC5mbG9v"
        "cihzZWMgLyAzNjAwKSArICcg5bCP5pe2ICcgKyBNYXRoLmZsb29yKChzZWMgJSAzNjAwKSAvIDYw"
        "KSArICcg5YiGJzsKICAgIHJldHVybiBNYXRoLmZsb29yKHNlYyAvIDg2NDAwKSArICcg5aSpICcg"
        "KyBNYXRoLmZsb29yKChzZWMgJSA4NjQwMCkgLyAzNjAwKSArICcg5bCP5pe2JzsKICB9CiAgZnVu"
        "Y3Rpb24gdHMobXMpewogICAgaWYgKCFtcykgcmV0dXJuICfigJQnOwogICAgdmFyIGQgPSBuZXcg"
        "RGF0ZShtcyk7CiAgICByZXR1cm4gZC50b0xvY2FsZURhdGVTdHJpbmcoJ3poLUNOJykgKyAnICcg"
        "KyBkLnRvTG9jYWxlVGltZVN0cmluZygnemgtQ04nKTsKICB9CgogIGZ1bmN0aW9uIHBhaW50KGEp"
        "ewogICAgdmFyIGNvbm4gPSBhLmNvbm5lY3Rpdml0eSB8fCB7fTsKICAgIHZhciBzdCA9IGEuc3Rh"
        "dHMgfHwge307CiAgICBkb2N1bWVudC50aXRsZSA9ICdBZ2VudFNvYyDCtyAnICsgKGEuaG9zdG5h"
        "bWUgfHwgYWdlbnRJZCk7CgogICAgJCgnY29ubi1kb3QnKS5jbGFzc05hbWUgPSAncGlsbC1sZycg"
        "KyAoY29ubi5vayA/ICcnIDogJyBvZmYnKTsKICAgICQoJ2Nvbm4tdGV4dCcpLnRleHRDb250ZW50"
        "ID0gY29ubi5vayA/ICflt7LogZTpgJonIDogJ+acquiBlOmAmic7CiAgICAkKCdjb25uLXN1Yicp"
        "LnRleHRDb250ZW50ID0gY29ubi5vayA/ICgn5bu26L+fICcgKyBjb25uLmxhdGVuY3lfbXMgKyAn"
        "IG1zJykgOiAoY29ubi5lcnJvciB8fCAn5peg5rOV6K6/6Zeu5LiK5ri4572R5YWzJyk7CiAgICAk"
        "KCdmLXVwc3RyZWFtJykudGV4dENvbnRlbnQgPSBhLnVwc3RyZWFtIHx8ICfigJQnOwogICAgJCgn"
        "Zi1sYXN0JykudGV4dENvbnRlbnQgPSBhZ28oYS5zaWxlbnRfc2VjKTsKICAgICQoJ2YtY291bnQn"
        "KS50ZXh0Q29udGVudCA9IChhLnJlcG9ydF9jb3VudCB8fCAwKSArICcg5qyhJzsKICAgICQoJ2Yt"
        "ZXJyJykudGV4dENvbnRlbnQgPSBjb25uLmVycm9yIHx8ICfmraPluLgnOwogICAgJCgnZi1ob3N0"
        "JykudGV4dENvbnRlbnQgPSBhLmhvc3RuYW1lIHx8IGEuYWdlbnRfaWQ7CiAgICAkKCdmLW5hbWUn"
        "KS50ZXh0Q29udGVudCA9IGEuYWdlbnRfbmFtZSB8fCAn4oCUJzsKICAgICQoJ2YtaXAnKS50ZXh0"
        "Q29udGVudCA9IGEuc291cmNlX2lwIHx8ICfigJQnOwogICAgJCgnZi1tb2RlJykudGV4dENvbnRl"
        "bnQgPSBhLm1vZGUgfHwgJ+KAlCc7CiAgICAkKCdmLXZlcicpLnRleHRDb250ZW50ID0gYS52ZXJz"
        "aW9uIHx8ICfigJQnOwogICAgJCgnZi11cHRpbWUnKS50ZXh0Q29udGVudCA9IHVwdGltZShhLnVw"
        "dGltZV9zZWMpOwogICAgJCgncy10b3RhbCcpLnRleHRDb250ZW50ID0gc3QudG90YWwgfHwgMDsK"
        "ICAgICQoJ3MtYmxvY2tlZCcpLnRleHRDb250ZW50ID0gc3QuYmxvY2tlZCB8fCAwOwogICAgJCgn"
        "cy1wYXNzZWQnKS50ZXh0Q29udGVudCA9IHN0LnBhc3NlZCB8fCAwOwogICAgJCgncy1lcnJzJyku"
        "dGV4dENvbnRlbnQgPSBzdC5lcnJvcnMgfHwgMDsKCiAgICB2YXIgYmFkZ2UgPSAnPHNwYW4gY2xh"
        "c3M9ImJhZGdlICcgKyAoYS5vbmxpbmUgPyAnb24nIDogJ29mZicpICsgJyI+JwogICAgICArIChh"
        "Lm9ubGluZSA/ICflnKjnur8nIDogJ+emu+e6vycpICsgJzwvc3Bhbj4nOwogICAgJCgnaGRyLWJh"
        "ZGdlJykuaW5uZXJIVE1MID0gYmFkZ2U7CiAgICAkKCdoZHItaG9zdCcpLnRleHRDb250ZW50ID0g"
        "YS5ob3N0bmFtZSB8fCBhLmFnZW50X2lkOwoKICAgIHZhciBiYW5uZXIgPSAkKCdiYW5uZXInKTsK"
        "ICAgIGlmICghYS5vbmxpbmUpewogICAgICBiYW5uZXIuc3R5bGUuZGlzcGxheSA9ICdibG9jayc7"
        "CiAgICAgIGJhbm5lci50ZXh0Q29udGVudCA9ICfor6Xnu4jnq6/lt7IgJyArIGFnbyhhLnNpbGVu"
        "dF9zZWMpICsgJ+WBnOatouS4iuaKpe+8iOi2hei/h+emu+e6v+mYiOWAvO+8ie+8jOS7peS4i+S4"
        "uuacgOWQjuS4gOasoeS4iuaKpeeahOW/q+eFp+OAgic7CiAgICB9IGVsc2UgewogICAgICBiYW5u"
        "ZXIuc3R5bGUuZGlzcGxheSA9ICdub25lJzsKICAgIH0KCiAgICB2YXIgcm93cyA9IChhLnJlY2Vu"
        "dCB8fCBbXSkubWFwKGZ1bmN0aW9uKGUpewogICAgICB2YXIgYWN0ID0gZS5hY3Rpb24gfHwgJ3Bh"
        "c3MnOwogICAgICB2YXIgY2xzID0gYWN0ID09PSAnYmxvY2snID8gJ2Jsb2NrJyA6IChhY3QgPT09"
        "ICdlcnJvcicgPyAnZXJyb3InIDogJ3Bhc3MnKTsKICAgICAgdmFyIGxhYmVsID0gYWN0ID09PSAn"
        "YmxvY2snID8gJ+aLpuaIqicgOiAoYWN0ID09PSAnZXJyb3InID8gJ+mUmeivrycgOiAn5pS+6KGM"
        "Jyk7CiAgICAgIHZhciBydWxlcyA9IChlLnJ1bGVzICYmIGUucnVsZXMubGVuZ3RoKSA/IGUucnVs"
        "ZXMuam9pbignLCAnKSA6ICfigJQnOwogICAgICByZXR1cm4gJzx0cj4nCiAgICAgICAgKyAnPHRk"
        "IGNsYXNzPSJtb25vIj4nICsgdHMoZS50cykgKyAnPC90ZD4nCiAgICAgICAgKyAnPHRkPjxzcGFu"
        "IGNsYXNzPSJ0YWcgJyArIGNscyArICciPicgKyBsYWJlbCArICc8L3NwYW4+PC90ZD4nCiAgICAg"
        "ICAgKyAnPHRkPicgKyBlc2MoZS5tZXRob2QpICsgJzwvdGQ+JwogICAgICAgICsgJzx0ZCBjbGFz"
        "cz0ibW9ubyI+JyArIGVzYyhlLnBhdGgpICsgJzwvdGQ+JwogICAgICAgICsgJzx0ZD4nICsgZXNj"
        "KGUucmlzayB8fCAn4oCUJykgKyAnPC90ZD4nCiAgICAgICAgKyAnPHRkPicgKyAoZS5zY29yZSA/"
        "IE51bWJlcihlLnNjb3JlKS50b0ZpeGVkKDIpIDogJ+KAlCcpICsgJzwvdGQ+JwogICAgICAgICsg"
        "Jzx0ZCBjbGFzcz0ibW9ubyI+JyArIGVzYyhydWxlcykgKyAnPC90ZD4nCiAgICAgICAgKyAnPHRk"
        "IGNsYXNzPSJtb25vIj4nICsgZXNjKGUucG9saWN5X3ZlciB8fCAn4oCUJykgKyAnPC90ZD4nCiAg"
        "ICAgICAgKyAnPHRkPicgKyBlc2MoZS5tZXNzYWdlIHx8ICfigJQnKSArICc8L3RkPicKICAgICAg"
        "ICArICc8L3RyPic7CiAgICB9KS5qb2luKCcnKTsKICAgICQoJ2Zsb3dzJykuaW5uZXJIVE1MID0g"
        "cm93czsKICAgICQoJ2Zsb3dzLWVtcHR5Jykuc3R5bGUuZGlzcGxheSA9IHJvd3MgPyAnbm9uZScg"
        "OiAnYmxvY2snOwogIH0KCiAgZnVuY3Rpb24gbG9hZCgpewogICAgZmV0Y2goJy92MS9mbGVldC9h"
        "Z2VudHMvJyArIGVuY29kZVVSSUNvbXBvbmVudChhZ2VudElkKSwgeyBjYWNoZTogJ25vLXN0b3Jl"
        "JyB9KQogICAgICAudGhlbihmdW5jdGlvbihyKXsKICAgICAgICBpZiAoIXIub2spIHRocm93IG5l"
        "dyBFcnJvcignbm90IGZvdW5kJyk7CiAgICAgICAgcmV0dXJuIHIuanNvbigpOwogICAgICB9KQog"
        "ICAgICAudGhlbihmdW5jdGlvbihhKXsKICAgICAgICBwYWludChhKTsKICAgICAgICAkKCdzdGFt"
        "cCcpLnRleHRDb250ZW50ID0gJ+abtOaWsOS6jiAnICsgbmV3IERhdGUoKS50b0xvY2FsZVRpbWVT"
        "dHJpbmcoJ3poLUNOJyk7CiAgICAgIH0pCiAgICAgIC5jYXRjaChmdW5jdGlvbigpewogICAgICAg"
        "ICQoJ3N0YW1wJykudGV4dENvbnRlbnQgPSAn6K+75Y+W5aSx6LSl77yM6YeN6K+V5Lit4oCmJzsK"
        "ICAgICAgfSk7CiAgfQoKICBsb2FkKCk7CiAgc2V0SW50ZXJ2YWwobG9hZCwgMzAwMCk7Cn0pKCk7"
        "Cjwvc2NyaXB0PgoiIiIKCgpkZWYgX3BhZ2UodGl0bGU6IHN0ciwgYm9keTogc3RyLCBqczogc3Ry"
        "LCBleHRyYV9oZWFkOiBzdHIgPSAiIikgLT4gc3RyOgogICAgcmV0dXJuICgKICAgICAgICAnPCFk"
        "b2N0eXBlIGh0bWw+XG48aHRtbCBsYW5nPSJ6aC1DTiI+XG48aGVhZD5cbicKICAgICAgICAnPG1l"
        "dGEgY2hhcnNldD0idXRmLTgiIC8+XG4nCiAgICAgICAgJzxtZXRhIG5hbWU9InZpZXdwb3J0IiBj"
        "b250ZW50PSJ3aWR0aD1kZXZpY2Utd2lkdGgsIGluaXRpYWwtc2NhbGU9MSIgLz5cbicKICAgICAg"
        "ICBmIjx0aXRsZT57dGl0bGV9PC90aXRsZT5cbiIKICAgICAgICBmIjxzdHlsZT57X1NIQVJFRF9D"
        "U1N9PC9zdHlsZT5cbiIKICAgICAgICBmIntleHRyYV9oZWFkfTwvaGVhZD5cbjxib2R5PlxuPGRp"
        "diBjbGFzcz1cIndyYXBcIj5cbiIKICAgICAgICBmIntib2R5fVxuPC9kaXY+XG57anN9XG48L2Jv"
        "ZHk+XG48L2h0bWw+XG4iCiAgICApCgoKZGVmIHJlbmRlcl9vdmVydmlld19wYWdlKCkgLT4gc3Ry"
        "OgogICAgIiIi5oC76KeI6aG1IEhUTUzjgIIiIiIKICAgIHJldHVybiBfcGFnZSgKICAgICAgICAi"
        "QWdlbnRTb2Mgwrcg57uI56uv5oC76KeIIiwKICAgICAgICBfbmF2KCkgKyBfT1ZFUlZJRVdfQk9E"
        "WSwKICAgICAgICBfT1ZFUlZJRVdfSlMsCiAgICApCgoKZGVmIHJlbmRlcl9kZXRhaWxfcGFnZSgp"
        "IC0+IHN0cjoKICAgICIiIue7iOerr+ivpuaDhemhtSBIVE1M44CCIiIiCiAgICBoZWFkZXIgPSAi"
        "IiIKICA8aGVhZGVyPgogICAgPGEgY2xhc3M9ImJhY2siIGhyZWY9Ii9mbGVldCI+4oaQIOi/lOWb"
        "nue7iOerr+aAu+iniDwvYT4KICAgIDxoMSBpZD0iaGRyLWhvc3QiPue7iOerr+ivpuaDhTwvaDE+"
        "CiAgICA8c3BhbiBpZD0iaGRyLWJhZGdlIj48L3NwYW4+CiAgICA8c3BhbiBjbGFzcz0iZ3JvdyI+"
        "PC9zcGFuPgogICAgPHNwYW4gY2xhc3M9InN0YW1wIiBpZD0ic3RhbXAiPuato+WcqOWKoOi9veKA"
        "pjwvc3Bhbj4KICA8L2hlYWRlcj4KIiIiCiAgICByZXR1cm4gX3BhZ2UoCiAgICAgICAgIkFnZW50"
        "U29jIMK3IOe7iOerr+ivpuaDhSIsCiAgICAgICAgaGVhZGVyICsgX0RFVEFJTF9CT0RZLAogICAg"
        "ICAgIF9ERVRBSUxfSlMsCiAgICApCgoKZGVmIGNyZWF0ZV9mbGVldF9wYWdlX3JvdXRlcigpIC0+"
        "IEFQSVJvdXRlcjoKICAgICIiIuaehOmAoCBmbGVldCDnnIvmnb/pobXpnaLot6/nlLHvvIjml6Dp"
        "ibTmnYPvvIzlhoXnvZHop4blm77vvInjgIIiIiIKICAgIHJvdXRlciA9IEFQSVJvdXRlcih0YWdz"
        "PVsiZmxlZXQiXSkKCiAgICBAcm91dGVyLmdldCgiL2ZsZWV0IiwgcmVzcG9uc2VfY2xhc3M9SFRN"
        "TFJlc3BvbnNlKQogICAgYXN5bmMgZGVmIGZsZWV0X292ZXJ2aWV3KCkgLT4gSFRNTFJlc3BvbnNl"
        "OgogICAgICAgICIiIue7iOerr+aAu+iniOeci+adv+OAgiIiIgogICAgICAgIHJldHVybiBIVE1M"
        "UmVzcG9uc2UocmVuZGVyX292ZXJ2aWV3X3BhZ2UoKSkKCiAgICBAcm91dGVyLmdldCgiL2ZsZWV0"
        "L2FnZW50L3thZ2VudF9pZH0iLCByZXNwb25zZV9jbGFzcz1IVE1MUmVzcG9uc2UpCiAgICBhc3lu"
        "YyBkZWYgZmxlZXRfYWdlbnRfZGV0YWlsKGFnZW50X2lkOiBzdHIpIC0+IEhUTUxSZXNwb25zZToK"
        "ICAgICAgICAiIiLljZXlj7Dnu4jnq6/or6bmg4XnnIvmnb/vvIjmlbDmja7nlLHliY3nq6/mjIkg"
        "YWdlbnRfaWQg5ouJ5Y+W77yJ44CCIiIiCiAgICAgICAgcmV0dXJuIEhUTUxSZXNwb25zZShyZW5k"
        "ZXJfZGV0YWlsX3BhZ2UoKSkKCiAgICByZXR1cm4gcm91dGVyCgoKX19hbGxfXyA9IFsiY3JlYXRl"
        "X2ZsZWV0X3BhZ2Vfcm91dGVyIiwgInJlbmRlcl9vdmVydmlld19wYWdlIiwgInJlbmRlcl9kZXRh"
        "aWxfcGFnZSJdCg=="
    ),
}

MAIN_IMPORT_ANCHOR = "from app.detection.pipeline import DetectionPipeline" + NL
MAIN_IMPORT_ADD = "from app.fleet import FleetRegistry, create_fleet_page_router, create_fleet_router" + NL

MAIN_SINGLETON_ANCHOR = "_audit = AuditLogger(enabled=settings.audit_enabled)" + NL
MAIN_SINGLETON_ADD = NL.join([
    "",
    "# ---- Fleet: 多终端总览（终端心跳上报驱动的内存表）----",
    "_fleet = FleetRegistry(",
    "    offline_after=settings.fleet_offline_after,",
    "    report_interval=settings.fleet_report_interval,",
    "    max_agents=settings.fleet_max_agents,",
    ")",
    "",
])

MAIN_MOUNT_ANCHOR = "app.include_router(create_nl_demo_router())" + NL
MAIN_MOUNT_ADD = NL.join([
    "",
    "# ---- Fleet: 多终端总览看板（终端上报 -> 内存表 -> 总览页 + 终端详情页）----",
    "if settings.fleet_enabled:",
    "    app.include_router(create_fleet_router(_fleet))",
    "    app.include_router(create_fleet_page_router())",
    "",
])

CFG_ANCHOR = "    audit_retention_days: int = 90" + NL
CFG_ADD = NL.join([
    "",
    "    # ---- Fleet: 多终端总览看板（桌面代理心跳上报驱动）----",
    "    fleet_enabled: bool = True",
    "    fleet_report_interval: int = 60",
    "    fleet_offline_after: int = 180",
    "    fleet_max_agents: int = 2000",
    "",
])


def write_files():
    for rel in sorted(FILES):
        path = os.path.join(ROOT, rel)
        folder = os.path.dirname(path)
        if folder and not os.path.isdir(folder):
            os.makedirs(folder)
        data = base64.b64decode(FILES[rel].encode("ascii"))
        handle = open(path, "wb")
        handle.write(data)
        handle.close()
        print("[写入] %s (%d bytes)" % (rel, len(data)))


def patch_file(path, anchor, addition, label):
    if not os.path.isfile(path):
        print("[跳过] %s 文件不存在: %s" % (label, path))
        return False
    handle = open(path, "rb")
    raw = handle.read()
    handle.close()
    anchor_b = anchor.encode("utf-8")
    add_b = addition.encode("utf-8")
    if add_b in raw:
        print("[跳过] %s（补丁已存在）" % label)
        return True
    if anchor_b not in raw:
        print("[警告] %s 锚点未找到，未做修改 -> %s" % (label, path))
        return False
    raw = raw.replace(anchor_b, anchor_b + add_b, 1)
    handle = open(path, "wb")
    handle.write(raw)
    handle.close()
    print("[补丁] %s" % label)
    return True


def main():
    write_files()
    ok = True
    main_py = os.path.join(ROOT, "app", "main.py")
    cfg_py = os.path.join(ROOT, "app", "config.py")
    ok = patch_file(main_py, MAIN_IMPORT_ANCHOR, MAIN_IMPORT_ADD, "main.py import") and ok
    ok = patch_file(main_py, MAIN_SINGLETON_ANCHOR, MAIN_SINGLETON_ADD, "main.py 注册表单例") and ok
    ok = patch_file(main_py, MAIN_MOUNT_ANCHOR, MAIN_MOUNT_ADD, "main.py 路由挂载") and ok
    ok = patch_file(cfg_py, CFG_ANCHOR, CFG_ADD, "config.py fleet 配置") and ok

    if "--rebuild" in sys.argv:
        print("")
        print("重建 api 容器 ...")
        rc = os.system("docker compose build api && docker compose up -d --force-recreate api")
        print("重建返回码: %d" % rc)
    else:
        print("")
        print("下一步执行：docker compose build api && docker compose up -d --force-recreate api")

    print("")
    print("完成后访问：http://<服务器地址>:8000/fleet")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
