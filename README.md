# ChatGPT 记录迁移工具

这个项目用于把 ChatGPT 记录转换成可迁移、可复用的文件包，适合用户把旧记录整理后喂给新项目。

## 能力边界（截至 2026-04-04）

- 不支持直接用 `https://chatgpt.com/g/.../project` 链接拉取历史（网页项目页需要登录态，且无公开“按项目链接拉取历史”接口）。
- 支持两种输入来源：
  - 方法1：官方导出数据（`export.zip` / `conversations.json`）
  - 方法2：浏览器书签脚本导出（登录态下直接拉 `/backend-api/*` 并下载 JSON）
- 输出会生成（默认）：
  - 每个会话目录（`session.md` + `session.html`）
  - 一个 `bundle/upload_chunk_XXX.md` 分片包（可按顺序上传到新项目）
  - `index.md` 和 `bundle/manifest.csv` 索引
  - 如需 JSON 额外文件，可加 `--with-json`

## 快速开始

1. 准备 Python 3.10+
2. 在项目根目录执行：

```bash
python -m venv .venv
. .venv/Scripts/activate
pip install -e .
```

3. 运行迁移（方法1 或方法2 导出的 JSON 都可）：

```bash
chatgpt-migrator \
  --export "D:/downloads/chatgpt_export.zip" \
  --out "./output/migration_output" \
  --project-url "https://chatgpt.com/g/g-p-69a46ad83fc08191bcefae6996d15b25-worldquant/project" \
  --keyword "worldquant" \
  --since "2025-01-01"
```

也可以不安装脚本，直接：

```bash
python -m chatgpt_migrator --export "D:/downloads/chatgpt_export.zip"
```

全量迁移（推荐）：把“全局页导出 + 每个项目页导出”的多个 JSON 一次性合并去重：

```bash
chatgpt-migrator \
  --export "D:/exports/chatgpt_global.json" \
  --export "D:/exports/chatgpt_worldquant_project.json" \
  --export "D:/exports/chatgpt_other_project.json" \
  --out "./output/migration_output_all"
```

## 方法2：生成书签脚本并导出 JSON

1. 先生成脚本：

```bash
chatgpt-migrator --generate-bookmarklet --bookmarklet-out "./bookmarklet_exporter.js"
```

会同时生成：
- `bookmarklet_exporter.js`
- `bookmarklet_exporter.bookmarklet.txt`（可直接粘贴到浏览器书签 URL）

2. 在浏览器里创建书签（URL 填 `*.bookmarklet.txt` 的整行内容）。
3. 建议打开目标项目页（如 `https://chatgpt.com/g/.../project`）且处于已登录状态，再点击书签。  
   现在项目页为严格模式：只导出当前项目侧栏可见会话（`scope_mode=project_dom_only_strict`），不会回退混入全局会话。
4. 用下载到的 JSON 再执行迁移。迁移阶段会生成每个会话的 `session.md` 与 `session.html`。如果有多个导出，重复写多个 `--export`：

```bash
chatgpt-migrator \
  --export "D:/downloads/chatgpt_bookmarklet_export_global.json" \
  --export "D:/downloads/chatgpt_bookmarklet_export_worldquant.json" \
  --out "./output/migration_output"
```

## 关键参数

- `--export`：必填，可重复传入多个导出文件（zip/json），工具会按会话 ID 自动去重合并
- `--out`：输出目录，默认 `output`
- `--project-url`：可选，项目链接（会自动提取 slug token 做过滤）
- `--keyword`：可重复传入多个关键词
- `--since/--until`：按 UTC 日期过滤（`YYYY-MM-DD`）
- `--max-conversations`：最多处理多少条（0=不限制）
- `--max-chunk-chars`：每个上传分片的字符上限
- `--include-empty`：包含无可提取文本的会话
- `--project-only`：仅保留项目作用域会话（需要书签导出里带 `_project_slug/_export_scope`）
- `--message-strategy`：`full`（默认）/ `user_last_assistant` / `user_only`
- `--with-json`：额外输出 JSON 文件（默认关闭）
- `--generate-bookmarklet`：生成书签导出脚本
- `--bookmarklet-out`：书签脚本输出路径
- `--bookmarklet-page-limit`：列表分页大小，默认 `100`
- `--bookmarklet-max-conversations`：书签导出会话上限，默认 `0`（不限制）
- `--bookmarklet-api-base`：默认 `https://chatgpt.com`
- `--bookmarklet-export-format`：仅支持 `json`（默认）

## 输出目录示例

```text
output/
  index.md
  sessions/
    0001_worldquant_strategy/
      session.md
      session.html
    0002_another_topic/
      session.md
      session.html
  bundle/
    manifest.csv
    UPLOAD_ORDER.txt
    upload_chunk_001.md
    upload_chunk_002.md
```

## 建议迁移流程

1. 用方法1（官方导出）或方法2（书签导出）拿到记录源文件。
2. 运行本工具得到 `output` 下的迁移结果目录。
3. 打开 `bundle/UPLOAD_ORDER.txt`，按顺序把 `upload_chunk_XXX.md` 上传到新项目知识库或作为上下文文件。
4. 在新项目先让 GPT 总结/重建结构，再进入继续开发。

## 注意

- 项目 URL 过滤是“相关性匹配”，不是官方项目 ID 精确拉取。
- 建议结合 `--keyword` 和日期范围减少误匹配。
- 方法2依赖网页接口与登录态，若 ChatGPT 前端接口变更，书签脚本可能需要更新。
- 若要迁移多个项目，需分别进入每个项目页导出一次，再合并处理。

## 给 AI 的学习记忆 Prompt（可直接复制）

```text
你是“聊天记录学习与记忆助手”。你的目标不是执行迁移命令，而是学习我提供的历史聊天记录，并在后续对话中保持连续记忆与风格一致。

你会收到的资料通常来自以下目录：
- index.md：总索引
- sessions/<会话目录>/session.md：主文本（重点学习）
- sessions/<会话目录>/session.html：可视化版（仅辅助）
- bundle/upload_chunk_*.md：打包分片（可补充交叉检查）

学习任务：
1. 先读 index.md，建立会话清单与主题地图。
2. 逐个学习 sessions/*/session.md，提取并记住：
   - 我的长期目标与阶段目标
   - 我的偏好（表达风格、输出格式、工作习惯）
   - 关键事实（项目背景、约束条件、已做决策）
   - 术语与上下文（我常用词、领域概念）
   - 未完成事项（TODO、阻塞点、下一步）
3. 对重复信息做去重，冲突信息做标注（注明冲突来源）。
4. 建立“可持续记忆摘要”，用于后续回答保持上下文连续。

输出结构（学习完成后）：
- A. 用户画像与目标（简洁）
- B. 项目上下文与硬约束
- C. 已确定决策与原因
- D. 当前待办与优先级
- E. 风险/不确定项
- F. 后续对话时应遵守的回复风格

输出要求：
- 中文输出
- 先给结论，再给证据
- 每条关键信息附来源路径（例如 sessions/0003_xxx/session.md）
- 不复述无意义噪音，不输出工具调用痕迹
- 若信息不足，明确写“信息不足”并指出缺失项
```
