// CalendarEvents, Notifications, ActivityLog - Últimas 3 páginas
import { useState } from 'react';
import { Layout } from '@/components/Layout';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Calendar, Clock, Users, MapPin } from 'lucide-react';

interface Event {
  id: string;
  title: string;
  type: 'meeting' | 'deadline' | 'reminder' | 'event';
  date: string;
  time: string;
  location: string;
  attendees: number;
}

export default function CalendarEvents() {
  const [events] = useState<Event[]>(
    Array.from({ length: 15 }, (_, i) => ({
      id: `event-${i + 1}`,
      title: `Evento ${i + 1}`,
      type: ['meeting', 'deadline', 'reminder', 'event'][Math.floor(Math.random() * 4)] as Event['type'],
      date: new Date(Date.now() + (i - 5) * 24 * 60 * 60 * 1000).toISOString().split('T')[0],
      time: `${9 + Math.floor(Math.random() * 8)}:00`,
      location: ['Sala A', 'Sala B', 'Online', 'Escritório'][Math.floor(Math.random() * 4)],
      attendees: Math.floor(Math.random() * 10) + 2
    }))
  );

  const today = new Date().toISOString().split('T')[0];
  const todayEvents = events.filter(e => e.date === today);
  const upcomingEvents = events.filter(e => e.date > today);

  return (
    <Layout>
      <div className="space-y-6">
        <div>
          <h1 className="text-3xl font-bold">Calendário e Eventos</h1>
          <p className="text-muted-foreground">Agenda e compromissos</p>
        </div>

        <div className="grid gap-4 md:grid-cols-4">
          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Total</CardTitle>
              <Calendar className="h-4 w-4 text-muted-foreground" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">{events.length}</div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Hoje</CardTitle>
              <Clock className="h-4 w-4 text-blue-600" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold text-blue-600">{todayEvents.length}</div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Próximos</CardTitle>
              <Calendar className="h-4 w-4 text-green-600" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold text-green-600">{upcomingEvents.length}</div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Participantes</CardTitle>
              <Users className="h-4 w-4 text-muted-foreground" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">
                {events.reduce((sum, e) => sum + e.attendees, 0)}
              </div>
            </CardContent>
          </Card>
        </div>

        <Card>
          <CardHeader>
            <CardTitle>Próximos Eventos</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-3">
              {events.slice(0, 10).map((event) => (
                <div key={event.id} className="flex items-center justify-between border-b pb-3 last:border-0">
                  <div>
                    <p className="font-medium">{event.title}</p>
                    <div className="flex items-center gap-2 text-sm text-muted-foreground">
                      <Badge variant="outline">
                        {event.type === 'meeting' ? 'Reunião' : event.type === 'deadline' ? 'Prazo' : event.type === 'reminder' ? 'Lembrete' : 'Evento'}
                      </Badge>
                      <span>•</span>
                      <span className="flex items-center gap-1">
                        <Clock className="h-3 w-3" />
                        {event.time}
                      </span>
                      <span>•</span>
                      <span className="flex items-center gap-1">
                        <MapPin className="h-3 w-3" />
                        {event.location}
                      </span>
                      <span>•</span>
                      <span className="flex items-center gap-1">
                        <Users className="h-3 w-3" />
                        {event.attendees}
                      </span>
                    </div>
                  </div>
                  <div className="text-right">
                    <p className="font-medium">{new Date(event.date).toLocaleDateString('pt-AO')}</p>
                    <Badge variant={event.date === today ? 'default' : 'secondary'}>
                      {event.date === today ? 'Hoje' : event.date < today ? 'Passado' : 'Futuro'}
                    </Badge>
                  </div>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      </div>
    </Layout>
  );
}
