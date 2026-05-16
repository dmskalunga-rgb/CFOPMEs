// =====================================================
// KWANZACONTROL - Supabase Database Types
// Auto-generated types for type-safe database access
// Data: 2026-04-04
// =====================================================

export type Json =
  | string
  | number
  | boolean
  | null
  | { [key: string]: Json | undefined }
  | Json[]

export interface Database {
  public: {
    Tables: {
      tenants: {
        Row: {
          id: string
          nif: string
          name: string
          legal_name: string | null
          address: string | null
          city: string | null
          phone: string | null
          email: string | null
          logo_url: string | null
          industry: string | null
          size: string | null
          fiscal_year_start: string | null
          currency: string
          timezone: string
          agt_certified: boolean
          agt_certificate_url: string | null
          agt_private_key_encrypted: string | null
          subscription_plan: string
          subscription_status: string
          subscription_expires_at: string | null
          settings: Json
          created_at: string
          updated_at: string
        }
        Insert: Omit<Database['public']['Tables']['tenants']['Row'], 'id' | 'created_at' | 'updated_at'>
        Update: Partial<Database['public']['Tables']['tenants']['Insert']>
      }
      users: {
        Row: {
          id: string
          tenant_id: string
          email: string
          full_name: string
          avatar_url: string | null
          role: string
          phone: string | null
          department: string | null
          position: string | null
          is_active: boolean
          last_login_at: string | null
          last_login_ip: string | null
          two_factor_enabled: boolean
          two_factor_secret: string | null
          preferences: Json
          created_at: string
          updated_at: string
        }
        Insert: Omit<Database['public']['Tables']['users']['Row'], 'created_at' | 'updated_at'>
        Update: Partial<Database['public']['Tables']['users']['Insert']>
      }
      customers: {
        Row: {
          id: string
          tenant_id: string
          nif: string | null
          name: string
          legal_name: string | null
          email: string | null
          phone: string | null
          address: string | null
          city: string | null
          postal_code: string | null
          country: string
          customer_type: string
          payment_terms: number
          credit_limit: number | null
          notes: string | null
          is_active: boolean
          created_at: string
          updated_at: string
        }
        Insert: Omit<Database['public']['Tables']['customers']['Row'], 'id' | 'created_at' | 'updated_at'>
        Update: Partial<Database['public']['Tables']['customers']['Insert']>
      }
      invoices: {
        Row: {
          id: string
          tenant_id: string
          invoice_number: string
          series: string
          customer_id: string
          customer_name: string
          customer_nif: string | null
          customer_address: string | null
          issue_date: string
          due_date: string
          payment_date: string | null
          subtotal: number
          iva_amount: number
          discount_amount: number
          total: number
          currency: string
          status: string
          payment_method: string | null
          payment_reference: string | null
          notes: string | null
          internal_notes: string | null
          agt_status: string | null
          agt_request_id: string | null
          agt_validation_code: string | null
          agt_submitted_at: string | null
          agt_validated_at: string | null
          agt_rejection_reason: string | null
          agt_hash: string | null
          agt_previous_hash: string | null
          agt_signature: string | null
          pdf_url: string | null
          created_by: string | null
          created_at: string
          updated_at: string
        }
        Insert: Omit<Database['public']['Tables']['invoices']['Row'], 'id' | 'created_at' | 'updated_at'>
        Update: Partial<Database['public']['Tables']['invoices']['Insert']>
      }
      invoice_items: {
        Row: {
          id: string
          invoice_id: string
          line_number: number
          product_code: string | null
          description: string
          quantity: number
          unit_price: number
          discount_percent: number
          discount_amount: number
          subtotal: number
          iva_rate: string
          iva_percent: number
          iva_amount: number
          total: number
          created_at: string
        }
        Insert: Omit<Database['public']['Tables']['invoice_items']['Row'], 'id' | 'created_at'>
        Update: Partial<Database['public']['Tables']['invoice_items']['Insert']>
      }
      employees: {
        Row: {
          id: string
          tenant_id: string
          employee_number: string | null
          nif: string | null
          full_name: string
          email: string | null
          phone: string | null
          date_of_birth: string | null
          address: string | null
          city: string | null
          position: string
          department: string | null
          hire_date: string
          termination_date: string | null
          employment_type: string
          gross_salary: number
          bank_name: string | null
          bank_account: string | null
          iban: string | null
          status: string
          notes: string | null
          created_at: string
          updated_at: string
        }
        Insert: Omit<Database['public']['Tables']['employees']['Row'], 'id' | 'created_at' | 'updated_at'>
        Update: Partial<Database['public']['Tables']['employees']['Insert']>
      }
      payslips: {
        Row: {
          id: string
          tenant_id: string
          employee_id: string
          employee_name: string
          employee_nif: string | null
          payroll_month: string
          payment_date: string | null
          gross_salary: number
          allowances: number
          bonuses: number
          overtime: number
          total_earnings: number
          inss_employee: number
          inss_employer: number
          irt: number
          irt_bracket: number | null
          other_deductions: number
          total_deductions: number
          net_salary: number
          pdf_url: string | null
          payment_status: string
          payment_reference: string | null
          notes: string | null
          created_by: string | null
          created_at: string
          updated_at: string
        }
        Insert: Omit<Database['public']['Tables']['payslips']['Row'], 'id' | 'created_at' | 'updated_at'>
        Update: Partial<Database['public']['Tables']['payslips']['Insert']>
      }
      transaction_categories: {
        Row: {
          id: string
          tenant_id: string | null
          name: string
          type: string
          parent_id: string | null
          color: string
          icon: string | null
          is_system: boolean
          is_active: boolean
          created_at: string
        }
        Insert: Omit<Database['public']['Tables']['transaction_categories']['Row'], 'id' | 'created_at'>
        Update: Partial<Database['public']['Tables']['transaction_categories']['Insert']>
      }
      transactions: {
        Row: {
          id: string
          tenant_id: string
          transaction_number: string | null
          type: string
          category_id: string | null
          category_name: string | null
          amount: number
          currency: string
          transaction_date: string
          description: string
          reference: string | null
          payment_method: string | null
          account: string | null
          invoice_id: string | null
          payslip_id: string | null
          is_reconciled: boolean
          reconciled_at: string | null
          reconciled_by: string | null
          ai_suggested_category: string | null
          ai_confidence: number | null
          ai_classified_at: string | null
          attachments: Json
          tags: string[] | null
          notes: string | null
          created_by: string | null
          created_at: string
          updated_at: string
        }
        Insert: Omit<Database['public']['Tables']['transactions']['Row'], 'id' | 'created_at' | 'updated_at'>
        Update: Partial<Database['public']['Tables']['transactions']['Insert']>
      }
      notifications: {
        Row: {
          id: string
          tenant_id: string
          user_id: string | null
          type: string
          category: string | null
          title: string
          message: string
          action_url: string | null
          action_label: string | null
          priority: string
          is_read: boolean
          read_at: string | null
          resource_type: string | null
          resource_id: string | null
          metadata: Json
          expires_at: string | null
          created_at: string
        }
        Insert: Omit<Database['public']['Tables']['notifications']['Row'], 'id' | 'created_at'>
        Update: Partial<Database['public']['Tables']['notifications']['Insert']>
      }
      audit_logs: {
        Row: {
          id: number
          tenant_id: string
          user_id: string | null
          user_email: string | null
          action: string
          resource_type: string
          resource_id: string | null
          resource_name: string | null
          changes: Json | null
          ip_address: string | null
          user_agent: string | null
          request_id: string | null
          previous_hash: string | null
          current_hash: string | null
          timestamp: string
        }
        Insert: Omit<Database['public']['Tables']['audit_logs']['Row'], 'id' | 'timestamp'>
        Update: Partial<Database['public']['Tables']['audit_logs']['Insert']>
      }
    }
    Views: {}
    Functions: {
      calculate_irt: {
        Args: {
          p_gross_salary: number
          p_inss_deduction: number
        }
        Returns: {
          irt_amount: number
          irt_bracket: number
        }[]
      }
      create_audit_log: {
        Args: {
          p_tenant_id: string
          p_action: string
          p_resource_type: string
          p_resource_id: string
          p_resource_name?: string
          p_changes?: Json
        }
        Returns: void
      }
    }
    Enums: {}
  }
}
