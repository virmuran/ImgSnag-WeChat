# -*- coding: utf-8 -*-
"""屏蔽尺寸 / 过滤规则 回归测试

运行：
    .venv/Scripts/python.exe tests/test_filter_rules.py

为什么单独一个文件：这是 v1.5.0 补上的「双向门」。旧版只有 add_blocked 一个入口 ——
在缩略图上右键误屏蔽一个尺寸，那类图从此静默消失，既看不到屏蔽了哪些尺寸、也没有
任何撤销入口，只能去手改 %APPDATA%/ImgSnagWeChat/blocked_sizes.json。这里钉住的是
「屏蔽表是可逆的、可读的、写不坏的」，具体锚点：

一、判定规则只此一份（classify）
   worker 解析时和界面撤销后重算时，必须走同一个函数。旧实现把三条规则散在 worker 的
   循环里，界面想重算只能抄一份，迟早对不上。这里钉住「同时命中多条时全部返回」——
   界面要能只撤掉其中一条（某张图既小又命中过屏蔽尺寸，撤销屏蔽后它该继续藏着）。

二、命中次数要能被看见
   面板上写「已过滤 N 张」才让用户判断这条规则是不是误伤。次数由解析结束时**批量**
   回写（一次解析同一尺寸可能命中几十张，逐张写盘会把解析拖成磁盘 IO 表演）。

三、存储必须是原子的
   直接覆盖写原文件时，进程若在写的中途被杀掉，留下的是半截 JSON；下次启动会把它
   当损坏文件、连带攒下的规则一起丢掉。这里用「临时文件 + os.replace」并断言没有
   残留 .tmp。

四、向后兼容与坏文件
   老文件（只有三个列表、没有 meta）必须照常读；meta 写坏也不能影响屏蔽规则本身；
   整个 JSON 损坏时留一份 .corrupt 现场再回默认值。

五、线程安全
   解析在后台线程收尾时回写命中次数，用户此刻正在界面里翻看/撤销 —— 读写必须同锁。
"""
import json
import os
import sys
import tempfile
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from web_image_dl.blocked_config import (       # noqa: E402
    BlockedConfigManager,
    RULE_SMALL,
    RULE_UI_NOISE,
    classify,
    reason_label,
    size_key,
)

_passed = 0
_failed = []


def check(cond, label):
    global _passed
    if cond:
        _passed += 1
    else:
        _failed.append(label)
        print(f'  ✗ {label}')


def eq(got, want, label):
    check(got == want, f'{label}\n      得到: {got}\n      期望: {want}')


def _tmp_manager():
    d = tempfile.mkdtemp(prefix='imgsnag_blocked_test_')
    return BlockedConfigManager(os.path.join(d, 'blocked_sizes.json'))


