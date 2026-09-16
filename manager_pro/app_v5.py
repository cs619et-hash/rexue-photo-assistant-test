import json, os, sys
from pathlib import Path

from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame

import app_v4 as core

APP_VERSION = '5.0'


def exe_dir():
    try:
        return Path(sys.executable).resolve().parent
    except Exception:
        return Path.cwd()


def bootstrap_existing_config():
    """沿用舊版設定；若沒有設定，只補不敏感且固定的連線資訊。"""
    cfg = core.load_cfg()
    changed = False
    defaults = {
        'line_worker_url': 'https://rexue-line.cs619et.workers.dev/webhook',
        'finance_sheet_id': '1Vy2tyBoxpIrAPTAWnFuemPLYje2Ip8ZvLV1GqRM-fU8',
        'finance_sheet_name': '2026年',
        'google_auto_sync': True,
        'google_sync_minutes': 2,
    }
    for k, v in defaults.items():
        if not cfg.get(k):
            cfg[k] = v
            changed = True

    # 若安裝包旁有 bootstrap.json，僅在本機讀取，不要求使用者再輸入。
    bp = exe_dir() / 'bootstrap.json'
    if bp.exists():
        try:
            data = json.loads(bp.read_text(encoding='utf-8'))
            for k in ('line_worker_url','line_api_token','finance_sheet_id','finance_sheet_name'):
                if data.get(k) and not cfg.get(k):
                    cfg[k] = data[k]
                    changed = True
        except Exception as e:
            core.log('bootstrap read failed: ' + str(e))

    if changed:
        core.save_cfg(cfg)
    return cfg


class StatusCard(QFrame):
    def __init__(self, title):
        super().__init__()
        self.setProperty('class','card')
        lay = QVBoxLayout(self)
        self.title = QLabel(title)
        self.value = QLabel('檢查中…')
        self.value.setStyleSheet('font-size:18px;font-weight:700;padding-top:4px')
        lay.addWidget(self.title)
        lay.addWidget(self.value)


class Main(core.Main):
    def __init__(self):
        self._boot_cfg = bootstrap_existing_config()
        super().__init__()
        self.setWindowTitle(f'熱血少年｜拍攝工作管理中心 PRO v{APP_VERSION}')
        # 把「連線設定」改成一般使用者看得懂的「帳號登入」。
        for b in self.findChildren(QPushButton):
            if b.text() == '連線設定':
                b.setText('帳號登入')
        self.refresh_login_status()

    def startup(self):
        core.find_client_secret()
        self.refresh_all()
        self.status.setText('準備完成。按「登入／開始使用」即可。')
        self.refresh_login_status()

    def settings_page(self):
        w = QWidget(); l = QVBoxLayout(w)
        h = QLabel('帳號登入')
        h.setStyleSheet('font-size:28px;font-weight:800')
        l.addWidget(h)

        desc = QLabel('不用再去 Google Cloud、LINE 或 Cloudflare 找設定。\n這台電腦之前儲存的連線資料會自動沿用。')
        desc.setWordWrap(True)
        desc.setStyleSheet('font-size:16px;padding:8px 0 14px 0')
        l.addWidget(desc)

        row = QHBoxLayout()
        self.google_login_card = StatusCard('Google')
        self.line_login_card = StatusCard('LINE 官方帳號')
        self.finance_login_card = StatusCard('熱血少年收支表')
        row.addWidget(self.google_login_card)
        row.addWidget(self.line_login_card)
        row.addWidget(self.finance_login_card)
        l.addLayout(row)

        self.auth_btn = QPushButton('登入／開始使用')
        self.auth_btn.setObjectName('primary')
        self.auth_btn.setStyleSheet('font-size:20px;font-weight:800;padding:16px')
        self.auth_btn.clicked.connect(self.one_click_login)
        l.addWidget(self.auth_btn)

        self.login_hint = QLabel('第一次使用若 Google 需要重新授權，瀏覽器會自動開啟；登入一次後之後不需要重做。')
        self.login_hint.setWordWrap(True)
        l.addWidget(self.login_hint)
        l.addStretch()
        return w

    def refresh_login_status(self):
        if not hasattr(self, 'google_login_card'):
            return
        try:
            g = core.google_creds(False)
            self.google_login_card.value.setText('✅ 已登入' if g else '需要登入')
        except Exception:
            self.google_login_card.value.setText('需要登入')

        cfg = core.load_cfg()
        if cfg.get('line_worker_url') and cfg.get('line_api_token'):
            self.line_login_card.value.setText('✅ 已帶入')
        elif cfg.get('line_worker_url'):
            self.line_login_card.value.setText('⚠️ 等待舊設定')
        else:
            self.line_login_card.value.setText('尚未連線')

        if cfg.get('finance_sheet_id') and cfg.get('finance_sheet_name'):
            self.finance_login_card.value.setText('✅ 已帶入')
        else:
            self.finance_login_card.value.setText('尚未連線')

    def one_click_login(self):
        if self.auth_busy or self.sync_busy or self.payment_busy:
            self.status.setText('目前正在處理中，請稍候。')
            return

        # 每次按登入都重新從本機舊設定讀取，避免換新版後還要重填。
        self.cfg = bootstrap_existing_config()
        self.refresh_login_status()

        try:
            creds = core.google_creds(False)
        except Exception:
            creds = None

        if creds:
            self.google_state.setText('Google：已授權')
            self.status.setText('帳號已登入，正在自動測試 LINE、Google 與收支表…')
            self.sync_all(auto=False)
        else:
            self.status.setText('正在開啟 Google 登入頁面…')
            self.authorize_google()

    def auth_done(self, result):
        super().auth_done(result)
        self.refresh_login_status()

    def auth_failed(self, err):
        super().auth_failed(err)
        self.refresh_login_status()

    def sync_done(self, msg):
        super().sync_done(msg)
        self.refresh_login_status()
        if 'LINE 尚未連線' not in str(msg) and 'Google 未完成' not in str(msg):
            self.status.setText('✅ 登入完成，系統已自動同步。' + (' ' + str(msg) if msg else ''))


if __name__ == '__main__':
    bootstrap_existing_config()
    core.init_db()
    app = QApplication(sys.argv)
    app.setStyleSheet(core.STYLE)
    m = Main()
    m.show()
    sys.exit(app.exec())
