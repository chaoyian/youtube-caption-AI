# YouTube 财经知识库

每天从 YouTube 频道取得新字幕，去除娱乐和广告噪声，使用 Poe 整理为可检索的金融研究笔记，并通过邮件和 Discord 发送。完整字幕只在运行期间存在，不提交到仓库。

## 工作方式

1. 每天北京时间 11:23 读取频道 RSS。
2. 使用 `yt-dlp` 获取字幕，失败时切换 `youtube-transcript-api`。
3. 清洗字幕并计算哈希。没有新字幕、字幕未变化或只是补发通知时，Poe 调用次数均为零。
4. 仅对新字幕或修订字幕生成研究笔记，提交知识库后再发送通知。
5. 抓取失败会写入状态，并在 Discord 告警；下一天自动重试。

AI 提示词会忽略黄段子、性暗示、冷笑话、广告和闲聊，仅提取宏观经济、市场、公司、行业、政策、数据、风险与资产价格信息。

## GitHub 设置

在仓库的 **Settings → Secrets and variables → Actions** 添加：

Secrets：

- `POE_API_KEY`：从 Poe 新建的 API Key。不要使用任何已经公开或发到聊天中的 Key。
- `RESEND_API_KEY`：Resend API Key。
- `DISCORD_WEBHOOK_URL`：目标频道的 Discord Webhook URL。

Variables：

- `POE_MODEL`：可选，默认 `GPT-5.4`。
- `EMAIL_FROM`：已在 Resend 验证的发件地址，例如 `知识库 <notes@updates.example.com>`。
- `EMAIL_TO`：收件地址；多个地址用英文逗号分隔。

然后在 **Actions → Daily finance knowledge → Run workflow** 中先开启 `preview`，检查生成的 Artifact。确认至少三篇笔记后，再关闭 `preview` 正式运行。

仓库的 Actions 设置必须允许工作流对仓库内容执行写操作。

## 新增频道

编辑 [`config/channels.yaml`](config/channels.yaml)：

```yaml
channels:
  - id: another-channel
    url: https://www.youtube.com/@example
    enabled: true
    languages: [zh-TW, zh-Hant, zh, en]
    backfill_days: 7
    tags: [财经]
```

`id` 只能使用小写英文字母、数字和连字符。新增频道不需要修改代码。

## 手动运行

安装 Python 3.11 或更新版本：

```bash
python -m pip install -e ".[test]"
```

只处理配置中的频道：

```bash
python -m yt_finance_kb process
```

预览单条视频：

```bash
POE_API_KEY="新建的Key" python -m yt_finance_kb process \
  --channel yutinghao-finance \
  --video "https://www.youtube.com/watch?v=VIDEO_ID" \
  --preview
```

`--force` 会无视字幕哈希重新调用 AI，只应在明确需要重做笔记时使用。`rebuild-indexes` 和 `notify` 命令不会调用 AI。

## 数据说明

- `knowledge/`：每期研究笔记。
- `indexes/topics/`、`indexes/entities/`：从状态确定性生成的索引。
- `state/videos.json`：字幕哈希、笔记版本、失败和投递状态。
- 字幕正文不会写入上述目录。

本项目整理的是节目内容，不做外部事实核验，也不构成投资建议。
