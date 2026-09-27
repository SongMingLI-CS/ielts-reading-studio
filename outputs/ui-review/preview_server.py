"""Isolated UI review; existing templates/assets, no model API calls."""
from pathlib import Path
import sqlite3
import shutil
import uvicorn
from app.config import AppConfig
from app.pipeline.service import ReadingStudioService
from app.web.app import create_app

ROOT = Path(__file__).resolve().parents[2]
REVIEW = Path(__file__).parent
DATA = REVIEW / "preview-data"
DATA.mkdir(exist_ok=True)
DB = DATA / "state.db"
if not DB.exists():
    with sqlite3.connect(f"file:{ROOT / 'output/state.db'}?mode=ro", uri=True) as src:
        with sqlite3.connect(DB) as dst:
            src.backup(dst)
    for name in ("packages", "corpora", "context-novel"):
        source = ROOT / "output" / name
        if source.exists():
            shutil.copytree(source, DATA / name, dirs_exist_ok=True)
config = AppConfig(base_dir=REVIEW, input_dir=DATA / "input", output_dir=DATA, database_path=DB)
service = ReadingStudioService(config)
application = create_app(config=config, service=service)
if __name__ == "__main__":
    uvicorn.run(application, host="127.0.0.1", port=8765, log_level="warning")
