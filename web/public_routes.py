"""Public website pages share the application bundle and real auth endpoints."""
from pathlib import Path
import re

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse


def create_router(get_react_directory, get_static_directory):
    router = APIRouter()

    @router.get('/login/', include_in_schema=False)
    @router.get('/register/', include_in_schema=False)
    def authentication_page():
        index = Path(get_react_directory()) / 'index.html'
        if not index.is_file():
            index = Path(get_static_directory()) / 'index.html'
        if not index.is_file():
            raise HTTPException(503, '页面暂时不可用，请稍后刷新')
        # Relative to /<application-prefix>/login/ or /register/. All existing
        # ./assets and ./static references keep the same application root.
        html = re.sub(r'<head(?:\s[^>]*)?>', lambda match: match.group(0) + '\n    <base href="../">',
                      index.read_text(encoding='utf-8'), count=1, flags=re.IGNORECASE)
        return HTMLResponse(html, headers={'Cache-Control': 'no-cache, no-store, must-revalidate',
                                          'Pragma': 'no-cache'})

    return router
