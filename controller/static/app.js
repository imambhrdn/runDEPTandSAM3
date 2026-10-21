const $ = s => document.querySelector(s);
let current = null, point = null, lastArtifacts = '', config = null;
let sourceUrl = null;
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
  if (switched) {
    point = null; $('#mark').hidden = true; lastArtifacts = '';
    $('#source-video').pause(); $('#source-video').removeAttribute('src'); $('#source-video').load();
    $('#source-video').hidden = true; $('#frame').hidden = false;
    if (sourceUrl) URL.revokeObjectURL(sourceUrl);
    sourceUrl = null;
    $('#selection-timeline').disabled = true; $('#selection-timeline').value = '0'; $('#selection-timeline').max = '0';
    $('#first-frame').disabled = true; $('#selection-time').textContent = '0.0 / 0.0 dtk';
    $('#selection-help').textContent = 'Upload ulang video untuk mengaktifkan timeline lokal. Worker tetap memakai frame pertama.';
    $('#point').textContent = 'Klik orang yang ingin diproses. Keyboard: Enter memilih tengah, panah menggeser titik.';
  }
  $('#pick').hidden = job.state !== 'ready';
  $('#face').disabled = job.state !== 'ready';
  if (switched && job.params) $('#face').checked = job.params.face_guidance !== false;
  $('#face-help').textContent = job.state === 'ready' ? 'Aktifkan untuk menambahkan mesh wajah pada hasil.' : 'Pengaturan terkunci untuk job yang sudah dimulai.';
  $('#result').hidden = false;
  if (job.state === 'ready') $('#frame').src = job.frame_url;
  $('#start').disabled = !canStart();
  $('#status').textContent = `${job.stage} (${job.state})`;
  $('#rental').textContent = job.gpu_name ? `Instance: ${(job.instance_ids || []).join(', ') || 'menunggu'} · GPU pada job terpilih` : '';
  $('#gpu-name').textContent = job.gpu_name || 'NVIDIA GPU';
  $('#gpu-caption').textContent = job.gpu_name ? 'Perangkat dari job yang sedang dilihat.' : 'Perangkat muncul setelah GPU dipilih.';
  $('#gpu-state').textContent = job.gpu_name ? `Job terpilih · ${job.state}` : 'Menunggu job';
  $('#warning').textContent = job.warning || '';
  $('#job-error').textContent = job.error || '';
  $('#cleanup-error').textContent = job.scheduler_error ? `Pemeriksaan/pembersihan perlu dicoba lagi: ${job.scheduler_error}. Periksa juga console Vast.ai.` : '';
  $('#cancel').hidden = ['ready', 'done', 'failed', 'cancelled', 'cleanup'].includes(job.state);
  $('#gpu-dot').className = `status-dot ${['booting', 'running'].includes(job.state) ? '' : job.state === 'done' ? '' : 'neutral'}`;
  updateLivePipeline(job);
  updateArtifactCards(job);
  const signature = JSON.stringify(job.artifacts);
  if (signature !== lastArtifacts) {
    lastArtifacts = signature;
    $('#videos').replaceChildren(); $('#downloads').replaceChildren();
    for (const [name, url] of Object.entries(job.artifacts || {})) {
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
    if (!config) throw new Error('Konfigurasi belum tersedia. Tunggu koneksi controller, lalu pilih file lagi.');
    if (file.size > config.max_upload_mb * 1024 ** 2) throw new Error('Video melebihi batas ukuran.');
    const extension = '.' + file.name.split('.').pop().toLowerCase();
    if (!['.mp4', '.mov', '.m4v', '.webm', '.mkv'].includes(extension)) throw new Error('Format video tidak didukung.');
    $('#file').disabled = true; $('#upload-status').textContent = 'Mengunggah dan menyiapkan frame di VPS…';
    $('#error').textContent = '';
    const job = await api('/api/upload', {method: 'POST', headers: {'X-Video-Ext': extension}, body: file});
    render(job); $('#upload-status').textContent = `${job.seconds} detik · ${job.width} × ${job.height}. Belum menyewa GPU.`;
    if (sourceUrl) URL.revokeObjectURL(sourceUrl);
    sourceUrl = URL.createObjectURL(file);
    $('#source-video').src = sourceUrl;
  } catch (error) { failure(error); } finally { $('#file').disabled = false; }
});
function selectPoint(x, y) {
  if (!current || current.state !== 'ready' || (!$('#source-video').hidden && $('#source-video').seeking)) return;
  x = Math.max(0, Math.min(.999999, x));
  y = Math.max(0, Math.min(.999999, y));
  point = {x: Math.floor(x * current.width), y: Math.floor(y * current.height)};
  $('#mark').hidden = false; $('#mark').style.left = `${x * 100}%`; $('#mark').style.top = `${y * 100}%`;
  $('#point').textContent = `Subjek dipilih pada ${point.x}, ${point.y}${!$('#source-video').hidden && $('#source-video').currentTime > 0 ? ' · percobaan tampilan, belum bisa dikirim ke worker' : ''}`;
  $('#start').disabled = !canStart();
}
function canStart() {
  // ponytail: later-frame clicks are UI-only until the worker accepts a frame index; never submit them as first-frame points.
  const video = $('#source-video');
  return current?.state === 'ready' && point && (video.hidden || (video.currentTime === 0 && !video.seeking));
}
$('#stage').addEventListener('click', event => {
  const rect = ($('#source-video').hidden ? $('#frame') : $('#source-video')).getBoundingClientRect();
  if (rect.width && rect.height) selectPoint((event.clientX - rect.left) / rect.width, (event.clientY - rect.top) / rect.height);
});
$('#stage').addEventListener('keydown', event => {
  if (!current || current.state !== 'ready') return;
  const moves = {ArrowLeft: [-.01, 0], ArrowRight: [.01, 0], ArrowUp: [0, -.01], ArrowDown: [0, .01], Enter: [0, 0], ' ': [0, 0]};
  const move = moves[event.key];
  if (!move) return;
  event.preventDefault();
  selectPoint((point ? point.x / current.width : .5) + move[0], (point ? point.y / current.height : .5) + move[1]);
});
$('#start').addEventListener('click', async () => {
  if (!canStart()) return;
  $('#start').disabled = true; $('#error').textContent = '';
  try {
    render(await api(`/api/jobs/${current.id}/start`, {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({...point, face_guidance: $('#face').checked, dilate: +$('#dilate').value,
        feather: +$('#feather').value, colormap: $('#colormap').value})}));
  } catch (error) { failure(error); $('#start').disabled = !canStart(); }
});
$('#cancel').addEventListener('click', async () => {
  try { render(await api(`/api/jobs/${current.id}/cancel`, {method: 'POST'})); } catch (error) { failure(error); }
});
async function poll() {
  try {
    const jobs = await api('/api/jobs');
    $('#jobs').replaceChildren();
    if (!jobs.length) {
      const empty = document.createElement('p'); empty.className = 'empty-history';
      empty.textContent = 'Belum ada proses. Unggah klip pertama Anda untuk mulai.'; $('#jobs').append(empty);
    }
    for (const job of jobs) {
      const button = document.createElement('button');
      button.textContent = `${job.id.slice(0, 8)} · ${job.state}`;
      button.setAttribute('aria-pressed', String(current?.id === job.id));
      button.addEventListener('click', () => render(job)); $('#jobs').append(button);
    }
    if (current) { const job = jobs.find(j => j.id === current.id); if (job) render(job); }
    else if (jobs.length) render(jobs[0]);
  } catch (error) { failure(error); }
  setTimeout(poll, 4000);
}
let demoTimer = null, demoStep = -1;
function updateLivePipeline(job) {
  if (demoTimer !== null) return;
  const s = job.state, stage = job.stage || '';
  let stepIdx = -1;
  if (s === 'done') {
    stepIdx = 4;
  } else if (stage.includes('Menempel') || stage.includes('Menyimpan') || stage.includes('Mengirim') || stage.includes('Composite')) {
    stepIdx = 3;
  } else if (stage.includes('Mask') || stage.includes('SAM') || stage.includes('ekspresi') || stage.includes('wajah')) {
    stepIdx = 2;
  } else if (stage.includes('Depth')) {
    stepIdx = 1;
  } else if (['creating', 'booting'].includes(s) || stage.includes('Persiapan') || stage.includes('ekstrak') || stage.includes('Menyewa') || stage.includes('Menyiapkan')) {
    stepIdx = 0;
  }
  if (stepIdx >= 0) {
    document.querySelectorAll('.pipeline-step').forEach((el, idx) => {
      el.classList.toggle('active', idx === stepIdx && s !== 'done' && s !== 'failed');
      el.classList.toggle('done', idx < stepIdx || s === 'done');
      const stateEl = el.querySelector('.step-state');
      if (s === 'done' || idx < stepIdx) stateEl.textContent = 'Selesai';
      else if (idx === stepIdx) stateEl.textContent = s === 'failed' ? 'Gagal' : 'Aktif';
      else stateEl.textContent = 'Menunggu';
    });
    $('#demo-counter').textContent = `${Math.min(4, Math.max(0, stepIdx + (s === 'done' ? 0 : 1)))} / 4 tahap`;
    $('#demo-status').textContent = s === 'done' ? 'Proses selesai' : s === 'failed' ? 'Proses terhenti' : `Proses aktual · ${job.stage || job.state}`;
  }
}
function updateArtifactCards(job) {
  const artifacts = job.artifacts || {};
  const samBtn = document.querySelector('[data-preview="sam"]');
  const compBtn = document.querySelector('[data-preview="composite"]');
  const faceBtn = document.querySelector('[data-preview="face"]');
  if (samBtn) {
    samBtn.innerHTML = artifacts['mask_preview.mp4']
      ? 'Lihat mask video <span aria-hidden="true">▷</span>'
      : 'Lihat preview <span aria-hidden="true">↗</span>';
  }
  if (compBtn) {
    compBtn.innerHTML = artifacts['composite.mp4']
      ? 'Lihat video composite <span aria-hidden="true">▷</span>'
      : 'Lihat preview <span aria-hidden="true">↗</span>';
  }
  if (faceBtn) {
    faceBtn.innerHTML = artifacts['face-tracking.json']
      ? 'Laporan tracking wajah <span aria-hidden="true">↓</span>'
      : 'Lihat preview <span aria-hidden="true">↗</span>';
  }
}
function showDemoStep() {
  const labels = ['Menyiapkan frame contoh', 'Membaca kedalaman tiap frame', 'Memisahkan subjek dari latar', 'Menyusun composite akhir'];
  document.querySelectorAll('.pipeline-step').forEach((element, index) => {
    element.classList.toggle('active', index === demoStep);
    element.classList.toggle('done', index < demoStep);
    element.querySelector('.step-state').textContent = index < demoStep ? 'Selesai' : index === demoStep ? 'Simulasi aktif' : 'Menunggu';
  });
  $('#demo-status').textContent = demoStep < 0 ? 'Simulasi visual · belum ada proses berjalan' : demoStep > 3 ? 'Simulasi selesai · tidak ada video yang diproses' : `Simulasi · ${labels[demoStep]}…`;
  $('#demo-counter').textContent = `${Math.max(0, demoStep)} / 4 tahap`;
}
$('#demo').addEventListener('click', () => {
  if (demoTimer !== null) {
    clearInterval(demoTimer); demoTimer = null; demoStep = -1;
    $('#demo').textContent = 'Putar simulasi ▷';
    if (current) updateLivePipeline(current);
    else showDemoStep();
    return;
  }
  demoStep = 0; showDemoStep(); $('#demo').textContent = 'Hentikan simulasi □';
  demoTimer = setInterval(() => {
    demoStep++; showDemoStep();
    if (demoStep === 4) {
      clearInterval(demoTimer); demoTimer = null; $('#demo').textContent = 'Ulangi simulasi ↺';
      if (current) setTimeout(() => updateLivePipeline(current), 2000);
    }
  }, 1800);
});
document.querySelectorAll('[data-preview]').forEach(button => {
  button.addEventListener('click', () => {
    const mode = button.dataset.preview;
    const card = button.closest('.output-card');
    $('#preview-title').textContent = card.querySelector('h3').textContent;
    const artifacts = current?.artifacts || {};
    if (mode === 'composite' && artifacts['composite.mp4']) {
      const vid = document.createElement('video');
      vid.src = artifacts['composite.mp4']; vid.controls = true; vid.autoplay = true; vid.playsInline = true;
      vid.style.width = '100%'; vid.style.maxHeight = '420px'; vid.style.borderRadius = '8px';
      $('#preview-content').replaceChildren(vid);
    } else if (mode === 'sam' && artifacts['mask_preview.mp4']) {
      const vid = document.createElement('video');
      vid.src = artifacts['mask_preview.mp4']; vid.controls = true; vid.autoplay = true; vid.playsInline = true;
      vid.style.width = '100%'; vid.style.maxHeight = '420px'; vid.style.borderRadius = '8px';
      $('#preview-content').replaceChildren(vid);
    } else if (mode === 'face' && artifacts['face-tracking.json']) {
      const p = document.createElement('p');
      p.textContent = current?.warning ? `Perhatian: ${current.warning}` : 'Tracking ekspresi wajah berhasil dipetakan.';
      const a = document.createElement('a');
      a.href = artifacts['face-tracking.json']; a.className = 'button'; a.download = 'face-tracking.json';
      a.textContent = 'Unduh face-tracking.json';
      $('#preview-content').replaceChildren(p, a);
    } else {
      $('#preview-content').replaceChildren(card.querySelector('.preview-image').cloneNode(true));
    }
    $('#preview-dialog').showModal();
  });
});
$('#close-preview').addEventListener('click', () => $('#preview-dialog').close());
function updateSelection() {
  const video = $('#source-video');
  $('#selection-timeline').value = String(video.currentTime);
  $('#selection-time').textContent = `${video.currentTime.toFixed(1)} / ${video.duration.toFixed(1)} dtk`;
  $('#selection-timeline').setAttribute('aria-valuetext', `${video.currentTime.toFixed(2)} detik dari ${video.duration.toFixed(2)} detik`);
  $('#selection-help').textContent = video.currentTime > 0
    ? 'Frame lain hanya untuk mencoba pemilihan objek. Kembali ke frame pertama dan pilih ulang sebelum memproses.'
    : 'Klik objek pada gambar. Pilihan dihapus setiap kali timeline digeser. Worker tetap memakai frame pertama.';
  $('#start').disabled = !canStart();
}
$('#source-video').addEventListener('loadeddata', () => {
  const video = $('#source-video');
  if (!Number.isFinite(video.duration) || video.duration <= 0) return;
  video.hidden = false; $('#frame').hidden = true;
  $('#selection-timeline').max = String(Math.max(0, video.duration - .001));
  $('#selection-timeline').disabled = false; $('#first-frame').disabled = false;
  point = null; $('#mark').hidden = true;
  updateSelection();
});
$('#source-video').addEventListener('seeking', () => {
  point = null; $('#mark').hidden = true; $('#start').disabled = true;
  $('#point').textContent = 'Klik objek lagi setelah frame selesai dimuat.';
});
$('#source-video').addEventListener('seeked', updateSelection);
$('#selection-timeline').addEventListener('input', () => {
  point = null; $('#mark').hidden = true; $('#start').disabled = true;
  $('#source-video').currentTime = Number($('#selection-timeline').value);
  updateSelection();
});
$('#first-frame').addEventListener('click', () => {
  $('#selection-timeline').value = '0';
  $('#selection-timeline').dispatchEvent(new Event('input'));
});
$('#source-video').addEventListener('error', () => {
  if (!$('#source-video').getAttribute('src')) return;
  $('#source-video').hidden = true; $('#frame').hidden = false;
  $('#selection-timeline').disabled = true; $('#first-frame').disabled = true;
  point = null; $('#mark').hidden = true; $('#start').disabled = true;
  $('#upload-status').textContent = 'Browser tidak bisa memutar file asli ini. Pemilihan subjek pada frame pertama tetap tersedia.';
  $('#selection-help').textContent = 'Timeline tidak tersedia untuk format ini di browser. Pilih objek pada frame pertama.';
});
window.addEventListener('pagehide', () => { if (sourceUrl) URL.revokeObjectURL(sourceUrl); });
api('/api/config').then(data => {
  config = data;
  if (typeof data.balance === 'number') {
    $('#balance-val').textContent = `US$ ${data.balance.toFixed(2)}`;
    $('#balance-sub').textContent = 'SALDO AKTIF';
  } else {
    $('#balance-val').textContent = 'US$ —';
  }
  const vram = data.min_vram === 0 ? 'VRAM tanpa batas minimum' : `VRAM ≥ ${data.min_vram} GB`;
  const target = data.machine_id === 0 ? 'Semua mesin' : `Machine ${data.machine_id}`;
  const cleanup = data.auto_destroy ? `Maksimal ${data.max_minutes} menit sewa` : 'Hapus GPU manual di Vast.ai; biaya terus berjalan meskipun job selesai/gagal atau batas waktu tercapai';
  $('#gpu-memory').textContent = data.min_vram ? `≥ ${data.min_vram} GB · filter` : 'Tanpa minimum · filter';
  $('#gpu-selection').textContent = data.machine_id === 0 ? 'Otomatis' : `Machine ${data.machine_id}`;
  $('#session-note').textContent = data.auto_destroy ? 'GPU dihapus otomatis setelah hasil diverifikasi atau job dihentikan.' : 'Penghapusan GPU manual. Biaya tetap berjalan setelah job selesai atau gagal, sampai instance dihapus di Vast.ai.';
  $('#cancel').textContent = data.auto_destroy ? 'Batalkan & hapus GPU' : 'Batalkan job · GPU tetap disimpan';
  $('#limits').textContent = `${target} · Batas US$${data.max_price}/jam · ${vram} · Prioritas disk terkecil, lalu harga termurah · ${cleanup} · Upload ≤ ${data.max_upload_mb} MB`;
  poll();
}).catch(failure);
