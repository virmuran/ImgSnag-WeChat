# -*- coding: utf-8 -*-
"""静态自检：找出「模块级未定义名」与「self.X 未定义属性」。

运行（在项目根）：
    .venv/Scripts/python.exe tools/name_check.py

为什么需要它：`py_compile` 只查语法，**抓不到名字丢失**。
本项目踩过一次真实事故 —— 拆分模块时把 `SaveWorker` 的 import 弄丢了，
文件照样能编译、当时的测试也没覆盖到那条路径，结果"保存"功能整个废掉，
直到用户实测才发现。

凡是「搬迁代码 / 改 import / 大量重命名」之后都该跑一遍。
检出的都是**候选问题**，需要人工判断（少量误报来自动态赋值/属性注入）。
"""
import ast
import builtins
import os
import re
import sys

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)          # tools/ 的上一级 = 项目根

#: 要扫描的源码根目录。
#: `android/app` 是手机端（Termux）代码 —— 里面的 web_image_dl 是自动同步的副本
#: （扫出来会和主项目一致），但主程序 imgsnag.py 是手写的，同样会踩"名字丢失"
#: 这类坑，所以一并扫。
TARGETS = [
    os.path.join(ROOT, 'web_image_dl'),
    os.path.join(ROOT, 'android', 'app'),
    # 安卓 APK 里的 Python（Chaquopy 的固定目录）。
    # 其中 web_image_dl/ 与 imgsnag.py 是自动同步的副本，但
    # imgsnag_android.py 是手写的 —— 手写的那份必须一起扫。
    os.path.join(ROOT, 'android-apk', 'app', 'src', 'main', 'python'),
]
BUILTINS = set(dir(builtins)) | {'__file__', '__name__', '__doc__', '__package__',
                                 '__spec__', '__loader__', 'self', 'cls'}

#: `except*`（3.11+）的节点类型；老版本没有就留空
_TRY_STAR = (ast.TryStar,) if hasattr(ast, 'TryStar') else ()

#: 会开新作用域的节点。遍历"本层名字引用"时必须跳过它们的内部 ——
#: 嵌套函数有自己的形参，被外层一起扫就会变成"未定义名"
#: （实测：make_get 里 `def _get(url, **kw)`，ast.walk 冲进 _get 体内，
#:   把 url / kw 报成未定义）。它们的内部由 iter_nested_defs 递归处理。
_SCOPE_BOUNDARY = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def iter_own_scope(node):
    """遍历 node 的名字引用，但**不深入**嵌套函数/类（它们的形参自成一域）。"""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, _SCOPE_BOUNDARY):
            continue
        yield child
        yield from iter_own_scope(child)


def iter_nested_defs(node):
    """列出嵌套在 node 里的所有函数/类定义（含更深层），用于递归检查。"""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, _SCOPE_BOUNDARY):
            yield child
        yield from iter_nested_defs(child)


def target_names(node):
    """赋值目标的「被绑定的名字」"""
    if isinstance(node, ast.Name):
        return {node.id}
    if isinstance(node, (ast.Tuple, ast.List)):
        out = set()
        for e in node.elts:
            out |= target_names(e)
        return out
    if isinstance(node, ast.Starred):
        return target_names(node.value)
    return set()


def stmt_bound_names(stmt):
    """一条语句绑定了哪些名字（含静态分支递归）"""
    if isinstance(stmt, ast.Import):
        return {a.asname or a.name.split('.')[0] for a in stmt.names}
    if isinstance(stmt, ast.ImportFrom):
        return {a.asname or a.name for a in stmt.names}
    if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return {stmt.name}
    if isinstance(stmt, ast.Assign):
        out = set()
        for t in stmt.targets:
            out |= target_names(t)
        return out
    if isinstance(stmt, ast.AnnAssign):
        return target_names(stmt.target)
    if isinstance(stmt, (ast.AugAssign,)):
        return target_names(stmt.target)
    if isinstance(stmt, ast.Global):
        return set(stmt.names)
    # 循环变量 / with as / except as —— 这些绑定挂在表达式上，不能只递归子语句
    if isinstance(stmt, (ast.For, ast.AsyncFor)):
        out = target_names(stmt.target)
        for sub in ast.iter_child_nodes(stmt):
            if isinstance(sub, ast.stmt):
                out |= stmt_bound_names(sub)
        return out
    if isinstance(stmt, (ast.With, ast.AsyncWith)):
        out = set()
        for item in stmt.items:
            if item.optional_vars is not None:
                out |= target_names(item.optional_vars)
        for sub in ast.iter_child_nodes(stmt):
            if isinstance(sub, ast.stmt):
                out |= stmt_bound_names(sub)
        return out
    # try：body / else / finally 里的绑定靠下面那条通用递归就能收上来，
    # 但 **except 块收不到** —— ast.ExceptHandler 不是 ast.stmt 的子类，
    # 会被 `isinstance(sub, ast.stmt)` 过滤掉，于是 except 里赋的值统统被当成
    # "未定义名"（imgsnag.py 里 `except` 中赋的 msg 就是这么被误报的）。
    # 所以 handler 体要单独补一遍。TryStar 是 3.11+ 的 `except*`，同理。
    if isinstance(stmt, (ast.Try,) + _TRY_STAR):
        out = set()
        for h in stmt.handlers:
            if h.name:
                out.add(h.name)
            out |= bound_in_body(h.body)
        for sub in ast.iter_child_nodes(stmt):
            if isinstance(sub, ast.stmt):
                out |= stmt_bound_names(sub)
        return out
    # if / while：把子语句里绑定的名字也算进来（静态近似）
    if isinstance(stmt, (ast.If, ast.While)):
        out = set()
        for sub in ast.iter_child_nodes(stmt):
            if isinstance(sub, ast.stmt):
                out |= stmt_bound_names(sub)
        return out
    return set()


