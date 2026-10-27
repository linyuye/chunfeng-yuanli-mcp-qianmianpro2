'use strict';
const $ = id => document.getElementById(id);
const fragment = new URLSearchParams(location.hash.slice(1));
let token = fragment.get('token') || sessionStorage.getItem('yuanli-token') || '';
if (token) sessionStorage.setItem('yuanli-token', token);
if (location.hash) history.replaceState(null, '', location.pathname);
window.addEventListener('hashchange', () => {
  const fresh = new URLSearchParams(location.hash.slice(1)).get('token');
  if (fresh) {
    token = fresh;
    sessionStorage.setItem('yuanli-token', token);
    history.replaceState(null, '', location.pathname);
    $('notice').hidden = true;
    poll();
  }
});
let state = {connected: false, devices: [], logs: []};
let busy = false;
let pending = 0;
let polling = false;
let lastLog = 0;

function notice(message, error = false) {
  $('notice').hidden = false;
  $('notice').textContent = message;
  $('notice').classList.toggle('error', error);
}

async function api(route, data, timeout = 48000) {
  const response = await fetch('/api/' + route, {
    method: data === undefined ? 'GET' : 'POST',
    headers: {'X-Control-Token': token, ...(data === undefined ? {} : {'Content-Type': 'application/json'})},
    body: data === undefined ? undefined : JSON.stringify(data),
    signal: AbortSignal.timeout(timeout),
  });
  const result = await response.json();
  if (!response.ok) {
    const error = new Error(result.error || '操作失败');
    error.status = response.status;
    throw error;
  }
  return result.result;
}

function render(next) {
  state = next;
  const connected = state.connected;
  const working = busy || state.scanning || state.connecting;
  $('connection-badge').textContent = state.scanning ? '扫描中' : state.connecting ? '连接中' : connected ? '已连接' : '未连接';
  $('connection-badge').className = 'badge' + (connected ? ' connected' : working ? ' working' : '');
  $('connection-description').textContent = connected ? (state.protocol_match ? '已匹配 FFAC 控制协议；可用动作仍需按型号实测。' : '已连接，但尚未确认可控制协议。') : '扫描附近蓝牙 LE 设备，选择目标后连接并检查控制协议。';
  $('address').textContent = connected ? `${state.device_name || '未知设备'} · ${state.address}` : `${state.devices.length} 台附近设备`;
  $('scan').disabled = working || connected;
  $('connect').disabled = working || connected || !$('devices').value;
  $('connect').hidden = connected;
  $('disconnect').hidden = !connected;
  $('disconnect').disabled = working;
  $('devices').hidden = connected;
  $('devices').disabled = working;
  for (const id of ['suction', 'vibration']) $(id).disabled = !connected || working || !(state.available_actions || []).includes(id);
  $('vibration-level').disabled = !(state.available_actions || []).includes('vibration');
  $('duration').disabled = !(state.available_actions || []).includes('vibration');
  $('stop').disabled = !connected;
  $('vibration-status').textContent = '1 秒限时试验';
  const selection = $('devices').value;
  const options = [{address: '', name: '请选择扫描到的设备'}, ...state.devices];
  const signature = JSON.stringify(options);
  if ($('devices').dataset.signature !== signature) {
    $('devices').replaceChildren(...options.map(device => {
      const option = document.createElement('option');
      option.value = device.address;
      const marker = device.protocol_candidate ? ' · FFAC候选' : '';
      const services = device.service_uuids?.length ? ` · ${device.service_uuids.map(uuid => uuid.slice(4, 8)).join(',')}` : '';
      option.textContent = device.address ? `${device.name} · ${device.address}${device.rssi == null ? '' : ` · ${device.rssi} dBm`}${marker}${services}` : device.name;
      return option;
    }));
    $('devices').dataset.signature = signature;
    if (options.some(d => d.address === selection)) $('devices').value = selection;
  }
  $('connect').disabled = working || connected || !$('devices').value;
  const latest = state.logs.at(-1)?.id || 0;
  if (lastLog !== latest) {
    lastLog = latest;
    $('logs').replaceChildren(...state.logs.slice(-50).reverse().map(log => {
      const row = document.createElement('div'); row.className = 'log ' + log.kind;
      const time = document.createElement('time'); time.textContent = log.time;
      const dot = document.createElement('span'); dot.className = 'log-dot';
      const body = document.createElement('div'); body.className = 'message'; body.textContent = log.message;
      if (log.detail) {const detail = document.createElement('small'); detail.textContent = log.detail; body.append(detail);}
      row.append(time, dot, body); return row;
    }));
  }
  $('notification-count').textContent = state.notification_count || 0;
  $('raw-notifications').textContent = (state.notifications || []).slice(-20).reverse().map(n => n.time + '  ' + n.data).join('\n') || '暂无回报。';
}

async function run(route, data = {}) {
  pending += 1; busy = true; render(state);
  try {
    const result = await api(route, data);
    render(result.state || result);
    const labels = {scan: state.devices.length ? '扫描完成，请选择设备后连接。' : '未发现设备，请确认开机并靠近电脑。', connect: '设备已连接。', disconnect: '设备已断开。', stop: '停止指令已发送。'};
    notice(labels[route] || (result.acknowledged ? '指令已发送，设备已回报对应动作。' : '指令已写入，暂未收到对应动作通知。'));
  } catch (error) {notice(error.message || '操作失败，请重试。', true);}
  finally {pending -= 1; busy = pending > 0; render(state);}
}

$('scan').addEventListener('click', () => run('scan'));
$('devices').addEventListener('change', () => { $('connect').disabled = busy || state.connected || !$('devices').value; });
$('connect').addEventListener('click', () => run('connect', {address: $('devices').value || null}));
$('disconnect').addEventListener('click', () => run('disconnect'));
$('suction').addEventListener('click', () => run('suction', {level: Number($('suction-level').value)}));
$('vibration').addEventListener('click', () => run('vibration', {level: Number($('vibration-level').value), duration: Number($('duration').value)}));
$('stop').addEventListener('click', () => run('stop'));
document.addEventListener('keydown', event => {if (event.key === 'Escape' && state.connected && !event.repeat) run('stop');});
for (const kind of ['suction', 'vibration']) {
  const input = $(kind + '-level');
  const update = () => {
    $(kind + '-output').replaceChildren(document.createTextNode(input.value), Object.assign(document.createElement('span'), {textContent: '%'}));
    document.querySelectorAll(`[data-target="${kind}-level"] button`).forEach(button => button.classList.toggle('selected', button.dataset.level === input.value));
  };
  input.addEventListener('input', update);
  document.querySelectorAll(`[data-target="${kind}-level"] button`).forEach(button => button.addEventListener('click', () => {input.value = button.dataset.level; update();}));
}
async function poll() {
  if (polling) return;
  polling = true;
  try {
    render(await api('status', undefined, 4000));
    $('service-status').textContent = '● 本地服务在线';
    $('service-status').className = 'service-state online';
  } catch (error) {
    $('service-status').textContent = '○ 本地服务未连接';
    $('service-status').className = 'service-state';
    $('connection-badge').textContent = '服务未连接';
    for (const id of ['connect', 'scan', 'suction', 'vibration', 'disconnect']) $(id).disabled = true;
    if (!token || error.status === 401) notice('页面的控制令牌已失效，请重新运行启动器打开页面。', true);
  } finally {polling = false;}
}
poll();
setInterval(poll, 1000);
