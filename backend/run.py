"""Entry point para el ejecutable PyInstaller."""
import uvicorn
from app.main import app  # import directo — funciona en PyInstaller y en dev

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
