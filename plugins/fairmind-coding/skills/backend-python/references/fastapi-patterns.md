# FastAPI Patterns Reference

## Application Structure

```
app/
├── main.py              # FastAPI app entry point
├── api/
│   ├── __init__.py
│   ├── deps.py          # Shared dependencies
│   └── v1/
│       ├── __init__.py
│       ├── router.py    # API router
│       ├── users.py     # User endpoints
│       └── items.py     # Item endpoints
├── core/
│   ├── __init__.py
│   ├── config.py        # Settings
│   └── security.py      # Auth utilities
├── models/
│   ├── __init__.py
│   └── user.py          # Database models
├── schemas/
│   ├── __init__.py
│   └── user.py          # Pydantic schemas
├── services/
│   ├── __init__.py
│   └── user.py          # Business logic
└── db/
    ├── __init__.py
    └── session.py       # Database session
```

## Application Setup

```python
# main.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from app.api.v1.router import api_router
from app.core.config import settings
from app.db.session import create_db_pool, close_db_pool

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await create_db_pool()
    yield
    # Shutdown
    await close_db_pool()

app = FastAPI(
    title=settings.PROJECT_NAME,
    description="API Description",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(api_router, prefix="/api/v1")

@app.get("/health")
async def health_check():
    return {"status": "healthy"}
```

## Router Organization

```python
# api/v1/router.py
from fastapi import APIRouter
from app.api.v1 import users, items, auth

api_router = APIRouter()
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(users.router, prefix="/users", tags=["users"])
api_router.include_router(items.router, prefix="/items", tags=["items"])
```

## Endpoint Patterns

### Basic CRUD

```python
# api/v1/users.py
from fastapi import APIRouter, HTTPException, Depends, Query, Path
from typing import List
from app.schemas.user import UserCreate, UserUpdate, UserResponse
from app.services.user import UserService
from app.api.deps import get_user_service

router = APIRouter()

@router.get("/", response_model=List[UserResponse])
async def list_users(
    skip: int = Query(0, ge=0),
    limit: int = Query(10, ge=1, le=100),
    service: UserService = Depends(get_user_service),
):
    """List all users with pagination."""
    return await service.get_many(skip=skip, limit=limit)

@router.get("/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: str = Path(..., description="User ID"),
    service: UserService = Depends(get_user_service),
):
    """Get a specific user by ID."""
    user = await service.get_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user

@router.post("/", response_model=UserResponse, status_code=201)
async def create_user(
    user_data: UserCreate,
    service: UserService = Depends(get_user_service),
):
    """Create a new user."""
    return await service.create(user_data)

@router.put("/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: str,
    user_data: UserUpdate,
    service: UserService = Depends(get_user_service),
):
    """Update a user."""
    user = await service.update(user_id, user_data)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user

@router.delete("/{user_id}", status_code=204)
async def delete_user(
    user_id: str,
    service: UserService = Depends(get_user_service),
):
    """Delete a user."""
    deleted = await service.delete(user_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="User not found")
```

## Dependency Injection

```python
# api/deps.py
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from typing import Generator
from app.db.session import get_session
from app.services.user import UserService
from app.core.security import verify_token

security = HTTPBearer()

# Database session dependency
async def get_db() -> Generator:
    async with get_session() as session:
        yield session

# Service dependencies
async def get_user_service(db = Depends(get_db)) -> UserService:
    return UserService(db)

# Auth dependencies
async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    service: UserService = Depends(get_user_service),
):
    token = credentials.credentials
    payload = verify_token(token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )
    user = await service.get_by_id(payload["sub"])
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )
    return user

# Role-based access
def require_role(allowed_roles: list[str]):
    async def role_checker(
        current_user = Depends(get_current_user),
    ):
        if current_user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        return current_user
    return role_checker

# Usage
@router.get("/admin/users")
async def admin_list_users(
    current_user = Depends(require_role(["admin"])),
):
    ...
```

## Query Parameters

```python
from fastapi import Query
from typing import Optional
from enum import Enum

class SortOrder(str, Enum):
    asc = "asc"
    desc = "desc"

@router.get("/search")
async def search_users(
    q: Optional[str] = Query(None, min_length=1, max_length=100),
    role: Optional[str] = Query(None),
    is_active: bool = Query(True),
    sort_by: str = Query("created_at"),
    sort_order: SortOrder = Query(SortOrder.desc),
    page: int = Query(1, ge=1),
    per_page: int = Query(10, ge=1, le=100),
):
    """Search users with filters and pagination."""
    ...
```

## Request Body Variations

```python
from fastapi import Body
from typing import Optional

# Multiple body parameters
@router.post("/compare")
async def compare_users(
    user1: UserCreate = Body(...),
    user2: UserCreate = Body(...),
    options: Optional[dict] = Body(None),
):
    ...

# Embed single body
@router.post("/create")
async def create_user(
    user: UserCreate = Body(..., embed=True),
):
    # Expects: {"user": {...}}
    ...

# Raw body access
from fastapi import Request

@router.post("/webhook")
async def handle_webhook(request: Request):
    body = await request.body()
    json_body = await request.json()
    ...
```

## File Uploads

```python
from fastapi import File, UploadFile
from typing import List

@router.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
):
    contents = await file.read()
    return {
        "filename": file.filename,
        "content_type": file.content_type,
        "size": len(contents),
    }

@router.post("/upload-multiple")
async def upload_files(
    files: List[UploadFile] = File(...),
):
    return [{"filename": f.filename} for f in files]
```

## Background Tasks

```python
from fastapi import BackgroundTasks

async def send_email(email: str, message: str):
    # Simulate email sending
    await asyncio.sleep(1)
    print(f"Email sent to {email}")

@router.post("/users/{user_id}/notify")
async def notify_user(
    user_id: str,
    background_tasks: BackgroundTasks,
    service: UserService = Depends(get_user_service),
):
    user = await service.get_by_id(user_id)
    background_tasks.add_task(send_email, user.email, "Welcome!")
    return {"message": "Notification queued"}
```

## Response Models

```python
from fastapi import Response
from fastapi.responses import JSONResponse, StreamingResponse
from typing import Any

# Custom response
@router.get("/custom")
async def custom_response():
    return JSONResponse(
        content={"message": "Custom"},
        headers={"X-Custom-Header": "value"},
    )

# Streaming response
@router.get("/stream")
async def stream_data():
    async def generate():
        for i in range(10):
            yield f"data: {i}\n\n"
            await asyncio.sleep(0.1)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
    )

# Multiple response types
@router.get(
    "/{item_id}",
    responses={
        200: {"model": ItemResponse},
        404: {"model": ErrorResponse},
    },
)
async def get_item(item_id: str):
    ...
```

## Middleware

```python
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
import time
import uuid

class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id

        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id

        return response

class TimingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start = time.time()
        response = await call_next(request)
        duration = time.time() - start
        response.headers["X-Process-Time"] = str(duration)
        return response

# Add to app
app.add_middleware(RequestIdMiddleware)
app.add_middleware(TimingMiddleware)
```
