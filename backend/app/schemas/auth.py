from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    access_code: str = Field(pattern=r"^\d{5}$")


class SessionUser(BaseModel):
    id: str | None = None
    email: str
    name: str | None = None
    role: str | None = None
    status: str | None = None


class SessionResponse(BaseModel):
    authenticated: bool
    user: SessionUser | None = None
