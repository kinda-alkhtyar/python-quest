import contextlib, http.client, importlib.util, io, json, os, pathlib, tempfile, threading, unittest
from http.server import ThreadingHTTPServer
ROOT=pathlib.Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0,str(ROOT))
import app
class CurriculumTests(unittest.TestCase):
 def test_structure_and_sources(self):
  c=app.CURRICULUM
  self.assertEqual(len(c['chapters']),27)
  self.assertEqual(len({l['id'] for l in c['lessons']}),len(c['lessons']))
  for ch in c['chapters']:
   self.assertTrue(any(l['chapter']==ch['id'] for l in c['lessons']))
   for p in range(ch['start'],ch['end']+1):self.assertTrue((ROOT/f'static/book/pages/{p}.jpg').is_file())
  for l in c['lessons']:
   self.assertTrue(l['explanation']['ar'] and l['explanation']['en'])
   self.assertGreaterEqual(l['exercise_count'],3)
 def test_all_reference_programs_and_variants(self):
  old=os.getcwd()
  for l in app.CURRICULUM['lessons']:
   if l['kind']!='code':continue
   for key,answer in [('code','output'),('variant','variant_output')]:
    with self.subTest(lesson=l['id'],program=key),tempfile.TemporaryDirectory() as d:
     try:
      os.chdir(d);s=io.StringIO()
      with contextlib.redirect_stdout(s):exec(compile(l[key],'<test>','exec'),{})
      self.assertEqual(s.getvalue().rstrip('\n'),l[answer])
     finally:os.chdir(old)
 def test_deterministic_grading(self):
  for l in app.LESSONS.values():
   if l['kind']=='code':
    self.assertTrue(app.grade(l,0,l['variant_output']))
    self.assertFalse(app.grade(l,0,'WRONG'+l['variant_output']))
    self.assertTrue(app.grade(l,1,l['token']))
   else:
    for i,q in enumerate(l['questions']):
     self.assertTrue(app.grade(l,i,q['answer']))
     self.assertFalse(app.grade(l,i,(q['answer']+1)%len(q['options'])))
 def test_invalid_exercises(self):
  l=next(iter(app.LESSONS.values()))
  for value in [-1,999,'0',True]:
   with self.assertRaises(ValueError):app.grade(l,value,'anything')
class APITests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.tmp=tempfile.TemporaryDirectory();cls.old=app.DB_PATH;app.DB_PATH=pathlib.Path(cls.tmp.name)/'test.sqlite3';app.init_db()
  cls.server=ThreadingHTTPServer(('127.0.0.1',0),app.Handler);cls.port=cls.server.server_port
  cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
 @classmethod
 def tearDownClass(cls):
  cls.server.shutdown();cls.server.server_close();cls.thread.join();app.DB_PATH=cls.old;cls.tmp.cleanup()
 def request(self,path,body=None,cookie='',headers=None):
  conn=http.client.HTTPConnection('127.0.0.1',self.port)
  h={'Content-Type':'application/json','Cookie':cookie};h.update(headers or {})
  conn.request('POST' if body is not None else 'GET',path,json.dumps(body) if body is not None else None,h)
  r=conn.getresponse();raw=r.read();result=(r.status,dict(r.getheaders()),raw);conn.close();return result
 def test_public_and_traversal(self):
  self.assertEqual(self.request('/')[0],200)
  self.assertEqual(self.request('/api/curriculum')[0],200)
  self.assertEqual(self.request('/../app.py')[0],404)
  self.assertEqual(self.request('/api/progress')[0],401)
  self.assertEqual(self.request('/vendor/pyodide/pyodide.asm.wasm')[1]['Content-Type'],'application/wasm')
 def test_account_progress_and_deduplication(self):
  r,h,b=self.request('/api/register',{'username':'student1','password':'a strong password'})
  self.assertEqual(r,200);cookie=h['Set-Cookie'].split(';')[0];csrf=json.loads(b)['csrf']
  self.assertIn('HttpOnly',h['Set-Cookie'])
  self.assertEqual(self.request('/api/register',{'username':'student1','password':'another password'})[0],409)
  self.assertEqual(self.request('/api/login',{'username':'student1','password':'incorrect password'})[0],401)
  l=next(l for l in app.LESSONS.values() if l['kind']=='code');body={'csrf':csrf,'lesson':l['id'],'exercise':0,'answer':l['variant_output']}
  self.assertEqual(json.loads(self.request('/api/attempt',body,cookie)[2])['awarded'],10)
  self.assertEqual(json.loads(self.request('/api/attempt',body,cookie)[2])['awarded'],0)
  body['answer']='WRONG';self.assertFalse(json.loads(self.request('/api/attempt',body,cookie)[2])['correct'])
  progress=json.loads(self.request('/api/progress',cookie=cookie)[2]);self.assertEqual(progress['xp'],10);self.assertEqual(progress['attempts'],3)
  self.assertEqual(progress['mistakes'],[])
  self.assertEqual(self.request('/api/note',{'csrf':csrf,'lesson':l['id'],'value':'ملاحظة عربية'},cookie)[0],200)
  self.assertEqual(json.loads(self.request('/api/progress',cookie=cookie)[2])['notes'][l['id']],'ملاحظة عربية')
  self.assertEqual(self.request('/api/attempt',{**body,'csrf':'wrong'},cookie)[0],403)
  self.assertEqual(self.request('/api/attempt',body,cookie,{'Origin':'https://evil.invalid'})[0],403)
  self.assertEqual(self.request('/api/logout',{'csrf':csrf},cookie)[0],200)
  self.assertEqual(self.request('/api/progress',cookie=cookie)[0],401)
 def test_invalid_and_isolation(self):
  status,h,b=self.request('/api/register',{'username':'student2','password':'password222'})
  cookie=h['Set-Cookie'].split(';')[0];csrf=json.loads(b)['csrf']
  self.assertEqual(json.loads(self.request('/api/progress',cookie=cookie)[2])['xp'],0)
  self.assertEqual(self.request('/api/attempt',{'csrf':csrf,'lesson':'missing','exercise':0,'answer':''},cookie)[0],400)
  self.assertEqual(self.request('/api/register',{'username':'x','password':'123'})[0],400)
if __name__=='__main__':unittest.main(verbosity=2)
