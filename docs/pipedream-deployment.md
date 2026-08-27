# 使用 Pipedream 触发每日工作流

Pipedream 只负责按时调用 GitHub API。字幕处理、AI 分析、提交、邮件和 Discord
通知仍全部在 GitHub Actions 内完成，不需要部署网站或服务器。

推荐排程为北京时间每天 `09:30–12:00`、每半小时一次，共 6 次。外部排程稳定后，
再把 GitHub 原生 `schedule` 调低为备用；首次部署前不要关闭现有排程。

## 1. 创建最小权限 GitHub Token

打开 GitHub 的 **Settings → Developer settings → Personal access tokens →
Fine-grained tokens → Generate new token**，然后设置：

- Token name：`pipedream-youtube-kb`
- Expiration：建议 90 天，并在到期前轮换
- Repository access：**Only select repositories** → `youtube-caption-AI`
- Repository permissions：**Actions → Read and write**
- 其他权限保持默认

生成后立即复制 token；GitHub 只显示一次。不要把 token 写入仓库、邮件或 Pipedream
代码中。

## 2. 在 Pipedream 保存 Secret

1. 登录 Pipedream，建立一个 Project，例如 `youtube-finance-kb`。
2. 进入该 Project 的 **Variables**。
3. 新增变量 `YOUTUBE_KB_GITHUB_TOKEN`。
4. 保持 **Secret** 开启，把 GitHub token 作为值保存。

使用 Project Secret 可把 token 限制在此 Project 内。代码通过
`process.env.YOUTUBE_KB_GITHUB_TOKEN` 读取，不会把值写进工作流源码。

## 3. 建立排程工作流

1. 在 Project 中选择 **New Workflow**。
2. 第一个 Trigger 选择 **Schedule → Cron Expression**。
3. Timezone 选择 `Asia/Shanghai`，Cron 填写 `30 9-11 * * *`。
4. 打开 Trigger 右上角菜单，选择 **Add trigger**。
5. 第二个 Trigger 同样选择 **Schedule → Cron Expression**。
6. Timezone 选择 `Asia/Shanghai`，Cron 填写 `0 10-12 * * *`。
7. 暂时保持工作流未部署，先完成下一步测试。

两条排程会共同在每天北京时间 `09:30`、`10:00`、`10:30`、`11:00`、
`11:30`、`12:00` 触发。标准 cron 无法用一条表达式精确表示这组时间而不额外包含
`09:00` 或 `12:30`，因此这里使用同一个 Workflow 的两个 Trigger。

## 4. 添加 GitHub 调用步骤

1. 在 Schedule 后点击 **Add step**。
2. 选择 **Run Node.js code**。
3. 把仓库中的 `integrations/pipedream/trigger-github.mjs` 完整复制到代码编辑器。
4. 保存步骤。

代码只会向以下 GitHub API 发送一次请求：

`POST /repos/chaoyian/youtube-caption-AI/actions/workflows/daily-knowledge.yml/dispatches`

请求指定 `main` 分支，并把 `trigger_source` 设为 `pipedream`。没有指定 `force`，因此
现有状态与字幕哈希仍会阻止重复 AI 分析和重复通知。

## 5. 测试与部署

1. 在 Pipedream 点击 **Run Now** 或测试代码步骤。
2. 返回 GitHub 仓库的 **Actions → Daily finance knowledge**。
3. 应看到名称为 `Daily finance knowledge (pipedream)` 的新运行。
4. 确认 Pipedream 返回 HTTP 2xx；新版本 GitHub API 通常还会返回运行链接。
5. 测试成功后回到 Pipedream，点击 **Deploy**，并确认 Schedule 已开启。

没有新字幕时，GitHub Action 正常成功且不会调用 AI 或重发邮件，这正是预期结果。

## 6. 运维检查

- Pipedream 的 **Inspector / Job History** 可查看每次外部触发是否成功。
- GitHub Actions 的运行名称会保留来源，方便区分 `pipedream`、`schedule` 和 `manual`。
- GitHub token 到期或被撤销时，Pipedream 会显示 401/403；生成新 token 后只需更新
  Project Secret，不需要修改代码。
- Pipedream 免费账户有每日 credit 限额。这个工作流每天运行 6 次，每次通常只执行
  一个很短的 HTTP 请求；仍应在 Pipedream Billing 页面确认账户实际配额。
- Pipedream 稳定运行数日后，可把 GitHub 原生 cron 改为少量备用触发，避免无意义的
  重复 Actions 运行。
