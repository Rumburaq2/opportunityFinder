// Minimal hand-written subset of the Supabase schema, just for the columns
// the web app reads/writes. Expand as we add features. (We can replace this
// later with `supabase gen types typescript --linked` if it grows unwieldy.)

export type Database = {
  public: {
    Tables: {
      profiles: {
        Row: {
          id: string;
          telegram_chat_id: number | null;
          stripe_customer_id: string | null;
          subscription_status: "none" | "active" | "past_due" | "canceled";
          subscription_current_period_end: string | null;
          created_at: string;
        };
        Insert: {
          id: string;
          telegram_chat_id?: number | null;
          stripe_customer_id?: string | null;
          subscription_status?: "none" | "active" | "past_due" | "canceled";
          subscription_current_period_end?: string | null;
        };
        Update: {
          id?: string;
          telegram_chat_id?: number | null;
          stripe_customer_id?: string | null;
          subscription_status?: "none" | "active" | "past_due" | "canceled";
          subscription_current_period_end?: string | null;
        };
        Relationships: [];
      };
      telegram_link_tokens: {
        Row: {
          token: string;
          user_id: string;
          expires_at: string;
          consumed_at: string | null;
          created_at: string;
        };
        Insert: {
          token: string;
          user_id: string;
          expires_at?: string;
          consumed_at?: string | null;
        };
        Update: {
          token?: string;
          user_id?: string;
          expires_at?: string;
          consumed_at?: string | null;
        };
        Relationships: [];
      };
    };
    Views: Record<string, never>;
    Functions: Record<string, never>;
  };
};
