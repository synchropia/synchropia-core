import jwt
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, Optional
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from src.config.settings import settings

# Initialize security schema for Bearer token authentication
security = HTTPBearer(auto_error=True)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """
    Helper function to generate a JWT access token.
    """
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=60)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
    return encoded_jwt

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> Dict[str, Any]:
    """
    Dependency Injection function to extract, decode, and validate the JWT token.
    
    Can be used:
    1. Per route:
       @app.get("/items")
       def read_items(current_user: dict = Depends(get_current_user)):
           ...
           
    2. Per router:
       router = APIRouter(dependencies=[Depends(get_current_user)])
       
    3. Globally for all routes:
       app = FastAPI(dependencies=[Depends(get_current_user)])
    """
    token = credentials.credentials
    try:
        # Decode the token
        payload = jwt.decode(
            token, 
            settings.JWT_SECRET_KEY, 
            algorithms=[settings.JWT_ALGORITHM]
        )
        
        # Verify the subject exists in the token payload
        user_id = payload.get("sub") or payload.get("user_id")
        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token payload is invalid (missing 'sub' or 'user_id' claim).",
                headers={"WWW-Authenticate": "Bearer"},
            )
            
        return payload
        
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token signature has expired.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials / invalid token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
