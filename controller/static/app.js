const $ = s => document.querySelector(s);
let current = null, point = null, lastArtifacts = '', config = null;
async function api(path, options = {}) {
  const response = await fetch(path, {...options, headers: {'X-Controller': '1', ...options.headers}});
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `Permintaan gagal (${response.status})`);
  return data;
}
function failure(error) { $('#error').textContent = error.message; }
function render(job) {
  const switched = current?.id !== job.id;
  current = job;
  if (switched) { point = null; $('#mark').hidden = true; lastArtifacts = ''; }
  $('#pick').hidden = job.state !== 'ready';
  $('#result').hidden = false;
  if (job.state === 'ready') $('#frame').src = job.frame_url;
  $('#start').disabled = !point;
  $('#status').textContent = `${job.stage} (${job.state})`;
  $('#rental').textContent = job.gpu_name ? `${job.gpu_name} · US$${Number(job.offer_price).toFixed(3)}/jam · Instance: ${(job.instance_ids || []).join(', ') || 'menunggu'}` : '';
  $('#warning').textContent = job.warning || '';
  $('#job-error').textContent = job.error || '';
  $('#cleanup-error').textContent = job.scheduler_error ? `Pemeriksaan/pembersihan perlu dicoba lagi: ${job.scheduler_error}. Periksa juga console Vast.ai.` : '';
  $('#cancel').hidden = ['ready', 'done', 'failed', 'cancelled', 'cleanup'].includes(job.state);
  const signature = JSON.stringify(job.artifacts);
  if (signature !== lastArtifacts) {
    lastArtifacts = signature;
    $('#videos').replaceChildren(); $('#downloads').replaceChildren();
    for (const [name, url] of Object.entries(job.artifacts)) {
      if (name.endsWith('.mp4')) {
        const box = document.createElement('div'), title = document.createElement('p'), video = document.createElement('video');
        title.textContent = name; video.src = url; video.controls = true; video.muted = true; video.playsInline = true;
        box.append(title, video); $('#videos').append(box);
      }
      const link = document.createElement('a'); link.href = url; link.download = name;
      link.className = 'button'; link.textContent = `Unduh ${name}`; $('#downloads').append(link);
    }
  }
}
$('#file').addEventListener('change', async event => {
  const file = event.target.files[0]; if (!file) return;
  try {
    if (file.size > config.max_upload_mb * 1024 ** 2) throw new Error('Video melebihi batas ukuran.');
    $('#file').disabled = true; $('#upload-status').textContent = 'Mengunggah dan menyiapkan frame di VPS…';
    $('#error').textContent = '';
    const extension = '.' + file.name.split('.').pop().toLowerCase();
    const job = await api('/api/upload', {method: 'POST', headers: {'X-Video-Ext': extension}, body: file});
    render(job); $('#upload-status').textContent = `${job.seconds} detik · ${job.width} × ${job.height}. Belum menyewa GPU.`;
  } catch (error) { failure(error); } finally { $('#file').disabled = false; }
});
$('#stage').addEventListener('click', event => {
  if (!current || current.state !== 'ready') return;
  const rect = $('#frame').getBoundingClientRect();
  const x = Math.max(0, Math.min(.999999, (event.clientX - rect.left) / rect.width));
  const y = Math.max(0, Math.min(.999999, (event.clientY - rect.top) / rect.height));
  point = {x: Math.floor(x * current.width), y: Math.floor(y * current.height)};
  $('#mark').hidden = false; $('#mark').style.left = `${x * 100}%`; $('#mark').style.top = `${y * 100}%`;
  $('#point').textContent = `Subjek dipilih pada ${point.x}, ${point.y}`; $('#start').disabled = false;
});
$('#start').addEventListener('click', async () => {
  if (!point) return;
  $('#start').disabled = true; $('#error').textContent = '';
  try {
    render(await api(`/api/jobs/${current.id}/start`, {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({...point, face_guidance: $('#face').checked, dilate: +$('#dilate').value,
        feather: +$('#feather').value, colormap: $('#colormap').value})}));
  } catch (error) { failure(error); $('#start').disabled = false; }
});
$('#cancel').addEventListener('click', async () => {
  try { render(await api(`/api/jobs/${current.id}/cancel`, {method: 'POST'})); } catch (error) { failure(error); }
});
async function poll() {
  try {
    const jobs = await api('/api/jobs');
    $('#jobs').replaceChildren();
    for (const job of jobs) {
      const button = document.createElement('button');
      button.textContent = `${job.id.slice(0, 8)} · ${job.state}`;
      button.addEventListener('click', () => render(job)); $('#jobs').append(button);
    }
    if (current) { const job = jobs.find(j => j.id === current.id); if (job) render(job); }
    else if (jobs.length) render(jobs[0]);
  } catch (error) { failure(error); }
  setTimeout(poll, 4000);
}
api('/api/config').then(data => {
  config = data;
  const vram = data.min_vram === 0 ? 'VRAM tanpa batas minimum' : `VRAM ≥ ${data.min_vram} GB`;
  const target = data.machine_id === 0 ? 'Semua mesin' : `Machine ${data.machine_id}`;
  const cleanup = data.auto_destroy ? `Maksimal ${data.max_minutes} menit sewa` : 'Hapus GPU manual di Vast.ai; biaya terus berjalan meskipun job selesai/gagal atau batas waktu tercapai';
  $('#limits').textContent = `${target} · Batas US$${data.max_price}/jam · ${vram} · Prioritas disk terkecil, lalu harga termurah · ${cleanup} · Upload ≤ ${data.max_upload_mb} MB`;
  poll();
}).catch(failure);
