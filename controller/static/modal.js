'use strict';
const $ = selector => document.querySelector(selector);
const video = $('#source-video');
let sourceUrl = null, ready = false, point = null, currentFile = null;

async function api(path, options = {}) {
  const response = await fetch('/modal-api' + path, {...options, headers: {'X-Controller': '1', ...options.headers}});
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `Permintaan gagal (${response.status})`);
  return data;
}

let currentJob = null;
let pollTimer = null;

async function checkConfig() {
  try {
    const config = await api('/api/config');
    if (config.enabled) {
      $('.engine-badge').innerHTML = '<i></i>Modal <span>/ terhubung</span>';
      $('.connection-note').textContent = 'Integrasi Modal aktif. Siap memproses job di GPU L4.';
      $('#process').disabled = false;
    } else {
      $('.engine-badge').innerHTML = '<i></i>Modal <span>/ belum aktif</span>';
      $('.connection-note').textContent = config.configured || 'MODAL_ENABLED belum aktif di server.';
      $('#process').disabled = true;
    }
  } catch (err) {
    $('.connection-note').textContent = 'Gagal membaca konfigurasi Modal.';
  }
}
checkConfig();

$('#process').addEventListener('click', async () => {
  if (!ready || !point) return;
  $('#process').disabled = true;
  $('#error').textContent = '';
  try {
    // 1. Upload video to modal-api
    const file = $('#source-file').files[0];
    const ext = '.' + file.name.split('.').pop().toLowerCase();
    $('.connection-note').textContent = 'Mengunggah video ke VPS…';
    const job = await api('/api/upload', {
      method: 'POST',
      headers: {'X-Video-Ext': ext},
      body: file
    });
    currentJob = job;

    // 2. Start job
    $('.connection-note').textContent = 'Memulai job di Modal…';
    const started = await api(`/api/jobs/${job.id}/start`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        x: Math.floor(point.x * $('#source-canvas').width),
        y: Math.floor(point.y * $('#source-canvas').height),
        face_guidance: $('#face-guidance').checked,
        dilate: +$('#dilate').value,
        feather: +$('#feather').value,
        colormap: $('#colormap').value
      })
    });
    currentJob = started;
    pollJob(job.id);
  } catch (err) {
    $('#error').textContent = err.message;
    $('#process').disabled = false;
  }
});

function pollJob(jid) {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(async () => {
    try {
      const job = await api(`/api/jobs/${jid}`);
      currentJob = job;
      $('.connection-note').textContent = `${job.stage} (${job.state})`;
      if (['done', 'failed', 'cancelled'].includes(job.state)) {
        clearInterval(pollTimer);
        pollTimer = null;
        $('#process').disabled = false;
        if (job.state === 'done') {
          $('.connection-note').textContent = 'Job selesai! Memuat hasil…';
          loadResults(job);
        } else if (job.state === 'failed') {
          $('#error').textContent = job.error || 'Job gagal.';
        }
      }
    } catch (_) {}
  }, 3000);
}

