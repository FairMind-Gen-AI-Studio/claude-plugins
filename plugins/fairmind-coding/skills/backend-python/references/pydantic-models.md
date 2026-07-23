# Pydantic Models Reference

## Basic Models

```python
from pydantic import BaseModel, Field, EmailStr
from typing import Optional, List
from datetime import datetime
from enum import Enum

class UserRole(str, Enum):
    user = "user"
    admin = "admin"
    moderator = "moderator"

class UserBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    email: EmailStr
    role: UserRole = UserRole.user

class UserCreate(UserBase):
    password: str = Field(..., min_length=8)

class UserUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    email: Optional[EmailStr] = None
    role: Optional[UserRole] = None

class UserResponse(UserBase):
    id: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
```

## Field Constraints

```python
from pydantic import Field
from typing import Annotated

# String constraints
class Product(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    sku: str = Field(..., pattern=r"^[A-Z]{3}-\d{6}$")
    description: str = Field(default="", max_length=2000)

# Numeric constraints
class Order(BaseModel):
    quantity: int = Field(..., ge=1, le=1000)
    price: float = Field(..., gt=0)
    discount: float = Field(default=0, ge=0, le=1)

# Using Annotated
PositiveInt = Annotated[int, Field(gt=0)]
Percentage = Annotated[float, Field(ge=0, le=100)]

class Stats(BaseModel):
    count: PositiveInt
    success_rate: Percentage
```

## Custom Validators

```python
from pydantic import BaseModel, field_validator, model_validator
from typing import Self

class UserCreate(BaseModel):
    username: str
    password: str
    password_confirm: str
    email: str

    @field_validator("username")
    @classmethod
    def username_alphanumeric(cls, v: str) -> str:
        if not v.isalnum():
            raise ValueError("Username must be alphanumeric")
        return v.lower()

    @field_validator("email")
    @classmethod
    def email_domain(cls, v: str) -> str:
        if not v.endswith("@company.com"):
            raise ValueError("Must use company email")
        return v

    @model_validator(mode="after")
    def check_passwords_match(self) -> Self:
        if self.password != self.password_confirm:
            raise ValueError("Passwords do not match")
        return self

# Before validation
class Item(BaseModel):
    tags: list[str]

    @field_validator("tags", mode="before")
    @classmethod
    def split_tags(cls, v):
        if isinstance(v, str):
            return [tag.strip() for tag in v.split(",")]
        return v
```

## Nested Models

```python
from pydantic import BaseModel
from typing import List, Optional

class Address(BaseModel):
    street: str
    city: str
    country: str
    postal_code: str

class ContactInfo(BaseModel):
    email: EmailStr
    phone: Optional[str] = None
    address: Optional[Address] = None

class Company(BaseModel):
    name: str
    contact: ContactInfo
    employees: List["Employee"] = []

class Employee(BaseModel):
    name: str
    position: str
    contact: ContactInfo
    company_id: str

# Forward reference resolution
Company.model_rebuild()
```

## Generic Models

```python
from pydantic import BaseModel
from typing import TypeVar, Generic, List, Optional

T = TypeVar("T")

class PaginatedResponse(BaseModel, Generic[T]):
    items: List[T]
    total: int
    page: int
    per_page: int
    pages: int

class ApiResponse(BaseModel, Generic[T]):
    success: bool = True
    data: Optional[T] = None
    error: Optional[str] = None

# Usage
class UserList(PaginatedResponse[UserResponse]):
    pass

def get_users() -> ApiResponse[List[UserResponse]]:
    users = [...]
    return ApiResponse(data=users)
```

## Model Configuration

