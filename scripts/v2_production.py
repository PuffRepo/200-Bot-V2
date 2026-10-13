"""Validate explicit production configuration; launch only when enabled."""
import argparse
import asyncio
import getpass
import os
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from v2.runtime_config import load_runtime

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,default=Path('settings/production.json'))
    parser.add_argument('--check-config',action='store_true',help='Validate IDs/policy/path offline, even while disabled; does not contact Discord or create a database.')
    args=parser.parse_args()
    os.chdir(ROOT)
    try:
        runtime=load_runtime(args.config,require_enabled=not args.check_config)
        if args.check_config:
            print(f"Configuration structure valid: guild {runtime['guild_id']}, season S9, database {runtime['database_path']}.")
            print('No connection or database changes. Role existence, hierarchy, channel permissions and token identity are checked at startup.')
            return 0
        from v2_lab import serve_lab
        import discord
        token_key=runtime.get('runtime',{}).get('token_environment_variable','DISCORD_BOT_TOKEN')
        token=os.environ.get(token_key) or getpass.getpass('Production bot token (hidden): ')
        if not token:raise ValueError('A bot token is required.')
        return asyncio.run(serve_lab(token,runtime))
    except KeyboardInterrupt:
        print('Production bot stopped.');return 0
    except Exception as error:
        # Never print tokens, configuration dumps, or authentication URLs.
        if type(error).__name__=='LoginFailure':print('Discord rejected the bot token.')
        else:print(f'Startup failed: {type(error).__name__}: {error}')
        return 2

if __name__=='__main__':raise SystemExit(main())
