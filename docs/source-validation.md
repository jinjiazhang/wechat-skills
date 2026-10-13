# 来源与独立验证

提取日期：2026-10-13。读取层从同机 wechat-agent 项目的已验证实现整理而来；新仓库的执行路径不导入或调用旧目录。

## 依赖来源

- vendor/wcdb_key_tool_windows.py 来自 https://github.com/TANGandXUE/wcdb-key-tool ，固定提交 `79f1b5b92e12c66aa281b4a60a3c478b5f547dfa`，原文件未修改，MIT 许可保存在 vendor/LICENSE.wcdb-key-tool。CLI 只通过独立 worker 调用只读扫描函数，不暴露 vendor 的其他命令。
- 图片候选验证思路参考 https://gist.github.com/acmerfight/0a01249ef72970d07a2603dbb629e80f ，参考提交 `c98dbb10cf23c30e83d5d744905101b0880887a0`；本包独立实现候选生成、文件读取及像素校验，未拷贝该工具源码。
- 原调查使用 REA 提交 `ded199abcde611a0ba4739ecb536e0ede0075670` 检查媒体空表结构；REA/Ghidra 不作为日常 CLI 运行依赖。Ghidra 对大型 DLL 的分析曾超时，不能据此声称验证了函数调用链。
- SQLCipher/OpenSSL 为本机已有工具的本地副本，纳入 Git 并附许可证、来源说明和 SHA-256 校验；不纳入纯 Python wheel。

## 本次新目录实测

- Windows 微信 4.1.11.24，独立重新提取并通过 HMAC 验证 18 个数据库密钥，无缺失数据库。
- 图片候选从本地 statistic 文件取得，以真实图片像素加载验证。
- 安装的 wechat-records 命令独立读取文件传输助手的文本、引用和语音元数据。
- 全库媒体验证共读取 47 条记录：24 文本、11 图片、9 引用、1 语音、1 type_47、1 type_10000。11 张图片均完整加载像素，语音 3,020 毫秒、5,425 字节与声明长度一致。
- 38 项测试通过；三个 SKILL.md 通过 skill-creator 验证。验证数据随本机收消息变化，以 state/validation/report.json 为准。

动态跨库查询不构成原子快照。未知类型保留类型标识，不表示已支持表情或系统消息语义。语音未转写，WXGF 仅首帧，两者不伪装成完整内容理解。

本机二进制 SHA-256：

- `libcrypto-3-x64.dll`: `1baa4840383ecbf2d02b4e6a22dc9cad7a7d66c555603dce615a0cf0476154d6`
- `sqlcipher.exe`: `aa6ff2dfe0770a6e34d1e7cbe2fd86ee7549c47897512a4d2e17a7732d9b3be9`
