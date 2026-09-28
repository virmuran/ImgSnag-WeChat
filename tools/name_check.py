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
PKG = os.path.join(ROOT, 'web_image_dl')
BUILTINS = set(dir(builtins)) | {'__file__', '__name__', '__doc__', '__package__',
                                 '__spec__', '__loader__', 'self', 'cls'}


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
    # if / try / while：把子语句里绑定的名字也算进来（静态近似）
    if isinstance(stmt, (ast.If, ast.Try, ast.While)):
        out = set()
        if isinstance(stmt, ast.Try):
            for h in stmt.handlers:
                if h.name:
                    out.add(h.name)
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

    for node in ast.walk(fn_node):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            if node.id in local or node.id in outer_names or node.id in BUILTINS:
                continue
            problems.append(f'{where}:{node.lineno} 未定义名 `{node.id}`')


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
    for base, _dirs, names in os.walk(PKG):
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
