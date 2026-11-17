from fastapi import FastAPI, Request
from fastapi.responses import Response
from workers import asgi


app = FastAPI(
    title="句读 · 日语学习（Cloudflare）",
    version="0.3.0-cloud",
    docs_url=None,
    redoc_url=None,
)
Default = asgi.entrypoint(app)


@app.get("/api/health")
async def health(request: Request) -> dict[str, object]:
    """Verify that the Worker and both D1 bindings are reachable."""
    env = request.scope["env"]
    content = await env.CONTENT_DB.prepare("SELECT 1 AS ok").all()
    user = await env.USER_DB.prepare("SELECT 1 AS ok").all()
    return {
        "status": "ok",
        "runtime": "cloudflare-workers-python",
        "content_database": bool(content.success),
        "user_database": bool(user.success),
    }


@app.get("/{path:path}")
async def frontend(path: str, request: Request) -> Response:
    """Serve the existing local-first frontend from Workers Static Assets."""
    env = request.scope["env"]
    asset_path = path or "index.html"
    response = await env.ASSETS.fetch(f"https://assets.local/{asset_path}")
    body = await response.bytes()
    return Response(content=body, status_code=response.status, headers=response.headers)

