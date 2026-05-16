/**
 * CRMManagement – wrapper legado
 * O CRM completo está agora em src/pages/CRMDashboard.tsx
 * Este componente é mantido para compatibilidade com importações existentes.
 */
import { useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { Loader2 } from 'lucide-react'

export function CRMManagement() {
  const navigate = useNavigate()

  useEffect(() => {
    navigate('/crm', { replace: true })
  }, [navigate])

  return (
    <div className="flex items-center justify-center h-96 gap-3 text-muted-foreground">
      <Loader2 className="h-6 w-6 animate-spin" />
      <span>A redirecionar para o CRM...</span>
    </div>
  )
}
