import sys, os, sqlite3, json, re, shutil, traceback
from pathlib import Path
from datetime import datetime
import requests
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,QMainWindow,QWidget,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,
    QStackedWidget,QTableWidget,QTableWidgetItem,QFrame,QLineEdit,QFormLayout,
    QMessageBox,QHeaderView,QFileDialog,QAbstractItemView
)

APP_NAME='熱血少年｜拍攝工作管理中心 PRO'
DATA_DIR=Path(os.getenv('APPDATA',str(Path.home()))) / 'RexueManager'
DATA_DIR.mkdir(parents=True,exist_ok=True)
DB=DATA_DIR/'rexue_manager.db'
CFG=DATA_DIR/'config.json'
TOKEN_FILE=DATA_DIR/'google_token.json'
CLIENT_FILE=DATA_DIR/'client_secret.json'
SCOPES=[
    'https://www.googleapis.com/auth/drive.metadata.readonly',
    'https://www.googleapis.com/auth/spreadsheets'
]

STYLE='''QWidget{background:#0b1019;color:#eef3fb;font-family:"Microsoft JhengHei";font-size:14px}
QFrame#side{background:#111827;border-right:1px solid #253047}
QLabel#brand{font-size:22px;font-weight:800;padding:12px}
QPushButton{background:#182235;border:1px solid #26354f;border-radius:9px;padding:10px 14px;text-align:left}
QPushButton:hover{background:#21304a}
QPushButton#primary{background:#2563eb;border:none;font-weight:700;text-align:center}
QPushButton#good{background:#146c43;border:none;font-weight:700;text-align:center}
QFrame.card{background:#121a29;border:1px solid #263147;border-radius:14px}
QLabel.big{font-size:28px;font-weight:800}
QLineEdit{background:#0f1724;border:1px solid #334155;border-radius:8px;padding:8px}
QTableWidget{background:#0f1724;border:1px solid #263147;border-radius:10px;gridline-color:#263147}
QHeaderView::section{background:#182235;color:#dbe7fb;padding:8px;border:0}'''

def conn():
    c=sqlite3.connect(DB); c.row_factory=sqlite3.Row; return c

def ensure_col(cur, table, col, decl):
    cols=[r[1] for r in cur.execute(f'PRAGMA table_info({table})').fetchall()]
    if col not in cols: cur.execute(f'ALTER TABLE {table} ADD COLUMN {col} {decl}')

def init_db():
    c=conn(); cur=c.cursor()
    cur.executescript('''
    PRAGMA journal_mode=WAL;
    CREATE TABLE IF NOT EXISTS customers(id INTEGER PRIMARY KEY, line_user_id TEXT UNIQUE, display_name TEXT, note TEXT DEFAULT '', created_at TEXT DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY, line_user_id TEXT, direction TEXT DEFAULT 'in', body TEXT, created_at TEXT, raw_json TEXT, UNIQUE(line_user_id,created_at,body));
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
    CREATE TABLE IF NOT EXISTS audit_log(id INTEGER PRIMARY KEY, action TEXT, detail TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS sync_sources(id INTEGER PRIMARY KEY, source_id TEXT UNIQUE, title TEXT, modified_time TEXT, last_sync TEXT);
    ''')
    ensure_col(cur,'payment_candidates','matched_booking_id','INTEGER')
    ensure_col(cur,'payment_candidates','display_name','TEXT')
    c.commit(); c.close()

def load_cfg():
    base={
      'line_worker_url':'',
      'line_api_token':'',
      'finance_sheet_id':'1Vy2tyBoxpIrAPTAWnFuemPLYje2Ip8ZvLV1GqRM-fU8',
      'finance_sheet_name':'2026年',
      'google_auto_sync':True,
      'google_sync_minutes':2
    }
    if CFG.exists():
        try: base.update(json.loads(CFG.read_text(encoding='utf-8')))
        except: pass
    return base

def save_cfg(cfg): CFG.write_text(json.dumps(cfg,ensure_ascii=False,indent=2),encoding='utf-8')

def normalize_date(v):
    s=str(v or '').strip()
    m=re.search(r'(\d{4})\D+(\d{1,2})\D+(\d{1,2})',s)
    if m:return f'{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}'
    return s

