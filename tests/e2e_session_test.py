"""端到端 SSH 会话测试：创建会话 → WebSocket 执行命令 → 验证连接信息"""
import asyncio, json, sys, urllib.request

BASE = 'http://127.0.0.1:8765'
HOST_ID = sys.argv[1] if len(sys.argv) > 1 else '24ae9ee4'


def api(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


async def main():
    # 1. 创建会话（从主机配置）
    st, sess = api('POST', '/api/sessions/from-host', {'host_id': HOST_ID, 'terminal_name': '测试终端'})
    assert st == 200, (st, sess)
    sid = sess['session_id']
    print('1. 会话创建 OK:', sid)

    # 2. WebSocket 连接并执行命令
    import websockets
    async with websockets.connect(f'ws://127.0.0.1:8765/ws/ssh/{sid}', max_size=10 * 1024 * 1024) as ws:
        await ws.send(json.dumps({'type': 'input', 'data': 'echo HELLO_WS_TEST && uname -a && whoami && pwd\r'}))
        out = ''
        deadline = asyncio.get_event_loop().time() + 20
        while asyncio.get_event_loop().time() < deadline:
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=3)
            except asyncio.TimeoutError:
                continue
            m = json.loads(msg)
            if m.get('type') == 'output':
                out += m.get('data', '')
                if 'HELLO_WS_TEST' in out and 'Linux' in out and 'pwd' in out and '# ' in out:
                    break
        assert 'HELLO_WS_TEST' in out, '命令输出未返回: ' + out[-300:]
        assert 'Linux' in out, out[-300:]
        print('2. WebSocket 执行命令 OK:')
        for line in out.strip().splitlines()[:6]:
            print('   |', line)

    # 3. 会话 active 状态 + host_name 兜底（第11条）
    st, act = api('GET', '/api/sessions/active')
    assert st == 200
    mine = [s for s in act.get('terminals', []) if s.get('session_id') == sid]
    assert mine, '会话不在 active 列表'
    print('3. active 列表 OK: host_name =', repr(mine[0].get('host_name')), 'terminal_name =', repr(mine[0].get('terminal_name')))

    # 4. hosts 连接信息（第9条）
    st, hs = api('GET', '/api/hosts')
    me = next(h for h in hs['hosts'] if h['id'] == HOST_ID)
    assert me.get('connected_since') and me.get('connected_duration') is not None
    print('4. 连接信息 OK: since =', me['connected_since'], 'duration =', me['connected_duration'], '秒')

    # 5. SFTP 上传（预操作 upload 依赖，第5条）
    import io
    boundary = '----e2etest'
    body = io.BytesIO()
    body.write(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="e2e_upload.txt"\r\nContent-Type: text/plain\r\n\r\n'.encode())
    body.write(b'hello from e2e test\n')
    body.write(f'\r\n--{boundary}--\r\n'.encode())
    req = urllib.request.Request(
        BASE + f'/api/sftp/{sid}/upload?remote_path=/tmp/e2e_upload.txt',
        data=body.getvalue(), method='POST',
        headers={'Content-Type': f'multipart/form-data; boundary={boundary}'})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            resp = json.loads(r.read().decode())
        assert r.status == 200, resp
        print('5. SFTP 上传 OK:', resp)
    except urllib.error.HTTPError as e:
        print('5. SFTP 上传 FAIL:', e.code, e.read().decode()[:300])
        raise

    # 6. 上传后执行命令验证文件存在
    async with websockets.connect(f'ws://127.0.0.1:8765/ws/ssh/{sid}', max_size=10 * 1024 * 1024) as ws:
        await ws.send(json.dumps({'type': 'input', 'data': 'cat /tmp/e2e_upload.txt && wc -c /tmp/e2e_upload.txt\r'}))
        out2 = ''
        deadline = asyncio.get_event_loop().time() + 15
        while asyncio.get_event_loop().time() < deadline:
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=3)
            except asyncio.TimeoutError:
                continue
            m = json.loads(msg)
            if m.get('type') == 'output':
                out2 += m.get('data', '')
                if 'hello from e2e test' in out2 and 'e2e_upload.txt' in out2 and '# ' in out2:
                    break
        assert 'hello from e2e test' in out2, '上传文件内容未读到: ' + out2[-300:]
        print('6. 上传文件可读 OK')

    print('E2E_OK')
    # 保留会话（前台会显示）；不主动断开验证重连能力


if __name__ == '__main__':
    asyncio.run(main())
