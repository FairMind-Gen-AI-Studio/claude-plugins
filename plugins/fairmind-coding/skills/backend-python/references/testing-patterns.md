# Testing Patterns Reference

## Pytest Basics

### Test Structure

```python
# tests/test_users.py
import pytest
from httpx import AsyncClient
from app.main import app

class TestUserAPI:
    """Test suite for user API endpoints."""

    @pytest.mark.asyncio
    async def test_create_user_success(self, client: AsyncClient):
        """Test successful user creation."""
        response = await client.post(
            "/api/v1/users",
            json={
                "name": "John Doe",
                "email": "john@example.com",
                "password": "securepass123",
            },
        )

        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "John Doe"
        assert data["email"] == "john@example.com"
        assert "password" not in data

    @pytest.mark.asyncio
    async def test_create_user_invalid_email(self, client: AsyncClient):
        """Test user creation with invalid email."""
        response = await client.post(
            "/api/v1/users",
            json={
                "name": "John Doe",
                "email": "invalid-email",
                "password": "securepass123",
            },
        )

        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_get_user_not_found(self, client: AsyncClient):
        """Test getting non-existent user."""
        response = await client.get("/api/v1/users/nonexistent-id")

        assert response.status_code == 404
```

## Fixtures

### conftest.py

```python
# tests/conftest.py
import pytest
import asyncio
from typing import AsyncGenerator
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from app.main import app
from app.db.session import get_db
from app.db.base import Base

# Test database URL
TEST_DATABASE_URL = "postgresql+asyncpg://test:test@localhost/test_db"

@pytest.fixture(scope="session")
def event_loop():
    """Create event loop for session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()

@pytest.fixture(scope="session")
async def engine():
    """Create test database engine."""
    engine = create_async_engine(TEST_DATABASE_URL, echo=True)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

    await engine.dispose()

@pytest.fixture
async def db_session(engine) -> AsyncGenerator[AsyncSession, None]:
    """Create database session for each test."""
    async with AsyncSession(engine) as session:
        yield session
        await session.rollback()

@pytest.fixture
async def client(db_session) -> AsyncGenerator[AsyncClient, None]:
    """Create test client with overridden dependencies."""

    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    app.dependency_overrides.clear()
```

### Factory Fixtures

```python
# tests/factories.py
import factory
from factory import fuzzy
from app.models.user import User

class UserFactory(factory.Factory):
    class Meta:
        model = User

    id = factory.LazyFunction(lambda: str(uuid.uuid4()))
    name = factory.Faker("name")
    email = factory.LazyAttribute(lambda o: f"{o.name.lower().replace(' ', '.')}@example.com")
    role = "user"
    created_at = factory.LazyFunction(datetime.utcnow)

# In conftest.py
@pytest.fixture
def user_factory(db_session):
    """Factory for creating test users."""
    async def create_user(**kwargs):
        user = UserFactory(**kwargs)
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)
        return user
    return create_user

# Usage in tests
@pytest.mark.asyncio
async def test_get_user(client, user_factory):
    user = await user_factory(name="Test User")
    response = await client.get(f"/api/v1/users/{user.id}")
    assert response.status_code == 200
```

## Mocking

### Using pytest-mock

```python
@pytest.mark.asyncio
async def test_send_email(client, mocker):
    """Test endpoint that sends email."""
    # Mock the email service
    mock_send = mocker.patch(
        "app.services.email.send_email",
        return_value=None,
    )

    response = await client.post(
        "/api/v1/users/1/notify",
        json={"message": "Hello"},
    )

    assert response.status_code == 200
    mock_send.assert_called_once_with(
        to="user@example.com",
        subject="Notification",
        body="Hello",
    )

@pytest.mark.asyncio
async def test_external_api_call(client, mocker):
    """Test with mocked external API."""
    mock_response = mocker.Mock()
    mock_response.json = mocker.AsyncMock(return_value={"data": "value"})
    mock_response.status = 200

    mocker.patch(
        "aiohttp.ClientSession.get",
        return_value=mocker.AsyncMock(__aenter__=mocker.AsyncMock(return_value=mock_response)),
    )

    response = await client.get("/api/v1/external-data")
    assert response.status_code == 200
```

### Using unittest.mock