def _read(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


# ================================================================
#  一、判定规则
# ================================================================

def test_classify():
    print('\n[FR-1] 判定规则（worker 与界面共用同一份）')
    empty = (set(), set(), set())

    eq(classify(1200, 800, '.jpg', 200_000, False, empty), (), '普通正文图不命中任何规则')
    eq(classify(200, 100, '.jpg', 200_000, False, empty), (RULE_SMALL,), '最长边正好 200px 算小图')
    eq(classify(201, 100, '.jpg', 200_000, False, empty), (), '最长边 201px 不算小图（边界不多吃一张）')
    eq(classify(250, 250, '.png', 5_000, True, empty), (RULE_UI_NOISE,),
       '透明 PNG 且 <10KB 判为排版装饰')
    eq(classify(250, 250, '.png', 12_000, True, empty), (), '12KB 的透明 PNG 不再算装饰')
    eq(classify(250, 250, '.jpg', 5_000, True, empty), (), 'JPG 即使报了透明通道也不算装饰')
    eq(classify(0, 0, '.jpg', 100, False, empty), (), '尺寸为 0 的坏图不判为小图')

    blocked = ({(250, 250)}, set(), set())
    eq(classify(250, 250, '.jpg', 200_000, False, blocked), ('blocked:ui',),
       '命中屏蔽表 → blocked:<分类>')

    # 关键：一条图同时被多条规则拦下时必须全部返回，界面才能只撤掉其中一条
    both = ({(100, 100)}, set(), set())
    eq(classify(100, 100, '.png', 3_000, True, both),
       (RULE_SMALL, RULE_UI_NOISE, 'blocked:ui'),
       '同时命中小图/装饰/屏蔽时要全部列出（撤销才能只撤一条）')

    eq(classify(500, 300, '.jpg', 200_000, False, (set(), {(500, 300)}, set())),
       ('blocked:avatar',), '头像分类命中')
    eq(classify(500, 300, '.jpg', 200_000, False, (set(), set(), {(500, 300)})),
       ('blocked:cover',), '封面分类命中')

    eq(size_key('ui', (100, 100)), 'ui:100x100', 'meta 键格式')
    check('200' in reason_label(RULE_SMALL), '小图规则的人话说明里带上了阈值')
    check('屏蔽' in reason_label('blocked:cover'), '屏蔽规则的人话说明能看出是用户屏蔽的')


def test_classify_reasons_on_imageinfo():
    print('\n[FR-2] ImageInfo 自带重算入口（界面撤销屏蔽时调它）')
    from web_image_dl.worker import ImageInfo

    info = ImageInfo(url='u', index=0, data=b'x' * 3000, width=100, height=100,
                     ext='.png', has_alpha=True)
    eq(info.classify_reasons((set(), set(), set())), (RULE_SMALL, RULE_UI_NOISE),
       'ImageInfo 与 classify 结论一致')
    eq(info.classify_reasons(({(100, 100)}, set(), set())),
       (RULE_SMALL, RULE_UI_NOISE, 'blocked:ui'), '传入屏蔽表后多出 blocked 项')

    big = ImageInfo(url='u', index=1, data=b'x' * 3000, width=800, height=600, ext='.jpg')
    eq(big.classify_reasons((set(), set(), set())), (), '普通大图没有任何命中')


# ================================================================
#  二、增删查 + 命中统计
# ================================================================

def test_crud():
    print('\n[FR-3] 屏蔽表可增可删可清空（旧版是单向门）')
    m = _tmp_manager()

    eq(m.list_blocked(), [], '初始没有任何屏蔽')
    eq(m.count(), 0, '初始计数为 0')
    check(os.path.exists(m.config_path), '首次构造就把配置文件建出来了')
    eq(_read(m.config_path), {'ui_exact': [], 'avatar_exact': [], 'cover_exact': [], 'meta': {}},
       '初始文件是三个空列表 + meta')

    check(m.add_blocked('ui', (100, 100)) is True, '新增一条屏蔽成功')
    check(m.add_blocked('ui', (100, 100)) is False, '重复新增返回 False（不写重复项）')
    check(m.add_blocked('nope', (1, 1)) is False, '未知分类被拒绝')
    eq(m.count(), 1, '重复新增没有把计数顶上去')

    m.add_blocked('cover', (900, 383))
    rows = m.list_blocked()
    eq([(r['category'], r['width'], r['height']) for r in rows],
       [('ui', 100, 100), ('cover', 900, 383)], '分类按固定顺序、同类内大尺寸优先')
    eq(rows[0]['category_label'], 'UI 装饰', '列表带上人话分类名')
    eq(rows[0]['hits'], 0, '新加入的命中次数从 0 起')
    check(bool(rows[0]['added_at']), '新加入的记录带加入时间')

    eq(m.get_blocked_sets(), ({(100, 100)}, set(), {(900, 383)}), 'worker 拿到的三组 set 正确')

    check(m.remove_blocked('ui', (100, 100)) is True, '撤销一条成功')
    check(m.remove_blocked('ui', (100, 100)) is False, '再撤销同一条返回 False（界面据此提示）')
    eq(m.count(), 1, '撤销后计数减一')
    eq(_read(m.config_path)['ui_exact'], [], '撤销已经落盘')
    eq(m.get_blocked_sets()[0], set(), 'worker 下次解析时该尺寸不再被屏蔽')

    m.add_blocked('avatar', (272, 272))
    eq(m.clear_blocked('avatar'), 1, '按分类清空返回清掉的条数')
    eq(m.clear_blocked('avatar'), 0, '已经空了再清返回 0')
    eq(m.count(), 1, '只清掉了指定的那一类')

    m.add_blocked('ui', (50, 50))
    eq(m.clear_blocked(), 2, '不指定分类则清空全部')
    eq(m.count(), 0, '清空后计数归零')
    eq(_read(m.config_path)['meta'], {}, '清空时统计信息一起清掉（不留孤儿）')


def test_hits():
    print('\n[FR-4] 命中次数：让用户看得出这条规则拦了多少张')
    m = _tmp_manager()
    m.add_blocked('ui', (100, 100))
    m.add_blocked('cover', (900, 383))

    m.add_hits({('ui', 100, 100): 12, ('cover', 900, 383): 3})
    rows = {r['category']: r for r in m.list_blocked()}
    eq(rows['ui']['hits'], 12, '第一次批量回写：12 张')
    check(bool(rows['ui']['last_hit']), '记下最后一次命中的时间')

    m.add_hits({('ui', 100, 100): 5})
    eq({r['category']: r for r in m.list_blocked()}['ui']['hits'], 17, '再次解析累加而不是覆盖')
    eq(_read(m.config_path)['meta']['ui:100x100']['hits'], 17, '累加结果已落盘')

    m.add_hits({('ui', 500, 500): 9, ('avatar', 1, 1): 4})
    eq(m.count(), 2, '不存在的尺寸/分类不会被凭空写进屏蔽表')
    eq({r['category']: r for r in m.list_blocked()}['ui']['hits'], 17, '未被屏蔽的尺寸不记命中')

    m.add_hits({})
    eq({r['category']: r for r in m.list_blocked()}['ui']['hits'], 17, '空计数安全返回')

    # 撤销后再收到迟到的命中（解析期间用户点了恢复）→ 不能给已经不存在的规则记账
    m.remove_blocked('ui', (100, 100))
    m.add_hits({('ui', 100, 100): 3})
    eq(m.count(), 1, '迟到命中不会把已撤销的规则复活')
    eq([r['category'] for r in m.list_blocked()], ['cover'], '屏蔽表没被写坏')


# ================================================================
#  三、存储可靠性
# ================================================================

def test_storage_is_atomic_and_tolerant():
    print('\n[FR-5] 存储：原子写 + 老格式兼容 + 坏文件不丢规则')
    m = _tmp_manager()
    m.add_blocked('ui', (100, 100))

    eq([f for f in os.listdir(os.path.dirname(m.config_path)) if f.endswith('.tmp')], [],
       '写完不留 .tmp 残骸')

    # 老版本文件：只有三个列表，没有 meta
    old = os.path.join(os.path.dirname(m.config_path), 'old.json')
    with open(old, 'w', encoding='utf-8') as f:
        json.dump({'ui_exact': [[100, 100]], 'avatar_exact': [[272, 272]], 'cover_exact': []}, f)
    m2 = BlockedConfigManager(old)
    eq(m2.count(), 2, '老格式（无 meta）照常读入')
    eq([r['hits'] for r in m2.list_blocked()], [0, 0], '老记录没有统计信息时按 0 显示')
    m2.add_hits({('ui', 100, 100): 2})
    eq({(r['category']): r['hits'] for r in m2.list_blocked()}['ui'], 2, '给老记录补统计也能写进去')

    # meta 被写坏（值不是对象、整个 meta 不是对象）→ 屏蔽规则必须照常可用
    weird = os.path.join(os.path.dirname(m.config_path), 'weird.json')
    with open(weird, 'w', encoding='utf-8') as f:
        json.dump({'ui_exact': [[7, 7]], 'meta': [1, 2, 3]}, f)
    m3 = BlockedConfigManager(weird)
    eq(m3.get_blocked_sets()[0], {(7, 7)}, 'meta 写坏不影响屏蔽规则本身')

    # 整个文件损坏 → 留现场（.corrupt）再回默认值
    broken = os.path.join(os.path.dirname(m.config_path), 'broken.json')
    with open(broken, 'w', encoding='utf-8') as f:
        f.write('{"ui_exact": [[1, 1]')       # 半截 JSON
    m4 = BlockedConfigManager(broken)
    eq(m4.count(), 0, '损坏文件回落到默认值')
    check(os.path.exists(broken + '.corrupt'), '损坏的原文件被留成 .corrupt（可人工抢救）')

    # 脏数据：非数字、长度不对、重复项
    dirty = os.path.join(os.path.dirname(m.config_path), 'dirty.json')
    with open(dirty, 'w', encoding='utf-8') as f:
        json.dump({'ui_exact': [['a', 'b'], [10, 10], [10, 10], [1], 'x', None]}, f)
    m5 = BlockedConfigManager(dirty)
    eq(m5.get_blocked_sets()[0], {(10, 10)}, '脏数据里的有效项留下、无效项丢掉、重复项去重')


def test_reload_and_threads():
    print('\n[FR-6] 热加载 + 线程安全（后台线程回写统计，界面同时在读）')
    m = _tmp_manager()
    m.add_blocked('ui', (100, 100))

    # 用户手改 JSON 后热加载
    data = _read(m.config_path)
    data['cover_exact'] = [[900, 383]]
    with open(m.config_path, 'w', encoding='utf-8') as f:
        json.dump(data, f)
    m.reload()
    eq(m.get_blocked_sets()[2], {(900, 383)}, 'reload 能读进外部改动')

    for i in range(20):
        m.add_blocked('ui', (200 + i, 100))

    errors = []

    def worker():
        try:
            for _ in range(30):
                m.add_hits({('ui', 100, 100): 1})
                m.list_blocked()
                m.count()
        except Exception as e:                # noqa: BLE001
            errors.append(repr(e))

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    eq(errors, [], '并发读写没有抛异常')
    eq({r['category'] + str(r['width']): r['hits'] for r in m.list_blocked()}['ui100'], 180,
       '6 线程 × 30 次累加，一次都没丢（否则就是读改写没锁住）')
    eq(len(_read(m.config_path)['ui_exact']), 21, '并发期间新增的 20 条一条没少')
    eq([f for f in os.listdir(os.path.dirname(m.config_path)) if f.endswith('.tmp')], [],
       '并发写也没留下半截临时文件')


def main():
    test_classify()
    test_classify_reasons_on_imageinfo()
    test_crud()
    test_hits()
    test_storage_is_atomic_and_tolerant()
    test_reload_and_threads()
    print(f'\n{"=" * 46}')
    print(f'通过 {_passed} 项，失败 {len(_failed)} 项')
    if _failed:
        for f in _failed:
            print(f'  ✗ {f}')
        sys.exit(1)
    print('全部通过')


if __name__ == '__main__':
    main()
