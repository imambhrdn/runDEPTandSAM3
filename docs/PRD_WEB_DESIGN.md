# Product Requirements Document (PRD): Web Design Frame Lab (Vast.ai GPU Controller)

**Versi:** 1.0  
**Tanggal:** 21 Oktober 2026  
**Status:** Disetujui untuk Implementasi  
**Target Platform:** Desktop Browser (Chrome, Edge, Firefox, Safari)  

---

## 1. Ringkasan Eksekutif & Tujuan

### 1.1 Latar Belakang
Frame Lab adalah aplikasi pengendali proses AI video berbasis web untuk mengekstrak kedalaman (*Depth Anything V2*), memisahkan subjek (*SAM 2.1*), serta melacak mesh ekspresi wajah (*MediaPipe Face Mesh*) menjadi video komposit siap pakai untuk model animasi video (misalnya Omni atau Seedance). Pemrosesan dijalankan di GPU cloud on-demand (Vast.ai) yang dikendalikan melalui VPS controller pribadi.

### 1.2 Tujuan Desain Web
Membangun antarmuka studio web modern, intuitif, berorientasi **Desktop-First**, yang memberikan kontrol penuh kepada pengguna dalam memilih objek video frame-by-frame, memantau pipeline komputasi GPU, dan mengunduh hasil tanpa kebingungan teknis.

---

## 2. Prinsip Desain & Identitas Visual (Design System)

### 2.1 Tema & Warna (Dark Theme with Lime Accent)
- **Background Utama:** `#0b0d0b` (Deep obsidian dark)
- **Panel / Surface:** `#121512` dan `#171f15` (Subtle muted green-dark)
- **Border / Garis Pembatas:** `#272c25` dan `#354029` (Fine hairline borders)
- **Aksen Utama:** `#c4f568` (Vibrant electric lime) — digunakan untuk fokus, titik seleksi, status aktif, dan tombol aksi primer.
- **Teks Utama:** `#eef0e9` (Clean high-contrast off-white)
- **Teks Sekunder (Muted):** `#959d91` (Neutral sage)
- **Status Berbahaya / Error:** `#ffb3a4`
- **Peringatan (Warning):** `#e6c77d`

### 2.2 Tipografi & Nuansa
- **Font:** Inter / System UI sans-serif modern, dipadukan dengan Monospace (`ui-monospace`) untuk angka, koordinat, dan stempel waktu.
- **Karakter:** Industrial Video Studio, presisi, minimalis, tanpa dekorasi berlebihan.

---

## 3. Arsitektur Informasi & Tata Letak (Layout)

Layout dibagi menjadi struktur 3 zona utama:
1. **Sidebar Navigasi Kiri (Fixed):** Identitas brand, pintasan workspace (Studio, Output Video, Riwayat), dan status koneksi VPS.
2. **Header / Topbar Atas:** Breadcrumb navigasi, **Kartu Saldo di Pojok Kanan Atas**, dan identitas pengguna.
3. **Main Studio Canvas (2 Kolom):**
   - **Kolom Utama (Kiri, ~70% lebar):** Panel input video sumber + penanda objek, dan panel pipeline visual.
   - **Kolom Sekunder (Kanan, ~30% lebar):** Kartu status GPU Engine dan Kartu Sesi Workspace.
4. **Section Bawah:** Galeri 4 Perspektif Output (*Depth, SAM, Composite, Face*), Live Monitoring, dan Riwayat Job.

---

## 4. Rincian Fitur & Komponen Antarmuka

### 4.1 Topbar & Saldo (Pojok Kanan Atas)
- **Komponen Saldo Vast.ai:**
  - Letak: Kanan atas layar, bersebelahan dengan avatar user.
  - Tampilan: Badge berbingkai halus dengan label `Saldo Vast.ai (PLACEHOLDER)` dan nilai `US$ —`.
  - Aturan: Tidak menampilkan angka palsu; hanya membaca saldo riil jika endpoint controller tersedia tanpa membuka API key ke browser.
