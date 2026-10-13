# WeChat Skills / CLI

可独立运行的 Windows 本地微信数据层，供后续聊天技能或 agent 组合调用。当前提供三个技能及同名 CLI：

| 技能 | CLI | 功能 |
| --- | --- | --- |
| [wechat-keys](skills/wechat-keys/SKILL.md) | `wechat-keys` | 发现账号、获取并验证数据库密钥、验证图片密钥 |
| [wechat-records](skills/wechat-records/SKILL.md) | `wechat-records` | 会话、联系人、消息、引用回复与未读查询 |
| [wechat-media](skills/wechat-media/SKILL.md) | `wechat-media` | 图片解密、WXGF 首帧、原始语音导出、真实媒体验证 |

## 安装与运行

```powershell
cd D:/Workspace/wechat-skills
./scripts/setup.ps1
. ./.venv/Scripts/Activate.ps1
wechat-keys discover
wechat-keys extract --db-dir "实际账号目录/db_storage" --output state/wxdb.json
wechat-keys images --config state/wxdb.json --output state/wxdb.json --force
wechat-records sessions --config state/wxdb.json
wechat-records messages --config state/wxdb.json --session filehelper --limit 20
wechat-media images --config state/wxdb.json --session filehelper --output state/images
```

本机已独立生成并验证 `state/wxdb.json`，可以直接从 records 命令开始；再次 extract 默认不会覆盖它。账号配置、媒体和运行结果被 Git 忽略，stdout 不输出密钥。

无需安装命令入口时，可在项目根目录使用 `python -m wechat_cli keys|records|media ...`；从任意目录也可运行 `python D:/Workspace/wechat-skills/skills/wechat-records/scripts/run.py ...`。技能目录保留在用户指定仓库内，尚未全局注册到 Codex。安装技能时保留整个仓库，或先安装 Python 包再安装技能目录。

所有业务源码、技能、密钥扫描器与 Windows x64 SQLCipher/OpenSSL 运行文件均在本仓库内，许可证和校验信息位于 tools。`scripts/setup.ps1` 在仓库内创建独立 `.venv`、安装 Python 依赖并检查运行文件，不需要旧项目或全局已安装的 wechat 包。首次安装需 Python 3.10+ 和网络；运行时仍需本机已登录微信及其账号数据。

无需激活环境时，直接运行 `.venv/Scripts/wechat-records.exe` 等命令。移动目录或在其他机器拉取后重新运行 setup；虚拟环境不可直接搬迁。账号配置含本机路径，必要时重新 extract；密钥和聊天文件不会随 Git 推送。

`WECHAT_SQLCIPHER` 可覆盖默认工具路径；不设置则使用仓库 tools。普通 wheel 仅封装 Python 代码，完整独立运行请使用整个 Git 仓库。

## Agent 接入

stdout 为单个 UTF-8 JSON，成功退出 0，失败退出 1；`--help` 为人类可读文本。先解析 `ok`，再处理 `data`；大整数消息 ID 输出为字符串。完整返回约定见 [CLI 契约](docs/cli-contract.md)。

推荐链路：选择账号 → 获取配置 → 选择准确会话 username → 获取消息 → 按需读取媒体 → 构造回复上下文。

新增实验性 `wechat-send` 和 `wechat-agent` CLI，可通过各自的 `--help` 查看命令。agent 使用 `init → next → reply` 串联流程，由当前助手生成回复，不调用外部模型 API，也不启动后台聊天进程。发送默认仅预览，显式传入 `--execute` 才执行；本地 SQLite 保存待回复批次、发送上限和去重状态。发送结果不确定时先用 `wechat-send reconcile` 核对，不应更换 token 盲目重发。

发送后端通过本地 Win32 输入和 Windows OCR 操作微信窗口，需要窗口前台可用；当前仍受焦点和界面识别问题影响，尚未完成真实发送端到端验证。单元测试通过不代表实聊发送成功。新增发送与 agent 的独立 skill 尚未完成，现有三个技能和读取功能可继续使用。

## 验证与来源

```powershell
.venv/Scripts/python.exe -m unittest discover -s tests -v
wechat-media validate --config state/wxdb.json --output state/validation
```

Windows 微信 4.1.11.24 实测。SILK 目前按原始字节导出，不含语音转写；WXGF 仅首帧；部分图片仅有缩略图。实时跨库读取不保证同一时刻的原子快照。[来源与验证](docs/source-validation.md)。
