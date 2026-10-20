#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
command -v docker >/dev/null || { echo 'Pasang Docker Engine + Compose plugin dahulu: https://docs.docker.com/engine/install/'; exit 1; }
docker compose version >/dev/null
docker info >/dev/null 2>&1 || { echo 'Jalankan dengan user yang memiliki akses Docker (atau sudo bash controller/setup.sh).'; exit 1; }
command -v python3 >/dev/null || { echo 'Python 3 diperlukan untuk membuat konfigurasi.'; exit 1; }
if [ ! -f .env ]; then
  read -r -p 'Domain untuk website (contoh gpu.example.com, DNS sudah mengarah ke VPS): ' DOMAIN
  read -r -s -p 'Vast.ai API key (input tersembunyi): ' VAST_API_KEY
  echo
  read -r -p 'Batas harga GPU US$/jam [0.30]: ' MAX_PRICE_HOUR
  export DOMAIN VAST_API_KEY
  export MAX_PRICE_HOUR="${MAX_PRICE_HOUR:-0.30}"
  WORKER_REF=$(git rev-parse HEAD)
  export WORKER_REF
  umask 077
  python3 - <<'PY'
import os, re, secrets
from pathlib import Path
name = os.environ['DOMAIN'].strip()
if not re.fullmatch(r'[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?', name) or '.' not in name:
    raise SystemExit('Domain tidak valid.')
key = os.environ['VAST_API_KEY'].strip()
if not key or not re.fullmatch(r'[a-zA-Z0-9_-]+', key):
    raise SystemExit('API key kosong atau format tidak didukung.')
price = float(os.environ['MAX_PRICE_HOUR'])
if not 0 < price <= 100:
    raise SystemExit('Batas harga tidak valid.')
password = secrets.token_urlsafe(24)
lines = {'DOMAIN': name, 'PUBLIC_URL': 'https://' + name, 'VAST_API_KEY': key,
         'CONTROLLER_PASSWORD': password, 'WORKER_REF': os.environ['WORKER_REF'],
         'VAST_MACHINE_ID': '28726', 'MAX_PRICE_HOUR': str(price), 'MIN_VRAM_GB': '16', 'MAX_RENT_MINUTES': '45',
         'DISK_GB': '30', 'MAX_UPLOAD_MB': '250',
         'VAST_IMAGE': 'pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime'}
with Path('.env').open('x') as output:
    output.write(''.join(f'{k}={v}\n' for k,v in lines.items()))
print('Konfigurasi disimpan di controller/.env (jangan dibagikan).')
print('Username: admin')
print('Password website:', password)
PY
  unset VAST_API_KEY
else
  echo 'Memakai controller/.env yang ada; tidak menimpa konfigurasi.'
fi
docker compose up -d --build
echo 'Website akan tersedia pada domain yang kamu atur setelah sertifikat HTTPS siap.'
echo 'Lihat log: cd controller && docker compose logs --tail=100 -f'
echo 'API key hanya berada di VPS. Penyewaan baru terjadi saat tombol Sewa GPU & proses ditekan.'
