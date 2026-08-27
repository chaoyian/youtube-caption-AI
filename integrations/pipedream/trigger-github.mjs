// Paste this file into a Pipedream Node.js code step.
// Keep the GitHub token in a project secret named YOUTUBE_KB_GITHUB_TOKEN.

export default defineComponent({
  async run({ $ }) {
    const token = process.env.YOUTUBE_KB_GITHUB_TOKEN;
    if (!token) {
      throw new Error("Missing Pipedream secret YOUTUBE_KB_GITHUB_TOKEN");
    }

    const response = await fetch(
      "https://api.github.com/repos/chaoyian/youtube-caption-AI/actions/workflows/daily-knowledge.yml/dispatches",
      {
        method: "POST",
        headers: {
          Accept: "application/vnd.github+json",
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
          "X-GitHub-Api-Version": "2026-03-10",
        },
        body: JSON.stringify({
          ref: "main",
          inputs: {
            trigger_source: "pipedream",
          },
        }),
      },
    );

    const responseBody = await response.text();
    if (!response.ok) {
      throw new Error(
        `GitHub workflow dispatch failed (${response.status}): ${responseBody.slice(0, 500)}`,
      );
    }

    const result = responseBody ? JSON.parse(responseBody) : {};
    $.export("$summary", "GitHub Action 已触发");
    return {
      status: response.status,
      workflow_run_id: result.workflow_run_id ?? null,
      html_url: result.html_url ?? null,
    };
  },
});
