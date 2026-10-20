# Pengendali VPS untuk Vast.ai

Website ini tetap hidup di VPS tanpa GPU. Unggah klip → klik subjek → mulai →
sewa satu GPU on-demand → proses depth/SAM/mesh → salin hasil ke VPS → verifikasi
SHA-256, durasi dan decode video → hapus instance → unduh untuk Flow.

## Instalasi

Kebutuhan: VPS Linux dengan Docker Engine + Compose plugin, Git, Python 3,
ruang penyimpanan hasil yang cukup, dan domain/subdomain dengan DNS A/AAAA
mengarah ke VPS. Buka TCP 80/443. Jika AAAA dipasang, IPv6 juga harus benar.
Port 80/443 harus bebas; jika sudah ada reverse proxy, integrasikan sendiri
alih-alih menjalankan proxy Caddy kedua. VPS tidak memerlukan CUDA/PyTorch.

```bash
git clone --branch vps-controller https://github.com/imambhrdn/runDEPTandSAM3.git runDEPTandSAM3-vps && bash runDEPTandSAM3-vps/controller/setup.sh
```

Installer meminta domain, API key Vast.ai (input tersembunyi), dan batas harga.
Buat API key di console Vast.ai; masukkan hanya di terminal VPS, bukan chat.
Password website dibuat otomatis dan ditampilkan sekali. Buka domain dengan
HTTPS, login `admin`, lalu gunakan website. Installer **tidak** menyewa GPU.
Simpan `controller/.env` dengan aman; jangan commit atau membagikannya.
Docker harus sudah terpasang; installer tidak mengubah firewall/DNS.

Target default: **machine ID 28726** (`VAST_MACHINE_ID` di `.env`). Ini ID
mesin, bukan offer ID atau ID instance. Aplikasi mencari offer aktif dari mesin
itu dan tidak pindah ke host lain.

Default: US$0,30/jam, VRAM ≥16 GB, compute capability ≥8.0 (BF16),
CUDA driver ≥12.4, host verified, reliabilitas ≥98%, unduh ≥100 Mbps,
CPU amd64, disk 30 GB, satu GPU, maksimal 45 menit sejak permintaan sewa.
Jika tidak ada penawaran, job gagal tanpa menaikkan anggaran otomatis.
Batas harga memakai `dph_total` dengan alokasi disk; transfer data dan biaya
lain yang ditagihkan Vast.ai tidak dibatasi oleh angka itu. Instalasi dan
unduhan model dilakukan di setiap instance baru, sehingga ikut waktu sewa.
Video maksimal 30 detik/250 MB, disarankan mulai dari klip 10 detik.

## Penggunaan dan pembatasan

- Memilih subjek dilakukan sebelum GPU disewa. Tombol mulai mengizinkan sewa
  untuk satu job; tidak ada penyewaan otomatis saat membuka halaman/upload.
- Satu job aktif pada satu waktu. GPU juga dihapus saat job gagal, dibatalkan,
  atau batas waktu tercapai. Timeout/pembatalan dapat kehilangan hasil yang
  belum tersalin; hasil yang sudah valid tetap disimpan di VPS.
- Hasil yang diterima harus lengkap sebelum jalur sukses menghapus GPU.
  Jika GPU sedang dibersihkan, file yang sudah tersimpan sudah bisa diunduh.
- Instance diberi label unik `depth-vps-<job-id>`. Scheduler hanya mengelola
  ID hasil create atau instance dengan label persis tersebut. Instance kamu
  yang dibuat di luar website tidak disentuh.
- Request sewa tidak diulang jika hasilnya ambigu (misalnya timeout). Sistem
  mencari instance berdasarkan label untuk menghindari sewa ganda. Jika tetap
  tertunda tanpa instance, periksa console Vast.ai; job tetap memblokir sewa
  baru sampai status diperjelas, alih-alih berasumsi tidak ada biaya.
