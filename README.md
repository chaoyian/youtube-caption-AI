# YouTube 知识AI整理

这是一个基于 YouTube API 的项目，通过从 YouTube 频道取得新字幕，整理为可检索的金融
研究笔记，并通过邮件和 Discord 发送。官方 YouTube Data API 负责发现新视频；字幕按
本地抓取器、Apify、Supadata 的顺序逐级回退，避免任何一家服务成为单点故障。

## 每日工作
当前 GitHub 原生排程在北京时间 `07:28–13:58`，于每小时的 `:28` 和 `:58`
检查一次。字幕尚未生成时
记为等待，后续触发点自动重试；同一小时设置两个错峰触发点，用来降低 GitHub
定时队列延迟或漏掉单次事件的影响。工作流使用明确的 UTC `23:28–05:58`，
不依赖 GitHub 的时区字段。
字幕成功后，当天余下任务会根据字幕哈希立即跳过，不重复调用 AI 或发送通知。

推荐用 Pipedream 在北京时间 `09:30–12:00` 每半小时调用一次现有
`workflow_dispatch`，避开 GitHub `schedule` 事件本身的延迟，同时保留少量 GitHub
原生排程作为备用。整个方案不需要网站或服务器；完整设置见
[`docs/pipedream-deployment.md`](docs/pipedream-deployment.md)。

## GitHub 设置

在仓库的 **Settings → Secrets and variables → Actions** 添加：

Secrets：

- `POE_API_KEY`：从 Poe 新建的 API Key。不要使用任何已经公开或发到聊天中的 Key。
- `YOUTUBE_API_KEY`：推荐。Google Cloud 中启用 YouTube Data API v3 后创建的 API
  Key，只用于发现公开视频和读取元数据，不用于下载字幕。
- `APIFY_TOKEN`：推荐的低成本字幕备用通道。Apify 免费方案每月提供平台额度；
  本项目默认使用按成功字幕计费的 `apihq/youtube-transcript-scraper` Actor。
- `SUPADATA_API_KEY`：可选的最后备用通道。只在免费抓取器和 Apify 都失败后调用，
  不再用于正常频道发现。
- `YOUTUBE_PROXY_URL`：可选。供 `yt-dlp` 与 `youtube-transcript-api` 共用的轮换
  住宅代理 URL，例如 `http://用户名:密码@代理主机:端口`。
- `RESEND_API_KEY`：Resend API Key。
- `GMAIL_USERNAME`：可选，作为发件人的完整 Gmail 地址。
- `GMAIL_APP_PASSWORD`：可选，开启 Google 两步验证后生成的 16 位应用专用密码；
  不要填写 Gmail 登录密码。
- `DISCORD_WEBHOOK_URL`：目标频道的 Discord Webhook URL。

Variables：

- `YOUTUBE_CHANNELS_JSON`：可选。用 JSON 配置全部频道，方便直接在 GitHub 仓库的
  Actions Variables 中增删频道；填写后会覆盖 `config/channels.yaml`，留空则使用仓库
  内配置。推荐填写为频道数组，例如：

  ```json
  [
    {
      "id": "yutinghao-finance",
      "url": "https://www.youtube.com/@yutinghaofinance",
      "youtube_channel_id": "UC0lbAQVpenvfA2QqzsRtL_g",
      "enabled": true,
      "languages": ["zh-TW", "zh-Hant", "zh", "en"],
      "backfill_days": 7,
      "tags": ["财经", "台湾", "美股", "宏观"]
    },
    {
      "id": "wtfinance-podcast",
      "url": "https://www.youtube.com/@WTFinancepodcast",
      "youtube_channel_id": "UCPI-DJWmId3Y-Dd1yI8LDnw",
      "enabled": true,
      "languages": ["en"],
      "backfill_days": 7,
      "tags": ["财经", "投资", "宏观", "英文"]
    }
  ]
  ```
- GitHub 设置路径为 **Settings → Secrets and variables → Actions → Variables**。
  修改变量会在下一次任务运行时生效，无需提交代码。变量内容必须保留所有需要采集的
  频道，因为它代表完整频道列表，而不是增量追加。
- `APIFY_TRANSCRIPT_ACTOR`：可选，默认
  `apihq~youtube-transcript-scraper`。只有明确更换 Apify Actor 时才填写。
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

### 视频发现与字幕回退

配置 `YOUTUBE_API_KEY` 后，程序使用官方 YouTube Data API 的频道上传播放列表发现
视频。若 API 暂时失败，会回退到 YouTube RSS；只有两者都失败时才会用 Supadata
发现频道内容。这样高频检查不会消耗 Supadata credits。

字幕按以下顺序尝试，任一成功即停止：

1. `yt-dlp`；
2. `youtube-transcript-api`；
3. Apify Actor（配置 `APIFY_TOKEN` 时）；
4. Supadata（配置 `SUPADATA_API_KEY` 时）。

GitHub 托管 Runner 使用云服务商 IP，前两种免费抓取方式可能被 YouTube 拦截；配置
`YOUTUBE_PROXY_URL` 后会通过同一轮换住宅代理重试。Apify 免费方案的每月平台额度
足够日更单频道作为备用，Supadata 因此只承担最后兜底。已处理视频最多每 7 天检查
一次修订，字幕哈希未变化时不会调用 AI 或重复投递。

Supadata 注册入口为 <https://dash.supadata.ai>，创建 Key 后将其直接保存为 GitHub
Secret，不要发到聊天或提交到仓库。

### 可选的代理方案

程序会把 `YOUTUBE_PROXY_URL` 同时交给 `yt-dlp` 和 `youtube-transcript-api`。建议
使用轮换住宅代理；免费公开代理和普通数据中心代理通常不可靠。代理 URL、YouTube
API Key、Apify Token 和其他凭据只存放在 GitHub Secret 中，错误写入公开状态文件
前会自动遮蔽。不要提交 YouTube 账号 Cookies。

## 新增频道

推荐在 GitHub 的 `YOUTUBE_CHANNELS_JSON` Variable 中直接修改完整频道列表。未设置该
变量时，编辑 [`config/channels.yaml`](config/channels.yaml)：

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

### 新频道验收测试

在 **Actions → Daily finance knowledge → Run workflow** 中勾选 `test_channels`，即可对
每个启用频道各取最新 2 条视频，强制重新抓取字幕、生成笔记并发送邮件和 Discord。
若只想测试一个新频道，同时填写它的 `channel` 配置 ID。验收模式会忽略 `video`、
`backfill_days`、`preview` 和 `force` 输入，并自动回溯查找最新视频。

本地也可以只验证抓取范围：

```bash
python -m yt_finance_kb process --latest-per-channel 2 --backfill-days 3650 --force
```

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
