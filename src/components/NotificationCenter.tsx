// =====================================================
// KWANZACONTROL - Notification Center Component
// Centro de Notificações com auto-refresh
// Data: 2026-04-04
// =====================================================

import { useState, useEffect } from 'react';
import { Bell, Check, X, ExternalLink, CheckCheck } from 'lucide-react';
import { useRealtimeNotifications } from '@/hooks/useRealtimeNotifications';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { notificationService } from '@/services';
import { useAuth } from '@/hooks/useAuth';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { ScrollArea } from '@/components/ui/scroll-area';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';

export function NotificationCenter() {
  const { user } = useAuth();
  
  // Use realtime notifications hook
  const {
    notifications,
    unreadCount,
    markAsRead,
    markAllAsRead,
    deleteNotification,
  } = useRealtimeNotifications();

  const unreadNotifications = notifications?.filter((n: any) => !n.is_read) || [];

  const getPriorityColor = (priority: string) => {
    switch (priority) {
      case 'CRITICAL': return 'destructive';
      case 'HIGH': return 'default';
      case 'MEDIUM': return 'secondary';
      default: return 'outline';
    }
  };

  const getTypeIcon = (type: string) => {
    switch (type) {
      case 'APPROVAL_REQUEST': return '📋';
      case 'APPROVAL_RESPONSE': return '✅';
      case 'BREAKGLASS_ALERT': return '🚨';
      case 'ELEVATED_SESSION': return '🔐';
      case 'SYSTEM_ALERT': return '⚠️';
      default: return 'ℹ️';
    }
  };

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon" className="relative">
          <Bell className="w-5 h-5" />
          {(unreadCount || 0) > 0 && (
            <Badge
              variant="destructive"
              className="absolute -top-1 -right-1 h-5 w-5 flex items-center justify-center p-0 text-xs"
            >
              {unreadCount}
            </Badge>
          )}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-96">
        <div className="flex items-center justify-between p-4 border-b">
          <h3 className="font-bold">Notificações</h3>
          {unreadNotifications.length > 0 && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => markAllAsRead()}
            >
              <Check className="w-4 h-4 mr-2" />
              Marcar todas como lidas
            </Button>
          )}
        </div>

        <ScrollArea className="h-96">
          {notifications && notifications.length > 0 ? (
            <div className="divide-y">
              {notifications.slice(0, 10).map((notification: any) => (
                <div
                  key={notification.id}
                  className={`p-4 hover:bg-muted/50 transition-colors ${
                    !notification.is_read ? 'bg-primary/5' : ''
                  }`}
                >
                  <div className="flex items-start gap-3">
                    <span className="text-2xl">{getTypeIcon(notification.type)}</span>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-start justify-between gap-2 mb-1">
                        <h4 className="font-medium text-sm">{notification.title}</h4>
                        {!notification.is_read && (
                          <Button
                            variant="ghost"
                            size="icon"
                            className="h-6 w-6"
                            onClick={() => markAsRead(notification.id)}
                          >
                            <X className="w-4 h-4" />
                          </Button>
                        )}
                      </div>
                      <p className="text-sm text-muted-foreground mb-2">
                        {notification.message}
                      </p>
                      <div className="flex items-center gap-2">
                        <Badge variant={getPriorityColor(notification.priority)} className="text-xs">
                          {notification.priority}
                        </Badge>
                        <span className="text-xs text-muted-foreground">
                          {new Date(notification.created_at).toLocaleString('pt-AO')}
                        </span>
                      </div>
                      {notification.action_url && (
                        <Button
                          variant="link"
                          size="sm"
                          className="h-auto p-0 mt-2"
                          onClick={() => window.location.href = notification.action_url}
                        >
                          <ExternalLink className="w-3 h-3 mr-1" />
                          Ver detalhes
                        </Button>
                      )}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="p-8 text-center text-muted-foreground">
              <Bell className="w-12 h-12 mx-auto mb-2 opacity-50" />
              <p>Nenhuma notificação</p>
            </div>
          )}
        </ScrollArea>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

export default NotificationCenter;
