from supabase import create_client
from constants import SUPABASE_URL, SUPABASE_SERVICE_ROLE

supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE) 