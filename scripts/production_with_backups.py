"""Supervise existing production runner and hourly off-volume backups."""
import argparse
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=Path('/app/private/production.json'))
    args = parser.parse_args()
    sys.path.insert(0, str(ROOT))
    from v2.backups import settings
    settings()  # Fail visibly on missing configuration, before launching the bot.
    interval = int(os.environ.get('LOUNGE_BACKUP_INTERVAL_SECONDS', '3600'))
    if interval < 300:
        raise ValueError('Backup interval must be at least 300 seconds')
    import json
    config_path = args.config.resolve()
    config = json.loads(config_path.read_text(encoding='utf-8'))
    database = Path(config['database_path'])
    if not database.is_absolute():
        database = ROOT / database
    public_enabled = os.environ.get('LOUNGE_PUBLIC_FEED_ENABLED', '') == '1'
    public_feed = None
    public_due = 0.0
    children = []
    stopping = False
    def stop(*_):
        nonlocal stopping
        stopping = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        bot = subprocess.Popen([sys.executable, str(ROOT / 'scripts/v2_production.py'), '--config', str(config_path)], cwd=ROOT)
        children.append(bot)
        backup = None
        due = 0.0
        while not stopping and bot.poll() is None:
            if public_enabled:
                if public_feed is not None and public_feed.poll() is not None:
                    children.remove(public_feed)
                    public_feed = None
                    public_due = time.monotonic() + 30
                    print('PUBLIC FEED ALERT: worker stopped; retrying in 30 seconds. Bot remains running.', flush=True)
                if public_feed is None and time.monotonic() >= public_due:
                    try:
                        public_feed = subprocess.Popen([sys.executable, str(ROOT / 'scripts/serve_public_s9.py'), '--database', str(database)], cwd=ROOT)
                        children.append(public_feed)
                    except OSError as error:
                        public_due = time.monotonic() + 30
                        print(f'PUBLIC FEED ALERT: worker could not start ({type(error).__name__}); retrying in 30 seconds. Bot remains running.', flush=True)
            if backup is not None and backup.poll() is not None:
                code = backup.returncode
                children.remove(backup)
                backup = None
                due = time.monotonic() + (interval if code == 0 else 300)
                if code:
                    print('BACKUP ALERT: backup failed; retrying in five minutes. Bot remains running.', flush=True)
            if backup is None and time.monotonic() >= due:
                backup = subprocess.Popen([sys.executable, str(ROOT / 'scripts/backup_production.py'), '--database', str(database)], cwd=ROOT)
                children.append(backup)
                backup_started = time.monotonic()
            if backup is not None and time.monotonic() - backup_started > 600 and backup.poll() is None:
                backup.kill()
                print('BACKUP ALERT: backup exceeded ten minutes; stopped backup worker.', flush=True)
            time.sleep(1)
        return 0 if stopping else bot.returncode
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
        deadline = time.monotonic() + 20
        for child in children:
            try:
                child.wait(timeout=max(0.1, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()


if __name__ == '__main__':
    raise SystemExit(main())
