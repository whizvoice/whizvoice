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

logger = logging.getLogger(__name__)

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

def verify_token(token: str, logger=None):
    current_logger = logger if logger else default_logger
    
    if not token:
        raise HTTPException(status_code=401, detail="No token provided")

    try:
        credentials = get_google_oauth_credentials()
        
        if not credentials or not hasattr(credentials, 'id_token') or not credentials.id_token:
            current_logger.warning("Credentials object or .credentials attribute was missing/empty in verify_token.")
            raise HTTPException(status_code=401, detail="Invalid token")
        
        # Verify the token
        idinfo = id_token.verify_oauth2_token(
            token, 
            requests.Request(),
            GOOGLE_CLIENT_ID,
            clock_skew_in_seconds=10
        )
        
        # Check if token is valid (has required fields)
        if 'aud' not in idinfo:
            current_logger.warning("Token missing 'aud' field")
            raise HTTPException(status_code=401, detail="Invalid token")
        
        if idinfo['aud'] != GOOGLE_CLIENT_ID:
            current_logger.warning(f"Token audience mismatch. Expected: {GOOGLE_CLIENT_ID}, Got: {idinfo['aud']}")
            raise HTTPException(status_code=401, detail="Invalid token")
        
        # Return token payload for downstream use
        return idinfo
        
    except ValueError as e:
        current_logger.warning(f"Token verification failed (ValueError): {str(e)}")
        raise HTTPException(status_code=401, detail="Invalid token")
    except Exception as e:
        current_logger.error(f"Unexpected error during token verification: {str(e)}")
        raise HTTPException(status_code=401, detail="Token verification failed")

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
        if not credentials.credentials:
            raise HTTPException(status_code=401, detail="No token provided")
        
        user_info = verify_token(credentials.credentials, logger)
        user_id = user_info.get("sub")
        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid token")
        
        return user_info
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in get_current_user: {str(e)}")
        raise HTTPException(status_code=401, detail="Authentication failed") 