- **Brand & Breadcrumb:** `frame lab / VIDEO WORKSPACE` dengan penanda lokasi aktif.

### 4.2 Panel 01: Sumber Video & Pemilihan Subjek
- **Area Unggah Video (Upload Zone):**
  - Mendukung drag-and-drop dan file picker.
  - Format didukung: MP4, MOV, WebM, MKV (H.264 disarankan).
  - Batasan: Durasi maksimal 30 detik (disarankan klip 10 detik), ukuran maksimal 250 MB.
- **Stage Gambar & Penanda Objek (Interactive Frame Picker):**
  - Menampilkan frame video resolusi penuh (lebar standar 960 px).
  - Kursor *crosshair*. Klik pada gambar langsung menempatkan penanda objek lingkaran beraksen lime.
  - Dukungan Keyboard: Tombol `Enter`/`Spasi` memilih titik tengah, tombol panah menggeser koordinat per piksel.
- **Slider Timeline Objek (Tepat di Bawah Gambar):**
  - Letak: Langsung di bawah area gambar pemilihan objek (bukan di popup terpisah).
  - Fungsi: Menggeser frame video asli di browser untuk meninjau subjek bergerak.
  - Tombol **"Frame pertama"**: Tombol instan untuk mengembalikan scrubbing ke `00:00`.
  - Guardrail Proteksi: Pemilihan titik pada frame > 0 diberi label uji tampilan. Tombol mulai proses hanya aktif jika titik berada pada frame pertama (karena model AI mempropagasi dari frame 0).
- **Pengaturan Pemrosesan:**
  - **Dilate (0–30 px):** Memperlebar jangkauan mask (default: 6).
  - **Feather (0–15 px):** Menghaluskan tepi mask (default: 3).
  - **Colormap:** Pilihan OpenCV colormap (`inferno`, `turbo`, `viridis`, `jet`).

### 4.3 Fitur Face Guidance (Mesh Wajah)
- **Tampilan:** Panel checkbox khusus yang selalu terlihat di area pengaturan.
- **Label & Penjelasan:**
  - Checkbox: `Face Guidance [MESH WAJAH]` (default: aktif).
  - Keterangan Wajib: *"Wajah tidak terlihat: lewati mesh, lanjutkan output video."*
  - Memastikan proses tidak gagal/batal jika subjek membelakangi kamera atau wajah tidak terdeteksi.

### 4.4 Panel 02: Pipeline Pemrosesan & Simulasi
- **Indikator 4 Tahap:**
  1. `01 Persiapan`: Ekstraksi frame & validasi resolusi.
  2. `02 Depth Anything`: Estimasi kedalaman monokuler.
  3. `03 SAM Segmentation`: Isolasi subjek video tracking.
  4. `04 Composite`: Penggabungan depth, video asli, dan mesh wajah.
- **Tombol "Putar simulasi" (▷):**
  - Mengaktifkan animasi loading berurutan pada keempat node pipeline untuk menguji interaktivitas UI tanpa menyewa GPU atau memotong saldo.

### 4.5 Kartu GPU Engine (Tanpa Tampilan Harga)
- **Desain:** Ilustrasi sirkuit PCB dan kipas GPU bergaya dark-tech dengan aksen hijau-lime.
- **Branding:** Tag `NVIDIA GPU`, arsitektur PyTorch, CUDA ≥12.4.
- **Spesifikasi:** Menampilkan filter memori (`VRAM ≥ 12 GB`).
- **Aturan Ketat:** **Tanpa harga sewa per jam pada kartu GPU**, sesuai preferensi pengguna untuk menjaga tampilan tetap bersih.
- **Status State:** Dot indikator dinamis (`Menunggu job`, `Menyewa GPU`, `Aktif`, `Selesai`).

