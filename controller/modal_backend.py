"""Modal GPU scheduler: independent store, SDK spawn/poll/cancel, claim-based dedup."""
import os
import threading
import time

from .core import Store, TERMINAL


class ModalSettings:
    """Read-only Modal configuration from environment."""

    def __init__(self, public_url, password, data):
        self.enabled = os.getenv('MODAL_ENABLED', '0') == '1'
        self.public_url = public_url
        self.password = password
        self.data = data / 'modal'
        self.data.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.app_name = os.getenv('MODAL_APP_NAME', 'frame-lab-modal')
        self.api_base = '/modal-api'
        self.function_name = os.getenv('MODAL_FUNCTION_NAME', 'process_job')
        self.gpu = os.getenv('MODAL_GPU', 'CPU')
        self.max_minutes = int(os.getenv('MODAL_MAX_MINUTES', '30'))
        self.max_upload_mb = int(os.getenv('MAX_UPLOAD_MB', '250'))
        # ponytail: token env validated lazily at first spawn, not at startup, to allow disabled mode without credentials.

    def config_dict(self):
        return dict(enabled=self.enabled, provider='modal', gpu=self.gpu,
                    max_upload_mb=self.max_upload_mb, max_minutes=self.max_minutes,
                    auto_destroy=True,
                    configured='Integrasi Modal aktif.' if self.enabled else 'MODAL_ENABLED belum diset ke 1.')


class ModalClient:
    """Thin wrapper around Modal SDK; import deferred to first use."""

    def __init__(self, settings):
        self.settings = settings
        self._func = None

    def _get_func(self):
        if self._func is None:
            import modal
            self._func = modal.Function.from_name(self.settings.app_name, self.settings.function_name)
        return self._func

    def spawn(self, controller_url, job_id, token):
        func = self._get_func()
        call = func.spawn(controller_url, job_id, token)
        return call.object_id

    def poll(self, call_id):
        """Return ('running', None), ('done', result), or ('error', message)."""
        import modal
        call = modal.FunctionCall.from_id(call_id)
        try:
            result = call.get(timeout=0)
            return ('done', result)
        except TimeoutError:
            return ('running', None)
        except Exception as exc:
            # Never leak internal exception details that might contain tokens.
            return ('error', f'{type(exc).__name__}: pemrosesan Modal gagal.')

    def cancel(self, call_id):
        import modal
        call = modal.FunctionCall.from_id(call_id)
        call.cancel(terminate_containers=True)


class ModalEngine:
    """Scheduler for Modal jobs. One active job at a time."""

    def __init__(self, store, client, settings, clock=time.time):
        self.store, self.client, self.settings, self.clock = store, client, settings, clock
        self.tick_lock = threading.Lock()

    def tick(self):
        with self.tick_lock:
            for job in self.store.all():
                if job['state'] in TERMINAL:
                    continue
                try:
                    self._step(job)
                except Exception as exc:
                    message = f'{type(exc).__name__}: {exc}' if isinstance(exc, RuntimeError) else type(exc).__name__
                    if job['state'] == 'queued':
                        self.store.update(job['id'], state='failed', stage='Gagal memulai Modal', error=message)
                    else:
                        self.store.update(job['id'], scheduler_error=message)

    def _step(self, job):
        jid, state = job['id'], job['state']
        now = self.clock()

        if state == 'queued':
            if not self.settings.enabled:
                self.store.update(jid, state='failed', stage='Modal belum diaktifkan',
                                  error='Set MODAL_ENABLED=1 dan konfigurasi token Modal.')
                return
            with self.store.lock:
                if self.store.get(jid)['state'] != 'queued':
                    return
                self.store.update(jid, state='creating', stage='Mengirim job ke Modal',
                                  gpu_name=self.settings.gpu,
                                  deadline=now + self.settings.max_minutes * 60)
            controller_url = self.settings.public_url + self.settings.api_base
            try:
                call_id = self.client.spawn(controller_url, jid, job['token'])
            except Exception:
                self.store.update(jid, stage='Modal spawn gagal atau ambiguous; menunggu claim dari worker')
                return
            with self.store.lock:
                current = self.store.get(jid)
                cancelled = current.get('cancel_requested')
                self.store.update(jid, modal_call_id=call_id,
                                  state='cleanup' if cancelled else 'booting',
                                  outcome='cancelled' if cancelled else None,
                                  stage='Menunggu worker Modal' if not cancelled else 'Membatalkan')
            if cancelled:
                try:
                    self.client.cancel(call_id)
                except Exception:
                    pass
            return

        if state == 'creating':
            # Spawn timed out or crashed before returning call_id.
            # NEVER re-spawn. Wait for worker claim or timeout.
            if now >= job.get('deadline', now + 1):
                self.store.update(jid, state='failed', stage='Batas waktu tercapai menunggu Modal spawn',
                                  error='Call ID tidak diterima. Periksa console Modal.')
            else:
                self.store.update(jid, stage='Menunggu claim dari worker Modal; tidak menyewa ulang')
            return

        if state in {'booting', 'running'}:
            if job.get('cancel_requested'):
                call_id = job.get('modal_call_id')
                if call_id:
                    try:
                        self.client.cancel(call_id)
                    except Exception:
                        pass
                self.store.update(jid, state='cleanup', outcome='cancelled', stage='Membatalkan job Modal')
                return
            if now >= job.get('deadline', now + 1):
                call_id = job.get('modal_call_id')
                if call_id:
                    try:
                        self.client.cancel(call_id)
                    except Exception:
                        pass
                self.store.update(jid, state='cleanup', outcome='failed',
                                  error='Batas waktu sewa tercapai.', stage='Menghapus job Modal')
                return
            # Poll remote status
            call_id = job.get('modal_call_id')
            if call_id:
                status, detail = self.client.poll(call_id)
                if status == 'error':
                    safe_error = str(detail).split(':', 1)[0] + ': pemrosesan Modal gagal.' if ':' in str(detail) else 'Modal gagal.'
                    self.store.update(jid, state='failed', stage='Worker Modal gagal', error=safe_error[:300])
            return

        if state == 'cleanup':
            # Modal auto-destroys containers; just transition to terminal.
            call_id = job.get('modal_call_id')
            if call_id:
                status, _ = self.client.poll(call_id)
                if status == 'running':
                    self.store.update(jid, stage='Menunggu container Modal berhenti')
                    return
            self.store.update(jid, state=job.get('outcome') or 'failed',
                              stage='Selesai' if job.get('outcome') == 'done' else 'Job Modal selesai',
                              finished=now, scheduler_error='')
