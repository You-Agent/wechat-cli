# macOS 缓存图片解密

本 fork 新增 `image-decrypt`：从本机 kvcomm **文件名**推导候选密钥，解密明确指定的 V2 DAT 文件，转换并完整解码校验后保存 PNG。不依赖数据库密钥，不连接调试器，不修改微信签名或 SIP，不安装 WeFlow，不打印或持久化图片密钥。

## 安装本 fork

```sh
git clone https://github.com/You-Agent/wechat-cli.git
cd wechat-cli
python3 -m venv .venv
.venv/bin/python -m pip install .
brew install ffmpeg
.venv/bin/wechat-cli image-decrypt --help
```

上游 npm 包不包含本 fork 的新增功能。已有 clone 可 `git pull` 后重新执行 `.venv/bin/python -m pip install .`。

## 使用

已配置 `~/.wechat-cli/config.json` 的 `db_dir` 时，会读取对应账号目录；也支持全局 `--config PATH`。没有配置时，显式提供**自己的账号目录**（`db_storage` 的父目录），不是聊天对象的账号。

```sh
.venv/bin/wechat-cli image-decrypt /absolute/path/image.dat \
  --account-dir /absolute/path/wxid_example_ab12 \
  --output /absolute/path/outputs/image.png
```

默认检查 macOS 微信容器常见 kvcomm 路径，也可重复指定 `--kvcomm-dir /absolute/path/kvcomm`。账号与缓存必须对应。这不是按联系人查找图片的命令：必须先确定实际 DAT 文件；现有 `history --media` 的候选路径不能保证对应某条消息。

输出必须是新的 `.png` 路径。原文件只读，临时目录权限为 0700，输出为 0600；成功后原子发布，失败时清理临时明文。不会覆盖已有文件。GIF 等标准动画只输出首帧；多分区 WXGF 暂不支持，直接报错。

## 验证规则和限制

1. 从 `key_<code>_*.statistic` 文件名提取 32 位正整数 code。
2. 规范化账号目录名，按缓存方案推导 AES 和 XOR 候选；AES 使用 MD5 十六进制前 16 个字符的 ASCII 字节，不是 hex 解码。
3. 检查 V2 长度边界、AES ECB PKCS#7 填充、解密长度和图片头。AES 段长度整除 16 时仍有完整填充块；XOR 长度为零时保留整个剩余段。
4. WXGF 提取大端长度限定的单个 HEVC 分区，交给 FFmpeg；标准格式也经 FFmpeg 转换。再次完整解码生成的 PNG，成功才交付。仅匹配文件头不算成功。

错误前缀区分 `key validation`（无缓存或候选不匹配）、`file decryption`（V2 文件结构无效）、`format conversion`（WXGF 不支持、缺 FFmpeg 或完整解码失败）。V2 缺乏完整性认证，损坏可能在转换阶段才被发现；无法仅凭解码失败断定密钥错误。

实机验证环境：Apple Silicon、macOS 26.6.2、微信 4.1.15，一张 V2 → WXGF → PNG 图片完整解码为 1920×1080。此结果不代表所有微信版本、图片格式或账号均兼容。缓存缺失、账号不匹配、未下载完整图片时可能失败。

## 数据库取钥经验（独立于图片命令）

同一环境中，旧扫描器没有找到数据库密钥；基于上游 PR #20 的运行期 SQLCipher 派生密钥捕获实验，在聊天活动期间验证了 6 个数据库的密钥对。未活动数据库不保证能获取；每组候选需通过数据库第一页 HMAC 和 SQLite 结构验证，不能把“捕获到字节”当成功。

该实验依赖应用调试权限和版本相关 ARM64 hook，**此次未合并实验扫描器或自动重签逻辑**。本 fork 的 `init` 仍是上游实现，不承诺上述版本数据库取钥可用。图片缓存命令无需重复重签、关闭 SIP 或内存扫描。App Management（应用管理）与 Full Disk Access（完全磁盘访问）是不同权限，读缓存也不应以修改应用签名作为前置步骤。

## 测试与来源

```sh
.venv/bin/python -m unittest discover -s tests -v
```

测试使用合成数据，不含真实账号、聊天记录、图片、缓存文件或密钥。

协议规则参考：

- [WeFlow macOS 缓存推导](https://github.com/jacklilyhello/WeFlow/blob/main/electron/services/keyServiceMac.ts)
- [wechat-decrypt V2 格式](https://github.com/acidvaccines/wechat-decrypt/blob/main/decode_image.py)
- [wxgf2jpg 分区格式](https://github.com/yansorau/wxgf2jpg/blob/main/pkg/util/dat2img/wxgf.go)
- [上游 PR #20 数据库运行期捕获实验](https://github.com/huohuoer/wechat-cli/pull/20)

本实现按格式规则独立编写；这些项目无需安装。

## 已有数据库密钥兼容

合入基于上游 PR #20 的派生密钥读取兼容：支持 `sqlcipher_derived_pair`、数据库页 HMAC 校验及 `biz_message_N.db`。解密缓存位于 `~/.wechat-cli/cache`。这只读取已有密钥，不包含运行期捕获或重签逻辑。
