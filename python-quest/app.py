"""Python Quest: local, standard-library web server. Python 3.10+."""
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
import argparse, hashlib, hmac, json, mimetypes, os, secrets, sqlite3, time
from urllib.parse import urlsplit
ROOT = Path(__file__).resolve().parent
CURRICULUM = json.loads((ROOT / 'data/curriculum.json').read_text(encoding='utf-8'))
LESSONS = {x['id']: x for x in CURRICULUM['lessons']}
DB_PATH = ROOT / 'data/progress.sqlite3'

def connect():
 db = sqlite3.connect(DB_PATH, timeout=10)
 db.row_factory = sqlite3.Row
 db.execute('PRAGMA foreign_keys=ON')
 return db

def init_db():
 with connect() as db:
  db.executescript('''
  PRAGMA journal_mode=WAL;
  CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, salt TEXT NOT NULL, password TEXT NOT NULL);
  CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, user_id INTEGER REFERENCES users(id), csrf TEXT NOT NULL, expires INTEGER NOT NULL);
  CREATE TABLE IF NOT EXISTS attempts(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), lesson TEXT NOT NULL, exercise INTEGER NOT NULL, correct INTEGER NOT NULL, answer TEXT NOT NULL, created INTEGER NOT NULL);
  CREATE TABLE IF NOT EXISTS solved(user_id INTEGER NOT NULL REFERENCES users(id), lesson TEXT NOT NULL, exercise INTEGER NOT NULL, PRIMARY KEY(user_id,lesson,exercise));
  CREATE TABLE IF NOT EXISTS drafts(user_id INTEGER NOT NULL REFERENCES users(id), lesson TEXT NOT NULL, code TEXT NOT NULL, PRIMARY KEY(user_id,lesson));
  CREATE TABLE IF NOT EXISTS notes(user_id INTEGER NOT NULL REFERENCES users(id), lesson TEXT NOT NULL, text TEXT NOT NULL, PRIMARY KEY(user_id,lesson));
  ''')

def password_hash(password, salt):
 return hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 200_000).hex()

def grade(lesson, exercise, answer):
 """Grade deterministic items on the server; code is checked in browser WASM."""
 if type(exercise) is not int or not 0 <= exercise < lesson['exercise_count']:
  raise ValueError('Invalid exercise')
 if lesson['kind'] == 'theory':
  return type(answer) is int and answer == lesson['questions'][exercise]['answer']
 if exercise == 0:
  return isinstance(answer,str) and answer.replace('\r\n','\n').rstrip('\n') == lesson['variant_output']
 if exercise == 1:
  return isinstance(answer,str) and answer.strip() == lesson['token']
 # Browser code execution cannot certify a high-stakes examination. It is local practice.
 if exercise in (2,3):
  return isinstance(answer,dict) and answer.get('passed') is True and isinstance(answer.get('code'),str) and len(answer['code']) <= 20000
 return False

