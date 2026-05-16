import { createClient } from '@supabase/supabase-js'

// Credentials are loaded from VITE_ environment variables (populated in .env at project root).
// Placeholder values prevent "supabaseUrl is required" crash when env vars are missing;
// Supabase API calls will fail with 401 rather than crashing the entire app.
const supabaseUrl = import.meta.env.VITE_SUPABASE_URL || 'https://placeholder.supabase.co'
const supabaseAnonKey = import.meta.env.VITE_SUPABASE_ANON_KEY || 'placeholder-anon-key'

export const supabase = createClient(supabaseUrl, supabaseAnonKey)

// Import the supabase client like this:
// For React:
// import { supabase } from "@/integrations/supabase/client";
// For React Native:
// import { supabase } from "@/src/integrations/supabase/client";
