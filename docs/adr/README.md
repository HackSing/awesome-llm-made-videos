# 架构决策记录（ADR）

本目录保存已定稿的架构决策。每项决策由同名 JSON 保存可审计内容，Markdown 提供
可读投影，并登记到 `docs/INDEX.md`。

ADR 定稿后不再修改（没有 update）；决策失效时用 `adr settle` 废弃或标记被替代，
资产移入 `archive/`。新决策取代旧决策时通过 `supersedes` 记录取代关系。
