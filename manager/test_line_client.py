import ast, io, json, re, unittest, urllib.request, urllib.error
from pathlib import Path
source=ast.parse(Path('rexue_manager.py').read_text())
nodes=[n for n in source.body if isinstance(n,(ast.FunctionDef,ast.ClassDef)) and n.name in ('NoCredentialRedirect','fetch_line_messages')]
ns={'urllib':urllib,'json':json,'re':re,'LINE_API_URL':'https://example.test/api/messages'}
exec(compile(ast.Module(body=nodes,type_ignores=[]),'client','exec'),ns)
class Opener:
 def __init__(self,code=200,body=b'{"messages":[]}'): self.code,self.body=code,body
 def open(self,req,timeout):
  assert req.get_header('User-agent')=='RexueManager/1.1'
  assert req.get_header('Authorization')=='Bearer test-token'
  if self.code!=200: raise urllib.error.HTTPError(req.full_url,self.code,'error',{},io.BytesIO(self.body))
  return io.BytesIO(self.body)
class ClientTests(unittest.TestCase):
 def test_success(self): self.assertEqual(ns['fetch_line_messages'](' test-token ',Opener()),[])
 def test_errors(self):
  for code,body,expected in [(401,b'Unauthorized','401'),(403,b'error code: 1010','1010'),(403,b'Forbidden','尚不能判定'),(404,b'Not found','404'),(503,b'Unavailable','503'),(200,b'<html>Login</html>','未回傳'),(200,b'{}','格式不符')]:
   with self.subTest(code=code,body=body),self.assertRaisesRegex(RuntimeError,expected): ns['fetch_line_messages']('test-token',Opener(code,body))
 def test_redirect_stops_secret(self):
  with self.assertRaises(RuntimeError): ns['NoCredentialRedirect']().redirect_request(None,None,302,'',{},'https://other.test/')
 def test_invalid_token(self):
  with self.assertRaises(ValueError): ns['fetch_line_messages']('bad\nvalue',Opener())
if __name__=='__main__': unittest.main()
