"""输出文件名工具（零依赖，便于单测）
"""
import os


def unique_path(folder, base, ext):
    """返回 folder 下不冲突的 `<base><ext>` 完整路径。

    冲突时依次尝试 `<base>_1<ext>`、`<base>_2<ext>` … 直到找到空位。

    旧实现只补一次 `_count` 后缀，且补完**不校验**该名字是否也已被占用，
    于是同一个目录下载第三次时，`img_01_0.jpg` 会直接被覆盖——静默丢文件。
    这里改成循环探测，代价是几十次 `os.path.exists`（微秒级），换来的是不丢数据。
    """
    path = os.path.join(folder, f'{base}{ext}')
    if not os.path.exists(path):
        return path
    n = 1
    while True:
        path = os.path.join(folder, f'{base}_{n}{ext}')
        if not os.path.exists(path):
            return path
        n += 1
