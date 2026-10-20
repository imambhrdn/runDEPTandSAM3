## Website pengendali di VPS Ubuntu

Lihat [panduan VPS](controller/README.md) untuk menyewa GPU otomatis pada
**machine ID 28726**, memproses klip, menyimpan hasil di VPS, lalu menghapus
instance. Jalur ini memakai Docker Compose dan domain HTTPS, tanpa GPU di VPS.

# Depth + SAM dengan panduan ekspresi

Jalankan `bash run.sh` di template PyTorch Vast.ai (Python 3.10–3.12).
Script memasang MediaPipe, termasuk pada instalasi lama, dan mempertahankan
cara menjalankan depth/SAM yang sudah ada. Model wajah Google diunduh pada
pemakaian pertama ke `checkpoints/face_landmarker.task` (butuh akses HTTPS
ke storage.googleapis.com). `FACE_MODEL` dapat menunjuk model lokal.

Di UI, opsi **Panduan ekspresi (Face Mesh)** aktif secara default. Klik subjek
seperti biasa. Wajah dilacak pada frame asli, kemudian garis wajah, mata,
alis dan bibir digambar di atas composite depth. Unduh composite untuk Flow.
Nonaktifkan opsi tersebut untuk menghasilkan composite tanpa mesh.

CLI: tambahkan `--face-guidance` pada perintah `prep_composite.py` yang biasa.
CLI tanpa flag ini tetap menghasilkan composite biasa.

Periksa seluruh video sebelum generate. Laporan `face-tracking.json` berisi
landmark dan indeks frame (mulai 0) yang gagal dideteksi. UI memberi peringatan
jika ada frame yang hilang; jika tidak ada wajah sama sekali, job gagal dengan
pesan yang bisa ditindaklanjuti. Laporan tanpa frame hilang bukan jaminan wajah
terpilih sudah benar. Paling cocok untuk satu subjek dengan wajah terlihat.
Pada beberapa orang, pemilihan menggunakan wajah terdekat dari klik awal dan
posisi wajah sebelumnya; crossing, gerakan cepat, atau occlusion bisa salah.

Mesh adalah panduan visual, bukan jaminan Flow meniru ekspresi. Gunakan prompt
agar garis mesh dan warna depth hilang dari hasil, sambil mempertahankan
waktu gerakan wajah/bibir. Ekspor tetap tanpa audio seperti versi sebelumnya.

## Satu perintah di Vast.ai

Setelah branch `face-guidance` tersedia di GitHub, jalankan di template
PyTorch dengan Python 3.10–3.12 (gunakan nama folder yang belum ada):

```bash
git clone --branch face-guidance https://github.com/imambhrdn/runDEPTandSAM3.git runDEPTandSAM3-face && bash runDEPTandSAM3-face/run.sh
```

`run.sh` memasang dependensi, mengunduh model depth/SAM/wajah, lalu membuka
server dan tunnel. Alamat web dan password ditampilkan di terminal. Untuk
menjalankan ulang checkout yang sama: `bash runDEPTandSAM3-face/run.sh`.
