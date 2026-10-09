# -*- coding: utf-8 -*-
"""向导式「应用内更新」界面

三页：**看更新内容** → **下载** → **完成**。为什么是向导而不是「一键更新」：
更新这件事有几个用户必须看见、也必须由他决定的点 —— 换了什么、要下多大的东西、
下一步会发生什么（程序要关掉）。把这些摊成三页，比一个按钮点完再弹三个框清楚，
也不会有「点了没反应，过一会儿自己关了」这种吓人的中间态。

两种运行形态走同一条向导，只在最后一页分岔（见 `update_dl.detect_install_mode`）：
  · **安装版** → 下载 setup.exe → 点「立即安装」→ 关掉本程序、启动安装器
  · **便携版** → 下载 portable.zip → 解压到一个带版本号的新目录 →
    点「打开新版文件夹」。**绝不覆盖正在用的那份**（程序没法一边跑一边替换自己，
    exe 与已加载的 DLL 都被系统锁着），换不换、什么时候换由用户决定。

⚠ 下载与解压都跑在 `_PrepareThread` 里。主线程只更新界面 —— 30MB 的包在慢网络上
要下几分钟，放主线程就是「窗口白屏、用户以为卡死」。

⚠ 离屏测试的注意点：向导是 `QWizard`（继承 QDialog），`exec()` 在离屏下会永久
阻塞且不报错。测试请直接 `wizard.show()` + 调 `next()`/`initializePage()` 驱动，
或者用 `_grab_and_close_dialog` 那套在 exec 循环里抓取。**别在离屏下 exec 完就走**。
"""
from __future__ import annotations

import os
import time

from PySide6.QtCore import Qt, QThread, Signal, QUrl, QTimer
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QWizard, QWizardPage, QVBoxLayout, QHBoxLayout, QLabel, QProgressBar,
    QPushButton, QTextEdit, QMessageBox, QSizePolicy,
)

from . import update_dl
from .updater import human_size

#: 页面序号（用常量而不是裸数字：插页时不会错位，测试也照同一个名字断言）
PAGE_INTRO = 0
PAGE_DOWNLOAD = 1
PAGE_DONE = 2


class _PrepareThread(QThread):
    """下载（便携版再解压）—— 全部在网络/磁盘上，不能占主线程。"""

    progressed = Signal(int, int)     # 已下载字节, 总字节（总字节未知时为 0）
    staged = Signal(str)              # 阶段说明（"正在下载…" / "正在解压…"）
    prepared = Signal(dict)           # {'path','port_dir','exe','reused'}
    failed = Signal(str)              # 人话错误
    cancelled = Signal()

    def __init__(self, url, dest, *, total=0, sha256="", mode="", version="",
                 port_parent="", downloader=None, parent=None):
        super().__init__(parent)
        self._url = url
        self._dest = dest
        self._total = int(total or 0)
        self._sha256 = sha256 or ""
        self._mode = mode
        self._version = version
        #: 便携版新版解压到「程序目录旁边」——由调用方给，便于测试替身（见 port_dir_for）
        self._port_parent = port_parent
        self._download = downloader or update_dl.download_file
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def _is_cancelled(self) -> bool:
        return self._cancel

    def _on_progress(self, done, total):
        self.progressed.emit(int(done), int(total or self._total or 0))

    def run(self):
        try:
            if not self._cancel and update_dl.is_ready(self._dest, self._total, self._sha256):
                # 上次已经下好了（用户点了重试、或上次取消在最后一刻）——别再下一遍
                path = self._dest
                reused = True
            else:
                self.staged.emit("正在下载…")
                path = self._download(
                    self._url, self._dest,
                    total=self._total, sha256=self._sha256,
                    progress=self._on_progress, cancelled=self._is_cancelled,
                )
                reused = False
        except update_dl.DownloadCancelled:
            self.cancelled.emit()
            return
        except update_dl.DownloadError as exc:
            self.failed.emit(str(exc))
            return
        except Exception as exc:                   # noqa: BLE001 - 兜底，别让线程静默死掉
            self.failed.emit(f"下载失败：{exc}")
            return

        out = {"path": path, "port_dir": "", "exe": "", "reused": reused}
        if self._mode == update_dl.MODE_PORTABLE:
            if self._cancel:
                self.cancelled.emit()
                return
            self.staged.emit("正在解压…")
            try:
                port_dir = update_dl.port_dir_for(self._port_parent, self._version)
                exe = update_dl.unpack_portable(path, port_dir)
            except update_dl.DownloadError as exc:
                self.failed.emit(str(exc))
                return
            except Exception as exc:               # noqa: BLE001
                self.failed.emit(f"解压失败：{exc}")
                return
            out["port_dir"] = port_dir
            out["exe"] = exe
        self.prepared.emit(out)


