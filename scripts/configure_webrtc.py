"""Create a private browser access code; never print or overwrite provider keys."""
from pathlib import Path
import secrets
import shutil
from dotenv import dotenv_values


def configure(root):
    path=root / ".env"
    if not path.exists(): shutil.copyfile(root / ".env.example",path)
    values=dotenv_values(path)
    lines=path.read_text(encoding="utf-8").splitlines()
    if not values.get("WEBRTC_ACCESS_CODE"):
        lines=[line for line in lines if not line.startswith("WEBRTC_ACCESS_CODE=")]
        lines.append("WEBRTC_ACCESS_CODE="+secrets.token_urlsafe(24))
    if not values.get("WEBRTC_MAX_CALLS"):
        lines=[line for line in lines if not line.startswith("WEBRTC_MAX_CALLS=")]
        lines.append("WEBRTC_MAX_CALLS=1")
    path.write_text("\n".join(lines)+"\n",encoding="utf-8")
    print("WebRTC access configured. Read WEBRTC_ACCESS_CODE in your local .env; keep it private.")


if __name__ == "__main__": configure(Path(__file__).resolve().parents[1])
