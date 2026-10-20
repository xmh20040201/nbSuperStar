#!/usr/bin/env python3
"""SuperStar Web Management Panel - Entry Point"""
import sys
from web import create_app

if __name__ == "__main__":
    host = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 5000
    debug = "--debug" in sys.argv or "-d" in sys.argv

    app = create_app()
    print(f"SuperStar Web Panel starting on http://{host}:{port}")
    app.run(host=host, port=port, debug=debug, threaded=True)
