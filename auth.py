from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt
from jwt.exceptions import InvalidTokenError
from datetime import datetime, timedelta
from typing import Optional, Dict
import os
from google.oauth2 import id_token
from google.auth.transport import requests

# Security scheme for JWT
security = HTTPBearer()

# Configuration (ideally stored in environment variables)
SECRET_KEY = os.environ.get("JWT_SECRET_KEY", "your-secret-key-for-jwt-should-be-kept-secret")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 24 hours
GOOGLE_CLIENT_IDS = [
    os.environ.get("GOOGLE_CLIENT_ID", "your-web-client-id.apps.googleusercontent.com"),
    "2815827813-se3l1u83nqbtda59dtplcbbjsr38oqln.apps.googleusercontent.com"  # Add your new web client ID here
]

class AuthError(Exception):
    """Custom exception for authentication errors"""
    def __init__(self, message: str, status_code: int = 401):
        self.message = message
        self.status_code = status_code
        super().__init__(self.message)

def create_access_token(data: Dict, expires_delta: Optional[timedelta] = None) -> str:
    """
    Create a JWT token with the specified data and expiration
    """
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """
    Verify a JWT token and return the payload
    """
    try:
        token = credentials.credentials
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        )

def verify_google_token(token: str) -> Dict:
    """
    Verify a Google ID token and return the claims
    """
    try:
        # Specify the CLIENT_ID of the app that accesses the backend
        for client_id in GOOGLE_CLIENT_IDS:
            try:
                idinfo = id_token.verify_oauth2_token(token, requests.Request(), client_id)
                
                # ID token is valid. Get the user's Google Account ID
                if idinfo.get('iss') not in ['accounts.google.com', 'https://accounts.google.com']:
                    continue  # Try the next client ID if issuer is wrong
                
                return {
                    "sub": idinfo.get("sub"),
                    "email": idinfo.get("email"),
                    "name": idinfo.get("name"),
                    "picture": idinfo.get("picture"),
                    "email_verified": idinfo.get("email_verified", False)
                }
            except ValueError:
                # Invalid token or wrong client ID for this token
                continue
        
        # If we get here, token was not verified with any client ID
        raise AuthError("Invalid Google token")
    
    except Exception as e:
        raise AuthError(f"Token verification failed: {str(e)}")

async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """
    Get the current user from a JWT token
    """
    try:
        payload = verify_token(credentials)
        user_id = payload.get("sub")
        if user_id is None:
            raise HTTPException(status_code=401, detail="Invalid authentication credentials")
        return payload
    except Exception as e:
        raise HTTPException(status_code=401, detail=str(e)) 