import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import unittest, tempfile, sys, sqlite3
from pathlib import Path
from unittest.mock import patch, Mock
if hasattr(sys.modules['__main__'], 'init_db'):
    app = sys.modules['__main__']
else:
    import app_v4 as app
from PySide6.QtWidgets import QApplication

class CoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        base=Path(self.tmp.name)
        self.patches=[]
        for key,file in [('DB','data.db'),('CFG','config.json'),('TOKEN_FILE','google.json'),('CLIENT_FILE','client.json'),('LOG_FILE','log.txt')]:
            p=patch.object(app,key,base/file); p.start(); self.patches.append(p)
        app.init_db()
        with patch.object(app.QTimer,'singleShot'):
            self.window=app.Main()
        self.window.timer.stop()
        self.window.cfg.update(line_worker_url='https://example.test/webhook',line_api_token='test-token')

    def tearDown(self):
        self.window.close()
        for p in reversed(self.patches): p.stop()
        self.tmp.cleanup()

    def count(self,table):
        c=app.conn()
        try:return c.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]
        finally:c.close()

    def fetch(self,payload):
        response=Mock(status_code=200)
        response.json.return_value=payload
        with patch.object(app.requests,'get',return_value=response):
            return self.window.sync_line_worker()

    def test_actual_worker_contract_and_repeat(self):
        payload={'messages':[{'sender_id':'Utest','message_key':'key1','text_content':'已付款 2,500 後五碼 01234','sent_at':1789500000000,'display_name':'家長'}]}
        self.assertEqual(self.fetch(payload),1)
        self.assertEqual(self.fetch(payload),0)
        self.assertEqual(self.count('messages'),1)
        self.assertEqual(self.count('payment_candidates'),1)
        c=app.conn()
        try:
            p=c.execute('SELECT * FROM payment_candidates').fetchone()
            self.assertEqual(p['amount'],2500);self.assertEqual(p['last5'],'01234')
            self.assertEqual(p['status'],'待確認')
        finally:c.close()

    def test_array_payload_and_revocation(self):
        self.fetch([{'sender_id':'Utest','message_key':'k','text_content':'付款 1600','sent_at':1789500000000}])
        self.fetch({'messages':[{'message_key':'k','revoked':1}]})
        c=app.conn()
        try:
            self.assertEqual(c.execute('SELECT body FROM messages').fetchone()[0],'[訊息已收回]')
            self.assertEqual(c.execute('SELECT status FROM payment_candidates').fetchone()[0],'已收回')
        finally:c.close()

    def test_invalid_payload_not_success(self):
        with self.assertRaises(RuntimeError):self.fetch({'error':'bad'})

    def test_preserves_customer_alias(self):
        self.fetch([{'sender_id':'Utest','message_key':'k','text_content':'你好','sent_at':1789500000000,'display_name':'原名'}])
        c=app.conn()
        c.execute("UPDATE customers SET custom_name='自訂姓名',note='保留筆記'");c.commit();c.close()
        self.fetch([{'sender_id':'Utest','message_key':'k','text_content':'你好','sent_at':1789500000000}])
        self.window.refresh_all()
        self.assertEqual(self.window.clientt.item(0,0).text(),'自訂姓名')
        self.assertEqual(self.window.clientt.item(0,2).text(),'保留筆記')

    def test_row_identity_survives_sort(self):
        headers=['時間戳記','比賽日期','球員姓名']
        row=['2026/09/16 08:31:11','2026/09/20','測試球員']
        a=app.parse_form_row('預約','sheet',2,headers,row)
        b=app.parse_form_row('預約','sheet',20,headers,row)
        self.assertEqual(a['source_key'],b['source_key'])

    def test_migration_backup_preserves_data(self):
        c=app.conn();c.execute("INSERT INTO customers(line_user_id,display_name,note) VALUES('u','名字','舊備註')");c.commit();c.close()
        app.init_db()
        self.assertTrue(app.DB.with_name('rexue_manager.before-v4.1.db').exists())
        self.assertEqual(self.count('customers'),1)

    def test_no_google_refresh_on_ui_startup(self):
        with patch.object(app,'find_client_secret'),patch.object(app,'google_creds') as creds,patch.object(app.QTimer,'singleShot'):
            self.window.startup();creds.assert_not_called()

    def test_confirmed_payment_cannot_repeat(self):
        c=app.conn();c.execute("INSERT INTO payment_candidates(id,status,amount) VALUES(1,'已確認',2500)");c.commit();c.close()
        self.window.refresh_all();self.window.payt.selectRow(0)
        with patch.object(app.QMessageBox,'warning'),patch.object(self.window,'append_finance') as append:
            self.window.confirm_payment();append.assert_not_called()

    def test_table_readonly_and_booking_id(self):
        c=app.conn();c.execute("INSERT INTO bookings(id,event_name,event_date) VALUES(5,'賽事','2026-09-20')");c.commit();c.close()
        self.window.refresh_all()
        self.assertEqual(self.window.bookt.item(0,0).data(app.Qt.ItemDataRole.UserRole),5)
        self.assertEqual(self.window.bookt.editTriggers(),app.QAbstractItemView.EditTrigger.NoEditTriggers)

    def test_finance_plain_text_not_formula(self):
        sheets=Mock()
        with patch.object(app,'google_services',return_value=(None,sheets)):
            self.window.append_finance({'event_name':'=HYPERLINK("bad")','amount':1000},{'amount':1000})
        kwargs=sheets.spreadsheets().values().append.call_args.kwargs
        self.assertEqual(kwargs['valueInputOption'],'RAW')

    def test_google_response_tab_and_preserved_status(self):
        drive=Mock();sheets=Mock()
        drive.files().list().execute.return_value={'files':[{'id':'s','name':'賽事預約','modifiedTime':'1'}]}
        sheets.spreadsheets().get().execute.return_value={'sheets':[{'properties':{'title':'說明','index':0}},{'properties':{'title':'表單回應 1','index':1}}]}
        sheets.spreadsheets().values().get().execute.return_value={'values':[['時間戳記','比賽日期','球員姓名'],['2026/9/16 08:30','2026/9/20','測試球員']]}
        with patch.object(app,'google_services',return_value=(drive,sheets)):
            self.assertEqual(self.window.sync_google_worker(lambda _:None),(1,1))
            c=app.conn();c.execute("UPDATE bookings SET amount=2500,photographer='攝影師',delivery_status='已交件'");c.commit();c.close()
            drive.files().list().execute.return_value={'files':[{'id':'s','name':'賽事預約','modifiedTime':'2'}]}
            self.window.sync_google_worker(lambda _:None)
        kwargs=sheets.spreadsheets().values().get.call_args.kwargs
        self.assertIn('表單回應 1',kwargs['range'])
        self.assertEqual(self.count('bookings'),1)
        c=app.conn()
        try:
            b=c.execute('SELECT * FROM bookings').fetchone()
            self.assertEqual(b['amount'],2500);self.assertEqual(b['delivery_status'],'已交件')
        finally:c.close()

if __name__=='__main__':unittest.main()
