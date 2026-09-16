import sys, os, sqlite3, json, re, shutil, threading, traceback, hashlib
from pathlib import Path
from datetime import datetime
import requests

from PySide6.QtCore import Qt, QTimer, QThread, Signal
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QStackedWidget, QTableWidget, QTableWidgetItem, QFrame, QLineEdit, QFormLayout,
    QMessageBox, QHeaderView, QFileDialog, QAbstractItemView, QDialog,
    QDialogButtonBox, QComboBox, QSpinBox
)

APP_NAME = '熱血少年｜拍攝工作管理中心 PRO v4.1'
DATA_DIR = Path(os.getenv('APPDATA', str(Path.home()))) / 'RexueManager'
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB = DATA_DIR / 'rexue_manager.db'
CFG = DATA_DIR / 'config.json'
TOKEN_FILE = DATA_DIR / 'google_token.json'
CLIENT_FILE = DATA_DIR / 'client_secret.json'
LOG_FILE = DATA_DIR / 'rexue_manager.log'
DB_LOCK = threading.RLock()

SCOPES = [
    'https://www.googleapis.com/auth/drive.metadata.readonly',
    'https://www.googleapis.com/auth/spreadsheets'
]

STYLE = '''
QWidget{background:#0b1019;color:#eef3fb;font-family:"Microsoft JhengHei";font-size:14px}
QFrame#side{background:#111827;border-right:1px solid #253047}
QLabel#brand{font-size:22px;font-weight:800;padding:12px}
QPushButton{background:#182235;border:1px solid #26354f;border-radius:9px;padding:10px 14px;text-align:left}
QPushButton:hover{background:#21304a}
QPushButton:disabled{color:#708090;background:#111827}
QPushButton#primary{background:#2563eb;border:none;font-weight:700;text-align:center}
QPushButton#good{background:#146c43;border:none;font-weight:700;text-align:center}
QFrame.card{background:#121a29;border:1px solid #263147;border-radius:14px}
QLabel.big{font-size:28px;font-weight:800}
QLineEdit{background:#0f1724;border:1px solid #334155;border-radius:8px;padding:8px}
QTableWidget{background:#0f1724;border:1px solid #263147;border-radius:10px;gridline-color:#263147}
QHeaderView::section{background:#182235;color:#dbe7fb;padding:8px;border:0}
'''


def log(msg):
    try:
        with LOG_FILE.open('a', encoding='utf-8') as f:
            f.write(f'[{datetime.now().isoformat(timespec="seconds")}] {msg}\n')
    except Exception:
        pass


