from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt
from jwt.exceptions import InvalidTokenError
from datetime import datetime, timedelta
from typing import Optional, Dict
import os
from constants import GOOGLE_WEB_CLIENT_SECRET
from google.oauth2 import id_token
from google.auth.transport import requests
import logging

# Security scheme for JWT
security = HTTPBearer()

# Configuration (ideally stored in environment variables)
SECRET_KEY = GOOGLE_WEB_CLIENT_SECRET
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 24 hours
REFRESH_TOKEN_EXPIRE_DAYS = 7  # Example: 7 days for refresh token
GOOGLE_CLIENT_IDS = [
    "2815827813-se3l1u83nqbtda59dtplcbbjsr38oqln.apps.googleusercontent.com",  # Web client ID
    "2815827813-kdkrisushm16fsi95533kmll1usm3uco.apps.googleusercontent.com"   # Android client ID
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
    to_encode.update({"exp": expire, "type": "access"})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def create_refresh_token(data: Dict, expires_delta: Optional[timedelta] = None) -> str:
    """
    Create a JWT refresh token with the specified data and expiration
    """
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS))
    to_encode.update({"exp": expire, "type": "refresh"})
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
    except InvalidTokenError as e:
        # Log the specific InvalidTokenError before raising HTTPException
        logger = logging.getLogger(__name__)
        logger.error(f"JWT InvalidTokenError: {str(e)}. Token (first 15 chars): {token[:15]}...")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except Exception as e:
        # Catch any other potential errors during decoding and log them
        logger = logging.getLogger(__name__)
        logger.error(f"JWT General Decoding Error: {str(e)}. Token (first 15 chars): {token[:15]}...")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token decoding error", # More generic detail for unexpected errors
            headers={"WWW-Authenticate": "Bearer"},
        )

def verify_google_token(token: str) -> Dict:
    """
    Verify a Google ID token and return the claims
    """
    try:
        print(f"Attempting to verify Google token with client IDs: {GOOGLE_CLIENT_IDS}")
        # Specify the CLIENT_ID of the app that accesses the backend
        for client_id in GOOGLE_CLIENT_IDS:
            try:
                print(f"Trying to verify with client ID: {client_id}")
                idinfo = id_token.verify_oauth2_token(token, requests.Request(), client_id)
                print(f"Token verification successful with client ID: {client_id}")
                print(f"Token info: {idinfo}")
                
                # ID token is valid. Get the user's Google Account ID
                if idinfo.get('iss') not in ['accounts.google.com', 'https://accounts.google.com']:
                    print(f"Invalid issuer: {idinfo.get('iss')}")
                    continue  # Try the next client ID if issuer is wrong
                
                return {
                    "sub": idinfo.get("sub"),
                    "email": idinfo.get("email"),
                    "name": idinfo.get("name"),
                    "picture": idinfo.get("picture"),
                    "email_verified": idinfo.get("email_verified", False)
                }
            except ValueError as e:
                print(f"Token verification failed with client ID {client_id}: {str(e)}")
                continue
        
        # If we get here, token was not verified with any client ID
        print("Token verification failed with all client IDs")
        raise AuthError("Invalid Google token")
    
    except Exception as e:
        print(f"Unexpected error during token verification: {str(e)}")
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