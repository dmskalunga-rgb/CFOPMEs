// Keyboard Shortcuts - Atalhos de teclado
import { useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { ROUTE_PATHS } from '@/lib/index';

interface ShortcutConfig {
  key: string;
  ctrl?: boolean;
  shift?: boolean;
  alt?: boolean;
  action: () => void;
  description: string;
}

export function useKeyboardShortcuts() {
  const navigate = useNavigate();

  const shortcuts: ShortcutConfig[] = [
    // Navegação
    {
      key: 'd',
      ctrl: true,
      description: 'Ir para Dashboard',
      action: () => navigate(ROUTE_PATHS.DASHBOARD),
    },
    {
      key: 'f',
      ctrl: true,
      description: 'Ir para Faturação',
      action: () => navigate(ROUTE_PATHS.INVOICING),
    },
    {
      key: 'u',
      ctrl: true,
      description: 'Ir para Utilizadores',
      action: () => navigate(ROUTE_PATHS.USERS),
    },
    {
      key: 's',
      ctrl: true,
      description: 'Ir para Configurações',
      action: () => navigate(ROUTE_PATHS.SETTINGS),
    },
    {
      key: 'p',
      ctrl: true,
      description: 'Ir para Performance',
      action: () => navigate(ROUTE_PATHS.PERFORMANCE_MONITOR),
    },
    // Ações
    {
      key: 'k',
      ctrl: true,
      description: 'Abrir busca rápida',
      action: () => {
        // Trigger search modal
        const event = new CustomEvent('open-search');
        window.dispatchEvent(event);
      },
    },
    {
      key: '/',
      description: 'Focar na busca',
      action: () => {
        const searchInput = document.querySelector('input[type="search"]') as HTMLInputElement;
        searchInput?.focus();
      },
    },
    {
      key: '?',
      shift: true,
      description: 'Mostrar atalhos',
      action: () => {
        const event = new CustomEvent('show-shortcuts');
        window.dispatchEvent(event);
      },
    },
  ];

  const handleKeyDown = useCallback(
    (event: KeyboardEvent) => {
      const shortcut = shortcuts.find(
        (s) =>
          s.key.toLowerCase() === event.key.toLowerCase() &&
          !!s.ctrl === (event.ctrlKey || event.metaKey) &&
          !!s.shift === event.shiftKey &&
          !!s.alt === event.altKey
      );

      if (shortcut) {
        event.preventDefault();
        shortcut.action();
      }
    },
    [shortcuts]
  );

  useEffect(() => {
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [handleKeyDown]);

  return shortcuts;
}

// Shortcuts Help Modal Component
import { useState, useEffect as useEffectReact } from 'react';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Badge } from '@/components/ui/badge';

export function KeyboardShortcutsHelp() {
  const [open, setOpen] = useState(false);
  const shortcuts = useKeyboardShortcuts();

  useEffectReact(() => {
    const handleShow = () => setOpen(true);
    window.addEventListener('show-shortcuts', handleShow);
    return () => window.removeEventListener('show-shortcuts', handleShow);
  }, []);

  const formatShortcut = (shortcut: ShortcutConfig) => {
    const keys = [];
    if (shortcut.ctrl) keys.push('Ctrl');
    if (shortcut.shift) keys.push('Shift');
    if (shortcut.alt) keys.push('Alt');
    keys.push(shortcut.key.toUpperCase());
    return keys;
  };

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>Atalhos de Teclado</DialogTitle>
          <DialogDescription>
            Use estes atalhos para navegar mais rapidamente
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4 mt-4">
          {shortcuts.map((shortcut, index) => (
            <div key={index} className="flex items-center justify-between py-2 border-b">
              <span className="text-sm">{shortcut.description}</span>
              <div className="flex gap-1">
                {formatShortcut(shortcut).map((key, i) => (
                  <Badge key={i} variant="secondary" className="font-mono">
                    {key}
                  </Badge>
                ))}
              </div>
            </div>
          ))}
        </div>
      </DialogContent>
    </Dialog>
  );
}