class UpdateWizard(QWizard):
    """更新向导本体。

    Args:
        info: `updater.UpdateInfo`（最近一次检查的结果）
        mode: `update_dl.MODE_INSTALLER` / `MODE_PORTABLE`
        asset: 要下载的那个资产（`update_dl.pick_asset` 的结果）；None = 这次没有
               适合本形态的包，向导退化成一页说明 + 前往发布页
        downloader: 注入用的下载函数（测试用；默认 `update_dl.download_file`）
    """

    #: 安装版点了「立即安装」——主窗口收到后要退出自己（否则安装器覆盖不了程序文件）
    install_launched = Signal()

    def __init__(self, info, mode, asset=None, downloader=None, parent=None):
        super().__init__(parent)
        self.info = info
        self.mode = mode
        self.asset = asset
        self._download = downloader
        self._thread = None
        self._result = {}          # 预下载/解压的结果
        self._started_at = 0.0
        self._closing = False      # 向导正在关闭 —— 线程晚到的信号一律丢弃

        self.setWindowTitle("软件更新")
        self.setMinimumSize(560, 440)
        self.setWizardStyle(QWizard.ModernStyle)
        self.setOption(QWizard.NoBackButtonOnStartPage, True)
        self.setOption(QWizard.NoCancelButtonOnLastPage, True)
        self.setButtonText(QWizard.CancelButton, "稍后")
        self.setButtonText(QWizard.NextButton, "下一步")

        self._intro = self._build_intro_page()
        self._download_page = self._build_download_page()
        self._done_page = self._build_done_page()
        self.addPage(self._intro)
        self.addPage(self._download_page)
        self.addPage(self._done_page)

        self.currentIdChanged.connect(self._apply_page_ui)

    # ------------------------------------------------------------ 三个页面

    def _build_intro_page(self) -> QWizardPage:
        page = QWizardPage()
        page.setTitle("发现新版本")
        page.setSubTitle("")
        box = QVBoxLayout(page)
        box.setSpacing(8)

        self._intro_head = QLabel("")
        self._intro_head.setTextFormat(Qt.RichText)
        self._intro_head.setWordWrap(True)
        box.addWidget(self._intro_head)

        self._intro_warn = QLabel("")
        self._intro_warn.setStyleSheet("color:#fa8c16; font-size:11px;")
        self._intro_warn.setWordWrap(True)
        self._intro_warn.hide()
        box.addWidget(self._intro_warn)

        notes_label = QLabel("更新内容")
        notes_label.setStyleSheet("color:#666; font-size:11px;")
        box.addWidget(notes_label)

        self.notes_view = QTextEdit()
        self.notes_view.setReadOnly(True)
        self.notes_view.setPlainText("")
        box.addWidget(self.notes_view, 1)

        row = QHBoxLayout()
        row.addStretch()
        self.page_link = QPushButton("前往发布页")
        self.page_link.setFlat(True)
        self.page_link.setCursor(Qt.PointingHandCursor)
        self.page_link.setStyleSheet(
            "QPushButton { color:#1677ff; border:none; background:transparent; }"
            "QPushButton:hover { text-decoration: underline; }")
        self.page_link.clicked.connect(self._open_release_page)
        row.addWidget(self.page_link)
        box.addLayout(row)
        return page

    def _build_download_page(self) -> QWizardPage:
        page = QWizardPage()
        page.setTitle("正在下载")
        page.setSubTitle("")
        box = QVBoxLayout(page)
        box.setSpacing(10)

        self._dl_what = QLabel("")
        self._dl_what.setWordWrap(True)
        box.addWidget(self._dl_what)

        self.progress = QProgressBar()
        self.progress.setMinimum(0)
        self.progress.setMaximum(100)
        self.progress.setValue(0)
        self.progress.setMinimumHeight(22)
        box.addWidget(self.progress)

        self._dl_status = QLabel("")
        self._dl_status.setStyleSheet("color:#666; font-size:12px;")
        self._dl_status.setWordWrap(True)
        box.addWidget(self._dl_status)

        self._dl_error = QLabel("")
        self._dl_error.setStyleSheet("color:#d4380d; font-size:12px;")
        self._dl_error.setWordWrap(True)
        self._dl_error.hide()
        box.addWidget(self._dl_error)

        self.retry_btn = QPushButton("重新下载")
        self.retry_btn.setMinimumHeight(28)
        self.retry_btn.hide()
        self.retry_btn.clicked.connect(self._start_download)
        box.addWidget(self.retry_btn, 0, Qt.AlignLeft)
        box.addStretch(1)
        return page

    def _build_done_page(self) -> QWizardPage:
        page = QWizardPage()
        page.setTitle("准备就绪")
        page.setSubTitle("")
        box = QVBoxLayout(page)
        box.setSpacing(10)

        self._done_head = QLabel("")
        self._done_head.setTextFormat(Qt.RichText)
        self._done_head.setWordWrap(True)
        box.addWidget(self._done_head)

        self._done_note = QLabel("")
        self._done_note.setStyleSheet("color:#666; font-size:12px;")
        self._done_note.setWordWrap(True)
        self._done_note.setTextInteractionFlags(Qt.TextSelectableByMouse)
        box.addWidget(self._done_note)
        box.addStretch(1)
        return page

    # ------------------------------------------------------------ 渲染

    def initializePage(self, page_id: int):
        super().initializePage(page_id)
        if page_id == PAGE_INTRO:
            self._paint_intro()
        elif page_id == PAGE_DOWNLOAD:
            self._start_download()
        elif page_id == PAGE_DONE:
            self._paint_done()

    def _apply_page_ui(self, page_id: int):
        """按当前页重新摆按钮。

        必须在 `currentIdChanged` 里做（而不是只在 `initializePage` 里）：
        QWizard 切换页之后会自己重刷一遍按钮文字与可见性，只在 initializePage 里
        设一次的话，会被它的内部刷新盖掉 —— 表现是「有时文案是对的，有时变回下一步」。
        """
        back = self.button(QWizard.BackButton)
        if back is not None:
            back.setVisible(page_id == PAGE_INTRO)
        if page_id == PAGE_DONE:
            self.setButtonText(QWizard.FinishButton, self._finish_label())
        else:
            self.setButtonText(QWizard.NextButton, "下一步")
        # 下载中禁用「下一步」，下载完才放行
        nxt = self.button(QWizard.NextButton)
        if nxt is not None and page_id == PAGE_DOWNLOAD:
            nxt.setEnabled(bool(self._result))

    def _finish_label(self) -> str:
        if self.asset is None:
            return "关闭"
        return "立即安装" if self.mode == update_dl.MODE_INSTALLER else "打开新版文件夹"

    def _paint_intro(self):
        info = self.info
        extra = f"　·　本次发布：{info.assets_text()}" if getattr(info, "assets", None) else ""
        self._intro_head.setText(
            f"<div style='font-size:16px; font-weight:bold;'>v{info.current} → v{info.latest}</div>"
            f"<div style='font-size:12px; color:#666;'>当前版本 v{info.current}，"
            f"最新版本 v{info.latest}{extra}</div>"
        )
        if getattr(info, "version_mismatch", False):
            self._intro_warn.setText("⚠ 这个版本的文件名版本号和 tag 对不上，下载前留意一下")
            self._intro_warn.show()
        else:
            self._intro_warn.hide()
        self.notes_view.setPlainText((info.notes or "").strip() or "（这次发布没有写更新说明）")

        if self.asset is None:
            self._intro_head.setText(
                self._intro_head.text()
                + f"<div style='font-size:12px; color:#d4380d; margin-top:6px;'>"
                  f"这次发布没有适合{update_dl.mode_label(self.mode)}的文件，"
                  f"请点右下角去发布页手动挑选。</div>"
            )

    def _paint_download_ui(self, name: str, size: int):
        self._dl_what.setText(
            f"正在获取 <b>{name}</b>（{human_size(size)}）。"
            "下载期间可以点「稍后」先不更新，下次打开还能继续。")
        if not size:
            self.progress.setRange(0, 0)          # 总大小未知 → 转圈
        else:
            self.progress.setRange(0, 100)

    def _paint_done(self):
        info = self.info
        if self.mode == update_dl.MODE_INSTALLER:
            self._done_page.setTitle("可以安装了")
            self._done_head.setText(
                f"<div style='font-size:14px; font-weight:bold;'>"
                f"v{info.latest} 已下载完成</div>"
                f"<div style='font-size:12px; color:#666;'>点「立即安装」就会："
                f"关闭本程序 → 启动安装程序 → 按提示完成升级。</div>"
            )
            self._done_note.setText(
                "安装包的校验已通过（字节数与内容摘要都与 GitHub 上的记录一致）。\n"
                "安装过程中会弹一次「是否允许此应用更改你的设备」，那是正常的。\n"
                "已抓下来的图片和下载历史都不会动。")
        else:
            self._done_page.setTitle("新版已解压好")
            self._done_head.setText(
                f"<div style='font-size:14px; font-weight:bold;'>"
                f"v{info.latest} 已解压到一个新文件夹</div>"
                f"<div style='font-size:12px; color:#666;'>"
                f"点「打开新版文件夹」，双击里面的 {update_dl.APP_EXE_NAME} 就能用上新版。</div>"
            )
            self._done_note.setText(
                f"位置：{self._result.get('port_dir') or ''}\n\n"
                f"1. 点「打开新版文件夹」，双击里面的 {update_dl.APP_EXE_NAME} 试一下新版\n"
                "2. 觉得没问题了，就把旧目录删掉、以后用这个新文件夹\n\n"
                "便携版没法在运行中替换自己（程序文件被系统占用着），"
                "所以新版放在你旧目录旁边的独立文件夹里，不动你正在用的那一份。")

    # ------------------------------------------------------------ 下载

    def _start_download(self):
        th = self._thread
        if th is not None and th.isRunning():
            return                                  # 别叠线程（重试按钮连点两下）
        if self.asset is None:
            return

        self._result = {}
        self._dl_error.hide()
        self.retry_btn.hide()
        self._paint_download_ui(self.asset.name, self.asset.size)
        self._dl_status.setText("正在连接…")
        self._started_at = time.time()

        dest = os.path.join(update_dl.update_dir(), self.asset.name)
        self._thread = _PrepareThread(
            self.asset.url, dest,
            total=self.asset.size,
            sha256=getattr(self.asset, "digest", ""),
            mode=self.mode,
            version=str(getattr(self.info, "latest", "") or ""),
            port_parent=update_dl.app_dir(),
            downloader=self._download,
            parent=self,
        )
        self._thread.progressed.connect(self._on_progress)
        self._thread.staged.connect(self._dl_status.setText)
        self._thread.prepared.connect(self._on_prepared)
        self._thread.failed.connect(self._on_failed)
        self._thread.cancelled.connect(self._on_cancelled)
        self._thread.start()
        self._apply_page_ui(self.currentId())

    def _on_progress(self, done: int, total: int):
        if self._closing:
            return
        if total:
            self.progress.setRange(0, 100)
            self.progress.setValue(min(100, int(done * 100 / total)))
            pct = done * 100 // total
        else:
            self.progress.setRange(0, 0)
            pct = 0
        elapsed = max(0.001, time.time() - self._started_at)
        speed = human_size(done / elapsed) + "/s" if done else ""
        if total:
            self._dl_status.setText(
                f"{pct}%　{human_size(done)} / {human_size(total)}　{speed}")
        else:
            self._dl_status.setText(f"{human_size(done)}　{speed}")

    def _on_prepared(self, result: dict):
        if self._closing:
            return
        self._result = dict(result or {})
        self.progress.setRange(0, 100)
        self.progress.setValue(100)
        self._dl_status.setText(
            "已下载（本地已有相同的文件，直接复用）" if self._result.get("reused")
            else "下载完成")
        nxt = self.button(QWizard.NextButton)
        if nxt is not None:
            nxt.setEnabled(True)
        self.next()                                 # 自动进完成页

    def _on_failed(self, message: str):
        if self._closing:
            return
        self._dl_error.setText(message)
        self._dl_error.show()
        self._dl_status.setText("下载没有完成")
        self.retry_btn.show()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)

    def _on_cancelled(self):
        if self._closing:
            return
        self._result = {}
        self._dl_status.setText("已取消下载")
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.setCurrentId(PAGE_INTRO)               # 退回第一页，用户想更新再点一次

    # ------------------------------------------------------------ 收尾

    def _open_release_page(self):
        QDesktopServices.openUrl(QUrl(getattr(self.info, "page_url", "") or ""))

    def accept(self):
        """点「完成」。安装版在这里把安装器交出去并请求主窗口退出。"""
        if self.asset is None:
            super().accept()
            return
        if self.mode == update_dl.MODE_INSTALLER:
            path = self._result.get("path") or ""
            if not path or not os.path.exists(path):
                QMessageBox.warning(self, "找不到安装包",
                                    "安装包不见了，请重新点一次「检查更新」再试。")
                self.setCurrentId(PAGE_DOWNLOAD)
                self._on_failed("安装包不见了，请点「重新下载」")
                return
            if not update_dl.launch_installer(path):
                QMessageBox.warning(
                    self, "没能启动安装程序",
                    "没能启动安装程序。可以手动双击这个文件完成升级：\n\n"
                    f"{path}")
                return
            # 先关掉向导再请主窗口退出：反过来的话，向导会一直挂在屏幕上
            # 等 `_shutdown()`（等后台线程收尾）跑完，用户看到的是「点了没反应」。
            super().accept()
            self.install_launched.emit()            # 主窗口收到就退出自己
            return
        # 便携版：只是把新解压出来的目录打开，用户自己决定何时换过去
        port_dir = self._result.get("port_dir") or ""
        if port_dir and os.path.isdir(port_dir):
            update_dl.reveal_folder(port_dir)
        super().accept()

    def done(self, result: int):
        """关闭向导前把下载线程收干净 —— 线程还在跑就销毁 QThread 会直接崩。

        另外要摘掉信号连接：线程是 QWizard 的子对象，向导销毁后它可能刚好跑完并
        发信号，那些槽会去碰已经拆掉的界面控件（`_on_prepared` 里还有 `self.next()`），
        轻则报错重则崩。置 `_closing` 并在槽里早退，比指望时序可靠。
        """
        self._closing = True
        th = self._thread
        if th is not None:
            for sig in (th.progressed, th.staged, th.prepared, th.failed, th.cancelled):
                try:
                    sig.disconnect()
                except (RuntimeError, TypeError):
                    pass
            if th.isRunning():
                th.cancel()
                th.wait(5000)
        super().done(result)


__all__ = ["UpdateWizard", "PAGE_INTRO", "PAGE_DOWNLOAD", "PAGE_DONE"]
