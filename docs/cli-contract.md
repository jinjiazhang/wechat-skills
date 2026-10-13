# CLI 返回与组合契约

所有命令的账号相关查询显式传 `--config`。输出为一个 UTF-8 JSON 对象：

```json
{"schema_version":1,"ok":true,"command":"records.messages","data":[]}
```

失败包含 `error.code`、`error.message`，退出码 1。基础错误码：NOT_FOUND、OUTPUT_EXISTS、INVALID_ARGUMENT、DEPENDENCY_MISSING、OPERATION_FAILED、RESOURCE_UNAVAILABLE。未知异常不回显内部消息，防止 SQL/候选密钥进入 agent 上下文。媒体批次部分失败时 data 保留逐项结果，整体 ok=false。未知命令域在 `python -m wechat_cli` 总入口返回退出码 2 和 stderr 用法。

## 查询

- `records sessions --limit N [--include-hidden]`：会话与显示名；用 username 作为后续查询目标。
- `records contacts --limit N`：联系人与备注、昵称。
- `records messages --session USER --limit N [--since UNIX_SECONDS] [--ascending] [--raw]`：默认最新 N 条；ascending 改为最早 N 条。
- `records unread --limit N [--include-hidden]`：当前未读候选，整体限制 N 条；不修改已读状态。

server_id、svr_id、sort_seq、local_type 在 JSON 中为十进制字符串；create_time 是 Unix 秒整数，time 是 UTC+08:00 的 ISO 时间。默认剔除原始 XML/hex，使用 message_text；--raw 加回 message_content。图片返回 image_metadata，语音返回 voice，引用返回 reply_text 和 reference。正文解析失败应检查 content_decode_status/content_parse_status。

增量查询不是持久游标协议。--since 为包含边界秒数，建议短重叠窗口，并在 agent 中用账号、session_username、source_db、local_id、server_id 组成消息身份去重。若同一秒结果超过 limit，需扩大窗口或 limit；本版不保证固定 limit 轮询无遗漏。sort_seq 不等同于服务器确认序列；unread 也不适合作完整历史同步游标。会话不存在或无本地记录时返回空列表。

## 媒体

`media images --session USER --limit N --output DIR` 返回资源列表：success、source、path、format、size、thumbnail_only，WXGF 另有 preview_path/preview_status。success 表示资源解密成功，WXGF 预览需单独看 preview_status。图片未经过 OCR，不自动解释图中文字。

`media voice --session USER --local-id N --server-id N --create-time N --output DIR` 必须使用同条记录的标识。结果为 format、size、sha256、path、decoded=false。资源不存在、空音频或歧义匹配返回错误，不向其他会话回退。

`media validate --output DIR` 遍历当前配置可读记录，导出媒体及 report.json；报告仅含类型计数、字节、摘要和解码状态，不含正文。源数据库是动态状态，各查询非原子快照。

## 配置与密钥

配置形状为 wxid（账号目录名）、base_dir（账号目录的父级）、db_keys（相对数据库路径映射到 enc_key），可选 image_aes_key/image_xor_key。路径分隔符统一为 `/`。extract 的缺失列表非空时 complete=false；配置只包含通过 HMAC 验证的密钥。images 基于 Windows statistic 文件生成候选，并用真实缩略图全量像素加载验证；不保证支持所有版本与账号格式。

keys 命令不会把密钥放进 stdout 或命令行参数。已有配置默认拒绝覆盖；显式 --force 才替换。state 中仍有明文密钥与聊天媒体，依赖操作系统用户目录权限保护，不能作为可发布代码提交。
