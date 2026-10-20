#!/usr/bin/env bash
# Menyiapkan semuanya, menjalankan server, dan membuka tunnel publik.
set -e
cd "$(dirname "$0")"
log(){ echo -e "\n==> $*"; }

log "Memeriksa ffmpeg dan cloudflared"
command -v ffmpeg >/dev/null || { apt-get update -qq && apt-get install -y -qq ffmpeg; }
if [ ! -x ./cloudflared ]; then
  case "$(uname -m)" in aarch64|arm64) A=arm64;; *) A=amd64;; esac
  wget -q -O cloudflared "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-$A"
  chmod +x cloudflared
fi

if [ ! -f .installed ]; then
  log "Memasang paket Python (beberapa menit)"
  pip install -q -r requirements.txt || pip install -q --break-system-packages -r requirements.txt
  SAM2_BUILD_CUDA=0 pip install -q "git+https://github.com/facebookresearch/sam2.git" \
    || SAM2_BUILD_CUDA=0 pip install -q --break-system-packages "git+https://github.com/facebookresearch/sam2.git"
  touch .installed
fi

log "Mengunduh model"
mkdir -p checkpoints
if [ ! -f checkpoints/sam2.1_hiera_large.pt ]; then
  wget -q --show-progress -O checkpoints/tmp.pt \
    https://dl.fbaipublicfiles.com/segment_anything_2/092824/sam2.1_hiera_large.pt
  mv checkpoints/tmp.pt checkpoints/sam2.1_hiera_large.pt
fi
python3 - <<'PY'
from huggingface_hub import snapshot_download
snapshot_download("depth-anything/Depth-Anything-V2-Large-hf")
PY

export APP_PASSWORD="${APP_PASSWORD:-$(python3 -c 'import secrets;print(secrets.token_hex(5))')}"
export PORT="${PORT:-8000}"
pkill -f "uvicorn app:app" 2>/dev/null || true
pkill -f "cloudflared tunnel" 2>/dev/null || true

log "Menjalankan server dan tunnel"
nohup uvicorn app:app --host 127.0.0.1 --port "$PORT" > server.log 2>&1 &
nohup ./cloudflared tunnel --no-autoupdate --url "http://127.0.0.1:$PORT" > tunnel.log 2>&1 &

URL=""
for i in $(seq 1 90); do
  URL=$(grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' tunnel.log | head -1 || true)
  [ -n "$URL" ] && break
  sleep 1
done
[ -n "$URL" ] || { echo "Tunnel gagal. Isi tunnel.log:"; tail -20 tunnel.log; exit 1; }

echo
echo "=========================================="
echo " SIAP. Buka di browser:  $URL"
echo " User: admin"
echo " Password: $APP_PASSWORD"
echo "=========================================="
echo "(Log server di bawah. Server tetap jalan walau terminal ditutup.)"
exec tail -f server.log
