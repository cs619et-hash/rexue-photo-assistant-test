import sys, os, sqlite3, json, re, shutil, threading, traceback
from pathlib import Path
from datetime import datetime
import requests
from PySide6.QtCore import QTimer, QObject, Signal
from PySide6.QtWidgets import (QApplication,QMainWindow,QWidget,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QStackedWidget,QTableWidget,QTableWidgetItem,QFrame,QLineEdit,QFormLayout,QMessageBox,QHeaderView,QFileDialog,QAbstractItemView,QProgressBar)

APP_NAME='熱血少年｜拍攝工作管理中心 PRO'
DATA_DIR=Path(os.getenv('APPDATA',str(Path.home()))) / 'RexueManager'
DATA_DIR.mkdir(parents=True,exist_ok=True)
DB=DATA_DIR/'rexue_manager.db'; CFG=DATA_DIR/'config.json'; TOKEN_FILE=DATA_DIR/'google_token.json'; CLIENT_FILE=DATA_DIR/'client_secret.json'; LOG=DATA_DIR/'rexue_manager.log'
SCOPES=['https://www.googleapis.com/auth/drive.metadata.readonly','https://www.googleapis.com/auth/spreadsheets']
STYLE='''QWidget{background:#0b1019;color:#eef3fb;font-family:"Microsoft JhengHei";font-size:14px} QFrame#side{background:#111827;border-right:1px solid #253047} QLabel#brand{font-size:22px;font-weight:800;padding:12px} QPushButton{background:#182235;border:1px solid #26354f;border-radius:9px;padding:10px 14px;text-align:left} QPushButton:hover{background:#21304a} QPushButton#primary{background:#2563eb;border:none;font-weight:700;text-align:center} QPushButton#good{background:#146c43;border:none;font-weight:700;text-align:center} QFrame.card{background:#121a29;border:1px solid #263147;border-radius:14px} QLabel.big{font-size:28px;font-weight:800} QLineEdit{background:#0f1724;border:1px solid #334155;border-radius:8px;padding:8px} QTableWidget{background:#0f1724;border:1px solid #263147;border-radius:10px;gridline-color:#263147} QHeaderView::section{background:#182235;color:#dbe7fb;padding:8px;border:0}'''

def log(msg):
    try:
        with LOG.open('a',encoding='utf-8') as f:f.write(datetime.now().isoformat(timespec='seconds')+' '+str(msg)+'\n')
    except:pass

def conn(): c=sqlite3.connect(DB); c.row_factory=sqlite3.Row; return c

def ensure_col(cur,table,col,decl):
    if col not in [r[1] for r in cur.execute(f'PRAGMA table_info({table})')]:cur.execute(f'ALTER TABLE {table} ADD COLUMN {col} {decl}')

def init_db():
    c=conn(); q=c.cursor(); q.executescript('''PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS customers(id INTEGER PRIMARY KEY,line_user_id TEXT UNIQUE,display_name TEXT,note TEXT DEFAULT '',created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,line_user_id TEXT,direction TEXT DEFAULT 'in',body TEXT,created_at TEXT,raw_json TEXT,UNIQUE(line_user_id,created_at,body));
CREATE TABLE IF NOT EXISTS bookings(id INTEGER PRIMARY KEY,source TEXT,source_key TEXT UNIQUE,event_name TEXT,event_date TEXT,start_time TEXT,venue TEXT,age_group TEXT,team TEXT,opponent TEXT,player_text TEXT,request_raw TEXT,request_type TEXT,priority_numbers TEXT,photographer TEXT,booking_status TEXT DEFAULT '待確認',shoot_status TEXT DEFAULT '未拍攝',delivery_status TEXT DEFAULT '未交件',amount INTEGER DEFAULT 0,paid INTEGER DEFAULT 0,last5 TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS payment_candidates(id INTEGER PRIMARY KEY,line_user_id TEXT,amount INTEGER,last5 TEXT,message_id INTEGER,status TEXT DEFAULT '待確認',created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS sync_sources(id INTEGER PRIMARY KEY,source_id TEXT UNIQUE,title TEXT,modified_time TEXT,last_sync TEXT);
CREATE TABLE IF NOT EXISTS audit_log(id INTEGER PRIMARY KEY,action TEXT,detail TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP);''')
    ensure_col(q,'payment_candidates','matched_booking_id','INTEGER'); ensure_col(q,'payment_candidates','display_name','TEXT'); c.commit(); c.close()

