"""Scheduled entry point: daily 09:30 Fortaleza slot, with login/retry catch-up."""
import argparse
import fcntl
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import subprocess
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
STATE_DIR = ROOT / '.generation_sync'
TZ = ZoneInfo('America/Fortaleza')


def due_slot(now):
    due = now.astimezone(TZ).replace(hour=9, minute=30, second=0, microsecond=0)
    if now.astimezone(TZ) < due:
        due -= timedelta(days=1)
    return due.date().isoformat()


def main(status=False):
    STATE_DIR.mkdir(exist_ok=True)
    state_path = STATE_DIR / 'status.json'
    if status:
        print(state_path.read_text() if state_path.exists() else 'No scheduled run recorded yet.')
        return 0
    logger = logging.getLogger('generation_sync')
    logger.setLevel(logging.INFO)
    handler = RotatingFileHandler(STATE_DIR / 'sync.log', maxBytes=1_000_000, backupCount=3)
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    logger.addHandler(handler)
    with (STATE_DIR / 'run.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        state = json.loads(state_path.read_text()) if state_path.exists() else {}
        now = datetime.now(TZ)
        slot = due_slot(now)
        if state.get('last_success_slot', '') >= slot:
            return 0
        logger.info('Starting sync for scheduled slot %s', slot)
        env = os.environ.copy()
        env['PATH'] = '/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin'
        env['DYLD_LIBRARY_PATH'] = '/opt/homebrew/opt/openssl@3/lib'
        state.update(last_attempt=now.isoformat(), last_attempt_slot=slot)
        try:
            result = subprocess.run(
                [str(ROOT / '.venv/bin/python'), '-B', str(ROOT / 'run_generation_sync.py'), '--apply'],
                cwd=ROOT, env=env, capture_output=True, text=True, timeout=600,
            )
            if result.returncode:
                logger.error('Sync failed: %s', (result.stderr or result.stdout)[-12000:])
                raise RuntimeError(f'Sync exited with code {result.returncode}; see sync.log')
            summary = json.loads(result.stdout)
            if not summary.get('uploaded_records'):
                raise RuntimeError('No source records uploaded; will retry')
            state.update(last_success_slot=slot, last_success_at=datetime.now(TZ).isoformat(),
                         last_result=summary, last_error=None)
            logger.info('Success: %s uploaded, %s new, %s changed',
                        summary['uploaded_records'], summary['new_records'], summary['changed_records'])
            for plant in summary['coverage']:
                if plant['missing_in_fabric']:
                    logger.warning('Missing in Fabric: %s: %s', plant['plant'], ', '.join(plant['missing_in_fabric']))
        except Exception as exc:
            state['last_error'] = str(exc)
            logger.error('%s; retry at next scheduled check', exc)
        temporary = state_path.with_suffix('.tmp')
        temporary.write_text(json.dumps(state, indent=2, ensure_ascii=False))
        temporary.replace(state_path)
        return 1 if state.get('last_error') else 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--status', action='store_true')
    raise SystemExit(main(status=parser.parse_args().status))
