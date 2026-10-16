# 本机语音导出（实验功能）

`voice-export` 从本机媒体数据库精确匹配语音并生成 WAV，不调用语音识别服务。

## 安装

在本 fork 的源码目录中，使用现有虚拟环境安装语音解码依赖：

```bash
.venv/bin/python -m pip install '.[audio]'
.venv/bin/wechat-cli voice-export --help
```

补取密钥还需要 macOS ARM64、Xcode Command Line Tools 提供的 `/usr/bin/lldb`，以及由用户准备并手动启动、允许调试的微信副本。本命令不会创建或重新签名该副本。已有媒体库密钥时，可直接执行导出。

## 补取媒体库密钥

`media-keys` 连接用户已启动、允许调试的微信副本。命令不会启动、退出或重新签名微信。

```bash
wechat-cli media-keys --pid PID --app /absolute/path/WeChat-capture.app --seconds 180
```

看到 `CAPTURE_ARMED` 后，用户在微信中播放目标语音。捕获窗口结束后，命令删除断点并分离调试器。每组新增密钥须通过数据库 HMAC、SQLite 页结构和 `quick_check` 校验，才会合并到已有密钥文件。旧密钥另存备份。捕获文件位于权限为 0700 的独立目录，输出不包含密钥字节。

此方法依赖 macOS ARM64 和版本相关的 SQLCipher 指令特征。只有实际访问的数据库才可能触发捕获，超时或无法连接时不代表取得了密钥。断点会影响微信播放的流畅度，应使用短窗口，结束后确认调试器已退出、微信正常运行。

## 导出语音

使用上述 `audio` 安装选项安装可选解码包 `pilk`。

```bash
wechat-cli voice-export "聊天对象" --start-time "2026-10-16" --end-time "2026-10-16" --output-dir /absolute/path/new-directory
```

命令联合匹配聊天对象和服务器消息 ID，并核对数据库提供的时间与本地消息 ID。缺失、歧义、空音频、未知格式或解码时长不符时报告错误。每条成功音频独立保存为 WAV，另存 `manifest.json`；输出目录必须不存在。部分媒体库缺密钥时，仅尝试可读库，并在结果中列出缺项。

## 本机验证范围

2026-10-17，在 Apple Silicon、微信 4.1.15 上捕获并验证了 `message/media_1.db` 密钥，从指定聊天导出 3 条语音 WAV。文件通过 PCM 帧长度检查和 FFmpeg 完整解码。`media_0.db` 未取得密钥，但这 3 条语音均在可读库中精确命中。未执行语音识别，也未证明其他微信版本兼容。
