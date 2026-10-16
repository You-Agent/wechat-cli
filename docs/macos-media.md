# macOS 图片与语音读取

本 fork 在聊天查询之外，提供图片解密、媒体库密钥补取和语音音频读取。语音音频可作为内部缓存，供调用方按需播放或识别；交付音频文件不是默认目标。

| 命令 | 用途 | 前置条件 |
|---|---|---|
| `image-decrypt` | 从明确指定的 V2 DAT 文件读取图片并保存 PNG | 对应账号的 kvcomm 缓存、FFmpeg |
| `voice-export` | 按聊天对象和消息 ID 获取语音音频，缓存为 WAV | 对应媒体库密钥、`audio` 解码依赖 |
| `media-keys` | 限时捕获媒体库派生密钥，实验功能 | macOS ARM64、LLDB、用户已启动且允许调试的微信副本 |

## 安装本 fork

```sh
git clone https://github.com/You-Agent/wechat-cli.git
cd wechat-cli
python3 -m venv .venv
.venv/bin/python -m pip install .
```

图片读取还需要 FFmpeg；语音读取需要可选解码包 `pilk`：

```sh
brew install ffmpeg
.venv/bin/python -m pip install '.[audio]'
.venv/bin/wechat-cli image-decrypt --help
.venv/bin/wechat-cli voice-export --help
```

上游 npm 包不包含本 fork 的新增功能。已有 clone 可 `git pull` 后重新执行安装；需要语音时保留 `audio` 安装选项。后文的 `wechat-cli` 可替换为源码虚拟环境中的 `.venv/bin/wechat-cli`。

## 数据库密钥

复用 `~/.wechat-cli/` 中的配置和已有密钥。支持 `sqlcipher_derived_pair`、数据库页 HMAC 校验及 `biz_message_N.db`；解密缓存位于 `~/.wechat-cli/cache`。

`init` 仍使用上游取钥实现，不保证适用于所有微信版本。macOS 上它可能自动重签微信，执行前应说明影响。媒体库缺密钥不等于其他已有密钥失效，不因此强制重新初始化。

### 补取媒体库密钥

`media-keys` 基于上游 PR #20 的运行期 SQLCipher 派生密钥捕获方法。需要 Xcode Command Line Tools 提供的 `/usr/bin/lldb`，以及由用户准备并手动启动、允许调试的微信副本。命令不会创建副本、启动、退出或重新签名微信。

先备份配置和已有密钥，再连接用户已启动的副本：

```sh
wechat-cli media-keys --pid PID --app /absolute/path/WeChat-capture.app --seconds 180
```

看到 `CAPTURE_ARMED` 后，用户在微信中播放目标语音。捕获窗口结束后，命令删除断点并分离调试器。每组新增密钥须通过数据库 HMAC、SQLite 页结构和 `quick_check` 校验，才会合并到已有密钥文件；旧密钥另存备份。捕获文件放在权限为 0700 的独立目录，输出不包含密钥字节。

此方法依赖版本相关的 ARM64 指令特征。只有实际访问的数据库才可能触发捕获，超时或无法连接时不代表取得了密钥。断点可能影响播放流畅度，应使用短窗口。结束后确认调试器已退出、微信正常运行，再读取目标音频；用户可手动退出调试副本并正常打开微信。

## 图片读取

`image-decrypt` 从本机 kvcomm **文件名**推导候选密钥，解密明确指定的 V2 DAT 文件，转换并完整解码校验后保存 PNG。它不依赖数据库密钥，不连接调试器，不修改微信签名或 SIP，不安装 WeFlow，不打印或持久化图片密钥。

已配置 `~/.wechat-cli/config.json` 的 `db_dir` 时，会读取对应账号目录；也支持全局 `--config PATH`。没有配置时，显式提供**自己的账号目录**（`db_storage` 的父目录），不是聊天对象的账号。

```sh
wechat-cli image-decrypt /absolute/path/image.dat \
  --account-dir /absolute/path/wxid_example_ab12 \
  --output /absolute/path/outputs/image.png
```

默认检查 macOS 微信容器常见 kvcomm 路径，也可重复指定 `--kvcomm-dir /absolute/path/kvcomm`。账号与缓存必须对应。必须先确定实际 DAT 文件；现有 `history --media` 的候选路径不能保证对应某条消息。