```python
from pydantic import BaseModel, ConfigDict
from datetime import datetime

class User(BaseModel):
    model_config = ConfigDict(
        # Convert from ORM models
        from_attributes=True,
        # Validate on assignment
        validate_assignment=True,
        # Extra fields handling
        extra="forbid",  # or "allow", "ignore"
        # Strip whitespace
        str_strip_whitespace=True,
        # Populate by name or alias
        populate_by_name=True,
    )

    id: str
    name: str
    email: str

# With aliases
class UserAPI(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    user_id: str = Field(alias="id")
    full_name: str = Field(alias="name")
```

## Serialization

```python
from pydantic import BaseModel, field_serializer, computed_field
from datetime import datetime

class User(BaseModel):
    id: str
    name: str
    email: str
    created_at: datetime
    _internal_data: str = ""

    @field_serializer("created_at")
    def serialize_datetime(self, dt: datetime) -> str:
        return dt.isoformat()

    @computed_field
    @property
    def display_name(self) -> str:
        return f"{self.name} <{self.email}>"

# Serialization options
user = User(...)

# To dict
user.model_dump()
user.model_dump(exclude={"_internal_data"})
user.model_dump(include={"id", "name"})
user.model_dump(exclude_unset=True)
user.model_dump(by_alias=True)

# To JSON
user.model_dump_json()
user.model_dump_json(indent=2)
```

## Discriminated Unions

```python
from pydantic import BaseModel, Field
from typing import Literal, Union
from typing_extensions import Annotated

class Cat(BaseModel):
    pet_type: Literal["cat"]
    name: str
    meows: int

class Dog(BaseModel):
    pet_type: Literal["dog"]
    name: str
    barks: float

# Discriminated union
Pet = Annotated[
    Union[Cat, Dog],
    Field(discriminator="pet_type")
]

class Owner(BaseModel):
    name: str
    pets: List[Pet]

# Validation automatically picks correct model
owner = Owner(
    name="John",
    pets=[
        {"pet_type": "cat", "name": "Whiskers", "meows": 5},
        {"pet_type": "dog", "name": "Buddy", "barks": 3.5},
    ]
)
```

## Settings Management

```python
from pydantic_settings import BaseSettings
from pydantic import Field
from typing import List
from functools import lru_cache

class Settings(BaseSettings):
    # Required
    database_url: str
    secret_key: str

    # With defaults
    debug: bool = False
    api_prefix: str = "/api/v1"

    # From env with different name
    redis_url: str = Field(alias="REDIS_CONNECTION_STRING")

    # Complex types
    allowed_origins: List[str] = ["http://localhost:3000"]

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }

@lru_cache
def get_settings() -> Settings:
    return Settings()

# Usage
settings = get_settings()
```

## Request/Response Patterns

```python
# Input schemas (what API receives)
class ItemCreate(BaseModel):
    name: str
    price: float
    category_id: str

class ItemUpdate(BaseModel):
    name: Optional[str] = None
    price: Optional[float] = None
    category_id: Optional[str] = None

# Output schemas (what API returns)
class ItemResponse(BaseModel):
    id: str
    name: str
    price: float
    category: "CategoryResponse"
    created_at: datetime

    model_config = {"from_attributes": True}

# Database schema (internal)
class ItemInDB(ItemCreate):
    id: str
    created_at: datetime
    updated_at: datetime

# Pagination
class PaginationParams(BaseModel):
    page: int = Field(1, ge=1)
    per_page: int = Field(10, ge=1, le=100)

class PaginatedItems(BaseModel):
    items: List[ItemResponse]
    total: int
    page: int
    per_page: int
    pages: int
```

## Error Schemas

```python
class ErrorDetail(BaseModel):
    loc: List[str]
    msg: str
    type: str

class ValidationErrorResponse(BaseModel):
    detail: List[ErrorDetail]

class HTTPErrorResponse(BaseModel):
    detail: str

# Usage in FastAPI
@app.post(
    "/items",
    response_model=ItemResponse,
    responses={
        400: {"model": ValidationErrorResponse},
        404: {"model": HTTPErrorResponse},
    },
)
async def create_item(item: ItemCreate):
    ...
```
