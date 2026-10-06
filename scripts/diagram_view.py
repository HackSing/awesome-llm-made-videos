#!/usr/bin/env python3
"""Mermaid 源码 → 独立 HTML 页面：`harness view` 的渲染、落盘与打开。

三层分开：`render_mermaid_html` 是纯函数（字符串进、字符串出）；`write_view` 只做文件 IO
（读源码、原子写页面）；`open_in_browser` 只做进程调用（系统默认浏览器）。

错误一律向上传递，由 harness.command_view 在 CLI 边界映射成错误码：
源文件不存在或不是常规文件 → FileNotFoundError；不是 UTF-8 → UnicodeDecodeError；
只有空白 → ValueError；打开失败 → OSError 或 subprocess.CalledProcessError。

页面在浏览器里从 CDN 加载 mermaid，harness 自身零依赖；离线时页面只显示源码文本。
"""

from __future__ import annotations

import html
import os
import string
import subprocess
import sys
from pathlib import Path

from managed_assets import atomic_write_text

# 单一来源：harness 导入它并列入 LOCAL_ONLY_DIRS，init/upgrade 据此落嵌套 .gitignore。
VIEWS_RELATIVE = ".docs-harness/views"

_MERMAID_SCRIPT_URL = "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js"
_PAGE_TEMPLATE = string.Template("""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>$title</title>
<style>
  body { margin: 0; padding: 24px 16px; background: #fff; color: #1f2328;
         font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
  h1 { margin: 0 0 16px; font-size: 18px; font-weight: 600; text-align: center; }
  pre.mermaid { margin: 0; text-align: center; font-family: inherit; background: transparent; }
</style>
</head>
<body>
<h1>$title</h1>
<pre class="mermaid">
$source
</pre>
<script src="$script_url"></script>
<script>mermaid.initialize({ startOnLoad: true });</script>
</body>
</html>
""")


def render_mermaid_html(source: str, title: str) -> str:
    """把一张图的 Mermaid 源码渲染成完整 HTML 页面；源码与标题都做 HTML 转义。

    mermaid 取 <pre> 的内容时会先解码实体，转义后的 `-->`、`<br>` 等仍按原样解析。
    """
    return _PAGE_TEMPLATE.substitute(
        title=html.escape(title),
        source=html.escape(source),
        script_url=_MERMAID_SCRIPT_URL,
    )


def _read_source(source_path: Path) -> str:
    if not source_path.is_file():
        raise FileNotFoundError(f"Mermaid 源文件不存在或不是常规文件：{source_path}")
    source = source_path.read_text(encoding="utf-8")
    if not source.strip():
        raise ValueError(f"Mermaid 源文件为空：{source_path}")
    return source


def write_view(target: Path, source_path: Path) -> Path:
    """读 source_path 的源码，写到 <target>/.docs-harness/views/<源文件名去扩展名>.html。

    目录不存在时自动创建；同名页面直接覆盖（反复修改同一张图时链接不变）。返回写入路径。
    """
    source = _read_source(source_path)
    output = target / VIEWS_RELATIVE / f"{source_path.stem}.html"
    atomic_write_text(output, render_mermaid_html(source, source_path.stem))
    return output


def open_in_browser(path: Path) -> None:
    """用系统默认浏览器打开 path；失败抛 OSError 或 CalledProcessError，不吞。

    打开命令的 stdout 丢弃：部分浏览器经 xdg-open 往 stdout 打印提示，会污染 --json 输出；
    stderr 照常透出，失败原因留在终端里。
    """
    if sys.platform == "win32":
        os.startfile(str(path))  # 仅 Windows 有 os.startfile
        return
    command = "open" if sys.platform == "darwin" else "xdg-open"
    subprocess.run([command, str(path)], check=True, stdout=subprocess.DEVNULL)