输出必须是新的 `.png` 路径。原文件只读，临时目录权限为 0700，输出为 0600；成功后原子发布，失败时清理临时明文。不会覆盖已有文件。GIF 等标准动画只输出首帧；多分区 WXGF 暂不支持，直接报错。

### 图片验证规则和限制

1. 从 `key_<code>_*.statistic` 文件名提取 32 位正整数 code。
2. 规范化账号目录名，按缓存方案推导 AES 和 XOR 候选；AES 使用 MD5 十六进制前 16 个字符的 ASCII 字节，不是 hex 解码。
3. 检查 V2 长度边界、AES ECB PKCS#7 填充、解密长度和图片头。AES 段长度整除 16 时仍有完整填充块；XOR 长度为零时保留整个剩余段。
4. WXGF 提取大端长度限定的单个 HEVC 分区，交给 FFmpeg；标准格式也经 FFmpeg 转换。再次完整解码生成的 PNG，成功才交付。仅匹配文件头不算成功。

错误前缀区分 `key validation`（无缓存或候选不匹配）、`file decryption`（V2 文件结构无效）、`format conversion`（WXGF 不支持、缺 FFmpeg 或完整解码失败）。V2 缺乏完整性认证，损坏可能在转换阶段才被发现；无法仅凭解码失败断定密钥错误。

App Management（应用管理）与 Full Disk Access（完全磁盘访问）是不同权限，读图片缓存不应以修改应用签名作为前置步骤。

## 语音读取

查询聊天时，按需获取语音消息对应的音频。当前底层命令名为 `voice-export`：它从本机媒体数据库精确匹配语音，将 WAV 缓存到指定目录，并返回对应的时间、消息 ID 和音频路径。

```sh
wechat-cli voice-export "聊天对象" --start-time "2026-10-16" --end-time "2026-10-16" --output-dir /absolute/path/new-cache-directory
```

命令联合匹配聊天对象和服务器消息 ID，并核对数据库提供的时间与本地消息 ID。缺失、歧义、空音频、未知格式或解码时长不符时报告错误。每条成功音频独立缓存为 WAV，另存 `manifest.json`；缓存目录必须不存在。

调用方使用每条结果的 `time`、`local_id` 与聊天消息关联，通过 `path` 读取音频，并逐条检查 `error`。部分媒体库缺密钥时，仅尝试可读库，并通过 `unavailable_media_databases` 列出缺项；目标音频均已取得时，无需补取其他库。

WAV 是读取语音的中间文件。获取成功只证明音频可读，不代表已知道语音内容。本命令不内置语音识别；调用方在识别前向用户确认是否识别及本次范围，再按确认结果识别为文字并结合聊天上下文分析。用户需要听取或获取文件时，再提供音频。

## 实机验证范围

- 图片：Apple Silicon、macOS 26.6.2、微信 4.1.15，一张 V2 → WXGF → PNG 图片完整解码为 1920×1080。
- 早期数据库取钥实验：同一环境下，旧扫描器没有找到密钥；运行期捕获在聊天活动期间验证了 6 个数据库的密钥对。未活动数据库不保证能获取。
- 语音：2026-10-17，在 Apple Silicon、微信 4.1.15 上捕获并验证了 `message/media_1.db` 密钥，从指定聊天取得 3 条语音 WAV。文件通过 PCM 帧长度检查和 FFmpeg 完整解码。`media_0.db` 未取得密钥，但这 3 条语音均在可读库中精确命中。

上述结果不代表所有微信版本、图片格式或账号均兼容。缓存缺失、账号不匹配或媒体未下载完整时可能失败。未执行语音识别。

## 测试与来源

```sh
.venv/bin/python -m unittest discover -s tests -v
```

测试使用合成数据，不含真实账号、聊天记录、图片、音频、缓存文件或密钥。

协议规则参考：

- [WeFlow macOS 缓存推导](https://github.com/jacklilyhello/WeFlow/blob/main/electron/services/keyServiceMac.ts)
- [wechat-decrypt V2 格式](https://github.com/acidvaccines/wechat-decrypt/blob/main/decode_image.py)
- [wxgf2jpg 分区格式](https://github.com/yansorau/wxgf2jpg/blob/main/pkg/util/dat2img/wxgf.go)
- [上游 PR #20 数据库运行期捕获实验](https://github.com/huohuoer/wechat-cli/pull/20)

本实现按格式规则独立编写；这些项目无需安装。