```python
from unittest.mock import AsyncMock, patch, MagicMock

@pytest.mark.asyncio
async def test_with_mock():
    # Mock async function
    with patch("app.services.user.fetch_user", new_callable=AsyncMock) as mock:
        mock.return_value = {"id": "1", "name": "Test"}

        result = await some_function()

        mock.assert_called_once_with("1")
        assert result["name"] == "Test"

# Mock context manager
@pytest.mark.asyncio
async def test_mock_context_manager():
    mock_session = MagicMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)

    with patch("app.db.get_session", return_value=mock_session):
        await some_db_operation()
```

## Parametrized Tests

```python
@pytest.mark.parametrize(
    "email,expected_status",
    [
        ("valid@example.com", 201),
        ("invalid-email", 422),
        ("", 422),
        ("a" * 256 + "@example.com", 422),
    ],
)
@pytest.mark.asyncio
async def test_create_user_email_validation(client, email, expected_status):
    response = await client.post(
        "/api/v1/users",
        json={"name": "Test", "email": email, "password": "password123"},
    )
    assert response.status_code == expected_status

@pytest.mark.parametrize(
    "user_data,expected_errors",
    [
        (
            {"email": "test@example.com"},
            ["name", "password"],
        ),
        (
            {"name": "Test"},
            ["email", "password"],
        ),
    ],
)
@pytest.mark.asyncio
async def test_create_user_required_fields(client, user_data, expected_errors):
    response = await client.post("/api/v1/users", json=user_data)
    assert response.status_code == 422
    errors = response.json()["detail"]
    error_fields = [e["loc"][-1] for e in errors]
    for field in expected_errors:
        assert field in error_fields
```

## Testing Authentication

```python
@pytest.fixture
async def auth_headers(client, user_factory):
    """Get auth headers for authenticated requests."""
    user = await user_factory(password="testpass123")

    response = await client.post(
        "/api/v1/auth/login",
        json={"email": user.email, "password": "testpass123"},
    )

    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}

@pytest.mark.asyncio
async def test_protected_endpoint(client, auth_headers):
    response = await client.get(
        "/api/v1/users/me",
        headers=auth_headers,
    )
    assert response.status_code == 200

@pytest.mark.asyncio
async def test_protected_endpoint_no_auth(client):
    response = await client.get("/api/v1/users/me")
    assert response.status_code == 401
```

## Integration Tests

```python
@pytest.mark.integration
@pytest.mark.asyncio
async def test_user_workflow(client):
    """Test complete user workflow."""
    # Create user
    create_response = await client.post(
        "/api/v1/users",
        json={
            "name": "Integration Test",
            "email": "integration@test.com",
            "password": "password123",
        },
    )
    assert create_response.status_code == 201
    user_id = create_response.json()["id"]

    # Login
    login_response = await client.post(
        "/api/v1/auth/login",
        json={"email": "integration@test.com", "password": "password123"},
    )
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Update profile
    update_response = await client.put(
        f"/api/v1/users/{user_id}",
        headers=headers,
        json={"name": "Updated Name"},
    )
    assert update_response.status_code == 200
    assert update_response.json()["name"] == "Updated Name"

    # Delete user
    delete_response = await client.delete(
        f"/api/v1/users/{user_id}",
        headers=headers,
    )
    assert delete_response.status_code == 204
```

## Test Coverage

```ini
# pyproject.toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
addopts = [
    "--cov=app",
    "--cov-report=term-missing",
    "--cov-report=html",
    "--cov-fail-under=80",
]

[tool.coverage.run]
source = ["app"]
omit = ["app/tests/*", "app/migrations/*"]

[tool.coverage.report]
exclude_lines = [
    "pragma: no cover",
    "def __repr__",
    "raise NotImplementedError",
    "if TYPE_CHECKING:",
]
```

## Running Tests

```bash
# Run all tests
pytest

# Run with verbose output
pytest -v

# Run specific test file
pytest tests/test_users.py

# Run specific test
pytest tests/test_users.py::TestUserAPI::test_create_user_success

# Run tests matching pattern
pytest -k "create_user"

# Run only marked tests
pytest -m "integration"

# Run with coverage
pytest --cov=app --cov-report=html

# Run in parallel
pytest -n auto
```
