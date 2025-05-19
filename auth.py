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

def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """
    Verify a JWT token and return the payload
    """
    actual_token_from_credentials = None
    token_display_for_logging = "Token not extracted or credentials object issue"
    current_logger = logging.getLogger(__name__) # Get logger instance once

    try:
        current_logger.info("[DEBUG] Entered verify_token")
        
        if credentials and credentials.credentials:
            actual_token_from_credentials = credentials.credentials
            if isinstance(actual_token_from_credentials, str):
                token_display_for_logging = (actual_token_from_credentials[:20] + "..." 
                                             if len(actual_token_from_credentials) > 20 
                                             else actual_token_from_credentials)
                if not actual_token_from_credentials:
                    token_display_for_logging = "Token was an empty string"
            else:
                token_display_for_logging = "Token in credentials was not a string (type: %s)" % type(actual_token_from_credentials).__name__
        else:
            current_logger.warning("[DEBUG] Credentials object or .credentials attribute was missing/empty in verify_token.")

        current_logger.info(f"[DEBUG] Attempting to verify token. Token (info for logging): {token_display_for_logging}")
        
        payload = None
        try:
            # Core operation: decode the token
            payload = jwt.decode(actual_token_from_credentials, SECRET_KEY, algorithms=[ALGORITHM])
        except UnboundLocalError as ule:
            # Specifically catch UnboundLocalError if it's about a variable named 'token'
            if 'local variable \'token\' referenced before assignment' in str(ule).lower():
                current_logger.error(
                    f"jwt.decode raised UnboundLocalError for 'token': {str(ule)}. "
                    f"This may indicate an issue within the JWT library or an unexpected token state. "
                    f"Input token info: {token_display_for_logging}"
                )
                # Convert to InvalidTokenError to be handled by the next except block
                raise InvalidTokenError("Internal error during token decoding (UnboundLocalError for 'token').") from ule
            else:
                raise # Re-raise other UnboundLocalErrors not matching the specific message
        # Other specific jwt exceptions like ExpiredSignatureError, etc., inherit from InvalidTokenError
        # and will be caught by the InvalidTokenError block if not caught before UnboundLocalError.

        current_logger.info(f"[DEBUG] Token decoded successfully: {payload}")
        return payload
        
    except InvalidTokenError as e: # Catches errors from jwt.decode, including our re-raised one
        current_logger.error(f"JWT InvalidTokenError: {str(e)}. Token (info from attempt): {token_display_for_logging}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid or expired token: {str(e)}",
            headers={"WWW-Authenticate": "Bearer"},
        ) from e
    except Exception as e: # Catches other errors (e.g., TypeError if token was None and not caught by ULE for 'token')
        current_logger.error(f"JWT General Decoding Error: {str(e)}. Token (info from attempt): {token_display_for_logging}")
        
        detail_message = f"Token decoding error: {str(e)}"
        if actual_token_from_credentials is None and isinstance(e, TypeError):
             detail_message = "Token decoding error: No token provided or token extraction failed."

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=detail_message,
            headers={"WWW-Authenticate": "Bearer"},
        ) from e

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
    logger.info("[DEBUG] Entered get_current_user")
    try:
        logger.info(f"[DEBUG] Incoming Authorization header: {credentials.credentials[:20]}...")
        payload = verify_token(credentials)
        user_id = payload.get("sub")
        if user_id is None:
            logger.error("[DEBUG] No user_id in token payload")
            raise HTTPException(status_code=401, detail="Invalid authentication credentials")
        logger.info(f"[DEBUG] Successfully authenticated user_id: {user_id}")
        return payload
    except Exception as e:
        logger.error(f"[DEBUG] Error in get_current_user: {str(e)}")
        raise HTTPException(status_code=401, detail=str(e)) 