# -*- coding: utf-8 -*-
"""版本检测（检查更新）回归测试

运行：
    .venv/Scripts/python.exe tests/test_updater.py

为什么单独一个文件：v1.6.0 新加的「关于 + 检查更新」有两个新入口，一个还直接把
用户带向下载。它们出错的方式都很安静 —— 不崩、不报错，只是**悄悄地不再工作**：

一、版本号只认 tag，资产名不参与比较
   GitHub Release 的 tag 决定「有没有新版本」。发版时若 tag 写 v1.5.0、资产名写成
   1.6.0（真会手滑的失误），客户端只读 tag 就会永远认为「已是最新」，更新通道
   静默失效。所以这里钉住：tag 解析、逐段比较、以及「tag 与资产名不一致」要被发现。

二、检查更新绝不能把界面搞崩
   api.github.com 在国内网络下常常超时或连不上。check_for_updates 承诺「任何失败
   都不抛异常」，并把人话写进 UpdateInfo.error。这里逐条钉住 HTTP 403/404/5xx、
   超时、连接失败、返回非预期结构这几条路径。

三、没有网也要能测
   所有网络入口都通过 fetch 参数注入，本文件**不联网**。
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests                                       # noqa: E402

from web_image_dl import updater as U                 # noqa: E402
from web_image_dl.settings import (                   # noqa: E402
    Settings, DEFAULTS, K_AUTO_CHECK_UPDATE, K_LAST_UPDATE_CHECK,
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


# ---------------------------------------------------------------- 造数据

def _asset(name, size=0, url='https://example.com/f'):
    return {'name': name, 'size': size, 'browser_download_url': url}


def _release(tag='v1.6.0', assets=None, body='更新说明', html='https://example.com/rel'):
    return {'tag_name': tag, 'body': body, 'html_url': html, 'assets': assets or []}


def _fetcher(payload):
    """返回一个合法的 fetch(url, timeout)"""
    return lambda url, timeout: payload


def _raiser(exc):
    def _f(url, timeout):
        raise exc
    return _f


def _http_error(code):
    """造一个带 status_code 的 requests.HTTPError（和真实抛出的形状一致）"""
    e = requests.HTTPError(f'HTTP {code}')
    e.response = type('R', (), {'status_code': code})()
    return e


# ================================================================
#  一、tag 解析与版本比较
# ================================================================

def test_tag_and_compare():
    print('\n[UP-1] tag 解析与版本比较（版本号只认 tag）')
    eq(U.parse_tag('v1.6.0'), '1.6.0', '去掉 v 前缀')
    eq(U.parse_tag('1.6.0'), '1.6.0', '本来没有前缀也照收')
    eq(U.parse_tag('V1.6.0'), '1.6.0', '大写 V 也去掉')
    eq(U.parse_tag('  v1.6.0  '), '1.6.0', '两端空格被清掉')
    eq(U.parse_tag(''), '', '空 tag 得到空串（不抛异常）')
    eq(U.parse_tag(None), '', 'None 也安全')

    def info_for(local, tag='v1.6.0'):
        return U.check_for_updates(fetch=_fetcher(_release(tag=tag)), current=local)

    eq(info_for('1.5.0').has_update, True, '本地 1.5.0 vs tag 1.6.0 → 有新版本')
    eq(info_for('1.5.0').latest, '1.6.0', '最新版本号已去掉 v')
    eq(info_for('1.6.0').has_update, False, '版本相同 → 没有新版本')
    eq(info_for('1.7.0').has_update, False, '本地更新（用户装了内侧包）→ 不提示降级')
    eq(info_for('1.5.10', tag='v1.5.9').has_update, False, '修订号按数字比：1.5.10 > 1.5.9')
    eq(info_for('1.5.9', tag='v1.5.10').has_update, True, '反向也成立，不是字符串比较')
    eq(info_for('1.5.0', tag='v2.0.0').has_update, True, '主版本跨代能被识别')
    eq(info_for('1.5.0', tag='v1.4.20260623').has_update, False,
       '老格式 tag（日期当修订号）不会误判为新版本')
    eq(info_for('').has_update, False, '本地版本号读不到时不乱提示有新版')
    eq(info_for('').latest, '1.6.0', '本地版本读不到也照样把远端版本记下来（供界面回显）')

    eq(info_for('1.5.0', tag='v1.6.0').latest_is_valid, True, '规范三段式 tag 标记为合法')
    eq(info_for('1.5.0', tag='v1.6').latest_is_valid, False, '两段式 tag 标记为不规范')
    eq(info_for('1.5.0', tag='v1.6.0-beta').latest_is_valid, False, '带后缀的 tag 标记为不规范')
    eq(info_for('1.5.0', tag='v1.6').has_update, True,
       '不规范 tag 仍能宽容比出高低（v1.6 补成 1.6.0，比 1.5.0 新），不是靠字符串比')
    eq(info_for('1.9.0', tag='v1.6').has_update, False, '不规范 tag 反向比较也不会倒挂')

    eq(U.CHECK_INTERVAL, 6 * 3600, '自动检查间隔是 6 小时（GitHub 未认证限流 60 次/小时）')
    check(U.GITHUB_REPO == 'virmuran/ImgSnag-WeChat', '仓库地址写的是本项目（改错仓库就永远查不到更新）')
    check(U.API_URL.endswith('/releases/latest'), '接口用的是 latest 而不是列表（草稿/预发布不会混进来）')


# ================================================================
#  二、发行资产识别
# ================================================================

def test_assets():
    print('\n[UP-2] 发行资产识别（安装包 / 便携包分得清）')
    eq(U.classify_asset('ImgSnagWeChat_1.6.0_setup.exe'), 'installer', 'setup.exe → 安装包')
    eq(U.classify_asset('ImgSnagWeChat_1.6.0_installer.exe'), 'installer', 'installer.exe 也算安装包')
    eq(U.classify_asset('ImgSnagWeChat_1.6.0_portable.zip'), 'portable', '.zip → 便携包')
    eq(U.classify_asset('ImgSnagWeChat.exe'), 'file', '裸 exe 归为其他文件')
    eq(U.classify_asset('checksums.txt'), 'file', '非 exe/zip 归为其他')
    eq(U.classify_asset(''), 'file', '空名字不炸')

    eq(U.extract_version_from_name('ImgSnagWeChat_1.6.0_setup.exe'), '1.6.0', '从文件名抠版本号')
    eq(U.extract_version_from_name('portable.zip'), '', '抠不到就返回空串')

    eq(U.human_size(0), '未知大小', '0 字节不显示成 0 B（接口没给大小）')
    eq(U.human_size(None), '未知大小', 'None 也安全')
    eq(U.human_size(512), '512 B', '小于 1KB 按字节显示')
    eq(U.human_size(1536), '1.5 KB', '转 KB')
    eq(U.human_size(29712382), '28.3 MB', '安装包体积按 MB 显示')

    info = U.check_for_updates(fetch=_fetcher(_release(assets=[
        _asset('ImgSnagWeChat_1.6.0_portable.zip', 31_800_000),
        _asset('ImgSnagWeChat_1.6.0_setup.exe', 30_500_000),
    ])), current='1.5.0')
    eq([a.kind for a in info.assets], ['installer', 'portable'],
       '资产按「安装包优先」排序（用户最可能想要的那个排第一）')
    eq(info.primary_asset().kind, 'installer', 'primary_asset 给的是安装包')
    eq(info.assets_text(), '安装包 29.1 MB · 便携包 30.3 MB', '一句话说清这次发布了什么')
    eq([a.size_text for a in info.assets], ['29.1 MB', '30.3 MB'], '每个资产带可读体积')

    many = U.check_for_updates(fetch=_fetcher(_release(assets=[
        _asset('a_setup.exe'), _asset('b_portable.zip'), _asset('c.txt'), _asset('d.txt'),
    ])), current='1.5.0')
    check(many.assets_text().endswith('等 4 个文件'), '资产多于 3 个时收敛成「等 N 个文件」')

    bad = U.check_for_updates(fetch=_fetcher(_release(assets=[
        {'size': 1}, {'name': ''}, 'not-a-dict', _asset('ok_setup.exe', 10),
    ])), current='1.5.0', )
    eq(len(bad.assets), 1, '坏资产项跳过，不影响好资产（也不抛异常）')

    empty = U.check_for_updates(fetch=_fetcher(_release(assets=[])), current='1.5.0')
    eq(empty.assets, [], '没有资产时是空列表')
    eq(empty.assets_text(), '', '没有资产时不拼出半截文案')
    eq(empty.primary_asset(), None, '没有资产时 primary_asset 返回 None')


# ================================================================
#  三、网络与错误分支
# ================================================================

def test_error_branches():
    print('\n[UP-3] 网络失败一律转成人话（且绝不抛异常）')

    def err(exc, local='1.5.0'):
        return U.check_for_updates(fetch=_raiser(exc), current=local)

    i = err(_http_error(403))
    eq(i.ok, False, 'HTTP 403 → ok=False')
    check('拒绝' in i.error or '稍后' in i.error, f'403 给出限流提示（实际：{i.error}）')
    eq(i.has_update, False, '失败时不会误报有新版')

    i = err(_http_error(404))
    check('正式发布' in i.error, f'404 提示仓库还没有正式发布（实际：{i.error}）')

    i = err(_http_error(500))
    check('500' in i.error, f'其他 HTTP 码照实显示（实际：{i.error}）')

    i = err(requests.exceptions.Timeout('timed out'))
    check('超时' in i.error, f'超时给出可理解的原因（实际：{i.error}）')
    check('网络' in i.error, '超时提示里提到网络，便于用户判断是不是公司网限制')

    i = err(requests.exceptions.ConnectionError('conn refused'))
    check('无法连接' in i.error, f'连接失败给出可理解的原因（实际：{i.error}）')

    i = err(RuntimeError('boom'))
    check('检查失败' in i.error, f'未识别异常走兜底文案（实际：{i.error}）')

    # 返回结构被代理/网关换掉时，也不能让界面崩在 .get 上
    i = U.check_for_updates(fetch=_fetcher(['not', 'a', 'dict']), current='1.5.0')
    eq(i.ok, False, '返回数组 → 判为失败而不是抛异常')
    check('非预期结构' in i.error, f'指出结构不对（实际：{i.error}）')

    i = U.check_for_updates(fetch=_fetcher(None), current='1.5.0')
    eq(i.ok, True, '返回空 → 当作「查不到版本」，不算错误')
    eq(i.latest, '', '空返回时没有版本号')
    eq(i.has_update, False, '空返回时不提示更新')

    i = U.check_for_updates(fetch=_fetcher(_release(tag='')), current='1.5.0')
    eq(i.ok, True, '仓库能访问但 tag 为空 → 不算错误')
    eq(i.has_update, False, '没有 tag 就不谈更新')

    i = U.check_for_updates(fetch=_fetcher(_release(assets=None)), current='1.5.0')
    eq(i.assets, [], 'assets 为 None 时是空列表')
    check('NoneType' not in str(i.error), 'assets 为 None 不算错误')
    eq(i.page_url, 'https://example.com/rel', '没资产也能拿到 Release 页面地址')

    i = U.check_for_updates(fetch=_fetcher({}), current='1.5.0')
    eq(i.ok, True, '空对象不炸')
    eq(i.page_url, U.RELEASES_PAGE, '缺 html_url 时回落到本项目 Release 页')


# ================================================================
#  四、发布失误的诊断
# ================================================================

def test_diagnostics():
    print('\n[UP-4] 诊断：tag 与资产名版本不一致要能被发现')
    ok = U.check_for_updates(fetch=_fetcher(_release(tag='v1.6.0', assets=[
        _asset('ImgSnagWeChat_1.6.0_setup.exe'),
    ])), current='1.5.0')
    eq(ok.version_mismatch, False, 'tag 与资产名一致时不报警')

    bad = U.check_for_updates(fetch=_fetcher(_release(tag='v1.5.0', assets=[
        _asset('ImgSnagWeChat_1.6.0_setup.exe'),      # 发版时手滑：tag 忘了改
    ])), current='1.4.0')
    eq(bad.version_mismatch, True, 'tag 与资产名版本不一致时报警（客户端只认 tag）')
    eq(bad.has_update, True, '本地 1.4.0 → 仍按 tag 判定有新版本')
    eq(bad.latest, '1.5.0', '最新版本以 tag 为准，不是资产名')

    eq(U.check_for_updates(fetch=_fetcher(_release(assets=[_asset('checksums.txt')])),
                           current='1.5.0').version_mismatch, False,
       '抠不出版本号的资产不算「不一致」（否则每次都误报）')

    info = U.check_for_updates(fetch=_fetcher(_release(body='## 新功能\n- 关于对话框')),
                               current='1.5.0')
    check('关于对话框' in info.notes, '更新说明原样带回来给界面显示')

    info = U.check_for_updates(fetch=_fetcher({'tag_name': 'v1.6.0'}), current='1.5.0')
    eq(info.notes, '', '没有 body 时说明为空串（界面自己有兜底文案）')
    eq(info.tag, 'v1.6.0', '原文 tag 也留着（界面回显「GitHub 最新发布」用）')


# ================================================================
#  五、描述函数本身
# ================================================================

def test_describe_error():
    print('\n[UP-5] 错误描述不会抛第二次（它自己崩了就没人兜底了）')
    for exc in (requests.exceptions.Timeout('t'),
                requests.exceptions.ConnectionError('c'),
                _http_error(403), _http_error(404), _http_error(503),
                RuntimeError('x'), ValueError('y'),
                Exception('裸异常')):
        try:
            msg = U._describe_error(exc)
            check(isinstance(msg, str) and msg.strip() != '',
                  f'{type(exc).__name__} 的描述非空')
        except Exception as e:                        # noqa: BLE001
            check(False, f'{type(exc).__name__} 的描述不该抛异常，实际抛了 {e!r}')


# ================================================================
#  六、设置项（自动检查开关）
# ================================================================

def test_settings_keys():
    print('\n[UP-6] 自动检查开关的默认值与读写')
    check(K_AUTO_CHECK_UPDATE in DEFAULTS, '自动检查开关已在 DEFAULTS 里注册')
    check(K_LAST_UPDATE_CHECK in DEFAULTS, '上次检查时间戳已在 DEFAULTS 里注册')
    eq(DEFAULTS[K_AUTO_CHECK_UPDATE], True, '首次安装默认开启自动检查')
    eq(DEFAULTS[K_LAST_UPDATE_CHECK], 0, '首次安装还没有检查记录（0 = 从没查过）')

    d = tempfile.mkdtemp(prefix='imgsnag_updater_test_')
    s = Settings()
    s.use_ini_file(os.path.join(d, 's.ini'))          # 别写进真实注册表
    eq(s.get(K_AUTO_CHECK_UPDATE), True, '新配置里读到默认值 True')
    eq(s.get(K_LAST_UPDATE_CHECK), 0, '新配置里读到默认值 0')
    s.set(K_AUTO_CHECK_UPDATE, False)
    eq(s.get(K_AUTO_CHECK_UPDATE), False, '关掉之后能读回 False')
    s.set(K_LAST_UPDATE_CHECK, 1730000000)
    eq(s.get(K_LAST_UPDATE_CHECK), 1730000000, '时间戳按整数存取')
    s.use_default_store()


def main():
    test_tag_and_compare()
    test_assets()
    test_error_branches()
    test_diagnostics()
    test_describe_error()
    test_settings_keys()
    print(f'\n{"=" * 46}')
    print(f'通过 {_passed} 项，失败 {len(_failed)} 项')
    if _failed:
        for f in _failed:
            print(f'  ✗ {f}')
        sys.exit(1)
    print('全部通过')


if __name__ == '__main__':
    main()
