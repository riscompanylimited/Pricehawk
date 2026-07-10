"""
PriceHawk API - CFW/Makro Branch
"""

import os
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from routers import auth, dashboard, products, matches, price_formula, price_by_location, alerts, categories, watchlists, scraper

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('user_sessions.log'),
        logging.StreamHandler()
    ]
)

app = FastAPI(title="PriceHawk CFW/Makro API")

CORS_ORIGINS = os.environ.get("CORS_ORIGINS", "http://localhost:3000")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in CORS_ORIGINS.split(",")],
    allow_origin_regex=r"https://.*\.vercel\.app",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(dashboard.router)
# products/export must be included before products/{sku} to avoid route shadowing
app.include_router(products.router)
app.include_router(matches.router)
app.include_router(price_formula.router)
app.include_router(price_by_location.router)
app.include_router(alerts.router)
app.include_router(categories.router)
app.include_router(watchlists.router)
app.include_router(scraper.router)


if __name__ == "__main__":
    import uvicorn
    config = uvicorn.Config(
        app=app,
        host="0.0.0.0",
        port=8000,
        timeout_keep_alive=300,
        timeout_graceful_shutdown=30,
        limit_max_requests=None,
    )
    server = uvicorn.Server(config)
    import asyncio
    asyncio.run(server.serve())
