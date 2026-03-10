from supabase import create_client
from app.config import SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY

# Клиент Supabase с правами service_role (для серверных операций)
supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
