@echo off
cd /d "c:\Users\AgusT\OneDrive\Escritorio\Bot MUSICA"

echo.
echo ============================================
echo   Installing Music Bot Dependencies
echo ============================================
echo.

python -m pip install --upgrade pip

echo.
echo Installing core dependencies...
python -m pip install python-dotenv requests

echo.
echo Installing Spotify & YouTube APIs...
python -m pip install spotipy yt-dlp

echo.
echo Installing FastAPI (for next phase)...
python -m pip install fastapi uvicorn pydantic

echo.
echo Installing Database...
python -m pip install sqlalchemy

echo.
echo Installing async & background tasks...
python -m pip install aiohttp asyncio-contextmanager

echo.
echo Installing ML & Data Analysis...
python -m pip install numpy pandas scikit-learn

echo.
echo Installing Web Scraping...
python -m pip install beautifulsoup4

echo.
echo Installing Utilities...
python -m pip install colorama tqdm

echo.
echo ============================================
echo   ✅ Installation Complete!
echo ============================================
echo.
pause
