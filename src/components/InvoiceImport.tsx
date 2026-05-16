/**
 * InvoiceImport — Componente de Importação de Facturas e Ficheiros
 *
 * Suporta:
 *  - CSV  (separador ; ou ,)  com mapeamento automático de colunas
 *  - JSON  (array de facturas)
 *  - Upload directo para Supabase Storage (bucket "imports")
 *  - Criação automática de clientes novos
 *  - Preview dos dados antes de confirmar
 *  - Histórico de importações (tabela invoice_imports)
 */

import { useState, useCallback, useRef, useEffect } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import {
  Upload, FileText, CheckCircle2, XCircle, AlertTriangle,
  RefreshCw, Download, ChevronDown, ChevronUp, Eye, Trash2,
  File, FileSpreadsheet, FileJson, Clock, Info,
  ArrowUpCircle, BookOpen, History,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Separator } from '@/components/ui/separator'
import { Progress } from '@/components/ui/progress'
import {
  Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle,
} from '@/components/ui/dialog'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { toast } from 'sonner'
import { supabase } from '@/integrations/supabase/client'
import {
  customerService, invoiceService,
  IVA_RATES, calcItem,
  type Customer, type InvoiceItem, type IvaRate,
} from '@/services/invoicingServiceReal'

// ─── Tipos ────────────────────────────────────────────────────────────────────

interface ImportRow {
  rowIndex:       number
  raw:            Record<string, string>
  // dados mapeados
  customer_name?:  string
  customer_nif?:   string
  customer_email?: string
  invoice_number?: string
  issue_date?:     string
  due_date?:       string
  description?:    string
  quantity?:       number
  unit_price?:     number
  discount?:       number
  iva_rate?:       IvaRate
  payment_method?: string
  notes?:          string
  currency?:       string
  // validação
  errors:         string[]
  warnings:       string[]
  status:         'valid' | 'warning' | 'error' | 'skipped'
}

interface ImportJob {
  id: string
  file_name: string
  file_type: string
  file_size: number | null
  status: string
  total_rows: number
  imported_rows: number
  skipped_rows: number
  error_rows: number
  errors: Array<{ row: number; msg: string }>
  created_at: string | null
  finished_at: string | null
}

// ─── Colunas CSV conhecidas → campo interno ───────────────────────────────────

const COL_MAP: Record<string, string> = {
  // Cliente
  'cliente': 'customer_name', 'nome_cliente': 'customer_name',
  'customer': 'customer_name', 'customer_name': 'customer_name', 'nome': 'customer_name',
  'nif': 'customer_nif', 'nif_cliente': 'customer_nif', 'contribuinte': 'customer_nif',
  'email': 'customer_email', 'email_cliente': 'customer_email',
  // Fatura
  'numero': 'invoice_number', 'número': 'invoice_number',
  'n_fatura': 'invoice_number', 'invoice_number': 'invoice_number',
  'nº_fatura': 'invoice_number', 'factura': 'invoice_number',
  // Datas
  'data_emissao': 'issue_date', 'data_emissão': 'issue_date',
  'emissao': 'issue_date', 'emissão': 'issue_date', 'issue_date': 'issue_date',
  'data_vencimento': 'due_date', 'vencimento': 'due_date', 'due_date': 'due_date',
  // Artigo
  'descricao': 'description', 'descrição': 'description', 'description': 'description',
  'servico': 'description', 'serviço': 'description', 'artigo': 'description',
  'quantidade': 'quantity', 'qtd': 'quantity', 'qty': 'quantity', 'quantity': 'quantity',
  'preco': 'unit_price', 'preço': 'unit_price', 'price': 'unit_price',
  'preco_unitario': 'unit_price', 'preço_unitário': 'unit_price', 'unit_price': 'unit_price',
  'desconto': 'discount', 'discount': 'discount', 'desconto_percent': 'discount',
  'iva': 'iva_rate', 'taxa_iva': 'iva_rate', 'iva_rate': 'iva_rate',
  // Pagamento
  'metodo_pagamento': 'payment_method', 'payment_method': 'payment_method',
  'pagamento': 'payment_method',
  'notas': 'notes', 'notes': 'notes', 'observacoes': 'notes',
  'moeda': 'currency', 'currency': 'currency',
}

function normalizeKey(k: string): string {
  return k.toLowerCase()
    .normalize('NFD').replace(/[\u0300-\u036f]/g, '')  // remover acentos
    .replace(/[\s\-\.]+/g, '_')
    .replace(/[^a-z0-9_]/g, '')
    .trim()
}

// ─── Parser CSV ───────────────────────────────────────────────────────────────

