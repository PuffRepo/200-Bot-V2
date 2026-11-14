"""Serve sanitized S9 JSON and published result PNGs; no database writes."""
import argparse
import os
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from v2.public_feed import create_server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',type=Path,required=True)
    parser.add_argument('--host',default='0.0.0.0')
    parser.add_argument('--port',type=int,default=int(os.environ.get('PORT','8080')))
    args=parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('Port must be 1–65535.')
    server = create_server(args.database,args.host,args.port)
    print('PUBLIC FEED: serving sanitized S9 JSON and recorded result images.',flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__=='__main__':raise SystemExit(main())