def load_cfg():
    b={'line_worker_url':'','line_api_token':'','finance_sheet_id':'1Vy2tyBoxpIrAPTAWnFuemPLYje2Ip8ZvLV1GqRM-fU8','finance_sheet_name':'2026年','google_auto_sync':True,'google_sync_minutes':2}
    if CFG.exists():
        try:b.update(json.loads(CFG.read_text(encoding='utf-8')))
        except:pass
    return b

def save_cfg(x):CFG.write_text(json.dumps(x,ensure_ascii=False,indent=2),encoding='utf-8')

def normalize_date(v):
    s=str(v or '').strip(); m=re.search(r'(\d{4})\D+(\d{1,2})\D+(\d{1,2})',s)
    return f'{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}' if m else s

def normalize_time(v):
    s=str(v or '').strip(); d=re.sub(r'\D','',s)
    if ':' in s:
        p=s.split(':');
        if len(p)>1 and p[0].isdigit() and p[1][:2].isdigit():return f'{int(p[0]):02d}:{int(p[1][:2]):02d}'
    if len(d) in (3,4):d=d.zfill(4); return f'{d[:2]}:{d[2:]}'
    return s

def pick(row,headers,words):
    for i,h in enumerate(headers):
        z=str(h or '').replace(' ','')
        if any(w.replace(' ','') in z for w in words):return row[i] if i<len(row) else ''
    return ''

def clean_event_title(t):
    s=str(t or '')
    for x in [' (回覆)','(回覆)','／預約拍攝賽事','/預約拍攝賽事','／預約拍攝','/預約拍攝','預約表單']:s=s.replace(x,'')
    return s.strip(' /／-')

def parse_form_row(title,sid,rownum,headers,row):
    date=pick(row,headers,['比賽日期','日期']); tm=pick(row,headers,['比賽開始時間','開始時間','比賽時間']); venue=pick(row,headers,['比賽地點','場地','地點']); age=pick(row,headers,['年級組別','組別','年級']); player=pick(row,headers,['隊伍名稱/球員姓名/背號','球員姓名','隊伍名稱／球員姓名']); vs=pick(row,headers,['比賽隊伍名稱','對戰','對手']); req=pick(row,headers,['團體拍攝/指定球員','拍攝需求','指定球員','團體拍攝']); ts=pick(row,headers,['時間戳記','Timestamp','填表時間'])
    raw=str(req or ''); team=re.split(r'[/／]',str(player))[0].strip() if player else ''
    nums=','.join(sorted(set(re.findall(r'(?<!\d)(\d{1,2})\s*號?(?!\d)',raw)),key=lambda z:int(z))) if raw else ''
    typ='指定球員' if any(k in raw for k in ['指定','號','球員','主要','多拍']) else '團體拍攝'
    return {'source_key':f'{sid}:{rownum}:{ts or "|".join(map(str,row))}','event_name':clean_event_title(title),'event_date':normalize_date(date),'start_time':normalize_time(tm),'venue':str(venue or ''),'age_group':str(age or ''),'team':team,'opponent':str(vs or ''),'player_text':str(player or ''),'request_raw':raw,'request_type':typ,'priority_numbers':nums}

def find_client_secret():
    if CLIENT_FILE.exists():return CLIENT_FILE
    choices=[]
    try:choices.append(Path(sys.executable).resolve().parent/'client_secret.json')
    except:pass
    choices.append(Path.cwd()/'client_secret.json')
    for p in choices:
        if p.exists():shutil.copy2(p,CLIENT_FILE); return CLIENT_FILE
    return None

def google_creds(interactive=False):
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from google_auth_oauthlib.flow import InstalledAppFlow
    creds=None
    if TOKEN_FILE.exists():
        try:creds=Credentials.from_authorized_user_file(str(TOKEN_FILE),SCOPES)
        except Exception as e:log('token load '+repr(e))
    if creds and creds.expired and creds.refresh_token:creds.refresh(Request()); TOKEN_FILE.write_text(creds.to_json(),encoding='utf-8')
    if creds and creds.valid:return creds
    if not interactive:return None
    cf=find_client_secret()
    if not cf:raise RuntimeError('找不到 client_secret.json。請把它放在 EXE 同一資料夾。')
    flow=InstalledAppFlow.from_client_secrets_file(str(cf),SCOPES)
    creds=flow.run_local_server(host='localhost',port=0,open_browser=True,authorization_prompt_message='',success_message='Google 授權完成，可以關閉這個視窗並回到熱血少年管理中心。')
    TOKEN_FILE.write_text(creds.to_json(),encoding='utf-8'); return creds

