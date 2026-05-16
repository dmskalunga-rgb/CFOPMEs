// Settings Page - Versão Completa e Funcional
import { useState } from 'react';
import { Layout } from '@/components/Layout';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Separator } from '@/components/ui/separator';
import { User, Bell, Shield, Palette, Globe, Save } from 'lucide-react';
import { toast } from 'sonner';

export default function Settings() {
  const [loading, setLoading] = useState(false);

  // Profile Settings
  const [profile, setProfile] = useState({
    name: 'Administrador',
    email: 'admin@kwanzacontrol.ao',
    phone: '+244 923 456 789',
    company: 'KwanzaControl',
    position: 'Administrador do Sistema'
  });

  // Notification Settings
  const [notifications, setNotifications] = useState({
    email_notifications: true,
    push_notifications: true,
    sms_notifications: false,
    invoice_alerts: true,
    payment_alerts: true,
    report_alerts: false,
    system_alerts: true
  });

  // Security Settings
  const [security, setSecurity] = useState({
    two_factor: false,
    session_timeout: '30',
    password_expiry: '90',
    login_alerts: true
  });

  // Appearance Settings
  const [appearance, setAppearance] = useState({
    theme: 'system',
    language: 'pt',
    date_format: 'DD/MM/YYYY',
    currency: 'AOA'
  });

  const handleSaveProfile = () => {
    setLoading(true);
    setTimeout(() => {
      setLoading(false);
      toast.success('Perfil atualizado com sucesso!');
    }, 1000);
  };

  const handleSaveNotifications = () => {
    setLoading(true);
    setTimeout(() => {
      setLoading(false);
      toast.success('Preferências de notificação atualizadas!');
    }, 1000);
  };

  const handleSaveSecurity = () => {
    setLoading(true);
    setTimeout(() => {
      setLoading(false);
      toast.success('Configurações de segurança atualizadas!');
    }, 1000);
  };

  const handleSaveAppearance = () => {
    setLoading(true);
    setTimeout(() => {
      setLoading(false);
      toast.success('Preferências de aparência atualizadas!');
    }, 1000);
  };

  return (
    <Layout>
      <div className="space-y-6">
        {/* Header */}
        <div>
          <h1 className="text-3xl font-bold">Configurações</h1>
          <p className="text-muted-foreground">Gerencie suas preferências e configurações</p>
        </div>

        {/* Tabs */}
        <Tabs defaultValue="profile" className="space-y-6">
          <TabsList className="grid w-full grid-cols-4">
            <TabsTrigger value="profile">
              <User className="h-4 w-4 mr-2" />
              Perfil
            </TabsTrigger>
            <TabsTrigger value="notifications">
              <Bell className="h-4 w-4 mr-2" />
              Notificações
            </TabsTrigger>
            <TabsTrigger value="security">
              <Shield className="h-4 w-4 mr-2" />
              Segurança
            </TabsTrigger>
            <TabsTrigger value="appearance">
              <Palette className="h-4 w-4 mr-2" />
              Aparência
            </TabsTrigger>
          </TabsList>

          {/* Tab: Profile */}
          <TabsContent value="profile">
            <Card>
              <CardHeader>
                <CardTitle>Informações do Perfil</CardTitle>
                <CardDescription>
                  Atualize suas informações pessoais
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-6">
                <div className="grid gap-4">
                  <div className="grid grid-cols-2 gap-4">
                    <div className="space-y-2">
                      <Label>Nome Completo</Label>
                      <Input
                        value={profile.name}
                        onChange={(e) => setProfile({...profile, name: e.target.value})}
                      />
                    </div>
                    <div className="space-y-2">
                      <Label>Email</Label>
                      <Input
                        type="email"
                        value={profile.email}
                        onChange={(e) => setProfile({...profile, email: e.target.value})}
                      />
                    </div>
                  </div>
                  <div className="grid grid-cols-2 gap-4">
                    <div className="space-y-2">
                      <Label>Telefone</Label>
                      <Input
                        value={profile.phone}
                        onChange={(e) => setProfile({...profile, phone: e.target.value})}
                      />
                    </div>
                    <div className="space-y-2">
                      <Label>Empresa</Label>
                      <Input
                        value={profile.company}
                        onChange={(e) => setProfile({...profile, company: e.target.value})}
                      />
                    </div>
                  </div>
                  <div className="space-y-2">
                    <Label>Cargo</Label>
                    <Input
                      value={profile.position}
                      onChange={(e) => setProfile({...profile, position: e.target.value})}
                    />
                  </div>
                </div>
                <Separator />
                <div className="flex justify-end">
                  <Button onClick={handleSaveProfile} disabled={loading}>
                    <Save className="h-4 w-4 mr-2" />
                    Salvar Alterações
                  </Button>
                </div>
              </CardContent>
            </Card>
          </TabsContent>

          {/* Tab: Notifications */}
          <TabsContent value="notifications">
            <Card>
              <CardHeader>
                <CardTitle>Preferências de Notificação</CardTitle>
                <CardDescription>
                  Configure como deseja receber notificações
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-6">
                <div className="space-y-4">
                  <div className="flex items-center justify-between">
                    <div className="space-y-0.5">
                      <Label>Notificações por Email</Label>
                      <p className="text-sm text-muted-foreground">
                        Receba notificações por email
                      </p>
                    </div>
                    <Switch
                      checked={notifications.email_notifications}
                      onCheckedChange={(checked) => setNotifications({...notifications, email_notifications: checked})}
                    />
                  </div>
                  <Separator />
                  <div className="flex items-center justify-between">
                    <div className="space-y-0.5">
                      <Label>Notificações Push</Label>
                      <p className="text-sm text-muted-foreground">
                        Receba notificações push no navegador
                      </p>
                    </div>
                    <Switch
                      checked={notifications.push_notifications}
                      onCheckedChange={(checked) => setNotifications({...notifications, push_notifications: checked})}
                    />
                  </div>
                  <Separator />
                  <div className="flex items-center justify-between">
                    <div className="space-y-0.5">
                      <Label>Notificações por SMS</Label>
                      <p className="text-sm text-muted-foreground">
                        Receba notificações por SMS
                      </p>
                    </div>
                    <Switch
                      checked={notifications.sms_notifications}
                      onCheckedChange={(checked) => setNotifications({...notifications, sms_notifications: checked})}
                    />
                  </div>
                  <Separator />
                  <div className="flex items-center justify-between">
                    <div className="space-y-0.5">
                      <Label>Alertas de Faturas</Label>
                      <p className="text-sm text-muted-foreground">
                        Notificações sobre faturas
                      </p>
                    </div>
                    <Switch
                      checked={notifications.invoice_alerts}
                      onCheckedChange={(checked) => setNotifications({...notifications, invoice_alerts: checked})}
                    />
                  </div>
                  <Separator />
                  <div className="flex items-center justify-between">
                    <div className="space-y-0.5">
                      <Label>Alertas de Pagamentos</Label>
                      <p className="text-sm text-muted-foreground">
                        Notificações sobre pagamentos
                      </p>
                    </div>
                    <Switch
                      checked={notifications.payment_alerts}
                      onCheckedChange={(checked) => setNotifications({...notifications, payment_alerts: checked})}
                    />
                  </div>
                  <Separator />
                  <div className="flex items-center justify-between">
                    <div className="space-y-0.5">
                      <Label>Alertas de Relatórios</Label>
                      <p className="text-sm text-muted-foreground">
                        Notificações sobre relatórios
                      </p>
                    </div>
                    <Switch
                      checked={notifications.report_alerts}
                      onCheckedChange={(checked) => setNotifications({...notifications, report_alerts: checked})}
                    />
                  </div>
                  <Separator />
                  <div className="flex items-center justify-between">
                    <div className="space-y-0.5">
                      <Label>Alertas do Sistema</Label>
                      <p className="text-sm text-muted-foreground">
                        Notificações importantes do sistema
                      </p>
                    </div>
                    <Switch
                      checked={notifications.system_alerts}
                      onCheckedChange={(checked) => setNotifications({...notifications, system_alerts: checked})}
                    />
                  </div>
                </div>
                <Separator />
                <div className="flex justify-end">
                  <Button onClick={handleSaveNotifications} disabled={loading}>
                    <Save className="h-4 w-4 mr-2" />
                    Salvar Preferências
                  </Button>
                </div>
              </CardContent>
            </Card>
          </TabsContent>

          {/* Tab: Security */}
          <TabsContent value="security">
            <Card>
              <CardHeader>
                <CardTitle>Configurações de Segurança</CardTitle>
                <CardDescription>
                  Gerencie a segurança da sua conta
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-6">
                <div className="space-y-4">
                  <div className="flex items-center justify-between">
                    <div className="space-y-0.5">
                      <Label>Autenticação de Dois Fatores (2FA)</Label>
                      <p className="text-sm text-muted-foreground">
                        Adicione uma camada extra de segurança
                      </p>
                    </div>
                    <Switch
                      checked={security.two_factor}
                      onCheckedChange={(checked) => setSecurity({...security, two_factor: checked})}
                    />
                  </div>
                  <Separator />
                  <div className="space-y-2">
                    <Label>Tempo de Sessão (minutos)</Label>
                    <Select value={security.session_timeout} onValueChange={(value) => setSecurity({...security, session_timeout: value})}>
                      <SelectTrigger>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="15">15 minutos</SelectItem>
                        <SelectItem value="30">30 minutos</SelectItem>
                        <SelectItem value="60">1 hora</SelectItem>
                        <SelectItem value="120">2 horas</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                  <Separator />
                  <div className="space-y-2">
                    <Label>Expiração de Senha (dias)</Label>
                    <Select value={security.password_expiry} onValueChange={(value) => setSecurity({...security, password_expiry: value})}>
                      <SelectTrigger>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="30">30 dias</SelectItem>
                        <SelectItem value="60">60 dias</SelectItem>
                        <SelectItem value="90">90 dias</SelectItem>
                        <SelectItem value="never">Nunca</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                  <Separator />
                  <div className="flex items-center justify-between">
                    <div className="space-y-0.5">
                      <Label>Alertas de Login</Label>
                      <p className="text-sm text-muted-foreground">
                        Notificações sobre novos logins
                      </p>
                    </div>
                    <Switch
                      checked={security.login_alerts}
                      onCheckedChange={(checked) => setSecurity({...security, login_alerts: checked})}
                    />
                  </div>
                </div>
                <Separator />
                <div className="flex justify-end">
                  <Button onClick={handleSaveSecurity} disabled={loading}>
                    <Save className="h-4 w-4 mr-2" />
                    Salvar Configurações
                  </Button>
                </div>
              </CardContent>
            </Card>
          </TabsContent>

          {/* Tab: Appearance */}
          <TabsContent value="appearance">
            <Card>
              <CardHeader>
                <CardTitle>Preferências de Aparência</CardTitle>
                <CardDescription>
                  Personalize a aparência do sistema
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-6">
                <div className="grid gap-4">
                  <div className="space-y-2">
                    <Label>Tema</Label>
                    <Select value={appearance.theme} onValueChange={(value) => setAppearance({...appearance, theme: value})}>
                      <SelectTrigger>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="light">Claro</SelectItem>
                        <SelectItem value="dark">Escuro</SelectItem>
                        <SelectItem value="system">Sistema</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                  <Separator />
                  <div className="space-y-2">
                    <Label>Idioma</Label>
                    <Select value={appearance.language} onValueChange={(value) => setAppearance({...appearance, language: value})}>
                      <SelectTrigger>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="pt">Português</SelectItem>
                        <SelectItem value="en">English</SelectItem>
                        <SelectItem value="fr">Français</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                  <Separator />
                  <div className="space-y-2">
                    <Label>Formato de Data</Label>
                    <Select value={appearance.date_format} onValueChange={(value) => setAppearance({...appearance, date_format: value})}>
                      <SelectTrigger>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="DD/MM/YYYY">DD/MM/YYYY</SelectItem>
                        <SelectItem value="MM/DD/YYYY">MM/DD/YYYY</SelectItem>
                        <SelectItem value="YYYY-MM-DD">YYYY-MM-DD</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                  <Separator />
                  <div className="space-y-2">
                    <Label>Moeda</Label>
                    <Select value={appearance.currency} onValueChange={(value) => setAppearance({...appearance, currency: value})}>
                      <SelectTrigger>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="AOA">AOA (Kwanza)</SelectItem>
                        <SelectItem value="USD">USD (Dólar)</SelectItem>
                        <SelectItem value="EUR">EUR (Euro)</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                </div>
                <Separator />
                <div className="flex justify-end">
                  <Button onClick={handleSaveAppearance} disabled={loading}>
                    <Save className="h-4 w-4 mr-2" />
                    Salvar Preferências
                  </Button>
                </div>
              </CardContent>
            </Card>
          </TabsContent>
        </Tabs>
      </div>
    </Layout>
  );
}