def normalize_time(v):
    s=str(v or '').strip()
    digs=re.sub(r'\D','',s)
    if ':' in s:
        p=s.split(':')
        if len(p)>=2 and p[0].isdigit() and p[1][:2].isdigit(): return f'{int(p[0]):02d}:{int(p[1][:2]):02d}'
    if len(digs) in (3,4):
        digs=digs.zfill(4); return f'{digs[:2]}:{digs[2:4]}'
    return s

def extract_payment(text):
    text=str(text or '')
    amount=None; last5=None
    patterns=[
      r'(?:匯款|轉帳|已付|付款|金額|匯了|轉了)\s*[:：]?\s*(?:NT\$?\s*)?([1-9]\d{2,6})',
      r'(?:NT\$?\s*)([1-9]\d{2,6})'
    ]
    for p in patterns:
        m=re.search(p,text,re.I)
        if m: amount=int(m.group(1)); break
    m=re.search(r'(?:末五碼|後五碼|五碼|末5碼|後5碼)\s*[:：]?\s*(\d{5})',text)
    if m:last5=m.group(1)
    return amount,last5

def find_client_secret():
    if CLIENT_FILE.exists(): return CLIENT_FILE
    candidates=[]
    try:candidates.append(Path(sys.executable).resolve().parent/'client_secret.json')
    except:pass
    candidates += [Path.cwd()/'client_secret.json']
    for p in candidates:
        if p.exists():
            shutil.copy2(p,CLIENT_FILE)
            return CLIENT_FILE
    return None

def google_creds(interactive=False):
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from google_auth_oauthlib.flow import InstalledAppFlow
    except Exception as e:
        raise RuntimeError('Google 元件未安裝完整：'+str(e))
    creds=None
    if TOKEN_FILE.exists():
        try:creds=Credentials.from_authorized_user_file(str(TOKEN_FILE),SCOPES)
        except:creds=None
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request()); TOKEN_FILE.write_text(creds.to_json(),encoding='utf-8')
    if creds and creds.valid:return creds
    if not interactive:return None
    cf=find_client_secret()
    if not cf: raise RuntimeError('找不到 Google OAuth 憑證 client_secret.json')
    flow=InstalledAppFlow.from_client_secrets_file(str(cf),SCOPES)
    creds=flow.run_local_server(port=0,open_browser=True,authorization_prompt_message='')
    TOKEN_FILE.write_text(creds.to_json(),encoding='utf-8')
    return creds

def google_services(interactive=False):
    creds=google_creds(interactive)
    if not creds:return None,None
    from googleapiclient.discovery import build
    return build('drive','v3',credentials=creds,cache_discovery=False), build('sheets','v4',credentials=creds,cache_discovery=False)

def clean_event_title(title):
    s=str(title or '')
    for x in [' (回覆)','(回覆)','／預約拍攝賽事','/預約拍攝賽事','／預約拍攝','/預約拍攝','預約表單']:
        s=s.replace(x,'')
    return s.strip(' /／-')

def pick(row, headers, words):
    for i,h in enumerate(headers):
        hs=str(h or '').replace(' ','')
        if any(w.replace(' ','') in hs for w in words):
            return row[i] if i<len(row) else ''
    return ''

def parse_form_row(title, sid, rownum, headers, row):
    event_date=pick(row,headers,['比賽日期','日期'])
    start_time=pick(row,headers,['比賽開始時間','開始時間','比賽時間'])
    venue=pick(row,headers,['比賽地點','場地','地點'])
    age=pick(row,headers,['年級組別','組別','年級'])
    player=pick(row,headers,['隊伍名稱/球員姓名/背號','球員姓名','隊伍名稱／球員姓名'])
    versus=pick(row,headers,['比賽隊伍名稱','對戰','對手'])
    req=pick(row,headers,['團體拍攝/指定球員','拍攝需求','指定球員','團體拍攝'])
    ts=pick(row,headers,['時間戳記','Timestamp','填表時間'])
    team=''
    if player:
        team=re.split(r'[/／]',str(player))[0].strip()
    opp=str(versus or '')
    raw=str(req or '')
    nums=','.join(sorted(set(re.findall(r'(?<!\d)(\d{1,2})\s*號?(?!\d)',raw)),key=lambda z:int(z))) if raw else ''
    typ='指定球員' if any(k in raw for k in ['指定','號','球員','主要','多拍']) else '團體拍攝'
    return {
      'source_key':f'{sid}:{rownum}:{ts or "|".join(map(str,row))}',
      'event_name':clean_event_title(title),'event_date':normalize_date(event_date),
      'start_time':normalize_time(start_time),'venue':str(venue or ''),'age_group':str(age or ''),
      'team':team,'opponent':opp,'player_text':str(player or ''),'request_raw':raw,
      'request_type':typ,'priority_numbers':nums
    }