def conn():
    c = sqlite3.connect(DB, timeout=30, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA busy_timeout=30000')
    c.execute('PRAGMA foreign_keys=ON')
    return c


def ensure_col(cur, table, col, decl):
    cols = [r[1] for r in cur.execute(f'PRAGMA table_info({table})').fetchall()]
    if col not in cols:
        cur.execute(f'ALTER TABLE {table} ADD COLUMN {col} {decl}')


def init_db():
    with DB_LOCK:
        backup = DB.with_name('rexue_manager.before-v4.1.db')
        if DB.exists() and not backup.exists():
            source = sqlite3.connect(DB, timeout=30); dest = sqlite3.connect(backup)
            try: source.backup(dest)
            finally: dest.close(); source.close()
        c = conn(); cur = c.cursor()
        cur.executescript('''
        PRAGMA journal_mode=WAL;
        PRAGMA synchronous=NORMAL;
        CREATE TABLE IF NOT EXISTS customers(
          id INTEGER PRIMARY KEY, line_user_id TEXT UNIQUE, display_name TEXT,
          note TEXT DEFAULT '', created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS messages(
          id INTEGER PRIMARY KEY, line_user_id TEXT, direction TEXT DEFAULT 'in', body TEXT,
          created_at TEXT, raw_json TEXT, UNIQUE(line_user_id,created_at,body)
        );
        CREATE TABLE IF NOT EXISTS bookings(
          id INTEGER PRIMARY KEY, source TEXT, source_key TEXT UNIQUE, event_name TEXT, event_date TEXT,
          start_time TEXT, venue TEXT, age_group TEXT, team TEXT, opponent TEXT, player_text TEXT,
          request_raw TEXT, request_type TEXT, priority_numbers TEXT, photographer TEXT,
          booking_status TEXT DEFAULT '待確認', shoot_status TEXT DEFAULT '未拍攝',
          delivery_status TEXT DEFAULT '未交件', amount INTEGER DEFAULT 0, paid INTEGER DEFAULT 0,
          last5 TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS payment_candidates(
          id INTEGER PRIMARY KEY, line_user_id TEXT, amount INTEGER, last5 TEXT, message_id INTEGER,
          status TEXT DEFAULT '待確認', created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS audit_log(
          id INTEGER PRIMARY KEY, action TEXT, detail TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS sync_sources(
          id INTEGER PRIMARY KEY, source_id TEXT UNIQUE, title TEXT, modified_time TEXT, last_sync TEXT
        );
        ''')
        ensure_col(cur, 'payment_candidates', 'matched_booking_id', 'INTEGER')
        ensure_col(cur, 'payment_candidates', 'display_name', 'TEXT')
        ensure_col(cur, 'messages', 'source_key', 'TEXT')
        ensure_col(cur, 'customers', 'custom_name', "TEXT DEFAULT ''")
        cur.execute('CREATE UNIQUE INDEX IF NOT EXISTS messages_source_key ON messages(source_key)')
        c.commit(); c.close()


def load_cfg():
    base = {
        'line_worker_url': '',
        'line_api_token': '',
        'finance_sheet_id': '1Vy2tyBoxpIrAPTAWnFuemPLYje2Ip8ZvLV1GqRM-fU8',
        'finance_sheet_name': '2026年',
        'google_auto_sync': True,
        'google_sync_minutes': 2
    }
    if CFG.exists():
        try:
            base.update(json.loads(CFG.read_text(encoding='utf-8')))
        except Exception:
            pass
    return base


def save_cfg(cfg):
    tmp = CFG.with_suffix('.tmp')
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(tmp, CFG)


def normalize_date(v):
    s = str(v or '').strip()
    m = re.search(r'(\d{4})\D+(\d{1,2})\D+(\d{1,2})', s)
    if m:
        return f'{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}'
    return s


def normalize_time(v):
    s = str(v or '').strip()
    digs = re.sub(r'\D', '', s)
    if ':' in s:
        p = s.split(':')
        if len(p) >= 2 and p[0].isdigit() and p[1][:2].isdigit():
            return f'{int(p[0]):02d}:{int(p[1][:2]):02d}'
    if len(digs) in (3, 4):
        digs = digs.zfill(4)
        return f'{digs[:2]}:{digs[2:4]}'
    return s


def extract_payment(text):
    text = re.sub(r'(?<=\d),(?=\d)', '', str(text or ''))
    amount = None; last5 = None
    for p in [
        r'(?:匯款|轉帳|已付|付款|金額|匯了|轉了)\s*[:：]?\s*(?:NT\$?\s*)?([1-9]\d{2,6})',
        r'(?:NT\$?\s*)([1-9]\d{2,6})'
    ]:
        m = re.search(p, text, re.I)
        if m:
            amount = int(m.group(1)); break
    m = re.search(r'(?:末五碼|後五碼|五碼|末5碼|後5碼)\s*[:：]?\s*(\d{5})', text)
    if m:
        last5 = m.group(1)
    return amount, last5


def find_client_secret():
    if CLIENT_FILE.exists():
        return CLIENT_FILE
    candidates = []
    try:
        candidates.append(Path(sys.executable).resolve().parent / 'client_secret.json')
    except Exception:
        pass
    candidates.append(Path.cwd() / 'client_secret.json')
    for p in candidates:
        if p.exists():
            shutil.copy2(p, CLIENT_FILE)
            return CLIENT_FILE
    return None


def google_creds(interactive=False):
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from google_auth_oauthlib.flow import InstalledAppFlow
    creds = None
    if TOKEN_FILE.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
        except Exception:
            creds = None
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        TOKEN_FILE.write_text(creds.to_json(), encoding='utf-8')
    if creds and creds.valid:
        return creds
    if not interactive:
        return None
    cf = find_client_secret()
    if not cf:
        raise RuntimeError('找不到 Google OAuth 憑證 client_secret.json')
    flow = InstalledAppFlow.from_client_secrets_file(str(cf), SCOPES)
    creds = flow.run_local_server(port=0, open_browser=True, authorization_prompt_message='', timeout_seconds=180)
    TOKEN_FILE.write_text(creds.to_json(), encoding='utf-8')
    return creds


def google_services(interactive=False):
    creds = google_creds(interactive)
    if not creds:
        return None, None
    from googleapiclient.discovery import build
    return (
        build('drive', 'v3', credentials=creds, cache_discovery=False),
        build('sheets', 'v4', credentials=creds, cache_discovery=False)
    )


def clean_event_title(title):
    s = str(title or '')
    for x in [' (回覆)', '(回覆)', '／預約拍攝賽事', '/預約拍攝賽事', '／預約拍攝', '/預約拍攝', '預約表單']:
        s = s.replace(x, '')
    return s.strip(' /／-')


def pick(row, headers, words):
    for i, h in enumerate(headers):
        hs = str(h or '').replace(' ', '')
        if any(w.replace(' ', '') in hs for w in words):
            return row[i] if i < len(row) else ''
    return ''


def parse_form_row(title, sid, rownum, headers, row):
    event_date = pick(row, headers, ['比賽日期', '日期'])
    start_time = pick(row, headers, ['比賽開始時間', '開始時間', '比賽時間'])
    venue = pick(row, headers, ['比賽地點', '場地', '地點'])
    age = pick(row, headers, ['年級組別', '組別', '年級'])
    player = pick(row, headers, ['隊伍名稱/球員姓名/背號', '球員姓名', '隊伍名稱／球員姓名'])
    versus = pick(row, headers, ['比賽隊伍名稱', '對戰', '對手'])
    req = pick(row, headers, ['團體拍攝/指定球員', '拍攝需求', '指定球員', '團體拍攝'])
    ts = pick(row, headers, ['時間戳記', 'Timestamp', '填表時間'])
    team = re.split(r'[/／]', str(player))[0].strip() if player else ''
    raw = str(req or '')
    nums = ','.join(sorted(set(re.findall(r'(?<!\d)(\d{1,2})\s*號?(?!\d)', raw)), key=lambda z: int(z))) if raw else ''
    typ = '指定球員' if any(k in raw for k in ['指定', '號', '球員', '主要', '多拍']) else '團體拍攝'
    return {
        'source_key': f'{sid}:v41:' + hashlib.sha256(str(ts or json.dumps(row,ensure_ascii=False)).encode()).hexdigest(),
        'legacy_suffix': ':' + str(ts or '|'.join(map(str,row))),
        'event_name': clean_event_title(title),
        'event_date': normalize_date(event_date),
        'start_time': normalize_time(start_time),
        'venue': str(venue or ''),
        'age_group': str(age or ''),
        'team': team,
        'opponent': str(versus or ''),
        'player_text': str(player or ''),
        'request_raw': raw,
        'request_type': typ,
        'priority_numbers': nums
    }


class TaskThread(QThread):
    progress = Signal(str)
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, fn):
        super().__init__()
        self.fn = fn

    def run(self):
        try:
            result = self.fn(self.progress.emit)
            self.succeeded.emit(result)
        except Exception:
            err = traceback.format_exc()
            log(err)
            self.failed.emit(err.splitlines()[-1] if err else '未知錯誤')


