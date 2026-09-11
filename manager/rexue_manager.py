import csv
import json
import os
import re
import shutil
import sqlite3
import sys
import threading
import queue
import webbrowser
import tempfile
import urllib.request
import urllib.error
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, messagebox, simpledialog, ttk

APP_DIR = os.path.join(os.path.expanduser('~'), 'RexueManager')
DB_PATH = os.path.join(APP_DIR, 'rexue_manager.db')
os.makedirs(APP_DIR, exist_ok=True)
CALENDAR_ID = os.environ.get('REXUE_CALENDAR_ID', 'primary')
GOOGLE_SCOPES = [
    'https://www.googleapis.com/auth/drive.readonly',
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/calendar.readonly',
]
LINE_API_URL = 'https://rexue-line.cs619et.workers.dev/api/messages'


class NoCredentialRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError('伺服器要求轉址，已停止傳送金鑰。請檢查 Worker 網址與存取設定。')


def fetch_line_messages(token, opener=None):
    token = token.strip()
    if not token or any(ord(c) < 33 or ord(c) > 126 for c in token):
        raise ValueError('金鑰格式無效，請使用 LINE 金鑰設定更新。')
    request = urllib.request.Request(LINE_API_URL, headers={
        'Authorization': f'Bearer {token}',
        'User-Agent': 'RexueManager/1.1',
        'Accept': 'application/json',
    })
    opener = opener or urllib.request.build_opener(NoCredentialRedirect())
    try:
        with opener.open(request, timeout=20) as result:
            raw = result.read(8 * 1024 * 1024 + 1)
            if len(raw) > 8 * 1024 * 1024:
                raise RuntimeError('伺服器資料超過本次讀取上限。')
            try:
                payload = json.loads(raw.decode('utf-8'))
            except (ValueError, UnicodeError):
                raise RuntimeError('伺服器未回傳訊息資料，可能是登入頁面或錯誤頁。')
            if not isinstance(payload, dict) or not isinstance(payload.get('messages'), list):
                raise RuntimeError('API 回應格式不符，請確認線上已部署訊息讀取功能。')
            messages = payload['messages']
            for m in messages:
                if not isinstance(m, dict) or not isinstance(m.get('text_content'), str):
                    raise RuntimeError('API 訊息格式不符，已停止顯示。')
            return messages
    except urllib.error.HTTPError as exc:
        body = exc.read(4096).decode('utf-8', errors='replace')
        if exc.code == 403 and re.search(r'error code\s*:\s*1010\b', body, re.I):
            detail = 'Cloudflare 拒絕此用戶端（1010）。這不是金鑰驗證結果，請檢查雲端存取規則。'
        elif exc.code == 403:
            detail = '存取遭拒（403），尚不能判定是金鑰錯誤；請檢查 Cloudflare 存取規則與伺服器回應。'
        elif exc.code == 401:
            detail = '驗證未通過（401）。請確認目前部署版本的金鑰設定與程式內設定一致。'
        elif exc.code == 404:
            detail = '找不到訊息 API（404）。目前網址未提供 /api/messages；需要確認 Worker 網址與部署內容。重新輸入金鑰無法修正。'
        elif exc.code == 503:
            detail = '伺服器暫時無法讀取資料（503）。請檢查 Worker 資料庫連接。'
        else:
            detail = f'伺服器回應 HTTP {exc.code}，尚未同步成功。'
        raise RuntimeError(detail) from None
    except urllib.error.URLError:
        raise RuntimeError('無法建立安全連線，請檢查網路、代理伺服器或憑證設定。') from None


def application_dir():
    """Return the folder containing the source script or packaged EXE."""
    return os.path.dirname(os.path.abspath(sys.argv[0]))


def clean_event_name(title):
    title = re.sub(r'\s*\(回覆\)\s*$', '', title or '')
    title = re.sub(r'\s*預約拍攝(賽事)?表單\s*$', '', title)
    return title.strip() or '未命名賽事'


def first_value(row, headers, words):
    for i, header in enumerate(headers):
        if any(word in str(header) for word in words) and i < len(row):
            return str(row[i] or '').strip()
    return ''