class Handler(BaseHTTPRequestHandler):
 server_version='PythonQuest/1.0'
 def log_message(self, fmt, *args):
  # Never log passwords, cookies or student answers.
  pass
 def reply(self, status, payload, cookie=None):
  data=json.dumps(payload,ensure_ascii=False).encode('utf-8')
  self.send_response(status);self.send_header('Content-Type','application/json; charset=utf-8')
  self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-store')
  self.send_header('X-Content-Type-Options','nosniff')
  if cookie:self.send_header('Set-Cookie',cookie)
  self.end_headers();self.wfile.write(data)
 def session(self):
  cookies={}
  for part in self.headers.get('Cookie','').split(';'):
   if '=' in part:
    k,v=part.strip().split('=',1);cookies[k]=v
  with connect() as db:
   row=db.execute('SELECT s.*, u.username FROM sessions s JOIN users u ON u.id=s.user_id WHERE token=? AND expires>?',(cookies.get('pq_session',''),int(time.time()))).fetchone()
  return row
 def do_GET(self):
  path=urlsplit(self.path).path
  if path=='/api/curriculum':return self.reply(200,CURRICULUM)
  if path=='/api/session':
   s=self.session()
   return self.reply(200,{'user':s['username'],'csrf':s['csrf']} if s else {'user':None})
  if path in ('/api/progress','/api/export'):
   s=self.session()
   if not s:return self.reply(401,{'error':'login'})
   with connect() as db:
    solved=[dict(r) for r in db.execute('SELECT lesson,exercise FROM solved WHERE user_id=?',(s['user_id'],))]
    mistakes=[dict(r) for r in db.execute('SELECT a.lesson,a.exercise,COUNT(*) AS count FROM attempts a WHERE a.user_id=? AND a.correct=0 AND NOT EXISTS (SELECT 1 FROM solved s WHERE s.user_id=a.user_id AND s.lesson=a.lesson AND s.exercise=a.exercise) GROUP BY a.lesson,a.exercise',(s['user_id'],))]
    attempts=db.execute('SELECT COUNT(*) FROM attempts WHERE user_id=?',(s['user_id'],)).fetchone()[0]
    drafts={r['lesson']:r['code'] for r in db.execute('SELECT lesson,code FROM drafts WHERE user_id=?',(s['user_id'],))}
    notes={r['lesson']:r['text'] for r in db.execute('SELECT lesson,text FROM notes WHERE user_id=?',(s['user_id'],))}
   return self.reply(200,{'solved':solved,'mistakes':mistakes,'attempts':attempts,'drafts':drafts,'notes':notes,'xp':len(solved)*10})
  if path.startswith('/api/'):return self.reply(404,{'error':'not_found'})
  target=(ROOT/'static'/('index.html' if path=='/' else path.lstrip('/'))).resolve()
  if not target.is_relative_to((ROOT/'static').resolve()) or not target.is_file():return self.reply(404,{'error':'not_found'})
  types={'.wasm':'application/wasm','.mjs':'text/javascript','.js':'text/javascript','.ttf':'font/ttf'}
  ctype=types.get(target.suffix,mimetypes.guess_type(str(target))[0] or 'application/octet-stream')
  self.send_response(200);self.send_header('Content-Type',ctype)
  self.send_header('Content-Length',str(target.stat().st_size));self.send_header('X-Content-Type-Options','nosniff')
  self.send_header('Cache-Control','no-cache' if target.suffix in ('.html','.js','.mjs','.css') else 'public, max-age=86400')
  self.end_headers()
  with target.open('rb') as f:
   while chunk:=f.read(65536):self.wfile.write(chunk)
 def do_POST(self):
  try:self._post()
  except (ValueError,TypeError,KeyError,json.JSONDecodeError):self.reply(400,{'error':'invalid_request'})
  except sqlite3.Error:self.reply(500,{'error':'database_error'})
 def _post(self):
  origin=self.headers.get('Origin')
  if origin and urlsplit(origin).netloc != self.headers.get('Host'):return self.reply(403,{'error':'origin'})
  if not self.headers.get('Content-Type','').startswith('application/json'):return self.reply(415,{'error':'json_required'})
  n=int(self.headers.get('Content-Length','0'))
  if not 0<n<=40000:return self.reply(413,{'error':'too_large'})
  body=json.loads(self.rfile.read(n))
  if not isinstance(body,dict):raise ValueError()
  path=urlsplit(self.path).path
  if path in ('/api/register','/api/login'):
   name=body.get('username','');password=body.get('password','')
   if not isinstance(name,str):raise ValueError()
   name=name.strip()
   if not isinstance(password,str) or not 2<=len(name)<=40 or not 8<=len(password)<=128:return self.reply(400,{'error':'credentials_length'})
   with connect() as db:
    if path=='/api/register':
     salt=secrets.token_hex(16)
     try:db.execute('INSERT INTO users(username,salt,password) VALUES(?,?,?)',(name,salt,password_hash(password,salt)))
     except sqlite3.IntegrityError:return self.reply(409,{'error':'username_exists'})
    u=db.execute('SELECT * FROM users WHERE username=?',(name,)).fetchone()
    if not u or not hmac.compare_digest(u['password'],password_hash(password,u['salt'])):return self.reply(401,{'error':'wrong_credentials'})
    token=secrets.token_urlsafe(32);csrf=secrets.token_urlsafe(24)
    db.execute('DELETE FROM sessions WHERE expires<=?',(int(time.time()),))
    db.execute('INSERT INTO sessions VALUES(?,?,?,?)',(token,u['id'],csrf,int(time.time())+86400*30))
   return self.reply(200,{'user':name,'csrf':csrf},f'pq_session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=2592000')
  s=self.session()
  if not s:return self.reply(401,{'error':'login'})
  if not hmac.compare_digest(str(body.get('csrf','')),s['csrf']):return self.reply(403,{'error':'csrf'})
  if path=='/api/logout':
   with connect() as db:db.execute('DELETE FROM sessions WHERE token=?',(s['token'],))
   return self.reply(200,{'ok':True},'pq_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0')
  if path in ('/api/draft','/api/note'):
   lid=body.get('lesson');value=body.get('value','')
   if lid not in LESSONS or not isinstance(value,str) or len(value)>20000:raise ValueError()
   table,col=('drafts','code') if path.endswith('draft') else ('notes','text')
   with connect() as db:db.execute(f'INSERT INTO {table} VALUES(?,?,?) ON CONFLICT(user_id,lesson) DO UPDATE SET {col}=excluded.{col}',(s['user_id'],lid,value))
   return self.reply(200,{'ok':True})
  if path=='/api/attempt':
   lid=body.get('lesson');q=body.get('exercise');answer=body.get('answer')
   if lid not in LESSONS:raise ValueError()
   correct=grade(LESSONS[lid],q,answer)
   with connect() as db:
    db.execute('INSERT INTO attempts(user_id,lesson,exercise,correct,answer,created) VALUES(?,?,?,?,?,?)',(s['user_id'],lid,q,int(correct),json.dumps(answer,ensure_ascii=False),int(time.time())))
    awarded=0
    if correct:
     c=db.execute('INSERT OR IGNORE INTO solved VALUES(?,?,?)',(s['user_id'],lid,q));awarded=10 if c.rowcount else 0
   return self.reply(200,{'correct':correct,'awarded':awarded})
  return self.reply(404,{'error':'not_found'})

if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('--strictPort',action='store_true',help=argparse.SUPPRESS);parser.add_argument('--host',default='127.0.0.1');parser.add_argument('--port',type=int,default=int(os.getenv('PORT','8000')));args=parser.parse_args()
 init_db()
 print(f'Python Quest: http://{args.host}:{args.port}',flush=True)
 try:ThreadingHTTPServer((args.host,args.port),Handler).serve_forever()
 except KeyboardInterrupt:print('\nStopped.')
