import sys, os, sqlite3, json, re
from pathlib import Path
from datetime import datetime
import requests
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QApplication,QMainWindow,QWidget,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QStackedWidget,QTableWidget,QTableWidgetItem,QFrame,QLineEdit,QFormLayout,QMessageBox,QHeaderView)

APP_NAME='熱血少年｜拍攝工作管理中心'
DATA_DIR=Path(os.getenv('APPDATA',str(Path.home()))) / 'RexueManager'
DATA_DIR.mkdir(parents=True,exist_ok=True)
DB=DATA_DIR/'rexue_manager.db'
CFG=DATA_DIR/'config.json'

STYLE='''
QWidget{background:#0c111b;color:#eef3fb;font-family:"Microsoft JhengHei";font-size:14px}
QFrame#side{background:#111827;border-right:1px solid #253047}
QLabel#brand{font-size:22px;font-weight:800;padding:12px}
QPushButton{background:#182235;border:1px solid #26354f;border-radius:9px;padding:10px 14px;text-align:left}
QPushButton:hover{background:#21304a}
QPushButton#primary{background:#2563eb;border:none;font-weight:700;text-align:center}
QFrame.card{background:#121a29;border:1px solid #263147;border-radius:14px}
QLabel.big{font-size:28px;font-weight:800}
QLineEdit{background:#0f1724;border:1px solid #334155;border-radius:8px;padding:8px}
QTableWidget{background:#0f1724;border:1px solid #263147;border-radius:10px;gridline-color:#263147}
QHeaderView::section{background:#182235;color:#dbe7fb;padding:8px;border:0}
'''

def conn():
    c=sqlite3.connect(DB)
    c.row_factory=sqlite3.Row
    return c

def init_db():
    c=conn(); cur=c.cursor()
    cur.executescript('''
    PRAGMA journal_mode=WAL;
    CREATE TABLE IF NOT EXISTS customers(id INTEGER PRIMARY KEY, line_user_id TEXT UNIQUE, display_name TEXT, note TEXT DEFAULT '', created_at TEXT DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY, line_user_id TEXT, direction TEXT DEFAULT 'in', body TEXT, created_at TEXT, raw_json TEXT, UNIQUE(line_user_id,created_at,body));
    CREATE TABLE IF NOT EXISTS bookings(id INTEGER PRIMARY KEY, source TEXT, source_key TEXT UNIQUE, event_name TEXT, event_date TEXT, start_time TEXT, venue TEXT, age_group TEXT, team TEXT, opponent TEXT, player_text TEXT, request_raw TEXT, request_type TEXT, priority_numbers TEXT, photographer TEXT, booking_status TEXT DEFAULT '待確認', shoot_status TEXT DEFAULT '未拍攝', delivery_status TEXT DEFAULT '未交件', amount INTEGER DEFAULT 0, paid INTEGER DEFAULT 0, last5 TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS payment_candidates(id INTEGER PRIMARY KEY, line_user_id TEXT, amount INTEGER, last5 TEXT, message_id INTEGER, status TEXT DEFAULT '待確認', created_at TEXT DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS audit_log(id INTEGER PRIMARY KEY, action TEXT, detail TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
    ''')
    c.commit(); c.close()

def load_cfg():
    if CFG.exists():
        try:return json.loads(CFG.read_text(encoding='utf-8'))
        except:pass
    return {'line_worker_url':'','line_api_token':'','forms_sync_url':'','finance_sync_url':'','finance_sheet_id':'1Vy2tyBoxpIrAPTAWnFuemPLYje2Ip8ZvLV1GqRM-fU8'}

def save_cfg(cfg): CFG.write_text(json.dumps(cfg,ensure_ascii=False,indent=2),encoding='utf-8')

def extract_payment(text):
    amount=None; last5=None
    m=re.search(r'(?:匯款|轉帳|已付|付款|金額)?\s*[:：]?\s*([1-9]\d{2,6})\s*元?',text)
    if m: amount=int(m.group(1))
    m=re.search(r'(?:末五碼|後五碼|五碼)\s*[:：]?\s*(\d{5})',text)
    if m:last5=m.group(1)
    return amount,last5

class Card(QFrame):
    def __init__(self,title,value):
        super().__init__(); self.setProperty('class','card'); l=QVBoxLayout(self); a=QLabel(title); self.v=QLabel(str(value)); self.v.setProperty('class','big'); l.addWidget(a); l.addWidget(self.v)