function loadResults(job) {
  if (job.artifacts && job.artifacts['composite.mp4']) {
    const videoUrl = job.artifacts['composite.mp4'];
    $('.execution-bottom p').innerHTML = `<a href="${videoUrl}" download class="file-button">Unduh composite.mp4</a>`;
  }
}
function clearPoint() {
  point = null;
  $('#selection-mark').hidden = true;
  $('#selection-info').textContent = 'Klik objek pada frame. Keyboard: Enter memilih tengah, panah menggeser titik.';
}
function resetSource() {
  ready = false;
  video.pause();
  video.removeAttribute('src');
  video.load();
  if (sourceUrl) URL.revokeObjectURL(sourceUrl);
  sourceUrl = null;
  $('#source-timeline').disabled = true;
  $('#source-timeline').max = '0';
  $('#source-timeline').value = '0';
  $('#source-timeline').removeAttribute('aria-valuetext');
  $('#source-time').textContent = '0.00 / 0.00 dtk';
  $('#reset-frame').disabled = true;
  $('#sample-source').hidden = false;
  $('#selection-stage').hidden = true;
  $('#compare-canvas').hidden = true;
  $('#source-badge').textContent = 'CONTOH';
  $('#source-info').textContent = 'MP4, MOV, WebM, MKV · maks. 250 MB';
  $('#original-label').textContent = 'CONTOH ASLI';
  clearPoint();
  $('#selection-info').textContent = 'Belum ada objek dipilih.';
}
function rejectSource(message) {
  resetSource();
  $('#error').textContent = message;
}
function drawFrame() {
  if (!ready || video.seeking || video.readyState < 2) return;
  for (const id of ['#source-canvas', '#compare-canvas']) {
    const canvas = $(id);
    const width = Math.min(960, video.videoWidth);
    canvas.width = width;
    canvas.height = Math.max(1, Math.round(video.videoHeight * width / video.videoWidth));
    canvas.getContext('2d').drawImage(video, 0, 0, canvas.width, canvas.height);
  }
  $('#source-timeline').value = String(video.currentTime);
  $('#source-time').textContent = `${video.currentTime.toFixed(2)} / ${video.duration.toFixed(2)} dtk`;
  $('#source-timeline').setAttribute('aria-valuetext', `${video.currentTime.toFixed(2)} detik dari ${video.duration.toFixed(2)} detik`);
  resizeStage();
}
function resizeStage() {
  if (!ready) return;
  const area = $('.media-area');
  const ratio = video.videoWidth / video.videoHeight;
  const width = Math.min(area.clientWidth, area.clientHeight * ratio);
  $('#selection-stage').style.width = `${width}px`;
  $('#selection-stage').style.height = `${width / ratio}px`;
}
new ResizeObserver(resizeStage).observe($('.media-area'));
$('#source-file').addEventListener('change', event => {
  const file = event.target.files[0];
  if (!file) return;
  $('#error').textContent = '';
  resetSource();
  // Allow choosing the same file again after a browser decode error.
  event.target.value = '';
  if (!/\.(mp4|mov|m4v|webm|mkv)$/i.test(file.name)) {
    $('#error').textContent = 'Format tidak didukung. Pilih MP4, MOV, M4V, WebM, atau MKV.';
    return;
  }
  if (!file.size || file.size > 250 * 1024 ** 2) {
    $('#error').textContent = 'Video harus berisi data dan berukuran maksimal 250 MB.';
    return;
  }
  $('#source-info').textContent = `${file.name} · ${(file.size / 1024 ** 2).toFixed(1)} MB · lokal`;
  $('#selection-info').textContent = 'Membaca video di browser…';
  sourceUrl = URL.createObjectURL(file);
  video.src = sourceUrl;
});
video.addEventListener('loadedmetadata', () => {
  if (!Number.isFinite(video.duration) || video.duration <= 0 || video.duration > 30 || !video.videoWidth || !video.videoHeight) {
    rejectSource('Durasi video harus terbaca dan maksimal 30 detik. Ekspor ulang sebagai MP4 atau WebM jika diperlukan.');
  }
});
video.addEventListener('loadeddata', () => {
  if (!sourceUrl || !Number.isFinite(video.duration) || video.duration <= 0 || video.duration > 30) return;
  ready = true;
  $('#source-timeline').max = String(Math.max(0, Math.floor((video.duration - .001) * 100) / 100));
  $('#source-timeline').disabled = false;
  $('#reset-frame').disabled = false;
  $('#sample-source').hidden = true;
  $('#selection-stage').hidden = false;
  $('#compare-canvas').hidden = false;
  $('#source-badge').textContent = 'VIDEO LOKAL';
  $('#original-label').textContent = 'FRAME ASLI';
  clearPoint();
  drawFrame();
});
video.addEventListener('seeking', clearPoint);
video.addEventListener('seeked', drawFrame);
video.addEventListener('error', () => {
  if (sourceUrl) rejectSource('Browser tidak bisa membaca video ini. Coba MP4 H.264 atau WebM; tidak ada file yang diunggah.');
});
$('#source-timeline').addEventListener('input', () => {
  if (!ready) return;
  clearPoint();
  video.currentTime = Number($('#source-timeline').value);
});
$('#reset-frame').addEventListener('click', () => {
  if (!ready) return;
  clearPoint();
  video.currentTime = 0;
});
function selectPoint(x, y) {
  if (!ready || video.seeking) return;
  point = {x: Math.max(0, Math.min(.999999, x)), y: Math.max(0, Math.min(.999999, y))};
  const mark = $('#selection-mark');
  mark.hidden = false;
  mark.style.left = `${point.x * 100}%`;
  mark.style.top = `${point.y * 100}%`;
  $('#selection-info').textContent = `Objek: ${Math.floor(point.x * $('#source-canvas').width)}, ${Math.floor(point.y * $('#source-canvas').height)} · waktu ${video.currentTime.toFixed(2)} dtk · pilihan lokal, belum dikirim ke worker.`;
}
$('#selection-stage').addEventListener('click', event => {
  const rect = $('#source-canvas').getBoundingClientRect();
  if (rect.width && rect.height) selectPoint((event.clientX - rect.left) / rect.width, (event.clientY - rect.top) / rect.height);
});
$('#selection-stage').addEventListener('keydown', event => {
  const moves = {ArrowLeft: [-.01, 0], ArrowRight: [.01, 0], ArrowUp: [0, -.01], ArrowDown: [0, .01], Enter: [0, 0], ' ': [0, 0]};
  if (!moves[event.key]) return;
  event.preventDefault();
  const [dx, dy] = moves[event.key];
  selectPoint((point?.x ?? .5) + dx, (point?.y ?? .5) + dy);
});
const descriptions = {
  depth: 'Depth Anything V2 · ilustrasi peta kedalaman, bukan hasil model.',
  sam: 'SAM 2.1 · ilustrasi mask subjek, belum ada segmentasi video.',
  composite: 'Composite · ilustrasi gabungan depth dan video asli, bukan hasil render.',
  face: 'Face Guidance · ilustrasi mesh wajah. Jika wajah tidak terlihat, pemrosesan nantinya tetap dilanjutkan.'
};
const tabs = [...document.querySelectorAll('[data-mode][role="tab"]')];
function selectTab(tab) {
  for (const button of tabs) {
    button.setAttribute('aria-selected', String(button === tab));
    button.tabIndex = button === tab ? 0 : -1;
  }
  $('#comparison').dataset.mode = tab.dataset.mode;
  $('#comparison-panel').setAttribute('aria-labelledby', tab.id);
  $('#output-description').textContent = descriptions[tab.dataset.mode];
}
for (const tab of tabs) {
  tab.addEventListener('click', () => selectTab(tab));
  tab.addEventListener('keydown', event => {
    let index = tabs.indexOf(tab);
    if (event.key === 'ArrowRight') index = (index + 1) % tabs.length;
    else if (event.key === 'ArrowLeft') index = (index + tabs.length - 1) % tabs.length;
    else if (event.key === 'Home') index = 0;
    else if (event.key === 'End') index = tabs.length - 1;
    else return;
    event.preventDefault();
    selectTab(tabs[index]);
    tabs[index].focus();
  });
}
$('#compare-slider').addEventListener('input', event => {
  const value = event.target.value;
  $('#comparison').style.setProperty('--split', `${value}%`);
  $('#compare-value').textContent = `${value}%`;
});
window.addEventListener('pagehide', resetSource);
