---
name: backend-python
description: Use when implementing backend features with Python, FastAPI, or Pydantic. This skill covers API design, data validation, async patterns, and testing strategies for Python backend applications.
---

# Backend Development with Python

## Overview

This skill provides guidance for backend development using Python with FastAPI for APIs and Pydantic for data validation.

**Announce at start:** "I'm using the backend-python skill for this Python backend implementation."

## When to Use

Use this skill when:
- Creating FastAPI endpoints
- Implementing Pydantic models for validation
- Writing async Python code
- Building REST APIs with Python
- Implementing background tasks
- Testing Python backend code

## Core Workflow

### Step 1: Understand API Requirements

Before implementing:
1. Review the API contract from user story
2. Identify data models and validation rules
3. Plan async vs sync operations
4. Design error handling strategy

### Step 2: Design Data Models

1. Define Pydantic models for request/response
2. Plan validation rules and constraints
3. Consider serialization needs
4. Document model relationships

### Step 3: Implement Endpoints

Follow this order:
1. **Models first** - Define Pydantic schemas
2. **Dependencies** - Create reusable dependencies
3. **Business logic** - Implement service layer
4. **Endpoints** - Create FastAPI routes
5. **Error handling** - Handle all error cases

### Step 4: Test and Document

1. Write pytest tests for endpoints
2. Verify error handling
3. Test async behavior
4. Generate OpenAPI docs

## Reference Files

| File | Content | When to Use |
|------|---------|-------------|
| `references/fastapi-patterns.md` | API design, dependencies | Creating endpoints |
| `references/pydantic-models.md` | Data validation, schemas | Data modeling |
| `references/async-patterns.md` | Async/await, concurrency | Async operations |
| `references/testing-patterns.md` | Pytest, fixtures | Testing |

## Key Principles

### API Design
- Use appropriate HTTP methods
- Return consistent response formats
- Use dependency injection
- Document with docstrings

### Data Validation
- Validate inputs with Pydantic
- Use custom validators for complex rules
- Handle validation errors gracefully
- Provide meaningful error messages

### Performance
- Use async for I/O operations
- Implement connection pooling
- Cache expensive computations
- Profile and optimize hot paths

## Integration with Fairmind

When working on Fairmind tasks:
1. Use `fairmind-context` skill to gather requirements
2. Check work package for API specifications
3. Reference architectural blueprints
4. Update journal with decisions

## Example Usage

```python
# Example: User API endpoint
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, EmailStr
from typing import Optional

router = APIRouter(prefix="/users", tags=["users"])

class UserCreate(BaseModel):
    name: str
    email: EmailStr
    role: str = "user"

class UserResponse(BaseModel):
    id: str
    name: str
    email: str
    role: str

    class Config:
        from_attributes = True

@router.post("/", response_model=UserResponse, status_code=201)
async def create_user(user_data: UserCreate):
    """Create a new user."""
    user = await user_service.create(user_data)
    return user
```

## Next Steps

After completing implementation:
- Use `fairmind-tdd` skill for testing
- Request code review
- Document API endpoints