def normalise_date(value):
    value = str(value or '').strip()
    m = re.search(r'(20\d{2})年\s*(\d{1,2})月\s*(\d{1,2})日', value)
    if m:
        return f'{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}'
    m = re.search(r'(20\d{2})[./-](\d{1,2})[./-](\d{1,2})', value)
    if m:
        return f'{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}'
    return value


def normalise_time(value):
    value = str(value or '').strip().replace(':', '')
    if not value or not value.isdigit():
        return str(value or '')
    value = value.zfill(4)
    return f'{value[:2]}:{value[2:]}'


def split_team_player(value):
    parts = [p.strip() for p in re.split(r'[/／]', str(value or '')) if p.strip()]
    if len(parts) >= 2:
        return parts[0], parts[1], parts[2] if len(parts) >= 3 else ''
    return str(value or '').strip(), '', ''

class App:
    def __init__(self, root):
        self.root = root
        self.root.title('熱血少年｜拍攝工作管理 v1.1')
        self.root.geometry('1180x720')
        self.root.minsize(1000, 620)
        self.db = sqlite3.connect(DB_PATH)
        self.db.row_factory = sqlite3.Row
        self.init_db()
        self.build_ui()
        self.refresh()
        self.line_busy = False
        self.line_results = queue.Queue()
        self.root.after(100, self.poll_line_result)

    def init_db(self):
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL, stage TEXT NOT NULL, start_date TEXT, end_date TEXT,
            venue TEXT, notes TEXT, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS cases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id INTEGER, date TEXT, time TEXT, team TEXT, player TEXT,
            number TEXT, grade TEXT, opponent TEXT, contact TEXT,
            quoted REAL DEFAULT 0, received REAL DEFAULT 0,
            status TEXT DEFAULT '新預約', notes TEXT, created_at TEXT NOT NULL,
            source_key TEXT, source_name TEXT, source_row INTEGER, last_synced_at TEXT,
            FOREIGN KEY(event_id) REFERENCES events(id)
        );
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT, case_id INTEGER, amount REAL,
            paid_date TEXT, note TEXT, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS calendar_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, google_event_id TEXT UNIQUE,
            summary TEXT, start_at TEXT, end_at TEXT, location TEXT,
            calendar_id TEXT, synced_at TEXT
        );
        ''')
        for name, definition in [
            ('source_key', 'TEXT'), ('source_name', 'TEXT'),
            ('source_row', 'INTEGER'), ('last_synced_at', 'TEXT')
        ]:
            try:
                self.db.execute(f'ALTER TABLE cases ADD COLUMN {name} {definition}')
            except sqlite3.OperationalError:
                pass
        self.db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_cases_source_key ON cases(source_key) WHERE source_key IS NOT NULL")
        self.db.commit()

    def build_ui(self):
        style = ttk.Style(); style.theme_use('clam')
        style.configure('Title.TLabel', font=('Microsoft JhengHei', 22, 'bold'))
        style.configure('Card.TLabel', font=('Microsoft JhengHei', 12))
        top = ttk.Frame(self.root, padding=16); top.pack(fill='x')
        ttk.Label(top, text='熱血少年｜拍攝工作管理', style='Title.TLabel').pack(side='left')
        ttk.Button(top, text='同步 Google 資料', command=self.sync_google).pack(side='right', padx=4)
        ttk.Button(top, text='同步 LINE 訊息', command=self.sync_line).pack(side='right', padx=4)
        ttk.Button(top, text='新增賽事', command=self.add_event).pack(side='right', padx=4)
        ttk.Button(top, text='新增預約案件', command=self.add_case).pack(side='right', padx=4)
        ttk.Button(top, text='備份資料', command=self.backup).pack(side='right', padx=4)

        connection_bar = ttk.Frame(self.root, padding=(16, 0, 16, 8))
        connection_bar.pack(fill='x')
        ttk.Button(connection_bar, text='LINE 金鑰設定', command=self.reset_line_token).pack(side='left')
        ttk.Button(connection_bar, text='雲端修復說明', command=self.cloud_help).pack(side='left', padx=8)
        ttk.Label(connection_bar, text='  v1.1｜LINE 連線診斷').pack(side='left')

        cards = ttk.Frame(self.root, padding=(16, 0)); cards.pack(fill='x')
        self.stats = {}
        for key, label in [('events','賽事'),('cases','案件'),('pending','待處理'),('due','待收款')]:
            f = ttk.LabelFrame(cards, text=label, padding=12); f.pack(side='left', fill='x', expand=True, padx=5)
            v = ttk.Label(f, text='0', font=('Microsoft JhengHei', 20, 'bold')); v.pack(); self.stats[key] = v

        body = ttk.Frame(self.root, padding=16); body.pack(fill='both', expand=True)
        left = ttk.LabelFrame(body, text='賽事總覽', padding=8); left.pack(side='left', fill='both', expand=True, padx=(0,8))
        right = ttk.LabelFrame(body, text='案件清單', padding=8); right.pack(side='left', fill='both', expand=True, padx=(8,0))
        self.event_tree = self.tree(left, ['id','賽事','階段','日期','場次','應收','待收'], [0,190,80,125,60,90,90])
        self.case_tree = self.tree(right, ['id','日期','賽事／球隊','球員','金額','已收','狀態'], [0,90,180,130,80,80,100])
        self.event_tree.bind('<<TreeviewSelect>>', lambda e: self.refresh_cases())
        self.case_tree.bind('<Double-1>', lambda e: self.edit_case())
        search = ttk.Frame(right); search.pack(fill='x', pady=(8,0))
        ttk.Label(search, text='搜尋：').pack(side='left')
        self.search_var = tk.StringVar(); ent = ttk.Entry(search, textvariable=self.search_var); ent.pack(side='left', fill='x', expand=True)
        ent.bind('<KeyRelease>', lambda e: self.refresh_cases())
        ttk.Button(search, text='編輯選取案件', command=self.edit_case).pack(side='left', padx=5)
        self.sync_status = ttk.Label(self.root, text='Google 資料尚未同步', foreground='#666')
        self.sync_status.pack(anchor='w', padx=18, pady=(0, 8))

    def cloud_help(self):
        win = tk.Toplevel(self.root)
        win.title('雲端訊息 API 修復')
        win.geometry('640x350')
        ttk.Label(win, text='程式已內附相符的 Worker v1.1。', padding=16).pack(anchor='w')
        ttk.Label(win, text='若連線顯示 404，代表線上網址沒有訊息讀取功能。\n\n1. 按下方按鈕，複製內附程式並開啟 Cloudflare。\n2. 登入後進入 rexue-line → Edit code → 最新版本。\n3. 全選程式碼後貼上，按 Deploy。\n4. 回本程式，使用原有金鑰同步。\n\n這個程式不會自行變更雲端密鑰或移除驗證。', padding=16).pack(anchor='w')
        def copy_worker():
            base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
            try:
                content = open(os.path.join(base, 'rexue-line-worker.mjs'), encoding='utf-8').read()
            except OSError:
                return messagebox.showerror('無法讀取', '內附 Worker 檔案遺失。', parent=win)
            self.root.clipboard_clear()
            self.root.clipboard_append(content)
            webbrowser.open('https://dash.cloudflare.com')
            messagebox.showinfo('已複製', '完整 Worker 程式已複製，可以貼入 Cloudflare。', parent=win)
        ttk.Button(win, text='複製修復程式並開啟 Cloudflare', command=copy_worker).pack(pady=8)

    def sync_line(self):
        if self.line_busy:
            return
        token = self.get_line_token()
        if not token:
            return
        self.line_busy = True
        self.sync_status.config(text='LINE 連線中，請稍候…')
        def fetch():
            try:
                messages = fetch_line_messages(token)
                self.line_results.put((True, messages))
            except Exception as exc:
                self.line_results.put((False, str(exc)))
        threading.Thread(target=fetch, daemon=True).start()

    def poll_line_result(self):
        try:
            success, result = self.line_results.get_nowait()
        except queue.Empty:
            pass
        else:
            self.line_busy = False
            if success:
                self.show_line_messages(result)
                self.sync_status.config(text=f'LINE 最後同步：{datetime.now():%Y-%m-%d %H:%M}｜最新 {len(result)} 則文字訊息')
            else:
                self.sync_status.config(text='LINE 尚未同步成功')
                messagebox.showerror('LINE 連線診斷', result, parent=self.root)
        self.root.after(100, self.poll_line_result)

    def get_line_token(self):
        path = os.path.join(APP_DIR, 'line_api_token.txt')
        if os.path.exists(path):
            return open(path, encoding='utf-8').read().strip()
        token = simpledialog.askstring('LINE API 金鑰', '請貼上 Cloudflare 設定的 EXE_API_TOKEN：', parent=self.root, show='*')
        if token:
            with open(path, 'w', encoding='utf-8') as f: f.write(token.strip())
            return token.strip()
        return None

    def reset_line_token(self):
        if self.line_busy:
            return messagebox.showinfo('請稍候', '目前正在連線，完成後即可修改設定。', parent=self.root)
        token = simpledialog.askstring('LINE 金鑰設定', '請輸入 Cloudflare 的 EXE_API_TOKEN：', parent=self.root, show='*')
        if not token or not token.strip():
            return
        path = os.path.join(APP_DIR, 'line_api_token.txt')
        try:
            with open(path + '.tmp', 'w', encoding='utf-8') as f:
                f.write(token.strip())
            os.replace(path + '.tmp', path)
        except OSError:
            return messagebox.showerror('設定失敗', '無法保存金鑰，請確認資料夾可寫入。', parent=self.root)
        messagebox.showinfo('設定已儲存', '尚未驗證連線，請按「同步 LINE 訊息」。', parent=self.root)

    def show_line_messages(self, messages):
        win = tk.Toplevel(self.root); win.title('LINE 訊息｜熱血少年'); win.geometry('820x520')
        tree = self.tree(win, ['時間', '訊息內容', '狀態'], [160, 530, 90])
        for item in messages:
            ts = item.get('received_at') or item.get('sent_at') or 0
            try: time_text = datetime.fromtimestamp(int(ts) / 1000).strftime('%Y-%m-%d %H:%M')
            except Exception: time_text = str(ts)
            tree.insert('', 'end', values=(time_text, item.get('text_content', ''), '已收到'))

    def tree(self, parent, cols, widths):
        frame = ttk.Frame(parent); frame.pack(fill='both', expand=True)
        t = ttk.Treeview(frame, columns=cols, show='headings', selectmode='browse')
        for c,w in zip(cols,widths): t.heading(c, text=c if c != 'id' else ''); t.column(c, width=w, anchor='w' if c not in ('金額','已收','應收','待收') else 'e')
        sb=ttk.Scrollbar(frame, orient='vertical', command=t.yview); t.configure(yscrollcommand=sb.set); t.pack(side='left',fill='both',expand=True); sb.pack(side='right',fill='y'); return t

    def refresh(self):
        self.refresh_events(); self.refresh_cases(); self.refresh_stats()
    def refresh_stats(self):
        q=lambda sql: self.db.execute(sql).fetchone()[0]
        self.stats['events'].config(text=q('SELECT COUNT(*) FROM events'))
        self.stats['cases'].config(text=q('SELECT COUNT(*) FROM cases'))
        self.stats['pending'].config(text=q("SELECT COUNT(*) FROM cases WHERE status NOT IN ('已收款','完成')"))
        self.stats['due'].config(text=f"${q('SELECT COALESCE(SUM(quoted-received),0) FROM cases'):,.0f}")
    def refresh_events(self):
        for x in self.event_tree.get_children(): self.event_tree.delete(x)
        rows=self.db.execute('''SELECT e.*, COUNT(c.id) n, COALESCE(SUM(c.quoted),0) total,
          COALESCE(SUM(c.quoted-c.received),0) due FROM events e LEFT JOIN cases c ON c.event_id=e.id GROUP BY e.id ORDER BY e.start_date DESC''').fetchall()
        for r in rows: self.event_tree.insert('', 'end', iid=r['id'], values=(r['id'],r['name'],r['stage'],self.date_range(r['start_date'],r['end_date']),r['n'],f"${r['total']:,.0f}",f"${r['due']:,.0f}"))
    def refresh_cases(self):
        for x in self.case_tree.get_children(): self.case_tree.delete(x)
        search=self.search_var.get().strip() if hasattr(self,'search_var') else ''
        event_id=self.event_tree.selection()[0] if hasattr(self,'event_tree') and self.event_tree.selection() else None
        sql='''SELECT c.*, e.name event_name FROM cases c LEFT JOIN events e ON e.id=c.event_id WHERE 1=1'''; args=[]
        if event_id: sql+=' AND c.event_id=?'; args.append(event_id)
        if search: sql+=' AND (c.team LIKE ? OR c.player LIKE ? OR e.name LIKE ? OR c.contact LIKE ?)'; args += [f'%{search}%']*4
        rows=self.db.execute(sql+' ORDER BY c.date DESC,c.time',args).fetchall()
        for r in rows: self.case_tree.insert('', 'end', iid=r['id'], values=(r['id'],r['date'],f"{r['event_name'] or ''}／{r['team']}",f"{r['player']} {r['number']}",f"${r['quoted']:,.0f}",f"${r['received']:,.0f}",r['status']))
    def date_range(self,a,b): return a if not b or a==b else f'{a}–{b}'
    def form(self,title,fields,values=None):
        win=tk.Toplevel(self.root); win.title(title); win.transient(self.root); win.grab_set(); vars={}
        for i,(key,label,kind,options) in enumerate(fields):
            ttk.Label(win,text=label).grid(row=i,column=0,sticky='w',padx=12,pady=6); var=tk.StringVar(value=(values or {}).get(key,'')); vars[key]=var
            if kind=='combo': w=ttk.Combobox(win,textvariable=var,values=options,state='readonly',width=38)
            else: w=ttk.Entry(win,textvariable=var,width=41)
            w.grid(row=i,column=1,padx=12,pady=6)
        return win,vars
    def add_event(self):
        f=[('name','賽事名稱','entry',None),('stage','賽事階段','combo',['分區賽','總決賽']),('start','開始日期 YYYY-MM-DD','entry',None),('end','結束日期（可空白）','entry',None),('venue','地點','entry',None),('notes','備註','entry',None)]
        w,v=self.form('新增賽事',f,{'stage':'分區賽'})
        ttk.Button(w,text='儲存',command=lambda:self.save_event(w,v)).grid(row=len(f),column=1,pady=12,sticky='e')
    def save_event(self,w,v):
        if not v['name'].get().strip(): return messagebox.showwarning('提醒','請輸入賽事名稱',parent=w)
        self.db.execute('INSERT INTO events(name,stage,start_date,end_date,venue,notes,created_at) VALUES(?,?,?,?,?,?,?)',(v['name'].get(),v['stage'].get(),v['start'].get(),v['end'].get() or v['start'].get(),v['venue'].get(),v['notes'].get(),datetime.now().isoformat())); self.db.commit(); w.destroy(); self.refresh()
    def add_case(self):
        events=self.db.execute('SELECT id,name,stage FROM events ORDER BY start_date DESC').fetchall()
        if not events: return messagebox.showinfo('提醒','請先新增賽事')
        labels=[f"{r['id']}｜{r['name']}｜{r['stage']}" for r in events]
        f=[('event','選擇賽事','combo',labels),('date','比賽日期 YYYY-MM-DD','entry',None),('time','比賽時間','entry',None),('team','隊伍名稱','entry',None),('player','球員姓名','entry',None),('number','背號','entry',None),('grade','年級組','entry',None),('opponent','對手／對戰','entry',None),('contact','家長／教練','entry',None),('quoted','應收金額','entry',None),('received','已收金額','entry',None),('status','狀態','combo',['新預約','已確認','已拍攝','已交件','已收款','完成'])]
        w,v=self.form('新增預約案件',f,{'status':'新預約','received':'0'}); v['event'].set(labels[0])
        ttk.Button(w,text='儲存',command=lambda:self.save_case(w,v,events,labels)).grid(row=len(f),column=1,pady=12,sticky='e')
    def save_case(self,w,v,events,labels):
        try: event_id=int(v['event'].get().split('｜')[0]); quoted=float(v['quoted'].get() or 0); received=float(v['received'].get() or 0)
        except: return messagebox.showwarning('提醒','賽事或金額格式不正確',parent=w)
        self.db.execute('INSERT INTO cases(event_id,date,time,team,player,number,grade,opponent,contact,quoted,received,status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',(event_id,v['date'].get(),v['time'].get(),v['team'].get(),v['player'].get(),v['number'].get(),v['grade'].get(),v['opponent'].get(),v['contact'].get(),quoted,received,v['status'].get(),datetime.now().isoformat())); self.db.commit(); w.destroy(); self.refresh()
    def edit_case(self):
        sel=self.case_tree.selection()
        if not sel: return messagebox.showinfo('提醒','請先選擇案件')
        r=self.db.execute('SELECT * FROM cases WHERE id=?',(sel[0],)).fetchone()
        f=[('date','比賽日期','entry',None),('time','比賽時間','entry',None),('team','隊伍名稱','entry',None),('player','球員姓名','entry',None),('number','背號','entry',None),('grade','年級組','entry',None),('opponent','對手／對戰','entry',None),('contact','家長／教練','entry',None),('quoted','應收金額','entry',None),('received','已收金額','entry',None),('status','狀態','combo',['新預約','已確認','已拍攝','已交件','已收款','完成'])]
        w,v=self.form('編輯案件',f,dict(r)); ttk.Button(w,text='儲存',command=lambda:self.update_case(w,v,r['id'])).grid(row=len(f),column=1,pady=12,sticky='e')
    def update_case(self,w,v,cid):
        try: quoted=float(v['quoted'].get() or 0); received=float(v['received'].get() or 0)
        except: return messagebox.showwarning('提醒','金額格式不正確',parent=w)
        self.db.execute('UPDATE cases SET date=?,time=?,team=?,player=?,number=?,grade=?,opponent=?,contact=?,quoted=?,received=?,status=? WHERE id=?',(v['date'].get(),v['time'].get(),v['team'].get(),v['player'].get(),v['number'].get(),v['grade'].get(),v['opponent'].get(),v['contact'].get(),quoted,received,v['status'].get(),cid)); self.db.commit(); w.destroy(); self.refresh()

    def google_credentials(self):
        """Authenticate once in the user's browser and cache the token locally."""
        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            from google_auth_oauthlib.flow import InstalledAppFlow
        except ImportError:
            messagebox.showerror('Google 連線尚未安裝', '請重新執行 Windows 打包檔，讓它安裝 Google 連線元件。')
            return None
        token_path = os.path.join(APP_DIR, 'google_token.json')
        credential_candidates = [
            os.path.join(application_dir(), 'credentials.json'),
            os.path.join(APP_DIR, 'credentials.json'),
        ]
        credential_path = next((p for p in credential_candidates if os.path.exists(p)), None)
        creds = None
        if os.path.exists(token_path):
            try:
                creds = Credentials.from_authorized_user_file(token_path, GOOGLE_SCOPES)
            except Exception:
                creds = None
        try:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            elif not creds or not creds.valid:
                if not credential_path:
                    messagebox.showinfo(
                        '第一次連結 Google',
                        '請將 Google OAuth「桌面應用程式」憑證檔命名為 credentials.json，\n'
                        '放在 EXE 同一個資料夾，再按一次「同步 Google 資料」。\n\n'
                        '只需設定一次；之後會在瀏覽器登入一次，EXE 就能自動同步。'
                    )
                    return None
                flow = InstalledAppFlow.from_client_secrets_file(credential_path, GOOGLE_SCOPES)
                creds = flow.run_local_server(port=0, prompt='consent')
            with open(token_path, 'w', encoding='utf-8') as f:
                f.write(creds.to_json())
            return creds
        except Exception as exc:
            messagebox.showerror('Google 連線失敗', f'{exc}\n\n請重新登入或把錯誤畫面傳給我。')
            return None

    def sync_google(self):
        creds = self.google_credentials()
        if not creds:
            return
        try:
            from googleapiclient.discovery import build
            self.sync_status.config(text='正在同步 Google 預約表單與日曆…')
            self.root.update_idletasks()
            sheets_count, case_count = self.sync_form_sheets(creds)
            calendar_count = self.sync_calendar(creds)
            self.refresh()
            self.sync_status.config(text=f'最後同步：{datetime.now():%Y-%m-%d %H:%M}｜表單 {sheets_count} 份、案件 {case_count} 筆、日曆 {calendar_count} 筆')
            messagebox.showinfo('同步完成', f'已自動匯入 {case_count} 筆預約案件。\n已讀取 {sheets_count} 份預約表單與 {calendar_count} 筆日曆行程。')
        except Exception as exc:
            self.sync_status.config(text='Google 同步失敗，資料未被刪除')
            messagebox.showerror('同步失敗', f'{exc}\n\n原有資料沒有被刪除，請把錯誤畫面傳給我。')

    def sync_form_sheets(self, creds):
        from googleapiclient.discovery import build
        drive = build('drive', 'v3', credentials=creds, cache_discovery=False)
        sheets = build('sheets', 'v4', credentials=creds, cache_discovery=False)
        files, token = [], None
        while True:
            result = drive.files().list(
                q="mimeType='application/vnd.google-apps.spreadsheet' and trashed=false",
                fields='nextPageToken,files(id,name)', pageSize=1000, pageToken=token
            ).execute()
            files.extend(result.get('files', []))
            token = result.get('nextPageToken')
            if not token:
                break
        form_files = [f for f in files if self.is_booking_sheet(f.get('name', ''))]
        imported = 0
        for file in form_files:
            meta = sheets.spreadsheets().get(spreadsheetId=file['id'], fields='sheets(properties(title,index))').execute()
            tabs = sorted(meta.get('sheets', []), key=lambda x: x.get('properties', {}).get('index', 0))
            if not tabs:
                continue
            tab = tabs[0].get('properties', {}).get('title', '')
            values = sheets.spreadsheets().values().get(
                spreadsheetId=file['id'], range=f"'{tab}'!A1:Z2000", valueRenderOption='FORMATTED_VALUE'
            ).execute().get('values', [])
            if len(values) < 2:
                continue
            headers = values[0]
            for row_number, row in enumerate(values[1:], start=2):
                if not any(str(x).strip() for x in row):
                    continue
                if self.import_form_row(file, row_number, file.get('name', ''), headers, row):
                    imported += 1
        self.db.commit()
        return len(form_files), imported

    @staticmethod
    def is_booking_sheet(name):
        text = str(name or '')
        return '預約' in text and any(x in text for x in ('回覆', '表單', '賽事'))

    def import_form_row(self, file, row_number, file_name, headers, row):
        date = normalise_date(first_value(row, headers, ['比賽日期']))
        if not date:
            return False
        time = normalise_time(first_value(row, headers, ['比賽開始時間', '比賽時間']))
        team_value = first_value(row, headers, ['隊伍名稱/球員姓名', '隊伍名稱／球員姓名', '隊伍名稱'])
        team, player, number = split_team_player(team_value)
        grade = first_value(row, headers, ['年級組別', '組別'])
        venue = first_value(row, headers, ['比賽地點', '比賽場地'])
        opponent = first_value(row, headers, ['比賽隊伍名稱', '對戰'])
        contact = first_value(row, headers, ['家長', '教練', '聯絡人', 'LINE'])
        all_text = ' '.join(str(x or '') for x in row)
        event_name = clean_event_name(file_name)
        stage = '總決賽' if '總決賽' in (event_name + all_text) else '分區賽'
        source_key = f"{file.get('id')}:{row_number}"
        now = datetime.now().isoformat()
        event = self.db.execute('SELECT id FROM events WHERE name=?', (event_name,)).fetchone()
        if event:
            event_id = event['id']
            self.db.execute('UPDATE events SET stage=?, venue=COALESCE(NULLIF(venue,\'\'),?), start_date=CASE WHEN start_date IS NULL OR start_date=\'\' THEN ? ELSE start_date END, end_date=CASE WHEN end_date IS NULL OR end_date=\'\' THEN ? ELSE end_date END WHERE id=?', (stage, venue, date, date, event_id))
        else:
            cur = self.db.execute('INSERT INTO events(name,stage,start_date,end_date,venue,notes,created_at) VALUES(?,?,?,?,?,?,?)', (event_name, stage, date, date, venue, '來源：Google 預約表單', now))
            event_id = cur.lastrowid
        existing = self.db.execute('SELECT id FROM cases WHERE source_key=?', (source_key,)).fetchone()
        if existing:
            self.db.execute('''UPDATE cases SET event_id=?,date=?,time=?,team=?,player=?,number=?,grade=?,opponent=?,contact=?,source_name=?,source_row=?,last_synced_at=? WHERE source_key=?''', (event_id,date,time,team,player,number,grade,opponent,contact,file_name,row_number,now,source_key))
        else:
            self.db.execute('''INSERT INTO cases(event_id,date,time,team,player,number,grade,opponent,contact,quoted,received,status,notes,created_at,source_key,source_name,source_row,last_synced_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', (event_id,date,time,team,player,number,grade,opponent,contact,0,0,'新預約','來源：Google 預約表單',now,source_key,file_name,row_number,now))
        return True

    def sync_calendar(self, creds):
        from googleapiclient.discovery import build
        service = build('calendar', 'v3', credentials=creds, cache_discovery=False)
        year = datetime.now().year
        result = service.events().list(
            calendarId=CALENDAR_ID,
            timeMin=f'{year}-01-01T00:00:00+08:00',
            timeMax=f'{year + 1}-01-01T00:00:00+08:00',
            singleEvents=True, orderBy='startTime', maxResults=2500, showDeleted=False
        ).execute()
        now = datetime.now().isoformat()
        for event in result.get('items', []):
            start = event.get('start', {}).get('dateTime') or event.get('start', {}).get('date', '')
            end = event.get('end', {}).get('dateTime') or event.get('end', {}).get('date', '')
            self.db.execute('''INSERT INTO calendar_events(google_event_id,summary,start_at,end_at,location,calendar_id,synced_at) VALUES(?,?,?,?,?,?,?) ON CONFLICT(google_event_id) DO UPDATE SET summary=excluded.summary,start_at=excluded.start_at,end_at=excluded.end_at,location=excluded.location,synced_at=excluded.synced_at''', (event.get('id'), event.get('summary', ''), start, end, event.get('location', ''), CALENDAR_ID, now))
        self.db.commit()
        return len(result.get('items', []))

    def backup(self):
        target=filedialog.asksaveasfilename(title='備份資料',initialfile=f'rexue_backup_{datetime.now():%Y%m%d_%H%M}.db',defaultextension='.db',filetypes=[('Database','*.db')])
        if target: self.db.commit(); shutil.copy2(DB_PATH,target); messagebox.showinfo('完成','資料備份完成')

def self_test(output_path):
    global APP_DIR, DB_PATH
    import io
    from unittest.mock import patch
    class FakeResponse(io.BytesIO):
        pass
    class FakeOpener:
        def open(self, request, timeout):
            assert request.get_header('User-agent') == 'RexueManager/1.1'
            assert request.get_header('Authorization') == 'Bearer local-test'
            return FakeResponse(b'{"messages": [{"text_content": "test", "received_at": 1}]}')
    with tempfile.TemporaryDirectory() as tmp:
        APP_DIR = tmp
        DB_PATH = os.path.join(tmp, 'test.db')
        root = tk.Tk()
        app = App(root)
        root.update()
        app.db.execute("INSERT INTO events(name,stage,created_at) VALUES('test','test','test')")
        app.db.commit()
        app.refresh()
        assert len(app.event_tree.get_children()) == 1
        token_path = os.path.join(APP_DIR, 'line_api_token.txt')
        Path = __import__('pathlib').Path
        Path(token_path).write_text('old-token', encoding='utf-8')
        with patch.object(simpledialog, 'askstring', return_value=None):
            app.reset_line_token()
        assert Path(token_path).read_text() == 'old-token'
        with patch.object(simpledialog, 'askstring', return_value='new-token'), patch.object(messagebox, 'showinfo'):
            app.reset_line_token()
        assert app.get_line_token() == 'new-token'
        app.show_line_messages(fetch_line_messages('local-test', FakeOpener()))
        root.update()
        texts = []
        def visit(w):
            if isinstance(w, ttk.Button): texts.append(w.cget('text'))
            for child in w.winfo_children(): visit(child)
        visit(root)
        assert 'LINE 金鑰設定' in texts and '雲端修復說明' in texts
        assert root.winfo_width() >= 1000
        app.db.close()
        root.destroy()
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump({'ok': True, 'version': '1.1', 'tests': ['GUI startup', 'SQLite persistence', 'cancel preserves token', 'token replacement', 'message display', 'settings buttons'], 'live_line_sync_verified': False}, f)

if __name__=='__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--self-test':
        self_test(sys.argv[2])
    else:
        root=tk.Tk(); App(root); root.mainloop()
