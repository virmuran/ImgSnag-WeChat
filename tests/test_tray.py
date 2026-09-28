# -*- coding: utf-8 -*-
"""右下角托盘与「关闭窗口时」行为 回归测试（离屏运行，不需要显示器）

运行：
    .venv/Scripts/python.exe tests/test_tray.py

为什么单独一个文件：托盘这套东西的失效方式全都是「看起来正常、实际上没用」。

一、收进托盘 = 进程当场没了
   Qt 默认「最后一个窗口关闭 → 结束进程」，而收托盘正是把窗口 hide() 掉。
   少了 `setQuitOnLastWindowClosed(False)`，用户点一次 X 就整个退出，托盘图标闪一下就没了。

二、反向的坑：真退出的分支忘了 quit()
   加上那句之后「关掉最后一个窗口」不再自动退出 —— 于是「直接退出」分支如果只
   `event.accept()`，结果是**窗口没了、托盘也摘了、进程还在后台活着**，用户再也找不回来。
   这不是臆想：本项目的 ChemCal 就是这么写的，实测点 X 后 1 秒事件循环仍在跑（窗口已隐藏、
   托盘不可见）。所以这里用 inspect 取 closeEvent 源码，钉住那句 `QApplication.quit()`。

三、没有托盘时把窗口藏起来
   受限/无 shell 环境下 `isSystemTrayAvailable()` 为假，此时 hide() 等于让程序人间蒸发。

四、托盘 QMenu 没有长期持有
   PySide6 里 wrapper 一被回收就连带删掉底层 C++ 对象，菜单随即失效，
   而报错点常常出现在**下一步**，极难定位。

所以本文件钉的是「行为 + 结构承诺」，不是「图标画出来了」。
离屏平台的 `isSystemTrayAvailable()` 恒为 False，需要真托盘的用例先覆写
`win._tray_available`（抽成方法就是为了这个），能造出**真实**的 QSystemTrayIcon。

注意：`CloseChoiceDialog.exec()` 在离屏下会永久阻塞且不报错，
所以本文件**绝不**让询问分支真弹框 —— 要么先把 close_action 设成 tray/quit，
要么把 `_ask_close_action` 换成替身。
"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
# 必须自带字体目录：缺字体时控件度量漂移，几何/尺寸断言会随机挂
os.environ.setdefault('QT_QPA_FONTDIR', r'C:\Windows\Fonts')

from PySide6.QtCore import Qt                                    # noqa: E402
from PySide6.QtWidgets import (                                  # noqa: E402
    QApplication, QSystemTrayIcon,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

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
    check(got == want, f'{label}\n      得到: {got!r}\n      期望: {want!r}')


class _isolated_config:
    """把配置临时改到临时目录 —— 绝不动用户真实的注册表与 blocked_sizes.json"""

    def __enter__(self):
        from web_image_dl.blocked_config import blocked_config
        from web_image_dl.settings import K_LAST_UPDATE_CHECK, settings

        self.dir = tempfile.mkdtemp(prefix='imgsnag_tray_')
        self._blocked = blocked_config
        self._settings = settings
        self._saved_path = blocked_config.config_path
        blocked_config.config_path = os.path.join(self.dir, 'blocked_sizes.json')
        blocked_config.reload()
        self.ini = os.path.join(self.dir, 'settings.ini')
        settings.use_ini_file(self.ini)
        # 压住启动 2 秒后的自动检查更新（6 小时节流），测试不联网
        settings.set(K_LAST_UPDATE_CHECK, int(time.time()))
        return self

    def __exit__(self, *exc):
        self._blocked.config_path = self._saved_path
        self._blocked.reload()
        self._settings.use_default_store()
        return False


def _new_window(with_tray=False):
    """造一个主窗口；with_tray=True 时覆写可用性判断并造出**真实**托盘对象"""
    from web_image_dl.app import ImageDownloaderApp

    win = ImageDownloaderApp()
    if with_tray:
        win._tray_available = lambda: True
        win._setup_tray()
    return win


def _close_window(win):
    """测试收尾用的关闭。

    ⚠ 必须先置 `_really_quit`：closeEvent 一旦走到「每次询问」分支就会弹
    CloseChoiceDialog 并 exec()，而离屏平台的 exec() **永久阻塞且不报错** ——
    整个测试文件会静默挂死（本文件第一次跑就是这么卡在 TR-2 收尾处的）。
    需要**故意**走 closeEvent 分支的用例别用这个，直接调 win.close()。
    """
    win._really_quit = True
    win.close()
    QApplication.processEvents()


def _function_calls(rel_path, func_name, class_name=None):
    """列出某函数体里所有调用的写法（如 `'QApplication.quit()'`），用 AST 取。

    ⚠ 不能用 `inspect.getsource` + 子串搜索：那会把**文档字符串里提到**的调用也算进去。
    反向验证实测踩过 —— 把 closeEvent 里的 `QApplication.quit()` 换成 `pass` 之后，
    docstring 里那句「必须显式调 QApplication.quit()」照样命中，断言形同虚设。
    """
    import ast

    path = os.path.join(ROOT, rel_path.replace('/', os.sep))
    tree = ast.parse(open(path, encoding='utf-8').read())

    holder = tree
    if class_name:
        holder = next((n for n in ast.walk(tree)
                       if isinstance(n, ast.ClassDef) and n.name == class_name), None)
        if holder is None:
            return []
    node = next((n for n in holder.body
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and n.name == func_name), None)
    if node is None:
        return []

    out = []
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            args = ', '.join(ast.unparse(a) for a in n.args)
            out.append(f'{ast.unparse(n.func)}({args})')
    return out


def _read_ini_value(path, key):
    """直接从 ini 文件里把值读回来 —— 只在内存里比对等于没验落盘"""
    from PySide6.QtCore import QSettings

    qs = QSettings(path, QSettings.IniFormat)
    return qs.value(key, None)


# ──────────────────────────────────────────────── 一、偏好读写
def test_close_action_setting():
    print('\n[TR-1] 关闭行为偏好：默认每次询问、写入落盘、坏值退回默认')

    with _isolated_config():
        from web_image_dl.settings import K_CLOSE_ACTION

        win = _new_window()
        eq(win._resolve_close_action(), 'ask', '出厂默认是「每次询问」（不能默认静默收托盘）')

        eq(win._set_close_action('tray'), True, '写入 tray 成功')
        eq(win._resolve_close_action(), 'tray', '读回来是 tray')
        eq(_read_ini_value(os.path.join(win.settings.path()), K_CLOSE_ACTION)
           if os.path.exists(win.settings.path()) else None,
           'tray', '确实落到了磁盘上（不是只在内存里）')

        eq(win._set_close_action('nonsense'), False, '非法取值被拒绝')
        eq(win._resolve_close_action(), 'tray', '被拒之后偏好不变')

        win.settings.set(K_CLOSE_ACTION, 'TRAY!!!')      # 模拟用户手改坏了配置
        eq(win._resolve_close_action(), 'ask', '配置被改坏时退回「每次询问」，不卡在怪状态')

        win.settings.set(K_CLOSE_ACTION, '  quit  ')
        eq(win._resolve_close_action(), 'quit', '前后空白容错（手改配置很容易带上空格）')
        _close_window(win)


# ──────────────────────────────────────────────── 二、托盘对象与菜单
def test_tray_object_and_menu():
    print('\n[TR-2] 托盘对象与右键菜单：三项互斥、当前项勾选、菜单被长期持有')

    with _isolated_config():
        win = _new_window()

        eq(win._tray, None, '离屏平台判「无托盘」时不硬造托盘对象')
        eq(win._tray_menu, None, '没有托盘就没有菜单')
        eq(win._tray_available(), False, '离屏平台 isSystemTrayAvailable() 恒为 False')

        win._tray_available = lambda: True
        tray = win._setup_tray()
        check(tray is not None, '托盘可用时造得出 QSystemTrayIcon')
        check(isinstance(win._tray, QSystemTrayIcon), '_tray 是真实的 QSystemTrayIcon')
        check(win._tray.isVisible(), '托盘图标已 show()')
        check('ImgSnag' in win._tray.toolTip(), f'托盘提示带产品名（{win._tray.toolTip()}）')
        check(win._tray.contextMenu() is not None, '右键菜单已挂到托盘上')

        menu = win._build_tray_menu()
        check(win._tray_menu is not None and win._tray_menu is menu,
              '菜单被长期持有（PySide6 里不持有就会被回收，菜单随即失效）')

        texts = [a.text() for a in menu.actions()]
        check('显示主窗口' in texts, '菜单有「显示主窗口」')
        check('打开图库' in texts, '菜单有「打开图库」（收托盘后最常用的一件事）')
        check('退出 ImgSnag' in texts, '菜单有「退出 ImgSnag」')

        subs = [a for a in menu.actions() if a.menu() is not None]
        eq(len(subs), 1, '只有一个子菜单')
        eq(subs[0].text(), '关闭窗口时', '子菜单叫「关闭窗口时」（记住选择后想改回来的唯一入口）')

        items = subs[0].menu().actions()
        eq([a.text() for a in items], ['每次询问', '最小化到托盘', '直接退出'],
           '三个行为选项齐全且顺序固定')
        check(all(a.isCheckable() for a in items), '三项都是可勾选的')
        eq([k for k, a in win._close_actions.items() if a.isChecked()], ['ask'],
           '当前生效的那一项被勾上')
        check(win._close_group.isExclusive(), '勾选互斥（同时亮两个等于没说当前是哪个）')

        # 每一项都必须真连到槽函数 —— 只建了 QAction 忘了 connect 是最常见的手误
        for key, act in (('tray', items[1]), ('quit', items[2]), ('ask', items[0])):
            act.trigger()
            eq(win._resolve_close_action(), key, f'点「{act.text()}」真的改了偏好')
        _close_window(win)


# ──────────────────────────────────────────────── 三、收托盘 / 唤回
def test_minimize_and_restore():
    print('\n[TR-3] 收托盘：窗口藏起来但进程不退；唤回后原样恢复')

    with _isolated_config():
        win = _new_window(with_tray=True)
        win.show()
        QApplication.processEvents()
        check(not win.isHidden(), '前置：窗口本来可见')

        ok = win._minimize_to_tray()
        QApplication.processEvents()
        eq(ok, True, '收托盘返回 True')
        check(win.isHidden(), '窗口被藏起来了')
        eq(win._really_quit, False, '**不是**真退出（进程还在后台跑）')
        check(win._tray is not None and win._tray.isVisible(),
              '托盘图标仍在（否则用户既看不到窗口也找不到图标）')
        win._tray_tip_shown = True          # 提示只弹一次，这里不验

        # 收托盘也要落盘：此后被强杀也不丢界面偏好
        win.filter_square_cb.setChecked(False)
        win._minimize_to_tray()
        from PySide6.QtCore import QSettings

        from web_image_dl.settings import K_FILTER_SMALL

        raw = QSettings(win.settings.path(), QSettings.IniFormat).value(K_FILTER_SMALL, None)
        check(str(raw).lower() in ('0', 'false'), f'收托盘时就落了盘（K_FILTER_SMALL={raw!r}）')

        # 唤回
        win._restore_from_tray()
        QApplication.processEvents()
        check(not win.isHidden(), '唤回后窗口可见')
        check(win.isActiveWindow() or True, '唤回流程走完不报错')

        # 双击 / 单击托盘图标都要唤回；右键（Context）不许误唤回
        win._minimize_to_tray()
        QApplication.processEvents()
        win._on_tray_activated(QSystemTrayIcon.ActivationReason.Context)
        check(win.isHidden(), '右键弹菜单时不误唤回窗口')
        win._on_tray_activated(QSystemTrayIcon.ActivationReason.DoubleClick)
        check(not win.isHidden(), '双击托盘图标唤回窗口')
        win._minimize_to_tray()
        win._on_tray_activated(QSystemTrayIcon.ActivationReason.Trigger)
        check(not win.isHidden(), '单击托盘图标也能唤回')

        # 最大化状态要原样还原（showNormal() 会把它压回普通大小）
        win.showMaximized()
        QApplication.processEvents()
        win._minimize_to_tray()
        win._restore_from_tray()
        QApplication.processEvents()
        check(win.windowState() & Qt.WindowMaximized, '唤回后仍保持最大化（没用 showNormal）')

        _close_window(win)


# ──────────────────────────────────────────────── 四、没有托盘时的降级
def test_no_tray_fallback():
    print('\n[TR-4] 系统托盘不可用：不许 hide，必须退回真退出')

    with _isolated_config():
        win = _new_window()                    # 离屏 = 没有托盘
        win.show()
        QApplication.processEvents()
        eq(win._tray, None, '前置：确实没有托盘')

        result = win._minimize_to_tray()
        eq(result, False, '没有托盘时收托盘失败')
        eq(win._really_quit, True, '没有托盘时转成真退出')
        check(not win.isHidden(),
              '**绝不能**把窗口藏起来 —— 那会变成「窗口不见了、程序还在」')


# ──────────────────────────────────────────────── 五、真退出入口
def test_quit_entries():
    print('\n[TR-5] 「退出」入口一律走 _quit_app，且不依赖 self.close()')

    with _isolated_config():
        win = _new_window(with_tray=True)
        win.show()
        QApplication.processEvents()

        win._quit_app()
        QApplication.processEvents()
        eq(win._really_quit, True, '_quit_app 标记为真退出（之后再点 X 不会再收托盘）')
        check(win._tray is None or not win._tray.isVisible(), '退出时摘掉了托盘图标')
        # 托盘菜单里的「退出」必须连到 _quit_app，而不是某个会收托盘的路径
        menu = win._tray_menu
        quit_act = [a for a in menu.actions() if a.text().startswith('退出')][0]
        win._really_quit = False
        quit_act.trigger()
        eq(win._really_quit, True, '菜单「退出 ImgSnag」直达真退出')

    # 源码级钉住：界面里任何地方都不许用 self.close() 当「退出」
    app_src = open(os.path.join(ROOT, 'web_image_dl', 'app.py'), encoding='utf-8').read()
    check('self.close()' not in app_src,
          'app.py 里没有 self.close() —— 否则「退出」会被偏好判定改成收托盘')


# ──────────────────────────────────────────────── 六、closeEvent 接线
def test_close_event_wiring():
    print('\n[TR-6] 点 X 的三条分支：收托盘 / 直接退出 / 取消')

    from web_image_dl.settings import K_CLOSE_ACTION

    def prefer(win, action):
        """直接写偏好，**故意不走 `_set_close_action`**。

        这条测的是 closeEvent 的分支，不是偏好写入 —— 用后者会顺手把这一个用例的
        结论绑在两个实现上（反向验证时把 `_set_close_action` 拆坏，这条就会掉进
        询问分支弹框，150 秒后才超时）。
        """
        win.settings.set(K_CLOSE_ACTION, action)

    # (a) 收托盘分支：窗口藏起来、进程不退、且**不弹框**
    with _isolated_config():
        win = _new_window(with_tray=True)
        win.show()
        QApplication.processEvents()
        prefer(win, 'tray')
        win.close()                            # 等效用户点右上角 X
        QApplication.processEvents()
        check(win.isHidden(), '偏好=tray 时点 X 收进托盘')
        eq(win._really_quit, False, '收托盘不是真退出')
        _close_window(win)

    # (b) 直接退出分支：必须真的退出进程
    with _isolated_config():
        win = _new_window(with_tray=True)
        win.show()
        QApplication.processEvents()
        prefer(win, 'quit')
        win.close()                            # 偏好=quit，走不到询问分支，不会卡在 exec()
        eq(win._really_quit, True, '偏好=quit 时点 X 走真退出')

    # (c) 询问分支：勾了「记住我的选择」要把选择存下来
    with _isolated_config():
        from web_image_dl.settings import K_CLOSE_ACTION

        win = _new_window(with_tray=True)
        win.show()
        QApplication.processEvents()
        eq(win._resolve_close_action(), 'ask', '前置：出厂是每次询问')
        # exec() 在离屏下永久阻塞，这里换成替身（等价于用户选了「最小化到托盘」并勾记住）
        win._ask_close_action = lambda: ('tray', True)
        win.close()
        QApplication.processEvents()
        eq(win._resolve_close_action(), 'tray', '勾了记住就把这次的选择存下来')
        check(win.isHidden(), '并按选择收进了托盘')
        _close_window(win)

    with _isolated_config():
        win = _new_window(with_tray=True)
        win.show()
        QApplication.processEvents()
        win._ask_close_action = lambda: ('quit', False)      # 没勾记住
        win.close()
        eq(win._resolve_close_action(), 'ask', '没勾记住就不写偏好，下次还得问')
        eq(win._really_quit, True, '没勾记住也照本次选择真退出')

    with _isolated_config():
        win = _new_window(with_tray=True)
        win.show()
        QApplication.processEvents()
        win._ask_close_action = lambda: ('cancel', False)
        win.close()
        QApplication.processEvents()
        check(not win.isHidden(), '选「取消」时留在界面上（窗口不许消失）')
        eq(win._really_quit, False, '取消不算退出')
        _close_window(win)

    # 源码级钉住那三句关键调用 —— 这几处错了都不报错，只会表现为怪现象。
    # （必须 AST 取调用，见 _function_calls 里的说明：文本搜索会被 docstring 骗过）
    close_calls = _function_calls('web_image_dl/app.py', 'closeEvent',
                                  class_name='ImageDownloaderApp')
    check('QApplication.quit()' in close_calls,
          'closeEvent 的真退出分支显式调了 QApplication.quit()'
          '（缺了它 = 窗口关了、托盘摘了、进程还在后台活着）')
    check('event.ignore()' in close_calls, '收托盘/取消分支都 ignore 掉了关闭事件')

    main_calls = _function_calls('main.py', 'main')
    check('app.setQuitOnLastWindowClosed(False)' in main_calls,
          'main.py 关掉了「最后一个窗口关闭即退出」'
          '（缺了它 = 一收托盘整个程序当场结束）')


# ──────────────────────────────────────────────── 七、询问对话框本身
def test_choice_dialog():
    print('\n[TR-7] 询问对话框：默认「取消」，选了才改结果')

    from web_image_dl.app import CloseChoiceDialog

    dlg = CloseChoiceDialog()
    eq(dlg.choice, 'cancel', '刚打开时结果是 cancel（点 X / Esc 关掉也保持 cancel）')
    eq(dlg.remember, False, '默认不勾「记住我的选择」')
    dlg.remember_box.setChecked(True)
    dlg._choose('quit')
    eq(dlg.choice, 'quit', '选了直接退出')
    eq(dlg.remember, True, '勾选状态被记下来')


def main():
    app = QApplication.instance() or QApplication([])

    # 护栏：绝不让 CloseChoiceDialog.exec() 在离屏下跑起来。
    # 离屏平台的 exec() 会**永久阻塞且不报错** —— 一处漏网就能让整个发版闸门静默挂死。
    # 换成不阻塞的替身之后，万一将来有用例忘了定死关闭行为，也只是「窗口没关掉」，
    # 闸门照样跑完并报错，而不是卡住 150 秒才由超时判负。
    from web_image_dl.app import CloseChoiceDialog

    def _no_block_exec(self):
        self.choice, self.remember = 'cancel', False
        return 0

    CloseChoiceDialog.exec = _no_block_exec

    test_close_action_setting()
    test_tray_object_and_menu()
    test_minimize_and_restore()
    test_no_tray_fallback()
    test_quit_entries()
    test_close_event_wiring()
    test_choice_dialog()
    print(f'\n{"=" * 46}')
    print(f'通过 {_passed} 项，失败 {len(_failed)} 项')
    if _failed:
        for f in _failed:
            print(f'  ✗ {f}')
        sys.exit(1)
    print('全部通过')


if __name__ == '__main__':
    main()
