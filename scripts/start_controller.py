"""Launch script for Doosan E0509 Demo Robot Controller REST Server."""
import sys
import uvicorn
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

def main():
    print("=" * 65)
    print("  [Vision Lab] Doosan E0509 Demo Robot Controller Starting...")
    print("  Listening on http://127.0.0.1:8011")
    print("=" * 65)
    uvicorn.run('vision_picking.server:create_app', factory=True, host="127.0.0.1", port=8011, workers=1, log_level="info")


if __name__ == "__main__":
    main()
