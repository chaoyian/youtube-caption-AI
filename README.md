# YouTube 知识AI整理

这是一个基于Youtube API的项目，通过从 YouTube 频道取得新字幕，整理为可检索的金融研究笔记，并通过邮件和 Discord 发送。邮件转发依赖resend，爬取字幕依赖Supadata，因此若你fork本仓库，你需要注册并填写这两个网站的API，同时你还需要一个AI API，用于整理，下面会有说明

## 每日工作
每天北京时间 `10:23–23:23` 每小时检查一次。字幕尚未生成时记为等待，
下一小时自动重试；某个时间点被 GitHub 延迟或漏掉时，后续小时仍会独立触发。
字幕成功后，当天余下任务会根据字幕哈希立即跳过，不重复调用 AI 或发送通知。

## GitHub 设置

在仓库的 **Settings → Secrets and variables → Actions** 添加：

Secrets：

- `POE_API_KEY`：从 Poe 新建的 API Key。不要使用任何已经公开或发到聊天中的 Key。
- `RESEND_API_KEY`：Resend API Key。
- `GMAIL_USERNAME`：可选，作为发件人的完整 Gmail 地址。
- `GMAIL_APP_PASSWORD`：可选，开启 Google 两步验证后生成的 16 位应用专用密码；
  不要填写 Gmail 登录密码。
- `DISCORD_WEBHOOK_URL`：目标频道的 Discord Webhook URL。
- `SUPADATA_API_KEY`：推荐。Supadata 字幕 API Key；免费额度足够日更频道使用。
- `YOUTUBE_PROXY_URL`：可选的高级备用方案。轮换住宅代理 URL，例如
  `http://用户名:密码@代理主机:端口`。使用 Supadata 时无需配置。

Variables：

- `POE_MODEL`：可选，默认 `GPT-5.4`。
- `POE_POINT_LIMIT_PER_VIDEO`：可选，默认 `10000`。这是每个新视频的硬预算护栏，
  不是要求程序必须花满；没有新字幕、字幕未变化或仅重试通知时均为 0 点。
- `POE_INPUT_POINTS_PER_1K`、`POE_OUTPUT_POINTS_PER_1K`：通常无需填写。程序内置
  GPT‑5.4（75/450）和 Kimi K3（100/500）的 Poe 当前费率；切换到其他主模型时，
  按 Poe 模型页的实时费率填写。
- `POE_AUX_MODEL`：可选的超长字幕提取模型。普通字幕会直接交给主模型，不调用它。
  若填写未内置费率的小模型（例如 `GPT-5.4-Mini`），还必须填写
  `POE_AUX_INPUT_POINTS_PER_1K` 和 `POE_AUX_OUTPUT_POINTS_PER_1K`。费率必须以 Poe
  模型页当日显示为准，避免过期价格破坏预算计算。
- `EMAIL_FROM`：已在 Resend 验证的发件地址，例如 `知识库 <notes@updates.example.com>`。
- `EMAIL_TO`：收件地址；多个地址用英文逗号分隔。
- `EMAIL_PROVIDER`：可选，`auto`（默认）、`gmail` 或 `resend`。`auto` 优先 Gmail，
  单个收件人投递失败时再尝试 Resend。
- `GMAIL_FROM`：可选的 Gmail 显示发件人，例如
  `财经知识库 <your-account@gmail.com>`；留空时根据 `GMAIL_USERNAME` 自动生成。

邮件会逐个收件人独立发送，不会在邮件头中暴露其他人的地址。只配置 Resend 时仍按
原方式工作；只配置 Gmail 时无需购买域名。两套凭据都存在且 `EMAIL_PROVIDER=auto`
时，Gmail 为主通道、Resend 为逐收件人备用通道，不会在主通道成功后重复发送。

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
    youtube_channel_id: UCxxxxxxxxxxxxxxxxxxxxxx # 可选，固定 ID 可避免云端解析错误
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

## AI 点数与笔记质量

- 正常一期只调用一次 GPT‑5.4；程序不会为了“省钱”固定增加一次小模型调用。
- 完整字幕在预算可容纳时直接分析，避免旧版分块先压成简单观点而丢掉数据、因果链和风险。
- 只有超长字幕才走分块；分块结果必须保留主张、证据、因果链、前提和反例。
- 最终笔记限制为 3–5 个核心判断、5–8 张卡片、最多 15 个实体，并要求跨栏目去重。
- 每次返回后的 token 与估算 Poe 点数写入对应视频的 `poe_usage` 状态。程序会在发起
  下一次调用前预留输入、输出和安全余量，超过每视频上限时停止，不会继续修复重试。

## 数据说明

- `knowledge/`：每期研究笔记。
- `indexes/topics/`、`indexes/entities/`：从状态确定性生成的索引。
- `state/videos.json`：字幕哈希、笔记版本、失败和投递状态。
- 字幕正文不会写入上述目录。
