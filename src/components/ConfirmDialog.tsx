import { useState } from 'react';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { Button } from '@/components/ui/button';
import { AlertTriangle, Trash2, LogOut, XCircle } from 'lucide-react';

interface ConfirmDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description: string;
  confirmLabel?: string;
  cancelLabel?: string;
  variant?: 'default' | 'destructive';
  onConfirm: () => void | Promise<void>;
  loading?: boolean;
}

export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmLabel = 'Confirmar',
  cancelLabel = 'Cancelar',
  variant = 'default',
  onConfirm,
  loading = false,
}: ConfirmDialogProps) {
  const [isLoading, setIsLoading] = useState(false);

  const handleConfirm = async () => {
    setIsLoading(true);
    try {
      await onConfirm();
      onOpenChange(false);
    } catch (error) {
      console.error('Error in confirm action:', error);
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle className="flex items-center gap-2">
            {variant === 'destructive' && (
              <AlertTriangle className="h-5 w-5 text-destructive" />
            )}
            {title}
          </AlertDialogTitle>
          <AlertDialogDescription>{description}</AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={isLoading || loading}>
            {cancelLabel}
          </AlertDialogCancel>
          <AlertDialogAction
            onClick={handleConfirm}
            disabled={isLoading || loading}
            className={variant === 'destructive' ? 'bg-destructive hover:bg-destructive/90' : ''}
          >
            {isLoading || loading ? 'A processar...' : confirmLabel}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

// Hook para usar o ConfirmDialog facilmente
export function useConfirmDialog() {
  const [isOpen, setIsOpen] = useState(false);
  const [config, setConfig] = useState<Omit<ConfirmDialogProps, 'open' | 'onOpenChange'>>({
    title: '',
    description: '',
    onConfirm: () => {},
  });

  const confirm = (newConfig: Omit<ConfirmDialogProps, 'open' | 'onOpenChange'>) => {
    setConfig(newConfig);
    setIsOpen(true);
  };

  const ConfirmDialogComponent = () => (
    <ConfirmDialog {...config} open={isOpen} onOpenChange={setIsOpen} />
  );

  return { confirm, ConfirmDialog: ConfirmDialogComponent };
}

// Componentes pré-configurados para ações comuns
interface DeleteConfirmProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  itemName: string;
  onConfirm: () => void | Promise<void>;
  loading?: boolean;
}

export function DeleteConfirm({
  open,
  onOpenChange,
  itemName,
  onConfirm,
  loading,
}: DeleteConfirmProps) {
  return (
    <ConfirmDialog
      open={open}
      onOpenChange={onOpenChange}
      title="Confirmar Exclusão"
      description={`Tem certeza que deseja excluir "${itemName}"? Esta ação não pode ser desfeita.`}
      confirmLabel="Excluir"
      variant="destructive"
      onConfirm={onConfirm}
      loading={loading}
    />
  );
}

interface LogoutConfirmProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onConfirm: () => void | Promise<void>;
}

export function LogoutConfirm({ open, onOpenChange, onConfirm }: LogoutConfirmProps) {
  return (
    <ConfirmDialog
      open={open}
      onOpenChange={onOpenChange}
      title="Confirmar Logout"
      description="Tem certeza que deseja sair? Você precisará fazer login novamente."
      confirmLabel="Sair"
      onConfirm={onConfirm}
    />
  );
}

interface CancelConfirmProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onConfirm: () => void | Promise<void>;
}

export function CancelConfirm({ open, onOpenChange, onConfirm }: CancelConfirmProps) {
  return (
    <ConfirmDialog
      open={open}
      onOpenChange={onOpenChange}
      title="Descartar Alterações"
      description="Tem certeza que deseja descartar as alterações? Todas as modificações não salvas serão perdidas."
      confirmLabel="Descartar"
      variant="destructive"
      onConfirm={onConfirm}
    />
  );
}
