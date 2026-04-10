"""Entry point para el ejecutable PyInstaller."""
import threading
import webbrowser
import time
import uvicorn
from app.main import app


def _open_browser():
    """Espera a que el servidor arranque y abre el navegador."""
    time.sleep(1.5)
    webbrowser.open("http://localhost:8000")


if __name__ == "__main__":
    threading.Thread(target=_open_browser, daemon=True).start()
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