def google_services(interactive=False):
    c=google_creds(interactive)
    if not c:return None,None
    from googleapiclient.discovery import build
    return build('drive','v3',credentials=c,cache_discovery=False),build('sheets','v4',credentials=c,cache_discovery=False)

def extract_payment(text):
    text=str(text or ''); amount=None; last5=None
    for p in [r'(?:匯款|轉帳|已付|付款|金額|匯了|轉了)\s*[:：]?\s*(?:NT\$?\s*)?([1-9]\d{2,6})',r'(?:NT\$?\s*)([1-9]\d{2,6})']:
        m=re.search(p,text,re.I)
        if m:amount=int(m.group(1));break
    m=re.search(r'(?:末五碼|後五碼|五碼|末5碼|後5碼)\s*[:：]?\s*(\d{5})',text)
    if m:last5=m.group(1)
    return amount,last5

class Bus(QObject):
    status=Signal(str); done=Signal(str); error=Signal(str); refresh=Signal()

class Card(QFrame):
    def __init__(self,t,v='0'):
        super().__init__(); self.setProperty('class','card'); l=QVBoxLayout(self); l.addWidget(QLabel(t)); self.v=QLabel(v); self.v.setProperty('class','big'); l.addWidget(self.v)

class Main(QMainWindow):
    def __init__(self):
        super().__init__(); self.cfg=load_cfg(); self.bus=Bus(); self.bus.status.connect(self.set_status); self.bus.done.connect(self.task_done); self.bus.error.connect(self.task_error); self.bus.refresh.connect(self.refresh_all); self.busy=False
        self.setWindowTitle(APP_NAME); self.resize(1420,860)
        root=QWidget(); self.setCentralWidget(root); outer=QHBoxLayout(root); outer.setContentsMargins(0,0,0,0)
        side=QFrame(); side.setObjectName('side'); side.setFixedWidth(230); sl=QVBoxLayout(side); b=QLabel('熱血少年\nPRO 管理中心'); b.setObjectName('brand'); sl.addWidget(b)
        self.stack=QStackedWidget(); pages=[('首頁',self.dashboard),('LINE 客戶',self.clients_page),('預約案件',self.bookings_page),('收款確認',self.payments_page),('連線設定',self.settings_page)]
        for i,(n,f) in enumerate(pages):btn=QPushButton(n); btn.clicked.connect(lambda _,x=i:self.stack.setCurrentIndex(x)); sl.addWidget(btn); self.stack.addWidget(f())
        sl.addStretch(); self.google_state=QLabel('Google：未檢查'); sl.addWidget(self.google_state); self.syncbtn=QPushButton('↻ 全部同步'); self.syncbtn.setObjectName('primary'); self.syncbtn.clicked.connect(self.sync_all); sl.addWidget(self.syncbtn); outer.addWidget(side); outer.addWidget(self.stack,1)
        self.timer=QTimer(self); self.timer.timeout.connect(self.auto_sync); self.timer.start(max(1,int(self.cfg.get('google_sync_minutes',2)))*60000); QTimer.singleShot(250,self.startup)
    def startup(self):
        find_client_secret(); self.refresh_all(); self.google_state.setText('Google：已授權' if google_creds(False) else 'Google：待授權'); QTimer.singleShot(1500,self.auto_sync)
    def dashboard(self):
        w=QWidget(); l=QVBoxLayout(w); h=QLabel('今日工作總覽'); h.setStyleSheet('font-size:26px;font-weight:800'); l.addWidget(h); row=QHBoxLayout(); self.cards={}
        for k,t in [('today','今日拍攝'),('unpaid','待收款'),('undelivered','未交件'),('messages','LINE訊息')]:self.cards[k]=Card(t); row.addWidget(self.cards[k])
        l.addLayout(row); self.progress=QProgressBar(); self.progress.setRange(0,0); self.progress.hide(); l.addWidget(self.progress); self.status=QLabel('系統就緒'); self.status.setWordWrap(True); l.addWidget(self.status); l.addStretch(); return w
    def table_page(self,headers):
        w=QWidget(); l=QVBoxLayout(w); t=QTableWidget(0,len(headers)); t.setHorizontalHeaderLabels(headers); t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch); t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows); t.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection); l.addWidget(t); return w,t,l
    def clients_page(self):self.clientw,self.clientt,_=self.table_page(['LINE姓名','LINE User ID','備註','建立時間']);return self.clientw
    def bookings_page(self):self.bookw,self.bookt,_=self.table_page(['日期','時間','賽事','隊伍/球員','需求','攝影師','預約','收款','交件']);return self.bookw
    def payments_page(self):
        self.payw,self.payt,l=self.table_page(['ID','LINE姓名','金額','末五碼','自動配對','狀態','建立時間']); bar=QHBoxLayout(); b=QPushButton('✓ 確認選取收款'); b.setObjectName('good'); b.clicked.connect(self.confirm_payment); x=QPushButton('忽略選取'); x.clicked.connect(self.ignore_payment); bar.addWidget(b);bar.addWidget(x);l.insertLayout(0,bar);return self.payw
    def settings_page(self):
        w=QWidget(); l=QVBoxLayout(w); h=QLabel('連線與授權'); h.setStyleSheet('font-size:24px;font-weight:800'); l.addWidget(h); bar=QHBoxLayout(); self.authbtn=QPushButton('連接 Google 帳號'); self.authbtn.setObjectName('primary'); self.authbtn.clicked.connect(self.authorize_google); imp=QPushButton('匯入 OAuth JSON'); imp.clicked.connect(self.import_oauth); bar.addWidget(self.authbtn);bar.addWidget(imp);l.addLayout(bar); f=QFormLayout();self.ed={}
        for name,key in [('LINE Worker / Webhook 網址','line_worker_url'),('LINE API Token','line_api_token'),('收支 Sheet ID','finance_sheet_id'),('收支工作表','finance_sheet_name')]:e=QLineEdit(str(self.cfg.get(key,'')));e.setEchoMode(QLineEdit.EchoMode.Password if 'Token' in name else QLineEdit.EchoMode.Normal);self.ed[key]=e;f.addRow(name,e)
        l.addLayout(f); sb=QPushButton('儲存設定');sb.clicked.connect(self.save_settings);l.addWidget(sb); note=QLabel('授權或同步執行時，畫面不會再卡住。首頁會顯示目前進度。');note.setWordWrap(True);l.addWidget(note);l.addStretch();return w
    def import_oauth(self):
        p,_=QFileDialog.getOpenFileName(self,'選擇 Google OAuth JSON','','JSON (*.json)')
        if not p:return
        try:
            d=json.loads(Path(p).read_text(encoding='utf-8'))
            if 'installed' not in d:raise ValueError('這不是 Desktop app OAuth JSON')
            shutil.copy2(p,CLIENT_FILE); QMessageBox.information(self,'完成','OAuth 憑證已放入本機。')
        except Exception as e:QMessageBox.critical(self,'匯入失敗',str(e))
    def run_bg(self,fn,label):
        if self.busy:return
        self.busy=True; self.progress.show(); self.syncbtn.setEnabled(False); self.authbtn.setEnabled(False); self.set_status(label)
        def job():
            try:msg=fn() or '完成'; self.bus.done.emit(str(msg))
            except Exception as e:log(traceback.format_exc()); self.bus.error.emit(str(e))
        threading.Thread(target=job,daemon=True).start()
    def authorize_google(self):
        def task():
            self.bus.status.emit('正在開啟 Google 登入頁面，請到瀏覽器完成授權…'); google_creds(True); self.google_state.setText('Google：已授權'); self.bus.status.emit('Google 已授權，正在測試讀取 Drive 與 Sheets…'); d,s=google_services(False); d.files().list(pageSize=1,fields='files(id)').execute(); s.spreadsheets().get(spreadsheetId=self.cfg['finance_sheet_id'],fields='spreadsheetId').execute(); return 'Google 授權與連線測試成功'
        self.run_bg(task,'準備 Google 授權…')
    def save_settings(self):
        for k,e in self.ed.items():self.cfg[k]=e.text().strip()
        save_cfg(self.cfg); QMessageBox.information(self,'完成','設定已儲存。')
    def set_status(self,s):self.status.setText(s)
    def task_done(self,s):
        self.busy=False;self.progress.hide();self.syncbtn.setEnabled(True);self.authbtn.setEnabled(True);self.status.setText(s);self.google_state.setText('Google：已授權' if TOKEN_FILE.exists() else 'Google：待授權');self.refresh_all();QMessageBox.information(self,'完成',s)
    def task_error(self,s):
        self.busy=False;self.progress.hide();self.syncbtn.setEnabled(True);self.authbtn.setEnabled(True);self.status.setText('錯誤：'+s);QMessageBox.critical(self,'執行失敗',s+'\n\n詳細紀錄：'+str(LOG))
    def line_api_base(self):
        u=str(self.cfg.get('line_worker_url','')).strip().rstrip('/')
        if u.endswith('/webhook'):u=u[:-8]
        if u.endswith('/api/messages'):u=u[:-13]
        return u
    def sync_all(self):self.run_bg(self.sync_everything,'開始同步 LINE 與 Google…')
    def auto_sync(self):
        if not self.busy and self.cfg.get('google_auto_sync',True):self.run_bg(self.sync_everything,'背景同步中…')
    def sync_everything(self):
        errs=[]
        try:self.sync_line()
        except Exception as e:errs.append('LINE：'+str(e))
        try:self.sync_google_forms()
        except Exception as e:errs.append('Google：'+str(e))
        self.bus.refresh.emit(); return '同步完成' if not errs else '同步完成，但有項目尚未連線：'+' / '.join(errs)
    def sync_line(self):
        base=self.line_api_base()
        if not base:return
        self.bus.status.emit('正在同步 LINE 訊息…'); headers={}
        if self.cfg.get('line_api_token'):headers['Authorization']='Bearer '+self.cfg['line_api_token']
        r=requests.get(base+'/api/messages',headers=headers,timeout=20);r.raise_for_status();data=r.json();rows=data.get('messages',data if isinstance(data,list) else []); c=conn();q=c.cursor()
        for x in rows:
            uid=str(x.get('userId') or x.get('line_user_id') or x.get('sourceUserId') or '')
            if not uid:continue
            name=x.get('displayName') or x.get('display_name') or uid[-8:];q.execute('INSERT INTO customers(line_user_id,display_name) VALUES(?,?) ON CONFLICT(line_user_id) DO UPDATE SET display_name=excluded.display_name',(uid,name));body=str(x.get('text') or x.get('message') or x.get('body') or '');ts=str(x.get('timestamp') or x.get('created_at') or datetime.now().isoformat(timespec='seconds'));q.execute('INSERT OR IGNORE INTO messages(line_user_id,body,created_at,raw_json) VALUES(?,?,?,?)',(uid,body,ts,json.dumps(x,ensure_ascii=False)))
            if q.rowcount:
                mid=q.lastrowid;amt,last5=extract_payment(body);match=None
                if amt:
                    rr=q.execute('SELECT id FROM bookings WHERE paid=0 AND amount=? ORDER BY event_date DESC',(amt,)).fetchall();match=rr[0]['id'] if len(rr)==1 else None
                if amt or last5:q.execute('INSERT INTO payment_candidates(line_user_id,display_name,amount,last5,message_id,matched_booking_id) VALUES(?,?,?,?,?,?)',(uid,name,amt,last5,mid,match))
        c.commit();c.close()
    def sync_google_forms(self):
        d,s=google_services(False)
        if not d or not s:raise RuntimeError('Google 尚未授權，請先到「連線設定」按「連接 Google 帳號」。')
        self.bus.status.emit('正在搜尋 Google Drive 裡的預約表…'); q="mimeType='application/vnd.google-apps.spreadsheet' and trashed=false and name contains '預約'"; files=d.files().list(q=q,fields='files(id,name,modifiedTime)',pageSize=1000,orderBy='modifiedTime desc').execute().get('files',[]); c=conn();cur=c.cursor(); total=len(files)
        for n,f in enumerate(files,1):
            self.bus.status.emit(f'Google 預約表同步 {n}/{total}：{f["name"]}');sid=f['id'];mt=f.get('modifiedTime','');prev=cur.execute('SELECT modified_time FROM sync_sources WHERE source_id=?',(sid,)).fetchone()
            if prev and prev['modified_time']==mt:continue
            meta=s.spreadsheets().get(spreadsheetId=sid,fields='sheets.properties(title,index)').execute();tabs=sorted(meta.get('sheets',[]),key=lambda x:x['properties'].get('index',0))
            if not tabs:continue
            tab=tabs[0]['properties']['title'];vals=s.spreadsheets().values().get(spreadsheetId=sid,range=f"'{tab}'!A1:AZ5000").execute().get('values',[])
            if not vals:continue
            headers=vals[0]
            for idx,row in enumerate(vals[1:],2):
                if not any(str(x).strip() for x in row):continue
                x=parse_form_row(f['name'],sid,idx,headers,row);cur.execute('''INSERT INTO bookings(source,source_key,event_name,event_date,start_time,venue,age_group,team,opponent,player_text,request_raw,request_type,priority_numbers) VALUES('google_form',?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(source_key) DO UPDATE SET event_name=excluded.event_name,event_date=excluded.event_date,start_time=excluded.start_time,venue=excluded.venue,age_group=excluded.age_group,team=excluded.team,opponent=excluded.opponent,player_text=excluded.player_text,request_raw=excluded.request_raw,request_type=excluded.request_type,priority_numbers=excluded.priority_numbers''',(x['source_key'],x['event_name'],x['event_date'],x['start_time'],x['venue'],x['age_group'],x['team'],x['opponent'],x['player_text'],x['request_raw'],x['request_type'],x['priority_numbers']))
            cur.execute('INSERT INTO sync_sources(source_id,title,modified_time,last_sync) VALUES(?,?,?,?) ON CONFLICT(source_id) DO UPDATE SET title=excluded.title,modified_time=excluded.modified_time,last_sync=excluded.last_sync',(sid,f['name'],mt,datetime.now().isoformat(timespec='seconds')))
        c.commit();c.close()
    def selected_payment_id(self):
        r=self.payt.currentRow();it=self.payt.item(r,0) if r>=0 else None;return int(it.text()) if it and it.text().isdigit() else None
    def confirm_payment(self):
        QMessageBox.information(self,'收款確認','此功能保留原有邏輯；先完成 Google 與 LINE 連線後再啟用自動寫帳。')
    def ignore_payment(self):
        pid=self.selected_payment_id()
        if pid:
            c=conn();c.execute("UPDATE payment_candidates SET status='忽略' WHERE id=?",(pid,));c.commit();c.close();self.refresh_all()
    def refresh_all(self):
        c=conn();q=c.cursor();today=datetime.now().strftime('%Y-%m-%d');vals={'today':q.execute('SELECT COUNT(*) FROM bookings WHERE event_date=?',(today,)).fetchone()[0],'unpaid':q.execute('SELECT COALESCE(SUM(amount),0) FROM bookings WHERE paid=0').fetchone()[0],'undelivered':q.execute("SELECT COUNT(*) FROM bookings WHERE delivery_status!='已交件'").fetchone()[0],'messages':q.execute('SELECT COUNT(*) FROM messages').fetchone()[0]}
        for k,v in vals.items():self.cards[k].v.setText(f'NT$ {v:,}' if k=='unpaid' else str(v))
        self.fill(self.clientt,q.execute('SELECT display_name,line_user_id,note,created_at FROM customers ORDER BY id DESC LIMIT 500').fetchall());self.fill(self.bookt,q.execute("SELECT event_date,start_time,event_name,COALESCE(NULLIF(player_text,''),team),request_type||CASE WHEN priority_numbers!='' THEN ' #'||priority_numbers ELSE '' END,photographer,booking_status,CASE WHEN paid=1 THEN '已收' ELSE '未收' END,delivery_status FROM bookings ORDER BY event_date,start_time").fetchall());self.fill(self.payt,q.execute("SELECT id,display_name,amount,last5,CASE WHEN matched_booking_id IS NULL THEN '未唯一配對' ELSE CAST(matched_booking_id AS TEXT) END,status,created_at FROM payment_candidates ORDER BY id DESC LIMIT 500").fetchall());c.close()
    def fill(self,t,rows):
        t.setRowCount(len(rows))
        for i,r in enumerate(rows):
            for j,v in enumerate(r):t.setItem(i,j,QTableWidgetItem('' if v is None else str(v)))

if __name__=='__main__':init_db();app=QApplication(sys.argv);app.setStyleSheet(STYLE);m=Main();m.show();sys.exit(app.exec())