class Card(QFrame):
    def __init__(self, title, value):
        super().__init__(); self.setProperty('class', 'card')
        l = QVBoxLayout(self)
        a = QLabel(title); self.v = QLabel(str(value)); self.v.setProperty('class', 'big')
        l.addWidget(a); l.addWidget(self.v)


class Main(QMainWindow):
    def __init__(self):
        super().__init__()
        self.cfg = load_cfg()
        self.sync_busy = False
        self.auth_busy = False
        self.worker = None
        self.auth_worker = None
        self.payment_worker = None
        self.payment_busy = False
        self.setWindowTitle(APP_NAME)
        self.resize(1420, 860)

        root = QWidget(); self.setCentralWidget(root)
        outer = QHBoxLayout(root); outer.setContentsMargins(0, 0, 0, 0)
        side = QFrame(); side.setObjectName('side'); side.setFixedWidth(230)
        sl = QVBoxLayout(side)
        b = QLabel('熱血少年\nPRO 管理中心'); b.setObjectName('brand'); sl.addWidget(b)
        self.stack = QStackedWidget()
        pages = [
            ('首頁', self.dashboard), ('LINE 客戶', self.clients_page), ('預約案件', self.bookings_page),
            ('收款確認', self.payments_page), ('連線設定', self.settings_page)
        ]
        for i, (n, f) in enumerate(pages):
            btn = QPushButton(n); btn.clicked.connect(lambda _, x=i: self.stack.setCurrentIndex(x))
            sl.addWidget(btn); self.stack.addWidget(f())
        sl.addStretch()
        self.google_state = QLabel('Google：未檢查'); sl.addWidget(self.google_state)
        self.sync_btn = QPushButton('↻ 全部同步'); self.sync_btn.setObjectName('primary')
        self.sync_btn.clicked.connect(self.sync_all); sl.addWidget(self.sync_btn)
        outer.addWidget(side); outer.addWidget(self.stack, 1)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.auto_sync)
        mins = max(1, int(self.cfg.get('google_sync_minutes', 2)))
        self.timer.start(mins * 60 * 1000)
        QTimer.singleShot(200, self.startup)

    def startup(self):
        find_client_secret()
        self.refresh_all()
        self.google_state.setText('Google：待同步驗證' if TOKEN_FILE.exists() else 'Google：待授權')
        QTimer.singleShot(1000, self.auto_sync)

    def dashboard(self):
        w = QWidget(); l = QVBoxLayout(w)
        title = QLabel('今日工作總覽'); title.setStyleSheet('font-size:26px;font-weight:800'); l.addWidget(title)
        row = QHBoxLayout(); self.cards = {}
        for k, t in [('today','今日拍攝'),('unpaid','待收款'),('undelivered','未交件'),('messages','LINE訊息')]:
            c = Card(t, '0'); self.cards[k] = c; row.addWidget(c)
        l.addLayout(row)
        self.status = QLabel('系統就緒'); self.status.setWordWrap(True); l.addWidget(self.status)
        l.addStretch(); return w

    def table_page(self, headers):
        w = QWidget(); l = QVBoxLayout(w)
        t = QTableWidget(0, len(headers)); t.setHorizontalHeaderLabels(headers)
        t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        t.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        l.addWidget(t); return w, t, l

    def clients_page(self):
        self.clientw, self.clientt, l = self.table_page(['LINE姓名','LINE User ID','備註','建立時間'])
        bar = QHBoxLayout()
        for label, fn in [('查看聊天訊息', self.show_messages), ('編輯本機姓名／備註', self.edit_client)]:
            b = QPushButton(label); b.clicked.connect(fn); bar.addWidget(b)
        l.insertLayout(0, bar)
        self.clientt.cellDoubleClicked.connect(lambda *_: self.show_messages())
        return self.clientw

    def bookings_page(self):
        self.bookw, self.bookt, l = self.table_page(['日期','時間','賽事','隊伍/球員','需求','攝影師','預約','收款','交件'])
        b = QPushButton('編輯選取案件／金額／交件'); b.clicked.connect(self.edit_booking); l.insertWidget(0,b)
        self.bookt.cellDoubleClicked.connect(lambda *_: self.edit_booking())
        return self.bookw

    def payments_page(self):
        self.payw, self.payt, l = self.table_page(['ID','LINE姓名','金額','末五碼','自動配對','狀態','建立時間'])
        bar = QHBoxLayout()
        b = QPushButton('✓ 確認選取收款'); b.setObjectName('good'); b.clicked.connect(self.confirm_payment)
        x = QPushButton('忽略選取'); x.clicked.connect(self.ignore_payment)
        bar.addWidget(b); bar.addWidget(x); l.insertLayout(0, bar)
        return self.payw

    def settings_page(self):
        w = QWidget(); l = QVBoxLayout(w)
        h = QLabel('連線與授權'); h.setStyleSheet('font-size:24px;font-weight:800'); l.addWidget(h)
        auth = QHBoxLayout()
        self.auth_btn = QPushButton('連接 Google 帳號'); self.auth_btn.setObjectName('primary'); self.auth_btn.clicked.connect(self.authorize_google)
        imp = QPushButton('匯入 OAuth JSON'); imp.clicked.connect(self.import_oauth)
        auth.addWidget(self.auth_btn); auth.addWidget(imp); l.addLayout(auth)
        f = QFormLayout(); self.ed = {}
        fields = [
            ('LINE Worker / Webhook 網址','line_worker_url'), ('LINE API Token','line_api_token'),
            ('收支 Sheet ID','finance_sheet_id'), ('收支工作表','finance_sheet_name')
        ]
        for name, key in fields:
            e = QLineEdit(str(self.cfg.get(key, '')))
            e.setEchoMode(QLineEdit.EchoMode.Password if 'Token' in name else QLineEdit.EchoMode.Normal)
            self.ed[key] = e; f.addRow(name, e)
        l.addLayout(f)
        s = QPushButton('儲存設定'); s.setObjectName('primary'); s.clicked.connect(self.save_settings); l.addWidget(s)
        note = QLabel('Google 授權後會自動搜尋名稱包含「預約」的 Google 試算表。同步在背景執行，不會再卡住畫面。')
        note.setWordWrap(True); l.addWidget(note); l.addStretch(); return w

    def import_oauth(self):
        p, _ = QFileDialog.getOpenFileName(self, '選擇 Google OAuth JSON', '', 'JSON (*.json)')
        if not p: return
        try:
            data = json.loads(Path(p).read_text(encoding='utf-8'))
            if 'installed' not in data:
                raise ValueError('這不是 Desktop app OAuth JSON')
            shutil.copy2(p, CLIENT_FILE)
            QMessageBox.information(self, '完成', 'OAuth 憑證已放入本機。接著按「連接 Google 帳號」。')
        except Exception as e:
            QMessageBox.critical(self, '匯入失敗', str(e))

    def authorize_google(self):
        if self.auth_busy or self.sync_busy or self.payment_busy:
            self.status.setText('請等目前同步完成後再授權。')
            return
        self.auth_busy = True
        self.auth_btn.setEnabled(False)
        self.status.setText('Google：正在開啟瀏覽器，等待你按允許…')

        def job(progress):
            progress('Google：正在等待瀏覽器授權…')
            google_creds(True)
            progress('Google：授權成功，正在測試 Drive / Sheets…')
            drive, sheets = google_services(False)
            drive.files().list(pageSize=1, fields='files(id)').execute()
            sid = self.cfg.get('finance_sheet_id')
            tab = self.cfg.get('finance_sheet_name', '2026年')
            sheets.spreadsheets().values().get(spreadsheetId=sid, range=f"'{tab}'!A1:F2").execute()
            return True

        self.auth_worker = TaskThread(job)
        self.auth_worker.progress.connect(self.status.setText)
        self.auth_worker.succeeded.connect(self.auth_done)
        self.auth_worker.failed.connect(self.auth_failed)
        self.auth_worker.start()

    def auth_done(self, _):
        self.auth_busy = False; self.auth_btn.setEnabled(True)
        self.google_state.setText('Google：已授權')
        self.status.setText('Google 授權與讀取測試成功。')
        QMessageBox.information(self, 'Google 已連線', '授權成功，Google Drive 與收支表讀取測試也通過。')
        self.sync_all()

    def auth_failed(self, err):
        self.auth_busy = False; self.auth_btn.setEnabled(True)
        self.google_state.setText('Google：授權失敗')
        self.status.setText('Google 授權失敗：' + err)
        QMessageBox.critical(self, 'Google 授權失敗', err + f'\n\n詳細紀錄：{LOG_FILE}')

    def save_settings(self):
        for k, e in self.ed.items():
            self.cfg[k] = e.text().strip()
        save_cfg(self.cfg)
        QMessageBox.information(self, '完成', '設定已儲存。')

    def line_api_base(self):
        u = str(self.cfg.get('line_worker_url','')).strip().rstrip('/')
        if not u: return ''
        if u.endswith('/webhook'): u = u[:-8]
        if u.endswith('/api/messages'): u = u[:-13]
        return u

    def auto_sync(self):
        if self.cfg.get('google_auto_sync', True) and not self.sync_busy and not self.auth_busy:
            self.sync_all(auto=True)

    def sync_all(self, auto=False):
        if self.sync_busy or self.auth_busy or self.payment_busy:
            if not auto:
                self.status.setText('同步已在進行中，不會重複啟動。')
            return
        self.sync_busy = True
        self.sync_is_auto = auto
        self.sync_btn.setEnabled(False)
        self.status.setText('同步準備中…')

        def job(progress):
            results = []
            try:
                progress('LINE：正在同步…')
                n = self.sync_line_worker()
                results.append(f'LINE {n} 筆')
            except Exception as e:
                results.append('LINE 尚未連線：' + str(e))
            try:
                progress('Google：正在搜尋預約表…')
                g = self.sync_google_worker(progress)
                results.append(f'Google {g[0]} 份表 / {g[1]} 筆預約')
            except Exception as e:
                results.append('Google 未完成：' + str(e))
            return '；'.join(results)

        self.worker = TaskThread(job)
        self.worker.progress.connect(self.status.setText)
        self.worker.succeeded.connect(self.sync_done)
        self.worker.failed.connect(self.sync_failed)
        self.worker.start()

    def sync_done(self, msg):
        self.sync_busy = False; self.sync_btn.setEnabled(True)
        self.refresh_all()
        self.status.setText('同步結果：' + str(msg))

    def sync_failed(self, err):
        self.sync_busy = False; self.sync_btn.setEnabled(True)
        self.refresh_all()
        self.status.setText('同步失敗：' + err)
        if not getattr(self, 'sync_is_auto', False):
            QMessageBox.critical(self, '同步失敗', err + f'\n\n詳細紀錄：{LOG_FILE}')

    def sync_line_worker(self):
        url = self.line_api_base()
        if not url:
            raise RuntimeError('尚未設定 LINE 網址')
        if not url.startswith('https://'):
            raise RuntimeError('LINE 網址必須使用 https://')
        headers = {}
        if self.cfg.get('line_api_token'):
            headers['Authorization'] = 'Bearer ' + self.cfg['line_api_token']
        r = requests.get(url + '/api/messages', headers=headers, timeout=20, allow_redirects=False)
        if 300 <= r.status_code < 400:
            raise RuntimeError('LINE 網址發生轉址，請確認填的是 Worker 網址')
        r.raise_for_status()
        data = r.json()
        rows = data if isinstance(data, list) else data.get('messages') if isinstance(data, dict) else None
        if not isinstance(rows, list):
            raise RuntimeError('LINE 回傳格式不符，沒有 messages 清單')
        if not rows:
            return 0
        with DB_LOCK:
            c = conn(); cur = c.cursor()
            try:
                added = 0
                for x in rows:
                    if not isinstance(x, dict): continue
                    key = str(x.get('message_key') or x.get('id') or '')
                    if x.get('revoked') in (1, True, '1'):
                        old = cur.execute('SELECT id FROM messages WHERE source_key=?', (key,)).fetchone()
                        if old:
                            cur.execute("UPDATE payment_candidates SET status='已收回' WHERE message_id=? AND status='待確認'", (old['id'],))
                            cur.execute("UPDATE messages SET body='[訊息已收回]',raw_json='{}' WHERE id=?", (old['id'],))
                        continue
                    uid = str(x.get('sender_id') or x.get('userId') or x.get('line_user_id') or x.get('sourceUserId') or '')
                    if not uid: continue
                    name = x.get('displayName') or x.get('display_name') or ''
                    cur.execute("INSERT INTO customers(line_user_id,display_name) VALUES(?,?) ON CONFLICT(line_user_id) DO UPDATE SET display_name=CASE WHEN excluded.display_name!='' THEN excluded.display_name ELSE customers.display_name END", (uid,name))
                    body = str(x.get('text_content') or x.get('text') or x.get('message') or x.get('body') or '')
                    ts = str(x.get('sent_at') or x.get('timestamp') or x.get('created_at') or '')
                    if not ts and not key: continue
                    if ts.isdigit():
                        n = int(ts); ts = datetime.fromtimestamp(n / 1000 if n > 100000000000 else n).isoformat(timespec='seconds')
                    if not key:
                        key = hashlib.sha256(json.dumps([uid,ts,body],ensure_ascii=False).encode()).hexdigest()
                    cur.execute('INSERT OR IGNORE INTO messages(line_user_id,body,created_at,raw_json,source_key) VALUES(?,?,?,?,?)', (uid,body,ts,json.dumps(x,ensure_ascii=False),key))
                    if cur.rowcount:
                        added += 1
                        mid = cur.lastrowid; amt, last5 = extract_payment(body)
                        if amt or last5:
                            match = self.auto_match_booking(cur, amt)
                            cur.execute('INSERT INTO payment_candidates(line_user_id,display_name,amount,last5,message_id,matched_booking_id) VALUES(?,?,?,?,?,?)', (uid,name,amt,last5,mid,match))
                c.commit()
            finally:
                c.close()
        return added

    def auto_match_booking(self, cur, amt):
        if not amt: return None
        rows = cur.execute('SELECT id FROM bookings WHERE paid=0 AND amount=? ORDER BY event_date DESC', (amt,)).fetchall()
        return rows[0]['id'] if len(rows) == 1 else None

    def sync_google_worker(self, progress):
        drive, sheets = google_services(False)
        if not drive or not sheets:
            raise RuntimeError('Google 尚未授權')
        q = "mimeType='application/vnd.google-apps.spreadsheet' and trashed=false and name contains '預約'"
        files = []; token = None
        while True:
            page = drive.files().list(q=q, fields='nextPageToken,files(id,name,modifiedTime)', pageSize=1000, orderBy='modifiedTime desc',pageToken=token).execute()
            files.extend(page.get('files',[])); token=page.get('nextPageToken')
            if not token: break
        total_rows = 0; changed_files = 0
        total = len(files)

        for pos, f in enumerate(files, start=1):
            sid = f['id']; title = f['name']; mt = f.get('modifiedTime','')
            progress(f'Google：檢查 {pos}/{total}｜{title}')

            with DB_LOCK:
                c = conn()
                try:
                    prev = c.execute('SELECT modified_time FROM sync_sources WHERE source_id=?', (sid,)).fetchone()
                finally:
                    c.close()
            if prev and prev['modified_time'] == mt:
                continue

            # 網路讀取時完全不持有 SQLite 交易鎖。
            meta = sheets.spreadsheets().get(spreadsheetId=sid, fields='sheets.properties(title,index)').execute()
            tabs = sorted(meta.get('sheets', []), key=lambda x: (0 if any(k in x['properties']['title'].lower() for k in ['表單回應','表單回覆','form responses']) else 1,x['properties'].get('index',0)))
            if not tabs:
                continue
            tab = tabs[0]['properties']['title']
            vals = sheets.spreadsheets().values().get(spreadsheetId=sid, range=f"'{tab.replace(chr(39),chr(39)*2)}'!A:AZ").execute().get('values', [])
            if not vals:
                continue
            headers = vals[0]
            joined = ' '.join(map(str,headers))
            if not any(k in joined for k in ['日期','時間戳記','Timestamp']) or not any(k in joined for k in ['隊伍','球員','拍攝需求']):
                progress(f'略過非預約格式：{title}'); continue
            parsed = []
            for idx, row in enumerate(vals[1:], start=2):
                if any(str(x).strip() for x in row):
                    parsed.append(parse_form_row(title, sid, idx, headers, row))

            # 每份表一次短交易，寫完立即 commit / close。
            with DB_LOCK:
                c = conn(); cur = c.cursor()
                try:
                    for x in parsed:
                        legacy = cur.execute('SELECT id,source_key FROM bookings WHERE source=? AND source_key LIKE ?',('google_form',sid+':%')).fetchall()
                        old = [b for b in legacy if ':v41:' not in b['source_key'] and b['source_key'].endswith(x['legacy_suffix'])]
                        exists = cur.execute('SELECT id FROM bookings WHERE source_key=?',(x['source_key'],)).fetchone()
                        if len(old)==1 and not exists:
                            cur.execute('UPDATE bookings SET source_key=? WHERE id=?',(x['source_key'],old[0]['id']))
                        cur.execute('''INSERT INTO bookings(source,source_key,event_name,event_date,start_time,venue,age_group,team,opponent,player_text,request_raw,request_type,priority_numbers)
                          VALUES('google_form',?,?,?,?,?,?,?,?,?,?,?,?)
                          ON CONFLICT(source_key) DO UPDATE SET
                          event_name=excluded.event_name,event_date=excluded.event_date,start_time=excluded.start_time,
                          venue=excluded.venue,age_group=excluded.age_group,team=excluded.team,opponent=excluded.opponent,
                          player_text=excluded.player_text,request_raw=excluded.request_raw,request_type=excluded.request_type,
                          priority_numbers=excluded.priority_numbers''',
                          (x['source_key'],x['event_name'],x['event_date'],x['start_time'],x['venue'],x['age_group'],x['team'],x['opponent'],x['player_text'],x['request_raw'],x['request_type'],x['priority_numbers']))
                    cur.execute('''INSERT INTO sync_sources(source_id,title,modified_time,last_sync) VALUES(?,?,?,?)
                      ON CONFLICT(source_id) DO UPDATE SET title=excluded.title,modified_time=excluded.modified_time,last_sync=excluded.last_sync''',
                      (sid,title,mt,datetime.now().isoformat(timespec='seconds')))
                    c.commit()
                except Exception:
                    c.rollback(); raise
                finally:
                    c.close()
            total_rows += len(parsed); changed_files += 1
        return changed_files, total_rows

    def selected_payment_id(self):
        r = self.payt.currentRow()
        if r < 0: return None
        it = self.payt.item(r, 0)
        return int(it.text()) if it and it.text().isdigit() else None

    def show_messages(self):
        row = self.clientt.currentRow()
        if row < 0: return
        uid = self.clientt.item(row,1).text()
        c = conn()
        try:
            rows = c.execute('SELECT created_at,body FROM messages WHERE line_user_id=? ORDER BY created_at DESC', (uid,)).fetchall()
        finally: c.close()
        d = QDialog(self); d.setWindowTitle('LINE 聊天紀錄'); d.resize(900,600)
        l = QVBoxLayout(d)
        t = QTableWidget(0,2); t.setHorizontalHeaderLabels(['時間','訊息'])
        t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        t.horizontalHeader().setSectionResizeMode(1,QHeaderView.ResizeMode.Stretch)
        l.addWidget(t); self.fill(t,rows); t.resizeRowsToContents(); d.exec()

    def edit_client(self):
        row = self.clientt.currentRow()
        if row < 0: return
        uid = self.clientt.item(row,1).text()
        d = QDialog(self); d.setWindowTitle('本機客戶姓名與備註'); f = QFormLayout(d)
        name = QLineEdit(self.clientt.item(row,0).text()); note = QLineEdit(self.clientt.item(row,2).text())
        f.addRow('本機顯示姓名',name); f.addRow('本機備註',note)
        f.addRow(QLabel('只儲存在這部電腦，不會修改 LINE 官方後台。'))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(d.accept); buttons.rejected.connect(d.reject); f.addRow(buttons)
        if d.exec() != QDialog.DialogCode.Accepted: return
        with DB_LOCK:
            c = conn()
            try:
                c.execute('UPDATE customers SET custom_name=?,note=? WHERE line_user_id=?',(name.text().strip(),note.text(),uid)); c.commit()
            finally: c.close()
        self.refresh_all()

    def edit_booking(self):
        row = self.bookt.currentRow()
        if row < 0: return
        bid = self.bookt.item(row,0).data(Qt.ItemDataRole.UserRole)
        c = conn()
        try: b = c.execute('SELECT * FROM bookings WHERE id=?',(bid,)).fetchone()
        finally: c.close()
        if not b: return
        d = QDialog(self); d.setWindowTitle('編輯案件'); f = QFormLayout(d)
        fields = {}
        for key,label in [('photographer','攝影師'),('booking_status','預約狀態'),('shoot_status','拍攝狀態'),('delivery_status','交件狀態')]:
            e = QLineEdit(b[key] or ''); fields[key]=e; f.addRow(label,e)
        amount = QSpinBox(); amount.setRange(0,10000000); amount.setValue(b['amount'] or 0); f.addRow('應收金額',amount)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(d.accept); buttons.rejected.connect(d.reject); f.addRow(buttons)
        if d.exec() != QDialog.DialogCode.Accepted: return
        with DB_LOCK:
            c=conn()
            try:
                c.execute('UPDATE bookings SET photographer=?,booking_status=?,shoot_status=?,delivery_status=?,amount=? WHERE id=?',tuple(e.text() for e in fields.values())+(amount.value(),bid)); c.commit()
            finally: c.close()
        self.refresh_all()

    def closeEvent(self,event):
        if any(w is not None and w.isRunning() for w in (self.worker,self.auth_worker,self.payment_worker)):
            self.status.setText('同步／授權尚未結束，完成後即可關閉。'); event.ignore(); return
        event.accept()

    def confirm_payment(self):
        if self.payment_busy or self.sync_busy or self.auth_busy:
            self.status.setText('請等目前工作完成後再確認收款。'); return
        pid = self.selected_payment_id()
        if not pid:
            return QMessageBox.information(self, '請選擇', '請先點選一筆待確認收款。')

        with DB_LOCK:
            c = conn()
            try:
                p = c.execute('SELECT * FROM payment_candidates WHERE id=?', (pid,)).fetchone()
                if not p: return
                if p['status'] != '待確認':
                    return QMessageBox.warning(self,'不可重複入帳','這筆不是待確認狀態。若顯示需核對，請先檢查收支表，不要重送。')
                bid = p['matched_booking_id']
                candidates = c.execute('SELECT * FROM bookings WHERE paid=0 ORDER BY event_date DESC').fetchall()
                if not candidates:
                    return QMessageBox.warning(self,'沒有案件','請先同步預約表，再編輯案件的應收金額。')
                d = QDialog(self); d.setWindowTitle('確認收款對應案件'); f = QFormLayout(d)
                combo = QComboBox()
                for candidate in candidates:
                    combo.addItem(f"{candidate['event_date']} {candidate['event_name']}｜{candidate['player_text'] or candidate['team']}｜應收 {candidate['amount']}",candidate['id'])
                if bid and combo.findData(bid)>=0: combo.setCurrentIndex(combo.findData(bid))
                else: combo.setCurrentIndex(-1)
                f.addRow('案件',combo); f.addRow(QLabel(f"金額：{p['amount']}；末五碼：{p['last5'] or ''}\n請核對實際入款。本程式無法查詢銀行。"))
                buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
                buttons.accepted.connect(d.accept); buttons.rejected.connect(d.reject); f.addRow(buttons)
                if d.exec()!=QDialog.DialogCode.Accepted or combo.currentIndex()<0: return
                bid=combo.currentData()
                b = c.execute('SELECT * FROM bookings WHERE id=?', (bid,)).fetchone()
                if not p['amount'] or p['amount'] != b['amount']:
                    return QMessageBox.warning(self,'金額需核對','此版只處理足額收款。請先核對案件應收金額與實際匯款金額。')
                p_copy = dict(p); b_copy = dict(b)
            finally:
                c.close()

        with DB_LOCK:
            c=conn()
            try:
                c.execute("UPDATE payment_candidates SET status='寫入中／需核對',matched_booking_id=? WHERE id=? AND status='待確認'",(bid,pid)); c.commit()
            finally: c.close()
        self.payment_busy=True
        def job(progress):
            progress('收款：正在寫入收支表…')
            self.append_finance(b_copy,p_copy)
            with DB_LOCK:
                c=conn()
                try:
                    c.execute('UPDATE bookings SET paid=1,last5=? WHERE id=?',(p_copy.get('last5'),bid))
                    c.execute("UPDATE payment_candidates SET status='已確認' WHERE id=?",(pid,))
                    c.execute("INSERT INTO audit_log(action,detail) VALUES('confirm_payment',?)",(f'payment={pid}, booking={bid}',)); c.commit()
                finally: c.close()
            return True
        self.payment_worker=TaskThread(job)
        self.payment_worker.progress.connect(self.status.setText)
        self.payment_worker.succeeded.connect(self.payment_done)
        self.payment_worker.failed.connect(self.payment_failed)
        self.payment_worker.start()

    def payment_done(self,_):
        self.payment_busy=False; self.refresh_all()
        QMessageBox.information(self,'完成','已確認收款，並寫入收支表。')

    def payment_failed(self,err):
        self.payment_busy=False; self.refresh_all()
        QMessageBox.warning(self,'需核對收支表','連線中斷或寫入失敗。為避免重複入帳，此筆已暫停重送；請核對收支表。\n'+err)
    def append_finance(self, b, p):
        _, sheets = google_services(False)
        if not sheets: raise RuntimeError('Google 尚未授權')
        sid = self.cfg.get('finance_sheet_id'); tab = self.cfg.get('finance_sheet_name','2026年')
        if not sid: raise RuntimeError('找不到收支表 ID')
        team = b.get('team') or ''
        sponsor = b.get('player_text') or p.get('display_name') or ''
        vals = [[b.get('event_name') or '', b.get('event_date') or '', team, sponsor, int(p.get('amount') or b.get('amount') or 0), p.get('last5') or '']]
        sheets.spreadsheets().values().append(
            spreadsheetId=sid, range=f"'{tab.replace(chr(39), chr(39)*2)}'!A:F", valueInputOption='RAW',
            insertDataOption='INSERT_ROWS', body={'values': vals}
        ).execute()

    def ignore_payment(self):
        if self.payment_busy: return
        pid = self.selected_payment_id()
        if not pid: return
        with DB_LOCK:
            c = conn()
            try:
                c.execute("UPDATE payment_candidates SET status='忽略' WHERE id=? AND status='待確認'", (pid,)); c.commit()
            finally:
                c.close()
        self.refresh_all()

    def refresh_all(self):
        try:
            with DB_LOCK:
                c = conn(); cur = c.cursor(); today = datetime.now().strftime('%Y-%m-%d')
                try:
                    vals = {
                        'today': cur.execute('SELECT COUNT(*) FROM bookings WHERE event_date=?', (today,)).fetchone()[0],
                        'unpaid': cur.execute('SELECT COALESCE(SUM(amount),0) FROM bookings WHERE paid=0').fetchone()[0],
                        'undelivered': cur.execute("SELECT COUNT(*) FROM bookings WHERE delivery_status!='已交件'").fetchone()[0],
                        'messages': cur.execute('SELECT COUNT(*) FROM messages').fetchone()[0]
                    }
                    clients = cur.execute("SELECT COALESCE(NULLIF(custom_name,''),NULLIF(display_name,''),'尚未取得名稱'),line_user_id,note,created_at FROM customers ORDER BY id DESC LIMIT 500").fetchall()
                    bookings = cur.execute("""SELECT event_date,start_time,event_name,COALESCE(NULLIF(player_text,''),team),
                      request_type||CASE WHEN priority_numbers!='' THEN ' #'||priority_numbers ELSE '' END,
                      photographer,booking_status,CASE WHEN paid=1 THEN '已收' ELSE '未收' END,delivery_status
                      ,id FROM bookings ORDER BY event_date,start_time""").fetchall()
                    pays = cur.execute("""SELECT p.id,p.display_name,p.amount,p.last5,
                      CASE WHEN p.matched_booking_id IS NULL THEN '未唯一配對' ELSE
                      (SELECT event_name||' / '||COALESCE(NULLIF(player_text,''),team) FROM bookings b WHERE b.id=p.matched_booking_id) END,
                      p.status,p.created_at FROM payment_candidates p ORDER BY p.id DESC LIMIT 500""").fetchall()
                finally:
                    c.close()
            for k, v in vals.items():
                self.cards[k].v.setText(f'NT$ {v:,}' if k == 'unpaid' else str(v))
            self.fill(self.clientt, clients); self.fill(self.bookt, [tuple(b)[:-1] for b in bookings]); self.fill(self.payt, pays)
            for i,b in enumerate(bookings): self.bookt.item(i,0).setData(Qt.ItemDataRole.UserRole,b['id'])
        except sqlite3.OperationalError as e:
            log('refresh_all sqlite error: ' + str(e))
            self.status.setText('資料庫忙碌，稍後會自動重試。')
            QTimer.singleShot(1000, self.refresh_all)

    def fill(self, t, rows):
        t.setRowCount(len(rows))
        for i, r in enumerate(rows):
            for j, v in enumerate(r):
                t.setItem(i, j, QTableWidgetItem('' if v is None else str(v)))


if __name__ == '__main__':
    if '--self-test' in sys.argv:
        import unittest
        from test_pro import CoreTests
        import io
        result=unittest.TextTestRunner(stream=sys.stderr or io.StringIO(),verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(CoreTests))
        if '--test-report' in sys.argv:
            Path(sys.argv[sys.argv.index('--test-report')+1]).write_text(json.dumps({'version':'4.1','ok':result.wasSuccessful(),'tests':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),'live_services_tested':False}),encoding='utf-8')
        sys.exit(0 if result.wasSuccessful() else 1)
    init_db()
    app = QApplication(sys.argv); app.setStyleSheet(STYLE)
    m = Main(); m.show(); sys.exit(app.exec())
