"""FastAPI Application Main Entrypoint with Async Lifespan Pool Management."""
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings
from app.routers import eval as eval_router
from app.routers import telemetry as telemetry_router
from app.sandbox.pool import pool
from app.utils.logger import logger


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for pool initialization and graceful shutdown."""
    logger.info("Initializing SWE-bench verification harness and pre-warmed sandbox pool...")
    await pool.initialize()
    yield
    logger.info("Draining and shutting down sandbox pool...")
    await pool.shutdown()
    logger.info("Service shutdown complete.")


app = FastAPI(
    title="SWE-Bench Verification Harness",
    description="Containerized execution harness for evaluating code patches and computing SWE-bench metrics.",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc"
)

# CORS Middleware setup
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API Routers
app.include_router(eval_router.router)
app.include_router(telemetry_router.router)


@app.get("/", summary="Root Endpoint")
async def root():
    return {
        "service": settings.SERVICE_NAME,
        "status": "ONLINE",
        "docs": "/docs",
        "metrics": "/metrics",
        "health": "/health"
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG,
        log_level=settings.LOG_LEVEL.lower()
    )
