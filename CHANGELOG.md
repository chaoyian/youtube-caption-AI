# Changelog

## 0.2.0 — 2026-08-29

### Added

- 项目内 Prompt Optimizer 开发工具：三候选、固定案例、匿名机器评测、用户选择/反馈/编辑和明确定稿。
- `Prompt optimization` GitHub Workflow：已授权 AI/自动化可远程触发字幕抓取、候选测试、跨轮反馈和最终定稿。
- 可恢复的 JSON 优化会话、独立 Poe 点数上限、平台期提示和生产提示词历史备份。
- 预览运行生成本地提示词评测案例；原始字幕和进行中的会话不会提交到 Git。
- 优化候选邮件预览，以及 `test-email --to` 和 Actions `test_email_to` 指定测试收件地址。

### Changed

- 金融研究质量指令改为包内版本化资源；固定 `ResearchNote` JSON 契约保持不变。
- 按评审反馈移除 `ResearchNote` 的独立实体字段、实体展示和实体索引；来源类型保留为内部约束，但不再在 Markdown 成品逐条展示。
- 测试邮件不再要求字幕服务配置，也不会修改正式收件名单或正式投递状态。

### Compatibility and limitations

- 现有 `process`、`notify`、`EMAIL_TO`、Gmail 和 Resend 配置继续兼容。
- 优化器复用 Poe API，调用量明显高于单次视频分析；默认会话上限为 50,000 点、最多五轮。
- 邮件只提供候选预览，不解析邮件回复；选择和反馈必须通过 CLI/API 提交。
- 本版本准备好代码和发布说明，但不会自动创建 Git 标签或 GitHub Release。
- 优化会话 Artifact 保留 30 天；continue/finalize 必须提供前一轮 Workflow Run ID。
