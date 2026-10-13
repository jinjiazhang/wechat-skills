---
name: wechat-keys
description: 获取本机已登录微信账号的数据库密钥和图片密钥，用于建立或修复本地读取配置。
---

获取当前用户授权账号的本地读取配置。运行 CLI，不把密钥值粘贴到对话或日志。

先在仓库根目录运行 `./scripts/setup.ps1`，然后使用 `.venv/Scripts/wechat-keys.exe`（或激活该虚拟环境）。Windows 4.1.11.24 已实测；微信需登录，进程内存读取需当前权限允许。

以下命令在仓库根目录运行，其他工作目录请传配置和输出的绝对路径。

```powershell
wechat-keys discover
wechat-keys extract --db-dir "账号目录/db_storage" --output "state/wxdb.json"
wechat-keys images --config "state/wxdb.json" --output "state/wxdb.json" --force
```

`discover` 返回候选账号，不能因目录最近修改就认定它是用户指定账号。已有会话提供的账号选择继续沿用；目标不明确且有多个候选时先明确账号。数据库提取只走只读扫描及 HMAC 验证，不调用 vendor 的实验调试器或退出登录流程。

输出包含配置路径、验证数量和缺失数据库，不含密钥。`complete=false` 表示配置不完整。默认不覆盖配置；用户要求刷新同一配置时可使用 `--force`。图片密钥会解密本地缩略图并完整加载像素验证；缺样本时请用户在电脑微信打开一张图片，再重试一次，不无限扫描。

安装后的 CLI 或当前技能 `scripts/run.py` 均可作为入口。配置必须留在本地；读取配置结构和返回格式见 [CLI 契约](../../../docs/cli-contract.md)。