### 4.6 Galeri 4 Perspektif Output (Showcase)
Bagian kartu visual untuk memvisualisasikan hasil per tahap:
1. **Depth Map:** Visualisasi gradasi warna kontras near/far (Inferno colormap).
2. **Subject Mask (SAM):** Visualisasi subjek tersegmentasi warna lime dengan bounding box.
3. **Final Composite:** Visualisasi gabungan video asli dengan depth hanya pada tubuh subjek.
4. **Face Guidance:** Visualisasi jaring kawat (wireframe mesh) wajah 468 titik.
- Setiap kartu memiliki tombol **"Lihat preview"** untuk membuka dialog modal perbesaran ilustrasi.

### 4.7 Live Monitor & Pengunduhan Hasil
- Tersembunyi saat awal, otomatis muncul saat job dibuat/berjalan.
- Menampilkan:
  - Status tahap berjalan (`stage`) dan status sistem (`state`).
  - Instance ID Vast.ai yang menangani komputasi.
  - Kotak peringatan kuning jika ada frame wajah yang hilang.
  - Kotak error merah jika terjadi kegagalan.
  - Tombol **"Batalkan job"** (dengan penjaga `AUTO_DESTROY=0`).
  - Player video hasil ganda: `composite.mp4` dan `mask_preview.mp4`.
  - Tombol unduh file instan (`.mp4` dan `face-tracking.json`).

### 4.8 Panel Riwayat Job
- Riwayat sesi yang tersimpan persisten di SQLite VPS.
- Daftar tombol batch/job ID lengkap dengan status badge (`ready`, `running`, `done`, `failed`).
- Mengklik riwayat langsung memuat ulang status dan player video terkait.

---

## 5. Alur Pengguna (User Journey)

```
[Buka Dashboard] 
       │
       ▼
[Unggah Klip Video (≤30 dtk)] 
       │
       ▼ (Server mengekstrak frame pertama, merender stage)
[Geser Slider Timeline & Klik Titik Subjek pada Frame Pertama]
       │
       ▼ (Opsi: Atur Dilate, Feather, Colormap, & Face Guidance)
[Klik "Mulai proses di GPU"]
       │
       ▼ (Controller menyewa GPU Vast.ai sesuai kriteria VRAM & harga)
[Pantau Progres Live (Tahap 1 s/d 4)]
       │
       ▼ (Worker menyelesaikan proses & mengunggah artefak ke VPS)
[Tonton Preview Composite & Unduh Video Akhir]
       │
       ▼ (Instance dipertahankan sesuai AUTO_DESTROY=0 agar log aman)
[User Menghapus Instance secara Manual di Vast.ai Console]
```

---

## 6. Aspek Keamanan & Guardrails

1. **Autentikasi & Proteksi CSRF:** Seluruh akses dashboard dilindungi HTTP Basic Auth (`admin`) dan header wajib `X-Controller: 1` untuk mencegah eksploitasi pihak ketiga.
2. **Kerahasiaan Kredensial:** API key Vast.ai, password SSH, token worker, dan kredensial cloud tidak pernah dikirimkan atau dirender di browser klien.
3. **Pencegahan Sewa Liar:** Tombol mulai proses terkunci rapat sampai ada titik koordinat yang valid pada frame pertama. Simulasi UI tidak pernah memicu panggilan sewa.
4. **Kebijakan Tagihan (Billing Guard):** Pengguna secara sadar diberi catatan bahwa instance tidak dihapus otomatis (`AUTO_DESTROY=0`) demi keamanan log, sehingga pengguna perlu menghentikannya di console Vast.ai jika sudah selesai.

---

## 7. Responsivitas & Standar Perangkat

- **Resolusi Utama (Optimal):** Desktop 1440 × 900 px ke atas (Full multi-column layout).
- **Laptop / Tablet (1024 px – 1280 px):** Grid kolom sekunder beradaptasi di bawah stage, sidebar menyusut rapi.
- **Mobile Fallback (< 768 px):** Tata letak satu kolom vertikal, seluruh interaksi sentuh dan scrubbing timeline tetap dapat dioperasikan.
- **Prefers-Reduced-Motion:** Animasi rotasi kipas GPU dan simulasi dinonaktifkan jika sistem operasi pengguna meminta pengurangan gerakan.
