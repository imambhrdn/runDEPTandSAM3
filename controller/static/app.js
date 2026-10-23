'use strict';
const $ = s => document.querySelector(s);
const video = $('#source-video');
const queue = [], cards = new Map();
let selected = null, config = null, busy = false, uploading = false, starting = false;
async function api(path, options = {}) {
  const response = await fetch(path, {...options, headers: {'X-Controller': '1', ...options.headers}});
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `Permintaan gagal (${response.status})`);
  return data;
}
function failure(error) { $('#error').textContent = error.message; }
function controls() {
  $('#start').disabled = !config || busy || uploading || starting || !queue.length || queue.some(j => !j.point);
  $('#start').textContent = `Proses ${queue.length || ''} video · 1 GPU`;
  $('#file').disabled = !config || uploading || starting;
}
function drawQueue() {
  $('#queue').replaceChildren();
  for (const job of queue) {
    const row = document.createElement('div'), choose = document.createElement('button'), remove = document.createElement('button');
    row.className = 'queue-row';
    choose.textContent = `${job.point ? 'Siap' : 'Pilih subjek'} · ${job.name}`;
    choose.setAttribute('aria-pressed', String(selected === job));
    choose.disabled = starting;
    choose.onclick = () => select(job);
    remove.textContent = 'Hapus'; remove.setAttribute('aria-label', `Hapus ${job.name}`); remove.disabled = starting;
    remove.onclick = async () => {
      try {
        await api(`/api/jobs/${job.id}/cancel`, {method: 'POST'});
        queue.splice(queue.indexOf(job), 1);
        if (job.url) URL.revokeObjectURL(job.url);
        if (selected === job) select(queue[0] || null);
        drawQueue();
      } catch (error) { failure(error); }
    };
    row.append(choose, remove); $('#queue').append(row);
  }
  controls();
}
function marker() {
  $('#mark').hidden = !selected?.point;
  if (selected?.point) {
    $('#mark').style.left = `${100 * selected.point.x / selected.width}%`;
    $('#mark').style.top = `${100 * selected.point.y / selected.height}%`;
  }
}
function select(job) {
  selected = job; video.pause(); video.removeAttribute('src'); video.load(); video.hidden = true;
  $('#frame').hidden = false; $('#pick').hidden = !job; $('#timeline').disabled = true; $('#timeline').value = '0';
  if (job) {
    $('#selected-name').textContent = job.name;
    $('#frame').src = job.frame_url;
    if (job.url) video.src = job.url;
  }
  $('#point').textContent = 'Klik subjek pada frame pertama. Enter memilih tengah; panah menggeser titik.';
  marker(); drawQueue();
}
function point(x, y) {
  if (!selected || starting || (!video.hidden && (video.seeking || video.currentTime > .001))) return;
  selected.point = {x: Math.floor(Math.max(0, Math.min(.999999, x)) * selected.width), y: Math.floor(Math.max(0, Math.min(.999999, y)) * selected.height)};
  marker(); drawQueue();
}
$('#stage').onclick = event => {
  const rect = (video.hidden ? $('#frame') : video).getBoundingClientRect();
  if (rect.width && rect.height) point((event.clientX - rect.left) / rect.width, (event.clientY - rect.top) / rect.height);
};
$('#stage').onkeydown = event => {
  if (!selected) return;
  const moves = {Enter: [0, 0], ' ': [0, 0], ArrowLeft: [-.01, 0], ArrowRight: [.01, 0], ArrowUp: [0, -.01], ArrowDown: [0, .01]};
  if (!moves[event.key]) return;
  event.preventDefault();
  const [x, y] = moves[event.key];
  point((selected.point ? selected.point.x / selected.width : .5) + x, (selected.point ? selected.point.y / selected.height : .5) + y);
};
video.onloadeddata = () => {
  if (!selected || !Number.isFinite(video.duration)) return;
  video.hidden = false; $('#frame').hidden = true; $('#timeline').disabled = false; $('#timeline').max = String(video.duration);
};
function seek() {
  if (starting) return;
  if (selected) selected.point = null;
  marker(); drawQueue();
  $('#point').textContent = 'Kembali ke frame pertama lalu pilih subjek ulang sebelum proses.';
}
video.onseeking = seek;
$('#timeline').oninput = () => { seek(); video.currentTime = Number($('#timeline').value); };
$('#first').onclick = () => { seek(); video.currentTime = 0; $('#timeline').value = '0'; };
video.onerror = () => { video.hidden = true; $('#frame').hidden = false; $('#timeline').disabled = true; };
$('#file').onchange = async event => {
  const files = Array.from(event.target.files); event.target.value = '';
  if (!config) return;
  if (files.length + queue.length > 8) return failure(new Error('Maksimal 8 video per antrean.'));
  uploading = true; controls(); $('#error').textContent = '';
  for (const file of files) {
    try {
      if (file.size > config.max_upload_mb * 1024 ** 2) throw new Error('Ukuran video melebihi batas.');
      $('#upload-status').textContent = `Mengunggah ${file.name}…`;
      const job = await api('/api/upload', {method: 'POST', headers: {'X-Video-Ext': '.' + file.name.split('.').pop().toLowerCase()}, body: file});
      queue.push({...job, name: file.name, point: null, url: URL.createObjectURL(file)});
      if (!selected) select(queue[0]);
      drawQueue();
    } catch (error) { failure(new Error(`${file.name}: ${error.message}`)); }
  }
  uploading = false; $('#upload-status').textContent = 'Upload selesai. Belum menyewa GPU.'; drawQueue();
};
$('#start').onclick = async () => {
  if ($('#start').disabled) return;
  starting = true; drawQueue(); $('#error').textContent = '';
  try {
    await api('/api/batches/start', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({jobs: queue.map(j => ({id: j.id, params: {...j.point, face_guidance: false, dilate: 6, feather: 3, colormap: 'inferno'}}))})});
    for (const job of queue) if (job.url) URL.revokeObjectURL(job.url);
    queue.length = 0; select(null); busy = true;
    await refresh();
  } catch (error) { failure(error); }
  finally { starting = false; drawQueue(); }
};
async function refresh() {
  const jobs = await api('/api/jobs');
  busy = jobs.some(j => !['ready', 'done', 'failed', 'cancelled'].includes(j.state));
  // Restore unfinished selections after refresh using the server's first-frame image.
  if (!uploading && !starting) for (const job of jobs.filter(j => j.state === 'ready').reverse()) {
    if (!queue.some(j => j.id === job.id) && queue.length < 8) queue.push({...job, name: `Video ${job.id.slice(0, 8)}`, point: null});
  }
  if (!selected && queue.length) select(queue[0]);
  drawQueue();
  const results = jobs.filter(j => j.state !== 'ready');
  $('#empty').hidden = results.length > 0;
  for (const job of results) {
    let card = cards.get(job.id);
    if (!card) {
      card = document.createElement('article'); card.dataset.job = job.id;
      const title = document.createElement('h3'), status = document.createElement('p'), error = document.createElement('p'), output = document.createElement('div'), cancel = document.createElement('button');
      title.textContent = job.batch_index === undefined ? `Video ${job.id.slice(0, 8)}` : `Video ${job.batch_index + 1} · ${job.id.slice(0, 8)}`;
      status.className = 'status'; error.className = 'job-error'; output.className = 'outputs'; cancel.textContent = 'Batalkan antrean';
      cancel.onclick = async () => { try { await api(`/api/jobs/${job.batch_owner || job.id}/cancel`, {method: 'POST'}); await refresh(); } catch (e) { failure(e); } };
      card.append(title, status, error, output, cancel); cards.set(job.id, card); $('#results').append(card);
    }
    card.querySelector('.status').textContent = `${job.stage} · ${job.state}`;
    card.querySelector('.job-error').textContent = [job.error, job.scheduler_error, job.warning].filter(Boolean).join(' · ');
    card.querySelector('button').hidden = ['done', 'failed', 'cancelled', 'cleanup'].includes(job.state) || (job.batch_owner && job.batch_owner !== job.id);
    for (const [name, url] of Object.entries(job.artifacts || {})) {
      if (card.querySelector(`[data-artifact="${name}"]`)) continue;
      const box = document.createElement('div'); box.dataset.artifact = name;
      if (name.endsWith('.mp4')) { const v = document.createElement('video'); v.src = url; v.controls = true; v.preload = 'none'; v.playsInline = true; box.append(v); }
      const link = document.createElement('a'); link.href = url; link.download = name; link.textContent = `Unduh ${name}`; box.append(link); card.querySelector('.outputs').append(box);
    }
  }
  controls();
}
async function poll() { try { await refresh(); } catch (error) { failure(error); } finally { setTimeout(poll, 4000); } }
api('/api/config').then(data => {
  config = data; $('#balance').textContent = typeof data.balance === 'number' ? `Saldo US$ ${data.balance.toFixed(2)}` : 'Saldo —';
  $('#limits').textContent = `Maks. 30 detik/video · ${data.max_upload_mb} MB/video · ${data.max_minutes} menit untuk seluruh antrean.`;
  $('#rental-note').textContent = data.auto_destroy ? 'GPU dihapus setelah seluruh antrean selesai atau batas waktu tercapai.' : 'GPU dihapus manual di Vast.ai. Biaya tetap berjalan setelah antrean selesai atau dihentikan.';
  controls(); poll();
}).catch(failure);
window.addEventListener('pagehide', () => { for (const job of queue) if (job.url) URL.revokeObjectURL(job.url); });
