from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt
from jwt.exceptions import InvalidTokenError, ExpiredSignatureError
from datetime import datetime, timedelta
from typing import Optional, Dict
import os
import time

try:
    from constants import GOOGLE_WEB_CLIENT_SECRET
except ImportError:
    # For testing environments where constants.py might not exist
    GOOGLE_WEB_CLIENT_SECRET = os.getenv("GOOGLE_WEB_CLIENT_SECRET", "")
from google.oauth2 import id_token
from google.auth.transport import requests
import logging

# Security scheme for JWT.
# auto_error=False so a missing/malformed Authorization header yields None here
# (instead of HTTPBearer's default 403) and we can raise a 401 ourselves below.
# 401 is the correct status for missing/expired credentials and is what the
# Android client's OkHttp TokenAuthenticator listens for to refresh-and-retry
# (OkHttp Authenticator fires only on 401/407, never 403).
security = HTTPBearer(auto_error=False)

# Configuration (ideally stored in environment variables)
SECRET_KEY = GOOGLE_WEB_CLIENT_SECRET
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 24 hours
REFRESH_TOKEN_EXPIRE_DAYS = 7  # Sliding window: each refresh issues a new token good for 7 days
REFRESH_SESSION_MAX_DAYS = 30  # Absolute cap: a session cannot be extended past 30 days from sign-in
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
    Create a JWT refresh token.

    The token carries a `session_start` Unix timestamp (the original sign-in time).
    Callers rotating an existing token pass the prior `session_start` through in
    `data`; sign-in callers omit it and it defaults to now. Expiry is the earlier
    of now + REFRESH_TOKEN_EXPIRE_DAYS and session_start + REFRESH_SESSION_MAX_DAYS,
    so the sliding window can never extend a session past the absolute cap.
    """
    to_encode = data.copy()
    now = int(time.time())
    session_start = int(to_encode.get("session_start") or now)
    sliding_expire = now + int((expires_delta or timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)).total_seconds())
    absolute_expire = session_start + REFRESH_SESSION_MAX_DAYS * 24 * 60 * 60
    to_encode.update({
        "exp": min(sliding_expire, absolute_expire),
        "type": "refresh",
        "session_start": session_start,
    })
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def refresh_session_expired(payload: Dict) -> bool:
    """True if the refresh token's session has passed the absolute cap.

    Tokens issued before `session_start` existed have no claim; they are treated
    as still within the cap so existing users are not logged out by the upgrade.
    """
    session_start = payload.get("session_start")
    if session_start is None:
        return False
    return time.time() > int(session_start) + REFRESH_SESSION_MAX_DAYS * 24 * 60 * 60

def verify_token(token: str, logger=None):
    current_logger = logger if logger else logging.getLogger(__name__)
    
    if not token:
        raise HTTPException(status_code=401, detail="No token provided")

    try:
        # This should verify SERVER JWT tokens, not Google ID tokens
        # Decode and verify the JWT token using our secret key
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        
        # Check if it's an access token (not refresh token)
        token_type = payload.get("type")
        if token_type != "access":
            current_logger.warning(f"Invalid token type: {token_type}")
            raise HTTPException(status_code=401, detail="Invalid token type")
        
        # Check if token has required fields
        user_id = payload.get("sub")
        if not user_id:
            current_logger.warning("Token missing 'sub' field")
            raise HTTPException(status_code=401, detail="Invalid token")
        
        # Return the payload for downstream use
        return payload
        
    except jwt.ExpiredSignatureError:
        current_logger.warning("Token has expired")
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError as e:
        current_logger.warning(f"Token verification failed (InvalidTokenError): {str(e)}")
        raise HTTPException(status_code=401, detail="Invalid token")
    except Exception as e:
        current_logger.error(f"Unexpected error during token verification: {str(e)}")
        raise HTTPException(status_code=401, detail="Token verification failed")

def verify_google_token(token: str) -> Dict:
    """
    Verify a Google ID token and return the claims
    """
    try:
        logger.debug("Attempting to verify Google token")
        # Specify the CLIENT_ID of the app that accesses the backend
        for client_id in GOOGLE_CLIENT_IDS:
            try:
                idinfo = id_token.verify_oauth2_token(token, requests.Request(), client_id)
                logger.debug("Token verification successful")

                # ID token is valid. Get the user's Google Account ID
                if idinfo.get('iss') not in ['accounts.google.com', 'https://accounts.google.com']:
                    logger.debug(f"Invalid issuer: {idinfo.get('iss')}")
                    continue  # Try the next client ID if issuer is wrong

                return {
                    "sub": idinfo.get("sub"),
                    "email": idinfo.get("email"),
                    "name": idinfo.get("name"),
                    "picture": idinfo.get("picture"),
                    "email_verified": idinfo.get("email_verified", False)
                }
            except ValueError as e:
                logger.debug(f"Token verification failed: {str(e)}")
                continue

        # If we get here, token was not verified with any client ID
        logger.warning("Token verification failed with all client IDs")
        raise AuthError("Invalid Google token")
    
    except Exception as e:
        print(f"Unexpected error during token verification: {str(e)}")
        raise AuthError(f"Token verification failed: {str(e)}")

async def get_current_user(credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)):
    """
    Get the current user from a JWT token
    """
    try:
        if credentials is None or not credentials.credentials:
            raise HTTPException(
                status_code=401,
                detail="No token provided",
                headers={"WWW-Authenticate": "Bearer"},
            )
        
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