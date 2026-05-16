// Empty States - Estados vazios com ilustrações
import { Button } from '@/components/ui/button';
import { 
  FileX, 
  Search, 
  Inbox, 
  Users, 
  FolderOpen,
  AlertCircle,
  CheckCircle,
  XCircle,
  Clock,
  TrendingUp
} from 'lucide-react';

interface EmptyStateProps {
  icon?: React.ComponentType<{ className?: string }>;
  title: string;
  description: string;
  action?: {
    label: string;
    onClick: () => void;
  };
  className?: string;
}

export function EmptyState({ 
  icon: Icon = FileX, 
  title, 
  description, 
  action,
  className 
}: EmptyStateProps) {
  return (
    <div className={`flex flex-col items-center justify-center py-12 px-4 text-center ${className}`}>
      <div className="rounded-full bg-muted p-6 mb-4">
        <Icon className="h-12 w-12 text-muted-foreground" />
      </div>
      <h3 className="text-lg font-semibold mb-2">{title}</h3>
      <p className="text-muted-foreground mb-6 max-w-md">{description}</p>
      {action && (
        <Button onClick={action.onClick}>
          {action.label}
        </Button>
      )}
    </div>
  );
}

// Predefined Empty States
export function NoResultsFound({ onReset }: { onReset?: () => void }) {
  return (
    <EmptyState
      icon={Search}
      title="Nenhum resultado encontrado"
      description="Não encontramos nada com os filtros aplicados. Tente ajustar sua busca."
      action={onReset ? { label: 'Limpar Filtros', onClick: onReset } : undefined}
    />
  );
}

export function NoDataYet({ onCreate }: { onCreate?: () => void }) {
  return (
    <EmptyState
      icon={Inbox}
      title="Nenhum dado ainda"
      description="Você ainda não tem nenhum item. Comece criando o primeiro!"
      action={onCreate ? { label: 'Criar Primeiro Item', onClick: onCreate } : undefined}
    />
  );
}

export function NoUsersFound({ onInvite }: { onInvite?: () => void }) {
  return (
    <EmptyState
      icon={Users}
      title="Nenhum usuário encontrado"
      description="Não há usuários cadastrados ainda. Convide membros para sua equipe."
      action={onInvite ? { label: 'Convidar Usuários', onClick: onInvite } : undefined}
    />
  );
}

export function NoFilesFound({ onUpload }: { onUpload?: () => void }) {
  return (
    <EmptyState
      icon={FolderOpen}
      title="Nenhum arquivo encontrado"
      description="Esta pasta está vazia. Faça upload de arquivos para começar."
      action={onUpload ? { label: 'Fazer Upload', onClick: onUpload } : undefined}
    />
  );
}

export function ErrorState({ onRetry }: { onRetry?: () => void }) {
  return (
    <EmptyState
      icon={AlertCircle}
      title="Algo deu errado"
      description="Não foi possível carregar os dados. Tente novamente."
      action={onRetry ? { label: 'Tentar Novamente', onClick: onRetry } : undefined}
    />
  );
}

export function SuccessState({ message, onContinue }: { message: string; onContinue?: () => void }) {
  return (
    <EmptyState
      icon={CheckCircle}
      title="Sucesso!"
      description={message}
      action={onContinue ? { label: 'Continuar', onClick: onContinue } : undefined}
    />
  );
}

export function PendingState({ message }: { message: string }) {
  return (
    <EmptyState
      icon={Clock}
      title="Aguardando..."
      description={message}
    />
  );
}

export function ComingSoonState() {
  return (
    <EmptyState
      icon={TrendingUp}
      title="Em breve!"
      description="Esta funcionalidade está em desenvolvimento e estará disponível em breve."
    />
  );
}
