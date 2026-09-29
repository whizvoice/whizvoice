from supabase import create_client
import os

try:
    from constants import SUPABASE_URL, SUPABASE_SERVICE_ROLE
except ImportError:
    # For testing environments where constants.py might not exist
    SUPABASE_URL = os.getenv("SUPABASE_URL", "")
    SUPABASE_SERVICE_ROLE = os.getenv("SUPABASE_SERVICE_ROLE", os.getenv("SUPABASE_KEY", ""))

supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE) 