function parseCSV(text: string): { headers: string[]; rows: Record<string, string>[] } {
  const lines = text.replace(/\r\n/g, '\n').replace(/\r/g, '\n').split('\n').filter(l => l.trim())
  if (lines.length < 2) return { headers: [], rows: [] }

  // Detectar separador
  const sep = lines[0].includes(';') ? ';' : lines[0].includes('\t') ? '\t' : ','

  const parseLine = (line: string): string[] => {
    const result: string[] = []
    let cur = ''
    let inQ = false
    for (let i = 0; i < line.length; i++) {
      const ch = line[i]
      if (ch === '"') {
        if (inQ && line[i + 1] === '"') { cur += '"'; i++ }
        else inQ = !inQ
      } else if (ch === sep && !inQ) {
        result.push(cur.trim()); cur = ''
      } else {
        cur += ch
      }
    }
    result.push(cur.trim())
    return result
  }

  const headers = parseLine(lines[0]).map(h => h.replace(/^["']|["']$/g, '').trim())
  const rows: Record<string, string>[] = []

  for (let i = 1; i < lines.length; i++) {
    const vals = parseLine(lines[i])
    if (vals.every(v => !v.trim())) continue
    const row: Record<string, string> = {}
    headers.forEach((h, j) => { row[h] = (vals[j] ?? '').replace(/^["']|["']$/g, '').trim() })
    rows.push(row)
  }
  return { headers, rows }
}

// ─── Mapear linha raw → ImportRow ────────────────────────────────────────────

function mapRow(raw: Record<string, string>, idx: number): ImportRow {
  const mapped: Record<string, string> = {}
  for (const [k, v] of Object.entries(raw)) {
    const norm = normalizeKey(k)
    const field = COL_MAP[norm]
    if (field) mapped[field] = v
  }

  const errors: string[] = []
  const warnings: string[] = []

  const customer_name  = mapped.customer_name  || ''
  const customer_nif   = mapped.customer_nif   || ''
  const customer_email = mapped.customer_email || ''
  const description    = mapped.description    || ''
  const issue_date     = normalizeDate(mapped.issue_date  || '')
  const due_date       = normalizeDate(mapped.due_date    || '')
  const quantity       = parseFloat(mapped.quantity    || '1')  || 1
  const unit_price     = parseFloat(mapped.unit_price  || '0')
  const discount       = parseFloat(mapped.discount    || '0')  || 0
  const iva_rate       = resolveIvaRate(mapped.iva_rate || '')
  const payment_method = mapped.payment_method || undefined
  const notes          = mapped.notes          || undefined
  const currency       = mapped.currency       || 'AOA'
  const invoice_number = mapped.invoice_number || undefined

  // Validações obrigatórias
  if (!customer_name.trim()) errors.push('Nome do cliente em falta')
  if (!description.trim())   errors.push('Descrição em falta')
  if (unit_price <= 0)       errors.push('Preço unitário inválido ou zero')
  if (!issue_date)           errors.push('Data de emissão inválida')
  if (!due_date)             errors.push('Data de vencimento inválida')

  // Avisos opcionais
  if (!customer_nif) warnings.push('NIF em falta')
  if (discount > 50) warnings.push(`Desconto elevado: ${discount}%`)

  const status = errors.length > 0 ? 'error' : warnings.length > 0 ? 'warning' : 'valid'

  return {
    rowIndex: idx + 1,
    raw,
    customer_name,
    customer_nif,
    customer_email,
    description,
    issue_date,
    due_date,
    quantity,
    unit_price,
    discount,
    iva_rate,
    payment_method,
    notes,
    currency,
    invoice_number,
    errors,
    warnings,
    status,
  }
}

function normalizeDate(s: string): string {
  if (!s) return ''
  // DD/MM/YYYY ou DD-MM-YYYY → YYYY-MM-DD
  const m = s.match(/^(\d{1,2})[\/\-\.](\d{1,2})[\/\-\.](\d{4})$/)
  if (m) return `${m[3]}-${m[2].padStart(2,'0')}-${m[1].padStart(2,'0')}`
  // YYYY-MM-DD
  if (/^\d{4}-\d{2}-\d{2}$/.test(s)) return s
  // Tentar Date.parse
  const d = new Date(s)
  if (!isNaN(d.getTime())) return d.toISOString().split('T')[0]
  return ''
}

function resolveIvaRate(raw: string): IvaRate {
  const r = raw.toLowerCase().trim()
  if (['0', '0%', 'isento', 'exempt', 'zero'].includes(r)) return 'exempt'
  if (['5', '5%', 'reduced', 'reduzida', 'reducida'].includes(r)) return 'reduced'
  return 'normal'
}

// ─── Helper Supabase ──────────────────────────────────────────────────────────

async function getTenantAndUser(): Promise<{ tenantId: string | null; userId: string | null }> {
  const { data: { user } } = await supabase.auth.getUser()
  if (!user) return { tenantId: null, userId: null }
  const { data } = await supabase.from('users').select('tenant_id').eq('id', user.id).maybeSingle()
  return { tenantId: data?.tenant_id ?? null, userId: user.id }
}

// ─── Formatar tamanho ─────────────────────────────────────────────────────────

function fmtSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1048576) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1048576).toFixed(1)} MB`
}

function fmtDate(s: string | null | undefined): string {
  if (!s) return '—'
  return new Date(s).toLocaleString('pt-AO', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' })
}

// ─── Template CSV ────────────────────────────────────────────────────────────

const CSV_TEMPLATE = `cliente;nif;email;data_emissao;data_vencimento;descricao;quantidade;preco;desconto;iva;metodo_pagamento;moeda;notas
Empresa ABC, Lda;5000000001LA001;financeiro@abc.ao;2026-04-01;2026-05-01;Consultoria de Gestão;1;150000;0;normal;TRANSFER;AOA;Pagamento 30 dias
Cliente Individual;5000000002LA002;;2026-04-05;2026-05-05;Serviços de TI;2;75000;5;normal;CASH;AOA;
Petro Angola SA;5000000003LA003;contas@petro.ao;2026-04-10;2026-05-10;Licença Software;1;500000;0;exempt;TRANSFER;AOA;Renovação anual`

const JSON_TEMPLATE = JSON.stringify([
  {
    customer_name: "Empresa ABC, Lda",
    customer_nif: "5000000001LA001",
    customer_email: "financeiro@abc.ao",
    issue_date: "2026-04-01",
    due_date: "2026-05-01",
    description: "Consultoria de Gestão",
    quantity: 1,
    unit_price: 150000,
    discount: 0,
    iva_rate: "normal",
    payment_method: "TRANSFER",
    currency: "AOA",
    notes: "Pagamento 30 dias"
  }
], null, 2)

// ═══════════════════════════════════════════════════════════════════════════════
// COMPONENTE PRINCIPAL
// ═══════════════════════════════════════════════════════════════════════════════

interface InvoiceImportProps {
  customers:   Customer[]
  onImportDone: () => void
}

export function InvoiceImport({ customers, onImportDone }: InvoiceImportProps) {
  // Estado geral
  const [tab, setTab] = useState<'upload' | 'history'>('upload')
  const [dragOver, setDragOver] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)

  // Ficheiro seleccionado
  const [file, setFile]         = useState<File | null>(null)
  const [fileType, setFileType]  = useState<'CSV' | 'JSON'>('CSV')

  // Preview
  const [rows, setRows]          = useState<ImportRow[]>([])
  const [headers, setHeaders]    = useState<string[]>([])
  const [previewExpanded, setPreviewExpanded] = useState(false)
  const [columnMapping, setColumnMapping]     = useState<Record<string, string>>({})
  const [showColumnMapper, setShowColumnMapper] = useState(false)

  // Import
  const [importing, setImporting]   = useState(false)
  const [progress, setProgress]     = useState(0)
  const [progressMsg, setProgressMsg] = useState('')
  const [importResult, setImportResult] = useState<{
    success: number; errors: number; skipped: number; jobId: string
  } | null>(null)

  // Histórico
  const [history, setHistory]     = useState<ImportJob[]>([])
  const [loadingHistory, setLoadingHistory] = useState(false)
  const [expandedJob, setExpandedJob]       = useState<string | null>(null)

  // Template dialog
  const [showTemplate, setShowTemplate] = useState(false)

  // ─── Carregar histórico ──────────────────────────────────────────────────

  const loadHistory = useCallback(async () => {
    setLoadingHistory(true)
    try {
      const { tenantId } = await getTenantAndUser()
      if (!tenantId) return
      const { data, error } = await supabase
        .from('invoice_imports')
        .select('id,file_name,file_type,file_size,status,total_rows,imported_rows,skipped_rows,error_rows,errors,created_at,finished_at')
        .eq('tenant_id', tenantId)
        .order('created_at', { ascending: false })
        .limit(20)
      if (error) { console.warn('history:', error); return }
      setHistory((data ?? []) as ImportJob[])
    } finally {
      setLoadingHistory(false)
    }
  }, [])

  useEffect(() => { loadHistory() }, [loadHistory])

  // ─── Processar ficheiro ──────────────────────────────────────────────────

  const processFile = useCallback(async (f: File) => {
    setFile(f)
    setImportResult(null)
    setRows([])
    setHeaders([])

    const ext = f.name.split('.').pop()?.toLowerCase()
    const type: 'CSV' | 'JSON' = (ext === 'json') ? 'JSON' : 'CSV'
    setFileType(type)

    const text = await f.text()

    if (type === 'CSV') {
      const { headers: h, rows: rawRows } = parseCSV(text)
      setHeaders(h)

      // Auto-mapear
      const autoMap: Record<string, string> = {}
      for (const h_ of h) {
        const norm = normalizeKey(h_)
        const field = COL_MAP[norm]
        if (field) autoMap[h_] = field
      }
      setColumnMapping(autoMap)

      // Verificar se precisa de mapeamento manual
      const mapped = Object.values(autoMap)
      const needsMapper = !mapped.includes('customer_name') || !mapped.includes('description')
      setShowColumnMapper(needsMapper)

      const parsed = rawRows.map((r, i) => mapRow(r, i))
      setRows(parsed)
    } else {
      // JSON
      try {
        const json = JSON.parse(text)
        const arr: Record<string, string>[] = Array.isArray(json) ? json : [json]
        setHeaders(arr.length > 0 ? Object.keys(arr[0]) : [])
        const parsed = arr.map((r, i) => mapRow(
          Object.fromEntries(Object.entries(r).map(([k, v]) => [k, String(v ?? '')])),
          i
        ))
        setRows(parsed)
        setShowColumnMapper(false)
      } catch {
        toast.error('JSON inválido. Verifique a estrutura do ficheiro.')
        setFile(null)
      }
    }
  }, [])

  // ─── Drag & Drop ─────────────────────────────────────────────────────────

  const onDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    setDragOver(false)
    const f = e.dataTransfer.files[0]
    if (f) processFile(f)
  }, [processFile])

  const onFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0]
    if (f) processFile(f)
  }

  // ─── Re-mapear com novas colunas ─────────────────────────────────────────

  const remapRows = useCallback((newMapping: Record<string, string>) => {
    if (!file) return
    const mapped = rows.map(r => {
      const remapped: Record<string, string> = {}
      for (const [h, v] of Object.entries(r.raw)) {
        const field = newMapping[h]
        if (field) remapped[field] = v
      }
      return mapRow(remapped, r.rowIndex - 1)
    })
    setRows(mapped)
  }, [file, rows])

  // ─── Executar importação ─────────────────────────────────────────────────

  const handleImport = async () => {
    const validRows = rows.filter(r => r.status !== 'error' && r.status !== 'skipped')
    if (validRows.length === 0) {
      toast.error('Nenhuma linha válida para importar')
      return
    }

    setImporting(true)
    setProgress(0)
    setProgressMsg('A preparar importação...')

    const { tenantId, userId } = await getTenantAndUser()
    if (!tenantId) { toast.error('Não autenticado'); setImporting(false); return }

    // 1. Criar job de importação
    const { data: job, error: jobErr } = await supabase
      .from('invoice_imports')
      .insert({
        tenant_id:   tenantId,
        created_by:  userId,
        file_name:   file?.name ?? 'import',
        file_type:   fileType,
        file_size:   file?.size ?? null,
        status:      'PROCESSING',
        total_rows:  rows.length,
        started_at:  new Date().toISOString(),
      })
      .select('id')
      .single()

    if (jobErr || !job) {
      toast.error('Erro ao criar job de importação')
      setImporting(false)
      return
    }

    // 2. Upload do ficheiro para Supabase Storage
    if (file) {
      const path = `${tenantId}/${job.id}_${file.name}`
      const { error: upErr } = await supabase.storage
        .from('imports')
        .upload(path, file, { upsert: true })
      if (!upErr) {
        await supabase
          .from('invoice_imports')
          .update({ storage_path: path })
          .eq('id', job.id)
      }
    }

    // 3. Processar cada linha
    let successCount = 0
    let errorCount   = 0
    const errDetails: Array<{ row: number; msg: string }> = []

    // Carregar clientes existentes para lookup por nome/NIF
    const allCustomers = await customerService.getAll()
    const customerByNif  = new Map<string, Customer>()
    const customerByName = new Map<string, Customer>()
    for (const c of allCustomers) {
      if (c.nif)  customerByNif.set(c.nif.toUpperCase(), c)
      customerByName.set(c.name.toLowerCase(), c)
    }

    for (let i = 0; i < validRows.length; i++) {
      const row = validRows[i]
      const pct = Math.round(((i + 1) / validRows.length) * 100)
      setProgress(pct)
      setProgressMsg(`A importar linha ${i + 1} de ${validRows.length}...`)

      try {
        // Encontrar ou criar cliente
        let customer: Customer | undefined

        if (row.customer_nif) {
          customer = customerByNif.get(row.customer_nif.toUpperCase())
        }
        if (!customer && row.customer_name) {
          customer = customerByName.get(row.customer_name.toLowerCase())
        }

        if (!customer && row.customer_name) {
          // Criar cliente novo automaticamente
          customer = await customerService.create({
            name:          row.customer_name,
            nif:           row.customer_nif,
            email:         row.customer_email,
            country:       'AO',
            customer_type: 'BUSINESS',
            payment_terms: 30,
            is_active:     true,
          })
          customerByName.set(customer.name.toLowerCase(), customer)
          if (customer.nif) customerByNif.set(customer.nif.toUpperCase(), customer)
        }

        if (!customer) throw new Error('Não foi possível criar/encontrar o cliente')

        // Calcular item
        const ivaPercent = IVA_RATES[row.iva_rate ?? 'normal']
        const item: InvoiceItem = {
          description:      row.description ?? '—',
          quantity:         row.quantity     ?? 1,
          unit_price:       row.unit_price   ?? 0,
          discount_percent: row.discount     ?? 0,
          iva_rate:         row.iva_rate     ?? 'normal',
          iva_percent:      ivaPercent,
          ...calcItem({
            quantity:         row.quantity     ?? 1,
            unit_price:       row.unit_price   ?? 0,
            discount_percent: row.discount     ?? 0,
            iva_percent:      ivaPercent,
          }),
        }

        // Criar fatura
        await invoiceService.create(
          {
            customer_id:      customer.id,
            customer_name:    customer.name,
            customer_nif:     customer.nif,
            customer_address: customer.address,
            issue_date:       row.issue_date  ?? new Date().toISOString().split('T')[0],
            due_date:         row.due_date    ?? new Date(Date.now() + 30 * 86400000).toISOString().split('T')[0],
            series:           'FT',
            currency:         row.currency    ?? 'AOA',
            payment_method:   row.payment_method,
            notes:            row.notes,
          },
          [item],
        )

        successCount++

      } catch (err: unknown) {
        errorCount++
        errDetails.push({
          row: row.rowIndex,
          msg: err instanceof Error ? err.message : 'Erro desconhecido',
        })
      }
    }

    // 4. Fechar job
    await supabase
      .from('invoice_imports')
      .update({
        status:        errorCount === validRows.length ? 'ERROR' : 'DONE',
        imported_rows: successCount,
        error_rows:    errorCount,
        skipped_rows:  rows.length - validRows.length,
        errors:        errDetails,
        finished_at:   new Date().toISOString(),
        updated_at:    new Date().toISOString(),
      })
      .eq('id', job.id)

    setImportResult({ success: successCount, errors: errorCount, skipped: rows.length - validRows.length, jobId: job.id })
    setImporting(false)
    setProgress(100)

    if (successCount > 0) {
      toast.success(`✅ ${successCount} fatura(s) importada(s) com sucesso!`)
      onImportDone()
    }
    if (errorCount > 0) {
      toast.warning(`⚠️ ${errorCount} linha(s) com erro durante a importação.`)
    }

    loadHistory()
  }

  // ─── Descarregar template ────────────────────────────────────────────────

  const downloadTemplate = (type: 'CSV' | 'JSON') => {
    const content = type === 'CSV' ? CSV_TEMPLATE : JSON_TEMPLATE
    const mime    = type === 'CSV' ? 'text/csv' : 'application/json'
    const ext     = type === 'CSV' ? 'csv' : 'json'
    const blob    = new Blob(['\uFEFF' + content], { type: `${mime};charset=utf-8;` })
    const url     = URL.createObjectURL(blob)
    const a       = document.createElement('a')
    a.href        = url
    a.download    = `template_importacao_faturas.${ext}`
    a.click()
    URL.revokeObjectURL(url)
  }

  // ─── Stats do preview ────────────────────────────────────────────────────

  const valid    = rows.filter(r => r.status === 'valid').length
  const warnings = rows.filter(r => r.status === 'warning').length
  const errors   = rows.filter(r => r.status === 'error').length
  const total    = rows.length

  // ─── Render ──────────────────────────────────────────────────────────────

  return (
    <div className="space-y-4">
      <Tabs value={tab} onValueChange={v => setTab(v as 'upload' | 'history')}>
        <TabsList>
          <TabsTrigger value="upload">
            <ArrowUpCircle className="h-4 w-4 mr-2" /> Importar Ficheiro
          </TabsTrigger>
          <TabsTrigger value="history" onClick={loadHistory}>
            <History className="h-4 w-4 mr-2" /> Histórico
            {history.length > 0 && (
              <Badge variant="secondary" className="ml-1 text-xs px-1.5">{history.length}</Badge>
            )}
          </TabsTrigger>
        </TabsList>

        {/* ════════════ TAB: UPLOAD ════════════ */}
        <TabsContent value="upload" className="space-y-4 mt-4">

          {/* Instruções + Template */}
          <Card className="border-primary/20 bg-primary/5">
            <CardHeader className="pb-2">
              <CardTitle className="text-sm flex items-center gap-2">
                <BookOpen className="h-4 w-4 text-primary" />
                Como importar faturas
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <ul className="text-sm text-muted-foreground space-y-1.5 list-none">
                <li className="flex items-start gap-2"><span className="text-primary font-bold">1.</span> Descarregue o template CSV ou JSON abaixo</li>
                <li className="flex items-start gap-2"><span className="text-primary font-bold">2.</span> Preencha os dados das suas faturas (uma por linha)</li>
                <li className="flex items-start gap-2"><span className="text-primary font-bold">3.</span> Faça upload do ficheiro preenchido nesta página</li>
                <li className="flex items-start gap-2"><span className="text-primary font-bold">4.</span> Verifique a pré-visualização e confirme a importação</li>
              </ul>
              <div className="flex flex-wrap gap-2 pt-1">
                <Button
                  variant="outline" size="sm"
                  onClick={() => downloadTemplate('CSV')}
                >
                  <FileSpreadsheet className="h-3.5 w-3.5 mr-1.5 text-emerald-600" />
                  Template CSV
                </Button>
                <Button
                  variant="outline" size="sm"
                  onClick={() => downloadTemplate('JSON')}
                >
                  <FileJson className="h-3.5 w-3.5 mr-1.5 text-blue-600" />
                  Template JSON
                </Button>
                <Button
                  variant="ghost" size="sm"
                  onClick={() => setShowTemplate(true)}
                >
                  <Eye className="h-3.5 w-3.5 mr-1.5" />
                  Ver Colunas
                </Button>
              </div>
            </CardContent>
          </Card>

          {/* Zona de Upload */}
          {!file && !importResult && (
            <div
              onDragOver={e => { e.preventDefault(); setDragOver(true) }}
              onDragLeave={() => setDragOver(false)}
              onDrop={onDrop}
              onClick={() => fileRef.current?.click()}
              className={`
                relative border-2 border-dashed rounded-xl p-10 text-center cursor-pointer transition-all
                ${dragOver
                  ? 'border-primary bg-primary/10 scale-[1.01]'
                  : 'border-border hover:border-primary/50 hover:bg-muted/30'
                }
              `}
            >
              <input
                ref={fileRef}
                type="file"
                accept=".csv,.json,.txt,.xls,.xlsx"
                className="hidden"
                onChange={onFileChange}
              />
              <div className="flex flex-col items-center gap-3">
                <div className="p-4 rounded-full bg-primary/10">
                  <Upload className="h-8 w-8 text-primary" />
                </div>
                <div>
                  <p className="font-semibold text-base">
                    {dragOver ? 'Largar para carregar' : 'Arraste o ficheiro aqui'}
                  </p>
                  <p className="text-sm text-muted-foreground mt-1">
                    ou <span className="text-primary font-medium">clique para seleccionar</span>
                  </p>
                  <p className="text-xs text-muted-foreground mt-2">
                    CSV, JSON — máximo 20 MB
                  </p>
                </div>
              </div>
            </div>
          )}

          {/* Ficheiro seleccionado + resumo */}
          {file && !importResult && (
            <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }}>
              <Card>
                <CardHeader className="pb-3">
                  <div className="flex items-start justify-between gap-3">
                    <div className="flex items-center gap-3">
                      {fileType === 'JSON'
                        ? <FileJson className="h-8 w-8 text-blue-500 flex-shrink-0" />
                        : <FileSpreadsheet className="h-8 w-8 text-emerald-600 flex-shrink-0" />
                      }
                      <div>
                        <CardTitle className="text-sm font-semibold">{file.name}</CardTitle>
                        <CardDescription className="text-xs">
                          {fmtSize(file.size)} · {fileType} · {total} linhas
                        </CardDescription>
                      </div>
                    </div>
                    <Button
                      variant="ghost" size="icon"
                      className="h-7 w-7 text-destructive hover:text-destructive flex-shrink-0"
                      onClick={() => { setFile(null); setRows([]); setImportResult(null); if (fileRef.current) fileRef.current.value = '' }}
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                  </div>
                </CardHeader>

                <CardContent className="space-y-4">
                  {/* Estatísticas do parse */}
                  <div className="grid grid-cols-4 gap-2 text-center">
                    {[
                      { label: 'Total',    val: total,    color: 'text-foreground',  bg: 'bg-muted/50'           },
                      { label: 'Válidas',  val: valid + warnings, color: 'text-emerald-700', bg: 'bg-emerald-50 dark:bg-emerald-950/30' },
                      { label: 'Avisos',   val: warnings, color: 'text-yellow-700',  bg: 'bg-yellow-50 dark:bg-yellow-950/30' },
                      { label: 'Erros',    val: errors,   color: 'text-destructive', bg: 'bg-red-50 dark:bg-red-950/30' },
                    ].map(({ label, val, color, bg }) => (
                      <div key={label} className={`${bg} rounded-lg p-2`}>
                        <p className="text-xs text-muted-foreground">{label}</p>
                        <p className={`text-xl font-bold ${color}`}>{val}</p>
                      </div>
                    ))}
                  </div>

                  {/* Alerta de colunas não mapeadas */}
                  {showColumnMapper && (
                    <Alert variant="destructive">
                      <AlertTriangle className="h-4 w-4" />
                      <AlertTitle>Mapeamento de colunas necessário</AlertTitle>
                      <AlertDescription className="space-y-2">
                        <p className="text-xs">
                          Algumas colunas não foram reconhecidas automaticamente.
                          Mapeie abaixo as colunas do seu ficheiro para os campos do sistema:
                        </p>
                        <ColumnMapper
                          headers={headers}
                          mapping={columnMapping}
                          onChange={m => { setColumnMapping(m); remapRows(m) }}
                        />
                      </AlertDescription>
                    </Alert>
                  )}

                  {/* Erros críticos */}
                  {errors > 0 && (
                    <Alert variant="destructive">
                      <AlertTriangle className="h-4 w-4" />
                      <AlertTitle>{errors} linha(s) com erros — serão ignoradas</AlertTitle>
                      <AlertDescription className="text-xs mt-1 space-y-0.5 max-h-24 overflow-y-auto">
                        {rows.filter(r => r.status === 'error').map(r => (
                          <div key={r.rowIndex}>
                            Linha {r.rowIndex}: {r.errors.join('; ')}
                          </div>
                        ))}
                      </AlertDescription>
                    </Alert>
                  )}

                  {/* Preview das linhas */}
                  <div>
                    <button
                      onClick={() => setPreviewExpanded(!previewExpanded)}
                      className="flex items-center gap-1.5 text-sm font-medium text-primary hover:underline"
                    >
                      <Eye className="h-4 w-4" />
                      {previewExpanded ? 'Ocultar' : 'Ver'} pré-visualização ({total} linhas)
                      {previewExpanded ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
                    </button>

                    <AnimatePresence>
                      {previewExpanded && (
                        <motion.div
                          initial={{ opacity: 0, height: 0 }}
                          animate={{ opacity: 1, height: 'auto' }}
                          exit={{ opacity: 0, height: 0 }}
                          className="overflow-hidden"
                        >
                          <div className="mt-3 max-h-72 overflow-y-auto rounded-lg border text-xs">
                            <table className="w-full border-collapse">
                              <thead className="sticky top-0 bg-muted z-10">
                                <tr>
                                  {['#', 'Cliente', 'NIF', 'Emissão', 'Vencimento', 'Descrição', 'Qtd', 'Preço', 'IVA', 'Estado'].map(h => (
                                    <th key={h} className="text-left px-2 py-2 font-semibold text-muted-foreground whitespace-nowrap">{h}</th>
                                  ))}
                                </tr>
                              </thead>
                              <tbody>
                                {rows.map(r => (
                                  <tr
                                    key={r.rowIndex}
                                    className={`border-t ${
                                      r.status === 'error'   ? 'bg-red-50 dark:bg-red-950/20' :
                                      r.status === 'warning' ? 'bg-yellow-50 dark:bg-yellow-950/20' :
                                      'hover:bg-muted/30'
                                    }`}
                                  >
                                    <td className="px-2 py-1.5 text-muted-foreground">{r.rowIndex}</td>
                                    <td className="px-2 py-1.5 max-w-[120px] truncate">{r.customer_name || '—'}</td>
                                    <td className="px-2 py-1.5 font-mono text-xs text-muted-foreground">{r.customer_nif || '—'}</td>
                                    <td className="px-2 py-1.5 whitespace-nowrap">{r.issue_date || '—'}</td>
                                    <td className="px-2 py-1.5 whitespace-nowrap">{r.due_date || '—'}</td>
                                    <td className="px-2 py-1.5 max-w-[150px] truncate">{r.description || '—'}</td>
                                    <td className="px-2 py-1.5 text-right">{r.quantity ?? 1}</td>
                                    <td className="px-2 py-1.5 text-right whitespace-nowrap">
                                      {r.unit_price?.toLocaleString('pt-AO') ?? '—'}
                                    </td>
                                    <td className="px-2 py-1.5 text-right">
                                      {r.iva_rate === 'exempt' ? '0%' : r.iva_rate === 'reduced' ? '5%' : '14%'}
                                    </td>
                                    <td className="px-2 py-1.5">
                                      {r.status === 'valid'   && <Badge className="text-xs bg-emerald-100 text-emerald-800 py-0">OK</Badge>}
                                      {r.status === 'warning' && (
                                        <span title={r.warnings.join('; ')}>
                                          <Badge className="text-xs bg-yellow-100 text-yellow-800 py-0">Aviso</Badge>
                                        </span>
                                      )}
                                      {r.status === 'error'   && (
                                        <span title={r.errors.join('; ')}>
                                          <Badge variant="destructive" className="text-xs py-0">Erro</Badge>
                                        </span>
                                      )}
                                    </td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </div>
                        </motion.div>
                      )}
                    </AnimatePresence>
                  </div>

                  {/* Progresso da importação */}
                  {importing && (
                    <div className="space-y-2">
                      <div className="flex items-center justify-between text-xs text-muted-foreground">
                        <span>{progressMsg}</span>
                        <span>{progress}%</span>
                      </div>
                      <Progress value={progress} className="h-2" />
                    </div>
                  )}

                  {/* Botões */}
                  <div className="flex flex-wrap gap-2 pt-1">
                    <Button
                      variant="outline" size="sm"
                      onClick={() => { setFile(null); setRows([]); if (fileRef.current) fileRef.current.value = '' }}
                      disabled={importing}
                    >
                      Cancelar
                    </Button>
                    <Button
                      size="sm"
                      onClick={handleImport}
                      disabled={importing || (valid + warnings) === 0}
                      className="flex-1 sm:flex-none"
                    >
                      {importing
                        ? <><RefreshCw className="h-4 w-4 mr-2 animate-spin" />A importar…</>
                        : <><ArrowUpCircle className="h-4 w-4 mr-2" />Importar {valid + warnings} fatura(s)</>
                      }
                    </Button>
                  </div>

                  {errors > 0 && (
                    <p className="text-xs text-muted-foreground flex items-center gap-1">
                      <Info className="h-3.5 w-3.5 flex-shrink-0" />
                      As {errors} linhas com erros serão ignoradas. As restantes {valid + warnings} serão importadas.
                    </p>
                  )}
                </CardContent>
              </Card>
            </motion.div>
          )}

          {/* Resultado da importação */}
          {importResult && (
            <motion.div initial={{ opacity: 0, scale: 0.97 }} animate={{ opacity: 1, scale: 1 }}>
              <Card className={`border-2 ${importResult.success > 0 ? 'border-emerald-400 bg-emerald-50 dark:bg-emerald-950/20' : 'border-destructive/40 bg-red-50 dark:bg-red-950/20'}`}>
                <CardHeader>
                  <CardTitle className="flex items-center gap-2">
                    {importResult.success > 0
                      ? <CheckCircle2 className="h-5 w-5 text-emerald-600" />
                      : <XCircle className="h-5 w-5 text-destructive" />
                    }
                    Importação {importResult.errors === 0 ? 'Concluída' : 'Concluída com Erros'}
                  </CardTitle>
                </CardHeader>
                <CardContent className="space-y-3">
                  <div className="grid grid-cols-3 gap-3 text-center">
                    <div className="p-3 bg-emerald-100 dark:bg-emerald-900/30 rounded-lg">
                      <p className="text-2xl font-bold text-emerald-700">{importResult.success}</p>
                      <p className="text-xs text-muted-foreground">Importadas</p>
                    </div>
                    <div className="p-3 bg-yellow-100 dark:bg-yellow-900/30 rounded-lg">
                      <p className="text-2xl font-bold text-yellow-700">{importResult.skipped}</p>
                      <p className="text-xs text-muted-foreground">Ignoradas</p>
                    </div>
                    <div className="p-3 bg-red-100 dark:bg-red-900/30 rounded-lg">
                      <p className="text-2xl font-bold text-destructive">{importResult.errors}</p>
                      <p className="text-xs text-muted-foreground">Com Erro</p>
                    </div>
                  </div>
                  <div className="flex gap-2 flex-wrap">
                    <Button size="sm" variant="outline" onClick={() => {
                      setImportResult(null); setFile(null); setRows([])
                      if (fileRef.current) fileRef.current.value = ''
                    }}>
                      <Upload className="h-3.5 w-3.5 mr-1.5" /> Nova Importação
                    </Button>
                    <Button size="sm" variant="outline" onClick={() => { setTab('history'); loadHistory() }}>
                      <History className="h-3.5 w-3.5 mr-1.5" /> Ver Histórico
                    </Button>
                  </div>
                </CardContent>
              </Card>
            </motion.div>
          )}
        </TabsContent>

        {/* ════════════ TAB: HISTÓRICO ════════════ */}
        <TabsContent value="history" className="space-y-3 mt-4">
          <div className="flex items-center justify-between">
            <div>
              <h3 className="font-semibold text-sm">Histórico de Importações</h3>
              <p className="text-xs text-muted-foreground">Tabela <code>invoice_imports</code> · últimas 20</p>
            </div>
            <Button variant="outline" size="sm" onClick={loadHistory} disabled={loadingHistory}>
              <RefreshCw className={`h-3.5 w-3.5 mr-1.5 ${loadingHistory ? 'animate-spin' : ''}`} />
              Actualizar
            </Button>
          </div>

          {loadingHistory && (
            <div className="flex items-center justify-center py-10">
              <RefreshCw className="h-5 w-5 animate-spin text-muted-foreground" />
            </div>
          )}

          {!loadingHistory && history.length === 0 && (
            <div className="text-center py-12 space-y-2 text-muted-foreground">
              <History className="h-12 w-12 mx-auto opacity-20" />
              <p>Nenhuma importação registada</p>
            </div>
          )}

          <div className="space-y-2">
            {history.map(job => (
              <Card
                key={job.id}
                className={`cursor-pointer hover:shadow-sm transition-shadow ${
                  job.status === 'DONE'       ? 'border-emerald-200 dark:border-emerald-800/40' :
                  job.status === 'ERROR'      ? 'border-destructive/30' :
                  job.status === 'PROCESSING' ? 'border-yellow-300 dark:border-yellow-700/40 animate-pulse' : ''
                }`}
                onClick={() => setExpandedJob(expandedJob === job.id ? null : job.id)}
              >
                <CardContent className="p-4">
                  <div className="flex items-center justify-between gap-3">
                    <div className="flex items-center gap-3 flex-1 min-w-0">
                      {job.file_type === 'JSON'
                        ? <FileJson className="h-5 w-5 text-blue-500 flex-shrink-0" />
                        : <FileSpreadsheet className="h-5 w-5 text-emerald-600 flex-shrink-0" />
                      }
                      <div className="min-w-0">
                        <p className="font-medium text-sm truncate">{job.file_name}</p>
                        <p className="text-xs text-muted-foreground">
                          {fmtDate(job.created_at)}
                          {job.file_size ? ` · ${fmtSize(job.file_size)}` : ''}
                        </p>
                      </div>
                    </div>
                    <div className="flex items-center gap-2 flex-shrink-0">
                      <div className="text-right hidden sm:block">
                        <p className="text-xs text-muted-foreground">Total / Importadas</p>
                        <p className="text-sm font-semibold">
                          {job.total_rows} / <span className="text-emerald-600">{job.imported_rows}</span>
                        </p>
                      </div>
                      <StatusJobBadge status={job.status} />
                      {expandedJob === job.id
                        ? <ChevronUp className="h-4 w-4 text-muted-foreground" />
                        : <ChevronDown className="h-4 w-4 text-muted-foreground" />
                      }
                    </div>
                  </div>

                  <AnimatePresence>
                    {expandedJob === job.id && (
                      <motion.div
                        initial={{ opacity: 0, height: 0 }}
                        animate={{ opacity: 1, height: 'auto' }}
                        exit={{ opacity: 0, height: 0 }}
                        className="overflow-hidden"
                      >
                        <Separator className="my-3" />
                        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-center text-xs">
                          {[
                            { label: 'Total', val: job.total_rows,    color: 'text-foreground' },
                            { label: 'Importadas', val: job.imported_rows, color: 'text-emerald-600' },
                            { label: 'Ignoradas',  val: job.skipped_rows,  color: 'text-yellow-600' },
                            { label: 'Erros',      val: job.error_rows,    color: 'text-destructive' },
                          ].map(({ label, val, color }) => (
                            <div key={label} className="p-2 bg-muted/40 rounded">
                              <p className="text-muted-foreground">{label}</p>
                              <p className={`text-lg font-bold ${color}`}>{val}</p>
                            </div>
                          ))}
                        </div>

                        {job.errors && job.errors.length > 0 && (
                          <div className="mt-3 p-3 bg-red-50 dark:bg-red-950/20 rounded-lg">
                            <p className="text-xs font-semibold text-destructive mb-1.5">Detalhes dos erros:</p>
                            <div className="space-y-0.5 max-h-24 overflow-y-auto text-xs text-muted-foreground">
                              {job.errors.map((e, i) => (
                                <p key={i}>Linha {e.row}: {e.msg}</p>
                              ))}
                            </div>
                          </div>
                        )}

                        {job.finished_at && (
                          <p className="text-xs text-muted-foreground mt-2">
                            <Clock className="h-3 w-3 inline mr-1" />
                            Concluído em {fmtDate(job.finished_at)}
                          </p>
                        )}
                      </motion.div>
                    )}
                  </AnimatePresence>
                </CardContent>
              </Card>
            ))}
          </div>
        </TabsContent>
      </Tabs>

      {/* ══ Modal: Ver Colunas Suportadas ══ */}
      <Dialog open={showTemplate} onOpenChange={setShowTemplate}>
        <DialogContent className="max-w-2xl max-h-[80vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>Colunas Suportadas na Importação</DialogTitle>
            <DialogDescription>
              Use estes nomes de colunas no seu CSV ou JSON. O sistema reconhece variações em PT e EN.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3 text-sm">
            {[
              {
                group: '👤 Cliente', cols: [
                  { field: 'cliente / customer_name / nome', required: true,  desc: 'Nome do cliente' },
                  { field: 'nif / contribuinte',             required: false, desc: 'Nº identificação fiscal' },
                  { field: 'email / email_cliente',          required: false, desc: 'Email do cliente' },
                ]
              },
              {
                group: '🧾 Fatura', cols: [
                  { field: 'data_emissao / issue_date',       required: true,  desc: 'Data de emissão (DD/MM/AAAA ou AAAA-MM-DD)' },
                  { field: 'data_vencimento / due_date',      required: true,  desc: 'Data de vencimento' },
                  { field: 'numero / invoice_number',         required: false, desc: 'Nº da fatura (gerado automaticamente se omitido)' },
                  { field: 'moeda / currency',                required: false, desc: 'AOA, USD, EUR (padrão: AOA)' },
                ]
              },
              {
                group: '📦 Linha de Artigo', cols: [
                  { field: 'descricao / description / artigo', required: true,  desc: 'Descrição do produto ou serviço' },
                  { field: 'quantidade / qtd / quantity',      required: false, desc: 'Quantidade (padrão: 1)' },
                  { field: 'preco / unit_price',               required: true,  desc: 'Preço unitário em AOA' },
                  { field: 'desconto / discount',              required: false, desc: 'Percentagem de desconto (0-100)' },
                  { field: 'iva / taxa_iva / iva_rate',        required: false, desc: 'normal (14%), reduced (5%), exempt (0%)' },
                ]
              },
              {
                group: '💳 Pagamento & Notas', cols: [
                  { field: 'metodo_pagamento / payment_method', required: false, desc: 'TRANSFER, CASH, CHEQUE, CARD, MOBILE' },
                  { field: 'notas / notes / observacoes',        required: false, desc: 'Observações visíveis na fatura' },
                ]
              },
            ].map(({ group, cols }) => (
              <div key={group}>
                <p className="font-semibold mb-1.5">{group}</p>
                <div className="rounded-lg border overflow-hidden">
                  <table className="w-full text-xs">
                    <thead className="bg-muted">
                      <tr>
                        <th className="text-left px-3 py-2 font-semibold text-muted-foreground">Coluna(s)</th>
                        <th className="px-3 py-2 font-semibold text-muted-foreground">Obrigatório</th>
                        <th className="text-left px-3 py-2 font-semibold text-muted-foreground">Descrição</th>
                      </tr>
                    </thead>
                    <tbody>
                      {cols.map((c, i) => (
                        <tr key={i} className="border-t">
                          <td className="px-3 py-2 font-mono text-xs">{c.field}</td>
                          <td className="px-3 py-2 text-center">
                            {c.required
                              ? <Badge variant="destructive" className="text-xs py-0">Sim</Badge>
                              : <Badge variant="secondary" className="text-xs py-0">Não</Badge>
                            }
                          </td>
                          <td className="px-3 py-2 text-muted-foreground">{c.desc}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            ))}
            <div className="flex gap-2 pt-2">
              <Button size="sm" variant="outline" onClick={() => downloadTemplate('CSV')}>
                <Download className="h-3.5 w-3.5 mr-1.5" /> Descarregar Template CSV
              </Button>
              <Button size="sm" variant="outline" onClick={() => downloadTemplate('JSON')}>
                <Download className="h-3.5 w-3.5 mr-1.5" /> Descarregar Template JSON
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  )
}

// ─── Sub-componente: Mapeador de Colunas ──────────────────────────────────────

const FIELD_OPTIONS = [
  { value: 'customer_name',  label: 'Nome do Cliente *' },
  { value: 'customer_nif',   label: 'NIF do Cliente' },
  { value: 'customer_email', label: 'Email do Cliente' },
  { value: 'invoice_number', label: 'Nº Fatura' },
  { value: 'issue_date',     label: 'Data de Emissão *' },
  { value: 'due_date',       label: 'Data de Vencimento *' },
  { value: 'description',    label: 'Descrição *' },
  { value: 'quantity',       label: 'Quantidade' },
  { value: 'unit_price',     label: 'Preço Unitário *' },
  { value: 'discount',       label: 'Desconto %' },
  { value: 'iva_rate',       label: 'Taxa IVA' },
  { value: 'payment_method', label: 'Método Pagamento' },
  { value: 'currency',       label: 'Moeda' },
  { value: 'notes',          label: 'Notas' },
]

function ColumnMapper({
  headers,
  mapping,
  onChange,
}: {
  headers: string[]
  mapping: Record<string, string>
  onChange: (m: Record<string, string>) => void
}) {
  return (
    <div className="mt-2 space-y-2 max-h-48 overflow-y-auto pr-1">
      {headers.map(h => (
        <div key={h} className="flex items-center gap-2 text-xs">
          <span className="font-mono text-primary min-w-[100px] truncate" title={h}>{h}</span>
          <span className="text-muted-foreground">→</span>
          <Select
            value={mapping[h] || 'ignore'}
            onValueChange={v => onChange({ ...mapping, [h]: v === 'ignore' ? '' : v })}
          >
            <SelectTrigger className="h-7 text-xs flex-1">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="ignore"><span className="text-muted-foreground">Ignorar coluna</span></SelectItem>
              {FIELD_OPTIONS.map(o => (
                <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      ))}
    </div>
  )
}

// ─── Badge de estado do job ───────────────────────────────────────────────────

function StatusJobBadge({ status }: { status: string }) {
  if (status === 'DONE')       return <Badge className="bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-400 text-xs"><CheckCircle2 className="h-3 w-3 mr-1" />Concluído</Badge>
  if (status === 'ERROR')      return <Badge variant="destructive" className="text-xs"><XCircle className="h-3 w-3 mr-1" />Erro</Badge>
  if (status === 'PROCESSING') return <Badge className="bg-yellow-100 text-yellow-800 dark:bg-yellow-900/40 text-xs"><RefreshCw className="h-3 w-3 mr-1 animate-spin" />Em progresso</Badge>
  return <Badge variant="secondary" className="text-xs"><Clock className="h-3 w-3 mr-1" />Pendente</Badge>
}

export default InvoiceImport
