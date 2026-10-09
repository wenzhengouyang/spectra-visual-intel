import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from spectra_agent.account_server import Store, EMPTY, make_server


class AccountStoreTest(unittest.TestCase):
    def test_password_hash_and_revocation(self):
        with tempfile.TemporaryDirectory() as temporary:
            store=Store(Path(temporary)/'accounts.db')
            self.assertTrue(store.register('alice', 'test-password-one'))
            self.assertFalse(store.register('alice', 'test-password-two'))
            self.assertTrue(store.verify_password('alice', 'test-password-one'))
            self.assertFalse(store.verify_password('alice', 'wrong-password'))
            self.assertFalse(store.verify_password('missing', 'test-password-one'))
            token=store.login('local:alice','alice')
            self.assertNotIn(b'test-password-one', store.path.read_bytes())
            store.change_password('alice','test-password-new')
            self.assertIsNone(store.session(token))
            self.assertTrue(store.verify_password('alice','test-password-new'))

    def test_isolation_revision_and_persistence(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'accounts.db'
            store=Store(path)
            a=store.login('a','a'); b=store.login('b','b')
            data={**EMPTY,'interests':['视频生成']}
            self.assertTrue(store.save('a',0,data))
            self.assertFalse(store.save('a',0,{**EMPTY,'interests':['世界模型']}))
            self.assertEqual(json.loads(store.session(b)['data']),EMPTY)
            self.assertEqual(json.loads(Store(path).session(a)['data'])['interests'],['视频生成'])

    def test_rate_limit(self):
        with tempfile.TemporaryDirectory() as temporary:
            store=Store(Path(temporary)/'accounts.db')
            self.assertTrue(all(store.allow_attempt('local') for _ in range(20)))
            self.assertFalse(store.allow_attempt('local'))


class AccountHTTPTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        root=Path(self.tmp.name)
        (root/'index.html').write_text('public site')
        (root/'.env.local').write_text('never public')
        self.server=make_server(root,root/'accounts.db','http://127.0.0.1',0,True)
        self.port=self.server.server_address[1]
        self.server.base=f'http://127.0.0.1:{self.port}'
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.cookie='';self.csrf=''

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();self.tmp.cleanup()

    def request(self,path,payload=None,origin=None,csrf=None):
        conn=http.client.HTTPConnection('127.0.0.1',self.port,timeout=5)
        headers={'Cookie':self.cookie,'Origin':origin or self.server.base,'X-CSRF-Token':self.csrf if csrf is None else csrf}
        if payload is not None:headers['Content-Type']='application/json'
        conn.request('POST' if payload is not None else 'GET',path,json.dumps(payload) if payload is not None else None,headers)
        response=conn.getresponse();body=response.read();cookies=response.getheader('Set-Cookie')
        result=(response.status,json.loads(body) if body.startswith(b'{') else body.decode(),cookies)
        conn.close();return result

    def register(self):
        status,_,cookie=self.request('/api/account/register',{'username':'alice','password':'test-password-one'})
        self.assertEqual(status,200);self.assertIn('HttpOnly',cookie);self.assertIn('SameSite=Lax',cookie)
        self.cookie=cookie.split(';')[0];self.csrf=self.request('/api/account')[1]['csrf']

    def test_register_save_and_logout(self):
        self.register()
        data={**EMPTY,'interests':['世界模型'],'favorites':['story_1'],'history':['story_1']}
        self.assertEqual(self.request('/api/account/data',{'revision':0,'data':data})[0],200)
        self.assertEqual(self.request('/api/account')[1]['data'],data)
        self.assertEqual(self.request('/api/account/data',{'revision':0,'data':data})[0],409)
        self.assertEqual(self.request('/api/account/logout',{})[0],200)
        self.assertIsNone(self.request('/api/account')[1]['user'])

    def test_auth_csrf_and_private_paths(self):
        self.assertEqual(self.request('/api/account/data',{})[0],401)
        self.assertEqual(self.request('/api/account/register',{'username':'alice','password':'test-password-one'},origin='https://evil.invalid')[0],403)
        self.register()
        self.assertEqual(self.request('/api/account/data',{},csrf='wrong')[0],403)
        for path in ['/.env.local','/accounts.db','/spectra_agent/config.v0.1.json','/assets/%2e%2e/.env.local']:
            self.assertEqual(self.request(path)[0],404)

    def test_password_change_revokes_other_device(self):
        self.register();old_cookie=self.cookie
        result=self.request('/api/account/password',{'old_password':'test-password-one','new_password':'test-password-two'})
        self.assertEqual(result[0],200)
        self.assertIsNone(self.request('/api/account')[1]['user'])
        self.cookie=result[2].split(';')[0]
        self.assertIsNotNone(self.request('/api/account')[1]['user'])

    def test_registration_can_be_closed_and_validation_fails_closed(self):
        self.server.registration=False
        self.assertEqual(self.request('/api/account/register',{'username':'alice','password':'test-password-one'})[0],403)
        self.assertEqual(self.request('/api/account/login',{'username':'alice','password':'short'})[0],400)
