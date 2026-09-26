# 候选筛选说明（给执行筛选的模型或人）

输入是一个 JSONL 文件，每行一条候选帖子，字段包括：`appearance_id`、`platform`、`url`、`uploader`、`title`（X 上是正文前 200 字）、`desc`（B 站简介，可能为空）、`desc_links`、`published_utc`、`duration_s`、`views`、`models_mentioned`。

对**每一行**输出一行 JSON，写入指定的输出文件，每条输入对应一条输出，不要漏也不要多。字段如下：

| 字段 | 取值 | 说明 |
|---|---|---|
| `appearance_id` | 原样 | 用来和输入对应 |
| `category` | `work` / `comparison` / `review` / `tutorial` / `news` / `promo` / `unrelated` | 见下方判断标准 |
| `llm_made` | `yes` / `likely` / `no` / `unclear` | 语言模型是否实际参与制作了这个视频本身 |
| `models` | 列表，如 `["claude-opus-5-5"]` | 实际参与制作的模型，不是仅被提到的模型 |
| `primary_path` | 见下方 | 只有 `work` 和 `comparison` 才需要填，其余留空 |
| `evidence` | `A` / `B` / `C` / `D` | A 有对得上的代码仓库，B 公开了提示词或详细流程，C 只有作者自述，D 连自述也没有 |
| `repost` | `original_claimed` / `repost_declared` / `repost_suspected` / `unknown` | 见下方 |
| `source_hint` | 字符串 | 原作者账号、原帖链接或 GitHub 仓库名，没有就留空 |
| `label_zh` | 不超过 20 个字 | 作品的中文短名，例如"中秋拼贴动画" |
| `note` | 不超过 60 个字 | 判断依据，写清楚哪些是作者称 |

## category 的判断标准

- `work`：视频本身就是模型参与做出来的作品，例如动画、MV、讲解片、宣传片、3D 场景、游戏录屏。
- `comparison`：同一个提示词交给多个模型、并排对比的视频。只要画面是模型做的，也属于作品。
- `review`：人对着镜头或录屏去评测模型、跑分、聊体验，视频本身不是模型做的。
- `tutorial`：教别人怎么安装、订阅、使用。
- `news`：资讯、发布会解读、合集盘点。
- `promo`：推广某个产品、服务或课程，视频本身不是模型作品。
- `unrelated`：和这些模型无关，只是标题碰巧命中了关键词。

标准拿不准时，按视频**本身**是不是模型做的来判断：是就算 `work`，不是就归到其他类别。

## primary_path 取值

`procedural_2d`（代码绘制的 2D、像素、排版动画）、`educational_explainer`（教学讲解）、`3d_or_realtime_graphics`（Blender、Three.js、WebGL）、`existing_source_transformation`（改编现有素材）、`external_video_model`（模型调度 Seedance、Veo 等视频模型生成主画面）、`app_or_game_capture`（模型写的程序的录屏）、`mixed_or_not_established`（多种方式混合，或看不出来）。

## repost 的判断标准

- `original_claimed`：上传者用第一人称说是自己做的，例如"我让 Opus……"。
- `repost_declared`：上传者自己说明是转载或搬运，例如"转自""原作者""搬运""up 未实测"，或者放了原作者的链接。
- `repost_suspected`：没有声明，但有明显迹象，例如标题是翻译腔、正文描述的是别人的经历，或者上传者是专门的搬运号。
- `unknown`：以上都不符合。

## 要求

- 只根据给出的文字判断，不要推测看不到的内容。
- 一句话生成、耗时、费用这类说法，一律写成"作者称"。
- 如果一行文字里写到了别的作者，比如"@xxx 做的""转自 xxx"，要写进 `source_hint`。