class Card(QFrame):
    def __init__(self,title,value):
        super().__init__(); self.setProperty('class','card')
        l=QVBoxLayout(self); a=QLabel(title); self.v=QLabel(str(value)); self.v.setProperty('class','big')
        l.addWidget(a); l.addWidget(self.v)

class Main(QMainWindow):
    def __init__(self):
        super().__init__(); self.cfg=load_cfg(); self.setWindowTitle(APP_NAME); self.resize(1420,860)
        root=QWidget(); self.setCentralWidget(root); outer=QHBoxLayout(root); outer.setContentsMargins(0,0,0,0)
        side=QFrame(); side.setObjectName('side'); side.setFixedWidth(230); sl=QVBoxLayout(side)
        b=QLabel('熱血少年\nPRO 管理中心'); b.setObjectName('brand'); sl.addWidget(b)
        self.stack=QStackedWidget()
        pages=[('首頁',self.dashboard),('LINE 客戶',self.clients_page),('預約案件',self.bookings_page),('收款確認',self.payments_page),('連線設定',self.settings_page)]
        for i,(n,f) in enumerate(pages):
            btn=QPushButton(n); btn.clicked.connect(lambda _,x=i:self.stack.setCurrentIndex(x)); sl.addWidget(btn); self.stack.addWidget(f())
        sl.addStretch()
        self.google_state=QLabel('Google：未檢查'); sl.addWidget(self.google_state)
        sync=QPushButton('↻ 全部同步'); sync.setObjectName('primary'); sync.clicked.connect(self.sync_all); sl.addWidget(sync)
        outer.addWidget(side); outer.addWidget(self.stack,1)
        self.timer=QTimer(self); self.timer.timeout.connect(self.auto_sync)
        mins=max(1,int(self.cfg.get('google_sync_minutes',2))); self.timer.start(mins*60*1000)
        QTimer.singleShot(200,self.startup)

    def startup(self):
        find_client_secret()
        self.refresh_all()
        try:
            c=google_creds(False)
            self.google_state.setText('Google：已授權' if c else 'Google：待授權')
        except:self.google_state.setText('Google：待授權')
        QTimer.singleShot(800,self.auto_sync)

    def dashboard(self):
        w=QWidget(); l=QVBoxLayout(w); title=QLabel('今日工作總覽'); title.setStyleSheet('font-size:26px;font-weight:800'); l.addWidget(title)
        row=QHBoxLayout(); self.cards={}
        for k,t in [('today','今日拍攝'),('unpaid','待收款'),('undelivered','未交件'),('messages','LINE訊息')]:
            c=Card(t,'0'); self.cards[k]=c; row.addWidget(c)
        l.addLayout(row)
        self.status=QLabel('系統就緒'); self.status.setWordWrap(True); l.addWidget(self.status)
        l.addStretch(); return w

    def table_page(self,headers):
        w=QWidget(); l=QVBoxLayout(w); t=QTableWidget(0,len(headers)); t.setHorizontalHeaderLabels(headers)
        t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        t.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        l.addWidget(t); return w,t,l

    def clients_page(self):
        self.clientw,self.clientt,_=self.table_page(['LINE姓名','LINE User ID','備註','建立時間']); return self.clientw

    def bookings_page(self):
        self.bookw,self.bookt,_=self.table_page(['日期','時間','賽事','隊伍/球員','需求','攝影師','預約','收款','交件']); return self.bookw

    def payments_page(self):
        self.payw,self.payt,l=self.table_page(['ID','LINE姓名','金額','末五碼','自動配對','狀態','建立時間'])
        bar=QHBoxLayout()
        b=QPushButton('✓ 確認選取收款'); b.setObjectName('good'); b.clicked.connect(self.confirm_payment)
        x=QPushButton('忽略選取'); x.clicked.connect(self.ignore_payment)
        bar.addWidget(b); bar.addWidget(x); l.insertLayout(0,bar)
        return self.payw

    def settings_page(self):
        w=QWidget(); l=QVBoxLayout(w); h=QLabel('連線與授權'); h.setStyleSheet('font-size:24px;font-weight:800'); l.addWidget(h)
        auth=QHBoxLayout()
        self.auth_btn=QPushButton('連接 Google 帳號'); self.auth_btn.setObjectName('primary'); self.auth_btn.clicked.connect(self.authorize_google)
        imp=QPushButton('匯入 OAuth JSON'); imp.clicked.connect(self.import_oauth)
        auth.addWidget(self.auth_btn); auth.addWidget(imp); l.addLayout(auth)
        f=QFormLayout(); self.ed={}
        fields=[('LINE Worker / Webhook 網址','line_worker_url'),('LINE API Token','line_api_token'),('收支 Sheet ID','finance_sheet_id'),('收支工作表','finance_sheet_name')]
        for name,key in fields:
            e=QLineEdit(str(self.cfg.get(key,'')))
            e.setEchoMode(QLineEdit.EchoMode.Password if 'Token' in name else QLineEdit.EchoMode.Normal)
            self.ed[key]=e; f.addRow(name,e)
        l.addLayout(f); b=QPushButton('儲存設定'); b.setObjectName('primary'); b.clicked.connect(self.save_settings); l.addWidget(b)
        note=QLabel('Google 授權完成後，程式會自動搜尋名稱包含「預約」的 Google 試算表，每 2 分鐘同步一次。'); note.setWordWrap(True); l.addWidget(note)
        l.addStretch(); return w

    def import_oauth(self):
        p,_=QFileDialog.getOpenFileName(self,'選擇 Google OAuth JSON','','JSON (*.json)')
        if not p:return
        try:
            data=json.loads(Path(p).read_text(encoding='utf-8'))
            if 'installed' not in data: raise ValueError('這不是 Desktop app OAuth JSON')
            shutil.copy2(p,CLIENT_FILE)
            QMessageBox.information(self,'完成','OAuth 憑證已放入本機。接著按「連接 Google 帳號」。')
        except Exception as e: QMessageBox.critical(self,'匯入失敗',str(e))

    def authorize_google(self):
        try:
            google_creds(True)
            self.google_state.setText('Google：已授權')
            QMessageBox.information(self,'Google 已連線','授權完成。現在會自動同步 Google 預約表與收支表。')
            self.sync_google_forms()
        except Exception as e:
            QMessageBox.critical(self,'Google 授權失敗',str(e))

    def save_settings(self):
        for k,e in self.ed.items(): self.cfg[k]=e.text().strip()
        save_cfg(self.cfg); QMessageBox.information(self,'完成','設定已儲存。')

    def line_api_base(self):
        u=str(self.cfg.get('line_worker_url','')).strip().rstrip('/')
        if not u:return ''
        if u.endswith('/webhook'):u=u[:-8]
        if u.endswith('/api/messages'):u=u[:-13]
        return u

    def sync_all(self):
        self.status.setText('同步中…'); QApplication.processEvents(); errs=[]
        for label,fn in [('LINE',self.sync_line),('Google',self.sync_google_forms)]:
            try: fn()
            except Exception as e: errs.append(label+': '+str(e))
        self.refresh_all()
        self.status.setText('同步完成' if not errs else '同步完成，但有項目尚未連線：'+' / '.join(errs))

    def auto_sync(self):
        if not self.cfg.get('google_auto_sync',True):return
        errs=[]
        try:self.sync_line()
        except Exception as e:errs.append('LINE '+str(e))
        try:self.sync_google_forms()
        except Exception as e:errs.append('Google '+str(e))
        self.refresh_all()
        if errs:self.status.setText('背景同步：'+' / '.join(errs[:2]))
        else:self.status.setText('背景同步完成 '+datetime.now().strftime('%H:%M:%S'))

    def sync_line(self):
        url=self.line_api_base()
        if not url:return
        headers={}
        if self.cfg.get('line_api_token'):headers['Authorization']='Bearer '+self.cfg['line_api_token']
        r=requests.get(url+'/api/messages',headers=headers,timeout=20); r.raise_for_status()
        data=r.json(); rows=data.get('messages',data if isinstance(data,list) else [])
        c=conn(); cur=c.cursor()
        for x in rows:
            uid=str(x.get('userId') or x.get('line_user_id') or x.get('sourceUserId') or '')
            if not uid:continue
            name=x.get('displayName') or x.get('display_name') or uid[-8:]
            cur.execute('INSERT INTO customers(line_user_id,display_name) VALUES(?,?) ON CONFLICT(line_user_id) DO UPDATE SET display_name=excluded.display_name',(uid,name))
            body=str(x.get('text') or x.get('message') or x.get('body') or '')
            ts=str(x.get('timestamp') or x.get('created_at') or datetime.now().isoformat(timespec='seconds'))
            cur.execute('INSERT OR IGNORE INTO messages(line_user_id,body,created_at,raw_json) VALUES(?,?,?,?)',(uid,body,ts,json.dumps(x,ensure_ascii=False)))
            if cur.rowcount:
                mid=cur.lastrowid; amt,last5=extract_payment(body)
                if amt or last5:
                    match=self.auto_match_booking(cur,amt,last5)
                    cur.execute('INSERT INTO payment_candidates(line_user_id,display_name,amount,last5,message_id,matched_booking_id) VALUES(?,?,?,?,?,?)',(uid,name,amt,last5,mid,match))
        c.commit(); c.close()

    def auto_match_booking(self,cur,amt,last5):
        if not amt:return None
        rows=cur.execute("SELECT id FROM bookings WHERE paid=0 AND amount=? ORDER BY event_date DESC",(amt,)).fetchall()
        return rows[0]['id'] if len(rows)==1 else None

    def sync_google_forms(self):
        drive,sheets=google_services(False)
        if not drive or not sheets:return
        q="mimeType='application/vnd.google-apps.spreadsheet' and trashed=false and name contains '預約'"
        files=drive.files().list(q=q,fields='files(id,name,modifiedTime)',pageSize=1000,orderBy='modifiedTime desc').execute().get('files',[])
        c=conn(); cur=c.cursor()
        for f in files:
            sid=f['id']; title=f['name']; mt=f.get('modifiedTime','')
            prev=cur.execute('SELECT modified_time FROM sync_sources WHERE source_id=?',(sid,)).fetchone()
            if prev and prev['modified_time']==mt:continue
            meta=sheets.spreadsheets().get(spreadsheetId=sid,fields='sheets.properties(title,index)').execute()
            tabs=sorted(meta.get('sheets',[]),key=lambda x:x['properties'].get('index',0))
            if not tabs:continue
            tab=tabs[0]['properties']['title']
            vals=sheets.spreadsheets().values().get(spreadsheetId=sid,range=f"'{tab}'!A1:AZ10000").execute().get('values',[])
            if not vals:continue
            headers=vals[0]
            for idx,row in enumerate(vals[1:],start=2):
                if not any(str(x).strip() for x in row):continue
                x=parse_form_row(title,sid,idx,headers,row)
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
        c.commit(); c.close()

    def selected_payment_id(self):
        r=self.payt.currentRow()
        if r<0:return None
        it=self.payt.item(r,0)
        return int(it.text()) if it and it.text().isdigit() else None

    def confirm_payment(self):
        pid=self.selected_payment_id()
        if not pid:return QMessageBox.information(self,'請選擇','請先點選一筆待確認收款。')
        c=conn(); cur=c.cursor(); p=cur.execute('SELECT * FROM payment_candidates WHERE id=?',(pid,)).fetchone()
        if not p:return
        bid=p['matched_booking_id']
        if not bid:
            c.close()
            return QMessageBox.warning(self,'需要人工確認','這筆金額無法唯一配對到一個未收款預約，暫時不自動寫入收支表，避免記錯帳。')
        b=cur.execute('SELECT * FROM bookings WHERE id=?',(bid,)).fetchone()
        try:
            self.append_finance(b,p)
            cur.execute('UPDATE bookings SET paid=1,last5=? WHERE id=?',(p['last5'],bid))
            cur.execute("UPDATE payment_candidates SET status='已確認' WHERE id=?",(pid,))
            cur.execute("INSERT INTO audit_log(action,detail) VALUES('confirm_payment',?)",(f'payment={pid}, booking={bid}',))
            c.commit(); QMessageBox.information(self,'完成','已確認收款，並同步寫入熱血少年收支表。')
        except Exception as e:
            QMessageBox.critical(self,'同步失敗','沒有修改收款狀態。\n'+str(e))
        finally:c.close()
        self.refresh_all()

    def append_finance(self,b,p):
        _,sheets=google_services(False)
        if not sheets:raise RuntimeError('Google 尚未授權')
        sid=self.cfg.get('finance_sheet_id'); tab=self.cfg.get('finance_sheet_name','2026年')
        if not sid:raise RuntimeError('找不到收支表 ID')
        team=b['team'] or ''
        sponsor=b['player_text'] or p['display_name'] or ''
        vals=[[b['event_name'] or '', b['event_date'] or '', team, sponsor, int(p['amount'] or b['amount'] or 0), p['last5'] or '']]
        sheets.spreadsheets().values().append(
          spreadsheetId=sid,range=f"'{tab}'!A:F",valueInputOption='USER_ENTERED',
          insertDataOption='INSERT_ROWS',body={'values':vals}).execute()

    def ignore_payment(self):
        pid=self.selected_payment_id()
        if not pid:return
        c=conn(); c.execute("UPDATE payment_candidates SET status='忽略' WHERE id=?",(pid,)); c.commit(); c.close(); self.refresh_all()

    def refresh_all(self):
        c=conn(); cur=c.cursor(); today=datetime.now().strftime('%Y-%m-%d')
        vals={
          'today':cur.execute('SELECT COUNT(*) FROM bookings WHERE event_date=?',(today,)).fetchone()[0],
          'unpaid':cur.execute('SELECT COALESCE(SUM(amount),0) FROM bookings WHERE paid=0').fetchone()[0],
          'undelivered':cur.execute("SELECT COUNT(*) FROM bookings WHERE delivery_status!='已交件'").fetchone()[0],
          'messages':cur.execute('SELECT COUNT(*) FROM messages').fetchone()[0]
        }
        for k,v in vals.items():self.cards[k].v.setText(f'NT$ {v:,}' if k=='unpaid' else str(v))
        self.fill(self.clientt,cur.execute('SELECT display_name,line_user_id,note,created_at FROM customers ORDER BY id DESC LIMIT 500').fetchall())
        self.fill(self.bookt,cur.execute("""SELECT event_date,start_time,event_name,COALESCE(NULLIF(player_text,''),team),
          request_type||CASE WHEN priority_numbers!='' THEN ' #'||priority_numbers ELSE '' END,
          photographer,booking_status,CASE WHEN paid=1 THEN '已收' ELSE '未收' END,delivery_status
          FROM bookings ORDER BY event_date,start_time""").fetchall())
        self.fill(self.payt,cur.execute("""SELECT p.id,p.display_name,p.amount,p.last5,
          CASE WHEN p.matched_booking_id IS NULL THEN '未唯一配對' ELSE
          (SELECT event_name||' / '||COALESCE(NULLIF(player_text,''),team) FROM bookings b WHERE b.id=p.matched_booking_id) END,
          p.status,p.created_at FROM payment_candidates p ORDER BY p.id DESC LIMIT 500""").fetchall())
        c.close()

    def fill(self,t,rows):
        t.setRowCount(len(rows))
        for i,r in enumerate(rows):
            for j,v in enumerate(r):t.setItem(i,j,QTableWidgetItem('' if v is None else str(v)))

if __name__=='__main__':
    init_db()
    app=QApplication(sys.argv); app.setStyleSheet(STYLE)
    m=Main(); m.show(); sys.exit(app.exec())
