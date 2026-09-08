import sys, os, tempfile
sys.path.insert(0, '.')
# 使用临时数据目录避免污染 data.json
from ssh_web_tool.storage import storage
d = tempfile.mkdtemp()
storage.data_file = os.path.join(d, 'data.json')
storage._data = storage._load()

from fastapi.testclient import TestClient
import main

c = TestClient(main.app)

# 1. 新建主机
r = c.post('/api/hosts', json={'name': 'web1', 'host': '10.0.0.1', 'username': 'root', 'group': 'prod'})
assert r.status_code == 200, r.text
hid = r.json()['id']

# 2. 复制主机（原 bug）
r = c.post(f'/api/hosts/{hid}/duplicate')
assert r.status_code == 200, r.text
assert r.json()['name'] == 'web1 副本'
print('2. 复制主机 API OK')

# 3. 复制分组（含主机）
c.post('/api/hosts', json={'name': 'web2', 'host': '10.0.0.2', 'username': 'root', 'group': 'prod'})
r = c.post('/api/groups/prod/duplicate')
assert r.status_code == 200, r.text
# prod 组内：web1 + web1副本 + web2 = 3 台
assert r.json()['name'] == 'prod 副本' and len(r.json()['copied_hosts']) == 3
print('3. 复制分组 API OK:', r.json()['name'], 'copied:', len(r.json()['copied_hosts']))

# 4. 排序 API
r = c.post('/api/hosts/reorder', json={'ids': [r.json()['copied_hosts'][0]['id'], hid]})
assert r.status_code == 200
r = c.post('/api/groups/reorder', json={'names': ['prod 副本', 'prod']})
assert r.status_code == 200
print('4. 排序 API OK')

# 5. 快捷指令新字段
r = c.post('/api/quick-commands', json={'name': '部署', 'command': 'sh deploy.sh {args}', 'type': 'param', 'pre_ops': [{'type': 'chmod', 'mode': '+x', 'path': '/a.sh'}]})
assert r.status_code == 200, r.text
qc = r.json()
assert qc['type'] == 'param' and qc['pre_ops'][0]['type'] == 'chmod'
assert 'param_hint' not in qc  # 字段已废弃，新数据不再写入
r = c.put(f"/api/quick-commands/{qc['id']}", json={'name': '部署2', 'command': 'echo {args}', 'type': 'param', 'pre_ops': []})
assert r.status_code == 200
print('5. 快捷指令 API OK')

# 6. hosts 列表带连接信息字段
r = c.get('/api/hosts')
assert r.status_code == 200
for h in r.json()['hosts']:
    assert 'connected_since' in h and 'connected_duration' in h
print('6. hosts 连接信息字段 OK')

# 7. 404 场景
r = c.post('/api/groups/不存在/duplicate')
assert r.status_code == 404
print('7. 404 处理 OK')
print('SMOKE_OK')
