import { createClient } from '@supabase/supabase-js'

const SUPABASE_URL = 'https://rmqjnoeqllkmhksgvopm.supabase.co'
const SUPABASE_KEY = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InJtcWpub2VxbGxrbWhrc2d2b3BtIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODEzNTg1NzMsImV4cCI6MjA5NjkzNDU3M30._cQs1DJPem3kavv-mRI_XR7ACo2HHONc4_0WTUXCsUA'

export const supabase = createClient(SUPABASE_URL, SUPABASE_KEY)
