def create_app():
    try:
        from fastapi import FastAPI
    except ImportError as exc:
        raise RuntimeError('Install API dependencies with: pip install -e ".[api]"') from exc

    app = FastAPI(title="EvalHub", version="0.1.0")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "service": "evalhub"}

    return app


app = create_app()