def bound_in_body(body):
    out = set()
    for stmt in body:
        out |= stmt_bound_names(stmt)
    return out


def check_name_loads(fn_node, outer_names, problems, where):
    """函数体内 Load 的名字是否都能找到出处"""
    params = set()
    args = fn_node.args
    for a in list(args.posonlyargs) + list(args.args) + list(args.kwonlyargs):
        params.add(a.arg)
    if args.vararg:
        params.add(args.vararg.arg)
    if args.kwarg:
        params.add(args.kwarg.arg)

    declared_global = set()
    for node in ast.walk(fn_node):
        if isinstance(node, ast.Global):
            declared_global |= set(node.names)

    local = set(params) | bound_in_body(fn_node.body) | declared_global
    # 嵌套定义、推导式变量、lambda 形参、except as、海象运算符 —— 都算局部绑定
    for sub in ast.walk(fn_node):
        if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and sub is not fn_node:
            local.add(sub.name)
        elif isinstance(sub, ast.comprehension):
            local |= target_names(sub.target)
        elif isinstance(sub, ast.ExceptHandler) and sub.name:
            local.add(sub.name)
        elif isinstance(sub, ast.NamedExpr) and isinstance(sub.target, ast.Name):
            local.add(sub.target.id)
        elif isinstance(sub, ast.Lambda):
            a = sub.args
            for x in list(a.posonlyargs) + list(a.args) + list(a.kwonlyargs):
                local.add(x.arg)
            if a.vararg:
                local.add(a.vararg.arg)
            if a.kwarg:
                local.add(a.kwarg.arg)

    for node in iter_own_scope(fn_node):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            if node.id in local or node.id in outer_names or node.id in BUILTINS:
                continue
            problems.append(f'{where}:{node.lineno} 未定义名 `{node.id}`')

    # 嵌套函数/类另起作用域，但能看见本层与更外层的名字（闭包）——
    # 所以把 `local | outer_names` 当作它们的外层继续检查
    inner_outer = local | outer_names
    for sub in iter_nested_defs(fn_node):
        if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
            check_name_loads(sub, inner_outer, problems, f'{where}::{sub.name}')
        else:
            check_self_attrs(sub, problems, f'{where}::{sub.name}')
            for m in sub.body:
                if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    check_name_loads(m, inner_outer, problems,
                                     f'{where}::{sub.name}::{m.name}')


def check_self_attrs(cls_node, problems, where):
    """类里 self.<attr> 的读取是否都有对应的赋值/方法/类属性"""
    defined = set()
    for stmt in cls_node.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            defined.add(stmt.name)
        elif isinstance(stmt, ast.Assign):
            for t in stmt.targets:
                defined |= target_names(t)
        elif isinstance(stmt, ast.AnnAssign):
            defined |= target_names(stmt.target)

    for node in ast.walk(cls_node):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) \
                and node.value.id == 'self':
            defined.add(node.attr)

    loaded = set()
    for node in ast.walk(cls_node):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) \
                and node.value.id == 'self' and isinstance(node.ctx, ast.Load):
            loaded.add((node.attr, node.lineno))

    for attr, lineno in sorted(loaded):
        if attr not in defined:
            problems.append(f'{where}:{lineno} 未定义的 self.{attr}')


def scan(path):
    with open(path, 'r', encoding='utf-8') as f:
        src = f.read()
    tree = ast.parse(src, filename=path)
    rel = os.path.relpath(path, ROOT)
    problems = []

    outer = bound_in_body(tree.body) | BUILTINS
    for stmt in tree.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            check_name_loads(stmt, outer, problems, rel)
        elif isinstance(stmt, ast.ClassDef):
            check_self_attrs(stmt, problems, rel)
            for sub in stmt.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    check_name_loads(sub, bound_in_body(tree.body),
                                     problems, f'{rel}::{stmt.name}')
    def _line(text):
        m = re.search(r':(\d+) ', text)
        return int(m.group(1)) if m else 0

    return sorted(set(problems), key=_line)


def main():
    files = []
    for pkg in TARGETS:
        if not os.path.isdir(pkg):
            continue
        for base, _dirs, names in os.walk(pkg):
            if '__pycache__' in base:
                continue
            for n in sorted(names):
                if n.endswith('.py'):
                    files.append(os.path.join(base, n))

    total = 0
    for p in files:
        probs = scan(p)
        if probs:
            total += len(probs)
            print(f'\n{os.path.relpath(p, ROOT)}')
            for x in probs:
                print(f'   {x}')
    print(f'\n扫描 {len(files)} 个模块，可疑项 {total} 条')
    if total:
        print('（多数是动态赋值造成的误报，但每一条都要人工看一眼）')
        sys.exit(0)      # 只报告，不阻断
    print('没有发现名字丢失 ✅')


if __name__ == '__main__':
    main()
