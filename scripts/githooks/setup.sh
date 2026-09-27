#!/bin/sh
# 新克隆机器执行一次：把 assets-check 挂到真实钩子目录的 pre-commit。
#
# 不使用 core.hooksPath：git 一旦设置该项，就完全不再读取 .git/hooks，
# 第三方工具（git lfs install、husky 等）装在那里的钩子会全部静默失效。
# 本脚本改为向钩子目录安装带标记的转发 shim，与各工具按文件名共存；
# 实现的单一来源仍是 scripts/githooks/pre-commit，shim 只转发退出码。
set -e

ROOT=$(git rev-parse --show-toplevel)
cd "$ROOT"

# 迁移：清掉旧版 setup.sh 设过的 core.hooksPath，让被屏蔽的第三方钩子恢复。
# 配置值做路径归一化后比较，兼容相对路径、./ 前缀与绝对路径写法。
CURRENT=$(git config --get core.hooksPath || true)
if [ -n "$CURRENT" ]; then
    case "$CURRENT" in
        /* | [A-Za-z]:*) CFG_ABS=$CURRENT ;;
        *) CFG_ABS=$ROOT/$CURRENT ;;
    esac
    RESOLVED=$(cd "$CFG_ABS" 2>/dev/null && pwd -P) || RESOLVED=
    WANT=$(cd "$ROOT/scripts/githooks" 2>/dev/null && pwd -P) || WANT=
    if [ -n "$RESOLVED" ] && [ "$RESOLVED" = "$WANT" ]; then
        git config --unset core.hooksPath
        echo "已清除 core.hooksPath=$CURRENT（.git/hooks 下的第三方钩子恢复生效）"
    else
        echo "警告：core.hooksPath 当前为 '$CURRENT'，git 不会读取 .git/hooks；" >&2
        echo "      本脚本安装的钩子不会运行。请先确认该设置是否必要。" >&2
    fi
fi

# 钩子目录用 --git-common-dir 解析：git worktree 之间共享钩子，装一次即可；
# 链接 worktree 的 .git 是文件不是目录，不能拼 .git/hooks 字面量。
COMMON=$(git rev-parse --git-common-dir)
case "$COMMON" in
    /* | [A-Za-z]:*) ;;
    *) COMMON=$ROOT/$COMMON ;;
esac
HOOKS=$COMMON/hooks
mkdir -p "$HOOKS"

MARKER=docs-harness-hook-shim
TARGET=$HOOKS/pre-commit
if [ -f "$TARGET" ] && ! grep -q "$MARKER" "$TARGET"; then
    echo "错误：$TARGET 已存在且不是本工具安装的转发钩子，未覆盖。" >&2
    echo "      请自行合并其内容，或备份后重新运行本脚本。" >&2
    exit 1
fi

cat > "$TARGET" <<'HOOK'
#!/bin/sh
# docs-harness-hook-shim —— 由 scripts/githooks/setup.sh 安装，勿手工编辑。
# 实现的单一来源是 scripts/githooks/pre-commit；此处只转发退出码。
ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0
[ -f "$ROOT/scripts/githooks/pre-commit" ] || exit 0
exec sh "$ROOT/scripts/githooks/pre-commit" "$@"
HOOK
chmod +x "$TARGET"
echo "已安装 pre-commit 转发钩子：$TARGET"

# 可见性：列出钩子目录下已识别的其它钩子，让「共存」而非「接管」对用户可见。
OTHERS=
for hook in "$HOOKS"/*; do
    [ -f "$hook" ] || continue
    case $hook in
        *.sample | */pre-commit) continue ;;
    esac
    OTHERS="$OTHERS $hook"
done
if [ -n "$OTHERS" ]; then
    echo "钩子目录下共存的其它钩子（不受影响，继续生效）："
    for hook in $OTHERS; do
        echo "  $hook"
    done
fi
