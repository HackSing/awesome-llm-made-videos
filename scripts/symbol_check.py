"""符号存活判定：注释剥离、词边界与带点引用归一。"""

from __future__ import annotations

import re

_IDENTIFIER_SYMBOL = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$")
_SLASH_COMMENT_SUFFIXES = frozenset({
    ".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx", ".vue", ".go", ".rs",
    ".java", ".kt", ".swift", ".c", ".h", ".cc", ".cpp", ".hpp", ".cs",
    ".dart", ".php",
})
_HASH_COMMENT_SUFFIXES = frozenset({
    ".py", ".sh", ".bash", ".zsh", ".rb", ".yaml", ".yml", ".toml", ".php",
})
_IDENTIFIER_TOKEN = re.compile(
    r"[A-Za-z_][A-Za-z0-9_]*(?:\s*\.\s*[A-Za-z_][A-Za-z0-9_]*)*"
)


def strip_code_comments(content: str, suffix: str) -> str:
    """去掉注释再判定符号存活：注释里提到的符号不能证明接口仍存在。

    保留字符串，因为错误码、方法通道名等字符串常量本身就是公开接口的一部分。
    """
    text = content
    lowered = suffix.lower()
    if lowered in _HASH_COMMENT_SUFFIXES:
        text = re.sub(r"(?m)#.*$", "", text)
    if lowered in _SLASH_COMMENT_SUFFIXES:
        # 行注释优先于块注释：`// ... /*/node_modules` 里的 `/*` 不是块注释起点。
        text = re.sub(r"(?m)//.*$|(?s:/\*.*?\*/)", "", text)
    if lowered == ".lua":
        text = re.sub(r"(?m)--.*$", "", text)
    if lowered in {".vue", ".html", ".htm"}:
        text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    return text


def _token_variants(token: str) -> set[str]:
    """带点 token 的所有连续子路径。

    源码里的 `state.lastSyncAt` 既是整条 token，也要能命中 `lastSyncAt`；
    `AccountDefaults.bindingStatusPlaceholder` 同时要能命中 `AccountDefaults`。
    """
    if "." not in token:
        return {token}
    parts = [part.strip() for part in token.split(".")]
    return {
        ".".join(parts[start:end])
        for start in range(len(parts))
        for end in range(start + 1, len(parts) + 1)
    }


def symbol_hits(content: str, symbols: list[str] | set[str], suffix: str) -> set[str]:
    """一次分词判断多个符号，供 plan check 在大型仓库上避免逐符号重复正则。

    配置与代码都走同一套 token 集合：字段、键名、字符串常量、环境变量和带点引用
    都会成为 token；路径等非标识符符号才退回子串判断。
    """
    stripped = strip_code_comments(content, suffix)
    tokens: set[str] = set()
    for token in _IDENTIFIER_TOKEN.findall(stripped):
        tokens.update(_token_variants(token))
    hits: set[str] = set()
    for symbol in symbols:
        if _IDENTIFIER_SYMBOL.fullmatch(symbol):
            if symbol in tokens:
                hits.add(symbol)
        elif symbol in stripped:
            hits.add(symbol)
    return hits


def symbol_hit(content: str, symbol: str, suffix: str) -> bool:
    """符号存活判定：配置用原词边界，代码用去注释后的词边界。

    不再要求定义形态：字段声明、枚举成员、类型标注赋值、环境变量、
    带点引用和字符串常量都是真实接口形态；注释中的出现才不算存活。
    """
    return bool(symbol_hits(content, [symbol], suffix))
