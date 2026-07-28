# YouTube 知识AI整理

这是一个基于Youtube API的项目，通过从 YouTube 频道取得新字幕，整理为可检索的金融研究笔记，并通过邮件和 Discord 发送。邮件转发依赖resend，爬取字幕依赖Supadata，因此若你fork本仓库，你需要注册并填写这两个网站的API，同时你还需要一个AI API，用于整理，下面会有说明

## 每日工作
每天北京时间 11:23 读取频道 RSS。

## GitHub 设置

在仓库的 **Settings → Secrets and variables → Actions** 添加：

Secrets：

- `POE_API_KEY`：从 Poe 新建的 API Key。不要使用任何已经公开或发到聊天中的 Key。
- `RESEND_API_KEY`：Resend API Key。
- `DISCORD_WEBHOOK_URL`：目标频道的 Discord Webhook URL。
- `SUPADATA_API_KEY`：推荐。Supadata 字幕 API Key；免费额度足够日更频道使用。
- `YOUTUBE_PROXY_URL`：可选的高级备用方案。轮换住宅代理 URL，例如
  `http://用户名:密码@代理主机:端口`。使用 Supadata 时无需配置。

Variables：

- `POE_MODEL`：可选，默认 `GPT-5.4`。
- `EMAIL_FROM`：已在 Resend 验证的发件地址，例如 `知识库 <notes@updates.example.com>`。
- `EMAIL_TO`：收件地址；多个地址用英文逗号分隔。

然后在 **Actions → Daily finance knowledge → Run workflow** 中先开启 `preview`，检查生成的 Artifact。确认至少三篇笔记后，再关闭 `preview` 正式运行。

仓库的 Actions 设置必须允许工作流对仓库内容执行写操作。

### 为什么需要字幕 API

GitHub 托管 Runner 使用云服务商 IP，YouTube 经常要求这类 IP 登录确认。更换
Selenium、Playwright 或其他爬虫不会改变 GitHub 的出口 IP，因此仍可能被拦截。
推荐配置 `SUPADATA_API_KEY`，由专业字幕服务获取字幕；程序只对新视频调用一次，
已处理视频最多每 7 天检查一次修订，避免浪费免费额度。

Supadata 注册入口为 <https://dash.supadata.ai>，创建 Key 后将其直接保存为 GitHub
Secret，不要发到聊天或提交到仓库。

### 可选的代理方案

如果不希望使用字幕 API，也可以配置 `YOUTUBE_PROXY_URL`。程序会把同一个代理交给
`yt-dlp` 和备用抓取器。建议使用轮换住宅代理；免费公开代理和普通数据中心代理通常
不可靠。代理 URL 只存放在 GitHub Secret 中，错误写入公开状态文件前会自动遮蔽
账号、密码和完整 URL。不要提交 YouTube 账号 Cookies。

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