- Status tersimpan di SQLite dan hasil pada Docker volume `jobs`. Restart
  melanjutkan pemantauan/pembersihan, tanpa menyewa ulang job yang sama.
  Jalankan **satu** container controller, satu proses Uvicorn, satu scheduler.
- Batas waktu merupakan watchdog di VPS, bukan pembatas tagihan di Vast.ai.
  **Jika VPS mati/API Vast.ai tidak dapat diakses, GPU bisa tetap ditagih.**
  Setelah VPS kembali aktif, pembersihan dilanjutkan. Untuk keadaan darurat,
  hapus instance berlabel terkait melalui console Vast.ai.
- Jangan hentikan VPS/container ketika ada GPU aktif tanpa menghapusnya.
  Jangan menjalankan `docker compose down -v`: volume memuat hasil, status,
  dan ID instance. Back up volume tersebut ketika tidak ada job aktif.
- File sumber/hasil dan riwayat tidak dihapus otomatis. Pantau ruang disk VPS.
  Tidak ada API key Vast.ai di GPU/browser; GPU hanya menerima token satu job.
- Worker mengambil kode pada commit `WORKER_REF`, bukan mengikuti branch yang
  berubah. Saat update, tunggu semua job bersih lalu atur `WORKER_REF` ke commit
  baru yang sudah di-push. Jangan mengubah protokol worker untuk job aktif.
- Face mesh tetap perlu diperiksa secara visual; tracking lengkap tidak
  menjamin wajah benar. Hasil composite tetap tanpa audio.

## Operasi

```bash
cd runDEPTandSAM3-vps/controller
docker compose logs --tail=100 -f
docker compose restart controller
# Setelah mengubah .env:
docker compose up -d --build
```

Untuk memperbarui kode, tunggu semua job selesai/GPU terhapus, `git pull`,
perbarui `WORKER_REF` di `.env` ke output `git rev-parse HEAD`, lalu rebuild.
Jika VPS sudah memakai reverse proxy, jangan membuka port controller langsung
ke internet; pertahankan HTTPS dan autentikasi. Basic Auth memakai user `admin`.

## Validasi pengembangan

```bash
python3 -m venv /tmp/controller-test
/tmp/controller-test/bin/pip install -r controller/requirements.txt httpx
/tmp/controller-test/bin/python -m unittest discover -s controller/tests -v
```

Pengujian menggunakan API Vast.ai simulasi, bukan penyewaan berbayar. Validasi
live memerlukan VPS/domain/API key dan biaya akun Vast.ai. Periksa keberhasilan
worker pada satu klip pendek sebelum penggunaan rutin.

### Hasil validasi versi ini

19 tes controller lulus, mencakup target machine/harga, sewa ambigu,
rekonsiliasi restart, kegagalan instalasi worker, pembatalan, checksum, video
rusak, dan penghapusan instance. Docker image dibangun dan dijalankan sebagai
user non-root; login, konfigurasi machine 28726, upload/frame extraction, serta
pemulihan job setelah restart berhasil. Konfigurasi Compose dan sintaks Caddy
juga diperiksa. Build lokal menggunakan CA tepercaya milik lingkungan cloud
untuk proxy-nya, tanpa mematikan verifikasi TLS. Sertifikat publik domain dan
siklus GPU berbayar belum diuji karena memerlukan konfigurasi VPS/akunmu.

Skema Vast.ai mengacu pada [CLI resmi](https://github.com/vast-ai/vast-python/blob/28bde4827374ef658694db5a1e1e61508b966e2f/vast.py):
POST `/api/v0/bundles/`, PUT `/api/v0/asks/{offer_id}/`, daftar instance v1,
serta GET/DELETE `/api/v0/instances/{instance_id}/`. Sewa dan DELETE tidak
memakai retry otomatis HTTP; pengulangan hanya melalui state machine yang
menyimpan intent dan ID instance.
