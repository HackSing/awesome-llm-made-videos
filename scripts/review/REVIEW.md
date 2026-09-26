# 复核说明（第二遍，给执行复核的模型或人）

第一遍筛选只看了标题和简介。复核针对已经判为作品、证据在 B 级以上的条目，以及所有同题对比，做三件事：把做法写清楚，打开附带的代码链接看一眼，能看画面的就看画面。

输入是 `data/raw/survey/*.jsonl`，每行一件作品：`work_id`、`title_zh`、`models`、`primary_path`、`evidence`、`triage_note`，以及 `appearances`（这件作品在各平台的每次出现，含正文 `text`、B 站简介 `desc`、简介里的链接 `desc_links`）。

输出写到 `data/review/<输入文件名>.jsonl`，每件作品一行，和输入顺序一致。`appearance_id` 取 `work_id` 把第一个 `-` 换成 `:`，例如 `bilibili-BV1xx` 对应 `bilibili:BV1xx`。生成脚本会把这些字段叠加到原有标签上。

## 字段

| 字段 | 说明 |
|---|---|
| `appearance_id` | 见上 |
| `method_zh` | 不超过 120 字。实际怎么做的：模型、工具链（p5.js、Three.js、Remotion、Blender、AE、ffmpeg、Seedance 等）、作者自报的耗时和费用、提示词长短、有没有参考素材。作者说的写成"作者称" |
| `observed_zh` | 不超过 40 字。你自己从抽帧里看到的画面，例如"2D 扁平卡通，约 12 个场景，有字幕"。没看到画面就留空，不要根据文字编 |
| `code_check_zh` | 不超过 80 字。打开代码链接看到了什么，例如"GitHub 仓库含 p5.js 分镜脚本和 ffmpeg 合成脚本，README 链接了这支视频"；链接打不开或不是代码也照实写。没有链接就留空 |
| `evidence` | 只在需要**下调**时填：代码链接打不开、不是代码、和视频无关时，按 PROMPT.md 的标准改成 B、C 或 D。不能填 A |
| `shortlist` | `yes` / `no`。从画面和做法看，值得推荐给别人看、或者做法值得学的，填 `yes` |
| `shortlist_zh` | 不超过 30 字。填 `yes` 的理由 |
| `review_by` | 固定写 `agent:text+links` 或 `agent:text+links+frames` |

同题对比（`comparisons.jsonl`）另加：

| 字段 | 说明 |
|---|---|
| `compare_models` | 列表，参与对比的模型 |
| `compare_task_zh` | 不超过 40 字，对比的题目 |
| `verdict_zh` | 不超过 80 字，作者给的结论，写成"作者称"；没给结论就写"未给结论" |
| `time_cost_zh` | 不超过 60 字，作者报的耗时、费用、token，没有就留空 |

## 看画面（B 站）

B 站的预览截图接口不用登录：

```
https://api.bilibili.com/x/player/videoshot?bvid=<BV号>&index=1
```

返回的 `data.image` 是雪碧图地址列表（前面补 `https:`），每张是 10×10 个缩略帧，按时间顺序排列。下载第一张到 `data/raw/frames/<BV号>-1.jpg`（这个目录不进 Git），再打开图片看。请求要带普通浏览器的 User-Agent，每两次接口请求之间间隔 4 到 7 秒；返回 `code` 不是 0 时停止继续请求 B 站，已经做完的照常输出，没做的 `observed_zh` 留空。

## 打开代码链接

GitHub 仓库用 `gh api repos/<owner>/<repo>` 和 `gh api repos/<owner>/<repo>/readme` 看简介、文件列表和 README；其他网页可以直接抓取。只看不下载，不运行任何代码。网盘链接不用打开，写"网盘链接，未打开"。

## 要求

- 只写看到的。画面、代码、作者说法三类信息分开写，不要混在一起推断。
- 不改 `data/review/` 以外的文件。