class Main(QMainWindow):
    def __init__(self):
        super().__init__(); self.cfg=load_cfg(); self.setWindowTitle(APP_NAME); self.resize(1380,820)
        root=QWidget(); self.setCentralWidget(root); outer=QHBoxLayout(root); outer.setContentsMargins(0,0,0,0)
        side=QFrame(); side.setObjectName('side'); side.setFixedWidth(220); sl=QVBoxLayout(side); b=QLabel('熱血少年\n管理中心'); b.setObjectName('brand'); sl.addWidget(b)
        self.stack=QStackedWidget(); pages=[('首頁',self.dashboard),('LINE 客戶',self.clients_page),('預約案件',self.bookings_page),('收款確認',self.payments_page),('系統設定',self.settings_page)]
        for i,(n,f) in enumerate(pages):
            btn=QPushButton(n); btn.clicked.connect(lambda _,x=i:self.stack.setCurrentIndex(x)); sl.addWidget(btn); self.stack.addWidget(f())
        sl.addStretch(); sync=QPushButton('↻ 全部同步'); sync.setObjectName('primary'); sync.clicked.connect(self.sync_all); sl.addWidget(sync)
        outer.addWidget(side); outer.addWidget(self.stack,1)
        QTimer.singleShot(100,self.refresh_all)
    def dashboard(self):
        w=QWidget(); l=QVBoxLayout(w); title=QLabel('今日工作總覽'); title.setStyleSheet('font-size:26px;font-weight:800'); l.addWidget(title)
        row=QHBoxLayout(); self.cards={}
        for k,t in [('today','今日拍攝'),('unpaid','待收款'),('undelivered','未交件'),('messages','LINE訊息')]:
            c=Card(t,'0'); self.cards[k]=c; row.addWidget(c)
        l.addLayout(row); self.status=QLabel('系統就緒'); l.addWidget(self.status); l.addStretch(); return w
    def table_page(self,headers):
        w=QWidget(); l=QVBoxLayout(w); t=QTableWidget(0,len(headers)); t.setHorizontalHeaderLabels(headers); t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch); l.addWidget(t); return w,t
    def clients_page(self): self.clientw,self.clientt=self.table_page(['LINE姓名','LINE User ID','備註','建立時間']); return self.clientw
    def bookings_page(self): self.bookw,self.bookt=self.table_page(['日期','時間','賽事','隊伍/球員','需求','攝影師','預約','收款','交件']); return self.bookw
    def payments_page(self): self.payw,self.payt=self.table_page(['LINE User ID','金額','末五碼','狀態','建立時間']); return self.payw
    def settings_page(self):
        w=QWidget(); l=QVBoxLayout(w); h=QLabel('系統連線設定'); h.setStyleSheet('font-size:24px;font-weight:800'); l.addWidget(h); f=QFormLayout(); self.ed={}
        fields=[('LINE Worker API','line_worker_url'),('LINE API Token','line_api_token'),('Google 表單同步端點','forms_sync_url'),('收支同步端點','finance_sync_url'),('收支 Sheet ID','finance_sheet_id')]
        for name,key in fields:
            e=QLineEdit(self.cfg.get(key,'')); e.setEchoMode(QLineEdit.EchoMode.Password if 'Token' in name else QLineEdit.EchoMode.Normal); self.ed[key]=e; f.addRow(name,e)
        l.addLayout(f); b=QPushButton('儲存設定'); b.setObjectName('primary'); b.clicked.connect(self.save_settings); l.addWidget(b); l.addStretch(); return w
    def save_settings(self):
        self.cfg={k:e.text().strip() for k,e in self.ed.items()}; save_cfg(self.cfg); QMessageBox.information(self,'完成','設定已儲存。')
    def sync_all(self):
        self.status.setText('同步中…')
        errs=[]
        try:self.sync_line()
        except Exception as e: errs.append('LINE: '+str(e))
        try:self.sync_forms()
        except Exception as e: errs.append('表單: '+str(e))
        self.refresh_all(); self.status.setText('同步完成' if not errs else '同步完成，但有項目未連線：'+' / '.join(errs))
    def sync_line(self):
        url=self.cfg.get('line_worker_url','').rstrip('/')
        if not url:return
        headers={}
        if self.cfg.get('line_api_token'): headers['Authorization']='Bearer '+self.cfg['line_api_token']
        r=requests.get(url+'/api/messages',headers=headers,timeout=20); r.raise_for_status(); data=r.json(); rows=data.get('messages',data if isinstance(data,list) else [])
        c=conn(); cur=c.cursor()
        for x in rows:
            uid=str(x.get('userId') or x.get('line_user_id') or x.get('sourceUserId') or '')
            if not uid: continue
            name=x.get('displayName') or x.get('display_name') or uid[-8:]
            cur.execute('INSERT INTO customers(line_user_id,display_name) VALUES(?,?) ON CONFLICT(line_user_id) DO UPDATE SET display_name=excluded.display_name',(uid,name))
            body=str(x.get('text') or x.get('message') or x.get('body') or '')
            ts=str(x.get('timestamp') or x.get('created_at') or datetime.now().isoformat(timespec='seconds'))
            cur.execute('INSERT OR IGNORE INTO messages(line_user_id,body,created_at,raw_json) VALUES(?,?,?,?)',(uid,body,ts,json.dumps(x,ensure_ascii=False)))
            mid=cur.lastrowid
            amt,last5=extract_payment(body)
            if (amt or last5) and mid:
                cur.execute('INSERT INTO payment_candidates(line_user_id,amount,last5,message_id) VALUES(?,?,?,?)',(uid,amt,last5,mid))
        c.commit(); c.close()
    def sync_forms(self):
        url=self.cfg.get('forms_sync_url','')
        if not url:return
        r=requests.get(url,timeout=25); r.raise_for_status(); data=r.json(); rows=data.get('bookings',data if isinstance(data,list) else [])
        c=conn(); cur=c.cursor()
        for x in rows:
            key=str(x.get('source_key') or x.get('timestamp') or json.dumps(x,sort_keys=True,ensure_ascii=False))
            raw=str(x.get('request_raw') or x.get('request') or '')
            nums=','.join(sorted(set(re.findall(r'(?<!\d)(\d{1,2})(?:號)?(?!\d)',raw)),key=lambda z:int(z)))
            typ='指定球員' if any(k in raw for k in ['指定','號','球員','主要拍']) else '團體拍攝'
            cur.execute('''INSERT OR IGNORE INTO bookings(source,source_key,event_name,event_date,start_time,venue,age_group,team,opponent,player_text,request_raw,request_type,priority_numbers,amount) VALUES('google_form',?,?,?,?,?,?,?,?,?,?,?,?,?)''',(key,x.get('event_name',''),x.get('event_date',''),x.get('start_time',''),x.get('venue',''),x.get('age_group',''),x.get('team',''),x.get('opponent',''),x.get('player_text',''),raw,typ,nums,int(x.get('amount') or 0)))
        c.commit(); c.close()
    def refresh_all(self):
        c=conn(); cur=c.cursor(); today=datetime.now().strftime('%Y-%m-%d')
        vals={'today':cur.execute('SELECT COUNT(*) FROM bookings WHERE event_date=?',(today,)).fetchone()[0], 'unpaid':cur.execute('SELECT COALESCE(SUM(amount),0) FROM bookings WHERE paid=0').fetchone()[0], 'undelivered':cur.execute("SELECT COUNT(*) FROM bookings WHERE delivery_status!='已交件'").fetchone()[0], 'messages':cur.execute('SELECT COUNT(*) FROM messages').fetchone()[0]}
        for k,v in vals.items(): self.cards[k].v.setText(f'NT$ {v:,}' if k=='unpaid' else str(v))
        rows=cur.execute('SELECT display_name,line_user_id,note,created_at FROM customers ORDER BY id DESC LIMIT 300').fetchall(); self.fill(self.clientt,rows)
        rows=cur.execute("SELECT event_date,start_time,event_name,COALESCE(NULLIF(player_text,''),team),request_type||CASE WHEN priority_numbers!='' THEN ' #'||priority_numbers ELSE '' END,photographer,booking_status,CASE WHEN paid=1 THEN '已收' ELSE '未收' END,delivery_status FROM bookings ORDER BY event_date,start_time").fetchall(); self.fill(self.bookt,rows)
        rows=cur.execute('SELECT line_user_id,amount,last5,status,created_at FROM payment_candidates ORDER BY id DESC LIMIT 300').fetchall(); self.fill(self.payt,rows)
        c.close()
    def fill(self,t,rows):
        t.setRowCount(len(rows))
        for i,r in enumerate(rows):
            for j,v in enumerate(r): t.setItem(i,j,QTableWidgetItem('' if v is None else str(v)))

if __name__=='__main__':
    init_db(); app=QApplication(sys.argv); app.setStyleSheet(STYLE); m=Main(); m.show(); sys.exit(app.exec())
