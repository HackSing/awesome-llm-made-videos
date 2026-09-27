#!/usr/bin/env node
// 结构护栏的 TS/JS 函数体量解析器：由 scripts/structure_check.py 以子进程调用。
// stdin: JSON 数组 [{ id, fileName, text }]；stdout: JSON 对象 { id: { 函数限定名: 行数 } | null }。
// 解析出现语法诊断的条目返回 null，与 Python ast 的"语法错误跳过函数级判定"同口径。
// 依赖 zbuddy/node_modules 里已有的 typescript，不引入新第三方包；找不到时由 Python 侧降级为 WARN。

const path = require("node:path");

function loadTypescript(moduleDirs) {
  for (const dir of moduleDirs) {
    try {
      return require(path.join(dir, "typescript"));
    } catch {
      // 尝试下一个候选目录
    }
  }
  throw new Error("typescript module not found in: " + moduleDirs.join(", "));
}

function scriptKindFor(ts, fileName) {
  const ext = path.extname(fileName).toLowerCase();
  if (ext === ".tsx") return ts.ScriptKind.TSX;
  if (ext === ".jsx") return ts.ScriptKind.JSX;
  if (ext === ".js" || ext === ".cjs" || ext === ".mjs") return ts.ScriptKind.JS;
  return ts.ScriptKind.TS;
}

function isFunctionLike(ts, node) {
  return (
    ts.isFunctionDeclaration(node) ||
    ts.isMethodDeclaration(node) ||
    ts.isConstructorDeclaration(node) ||
    ts.isGetAccessorDeclaration(node) ||
    ts.isSetAccessorDeclaration(node) ||
    ts.isArrowFunction(node) ||
    ts.isFunctionExpression(node)
  );
}

// 函数命名：声明/方法取自身名；赋给变量或属性的函数取变量/属性名；作为调用实参的匿名函数记为
// `callee#cb`（callee 为被调用表达式文本，截断 60 字符）。同名取最大行数，与 Python 侧一致。
function functionName(ts, node, sf) {
  if (node.name) return node.name.getText(sf);
  if (ts.isConstructorDeclaration(node)) return "constructor";
  const parent = node.parent;
  if (parent && (ts.isVariableDeclaration(parent) || ts.isPropertyAssignment(parent) || ts.isPropertyDeclaration(parent))) {
    return parent.name.getText(sf);
  }
  if (parent && ts.isCallExpression(parent) && parent.arguments.includes(node)) {
    return parent.expression.getText(sf).replace(/\s+/g, " ").slice(0, 60) + "#cb";
  }
  if (parent && ts.isExportAssignment(parent)) return "default";
  return "<anonymous>";
}

function enclosingClassName(ts, node, sf) {
  let current = node.parent;
  while (current) {
    if (ts.isClassDeclaration(current) || ts.isClassExpression(current)) {
      return current.name ? current.name.getText(sf) : "<class>";
    }
    current = current.parent;
  }
  return undefined;
}

function collectSpans(ts, sf) {
  const spans = {};
  function visit(node, prefix) {
    let nextPrefix = prefix;
    if (isFunctionLike(ts, node) && node.body) {
      const start = sf.getLineAndCharacterOfPosition(node.getStart(sf)).line;
      const end = sf.getLineAndCharacterOfPosition(node.body.getEnd()).line;
      const span = end - start + 1;
      const className = ts.isMethodDeclaration(node) || ts.isConstructorDeclaration(node) || ts.isAccessor(node)
        ? enclosingClassName(ts, node, sf)
        : undefined;
      const local = (className ? className + "." : "") + functionName(ts, node, sf);
      const qualified = prefix + local;
      spans[qualified] = Math.max(spans[qualified] || 0, span);
      nextPrefix = qualified + ".";
    }
    ts.forEachChild(node, (child) => visit(child, nextPrefix));
  }
  visit(sf, "");
  return spans;
}

function main() {
  const moduleDirs = process.argv.slice(2);
  const ts = loadTypescript(moduleDirs);
  const chunks = [];
  process.stdin.on("data", (chunk) => chunks.push(chunk));
  process.stdin.on("end", () => {
    const items = JSON.parse(Buffer.concat(chunks).toString("utf8"));
    const result = {};
    for (const item of items) {
      const sf = ts.createSourceFile(item.fileName, item.text, ts.ScriptTarget.Latest, true, scriptKindFor(ts, item.fileName));
      result[item.id] = sf.parseDiagnostics && sf.parseDiagnostics.length > 0 ? null : collectSpans(ts, sf);
    }
    process.stdout.write(JSON.stringify(result));
  });
}

main();
