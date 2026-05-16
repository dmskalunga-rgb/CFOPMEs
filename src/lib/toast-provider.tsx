// =====================================================
// KWANZACONTROL - Toast Provider
// Provider global para notificações toast
// Data: 2026-04-04
// =====================================================

import React, { createContext, useContext, ReactNode } from 'react';
import { ToastContainer, useToast as useToastHook, Toast } from '@/components/Toast';

interface ToastContextType {
  success: (title: string, description?: string) => void;
  error: (title: string, description?: string) => void;
  warning: (title: string, description?: string) => void;
  info: (title: string, description?: string) => void;
  toasts: Toast[];
  removeToast: (id: string) => void;
}

const ToastContext = createContext<ToastContextType | undefined>(undefined);

export function ToastProvider({ children }: { children: ReactNode }) {
  const toastMethods = useToastHook();

  return (
    <ToastContext.Provider value={toastMethods}>
      {children}
      <ToastContainer toasts={toastMethods.toasts} onClose={toastMethods.removeToast} />
    </ToastContext.Provider>
  );
}

export function useToast() {
  const context = useContext(ToastContext);
  if (!context) {
    throw new Error('useToast must be used within ToastProvider');
  }
  return context;
}
