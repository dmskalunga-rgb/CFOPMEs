// Developers - Portal de Desenvolvedores
import { useState } from 'react';
import { Layout } from '@/components/Layout';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Code, Key, Book, Activity } from 'lucide-react';
import { toast } from 'sonner';

interface APIKey {
  id: string;
  name: string;
  key: string;
  created: string;
  lastUsed: string;
  requests: number;
}

export default function Developers() {
  const [apiKeys] = useState<APIKey[]>([
    { id: '1', name: 'Production API', key: 'kc_prod_abc123...', created: '2026-01-15', lastUsed: '2 horas atrás', requests: 15420 },
    { id: '2', name: 'Development API', key: 'kc_dev_xyz789...', created: '2026-02-01', lastUsed: '1 dia atrás', requests: 8930 },
    { id: '3', name: 'Testing API', key: 'kc_test_def456...', created: '2026-03-10', lastUsed: '5 dias atrás', requests: 2150 }
  ]);

  const endpoints = [
    { method: 'GET', path: '/api/v1/invoices', description: 'Listar todas as faturas' },
    { method: 'POST', path: '/api/v1/invoices', description: 'Criar nova fatura' },
    { method: 'GET', path: '/api/v1/users', description: 'Listar usuários' },
    { method: 'POST', path: '/api/v1/payments', description: 'Processar pagamento' },
    { method: 'GET', path: '/api/v1/reports', description: 'Gerar relatórios' }
  ];

  const handleGenerateKey = () => {
    toast.success('Nova API Key gerada com sucesso!');
  };

  const handleCopyKey = (key: string) => {
    navigator.clipboard.writeText(key);
    toast.success('API Key copiada!');
  };

  const getMethodColor = (method: string) => {
    const colors = {
      GET: 'bg-blue-100 text-blue-700',
      POST: 'bg-green-100 text-green-700',
      PUT: 'bg-yellow-100 text-yellow-700',
      DELETE: 'bg-red-100 text-red-700'
    };
    return colors[method as keyof typeof colors] || 'bg-gray-100 text-gray-700';
  };

  return (
    <Layout>
      <div className="space-y-6">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-3xl font-bold">Portal de Desenvolvedores</h1>
            <p className="text-muted-foreground">API, documentação e ferramentas</p>
          </div>
          <Button onClick={handleGenerateKey}>
            <Key className="h-4 w-4 mr-2" />
            Gerar Nova API Key
          </Button>
        </div>

        <div className="grid gap-4 md:grid-cols-4">
          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">API Keys</CardTitle>
              <Key className="h-4 w-4 text-muted-foreground" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">{apiKeys.length}</div>
              <p className="text-xs text-muted-foreground">ativas</p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Requisições (30d)</CardTitle>
              <Activity className="h-4 w-4 text-muted-foreground" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">
                {apiKeys.reduce((sum, k) => sum + k.requests, 0).toLocaleString()}
              </div>
              <p className="text-xs text-muted-foreground">total</p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Endpoints</CardTitle>
              <Code className="h-4 w-4 text-muted-foreground" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">{endpoints.length}</div>
              <p className="text-xs text-muted-foreground">disponíveis</p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Uptime</CardTitle>
              <Activity className="h-4 w-4 text-green-600" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold text-green-600">99.9%</div>
              <p className="text-xs text-muted-foreground">últimos 30 dias</p>
            </CardContent>
          </Card>
        </div>

        <Tabs defaultValue="keys">
          <TabsList className="grid w-full grid-cols-3">
            <TabsTrigger value="keys">API Keys</TabsTrigger>
            <TabsTrigger value="docs">Documentação</TabsTrigger>
            <TabsTrigger value="examples">Exemplos</TabsTrigger>
          </TabsList>

          <TabsContent value="keys">
            <Card>
              <CardHeader>
                <CardTitle>Suas API Keys</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="space-y-4">
                  {apiKeys.map((key) => (
                    <div key={key.id} className="flex items-center justify-between border-b pb-4 last:border-0">
                      <div className="flex-1">
                        <p className="font-medium">{key.name}</p>
                        <div className="flex items-center gap-2 text-sm text-muted-foreground mt-1">
                          <code className="bg-muted px-2 py-1 rounded">{key.key}</code>
                          <Button variant="ghost" size="sm" onClick={() => handleCopyKey(key.key)}>
                            Copiar
                          </Button>
                        </div>
                        <p className="text-xs text-muted-foreground mt-1">
                          Criada: {key.created} • Último uso: {key.lastUsed}
                        </p>
                      </div>
                      <div className="text-right">
                        <p className="text-lg font-bold">{key.requests.toLocaleString()}</p>
                        <p className="text-xs text-muted-foreground">requisições</p>
                      </div>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>
          </TabsContent>

          <TabsContent value="docs">
            <Card>
              <CardHeader>
                <CardTitle>Endpoints da API</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="space-y-3">
                  {endpoints.map((endpoint, i) => (
                    <div key={i} className="flex items-center gap-3 border-b pb-3 last:border-0">
                      <Badge className={getMethodColor(endpoint.method)}>
                        {endpoint.method}
                      </Badge>
                      <code className="flex-1 text-sm">{endpoint.path}</code>
                      <p className="text-sm text-muted-foreground">{endpoint.description}</p>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>
          </TabsContent>

          <TabsContent value="examples">
            <Card>
              <CardHeader>
                <CardTitle>Exemplos de Código</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="space-y-4">
                  <div>
                    <p className="font-medium mb-2">JavaScript / Node.js</p>
                    <pre className="bg-muted p-4 rounded-lg overflow-x-auto">
                      <code>{`const response = await fetch('https://api.kwanzacontrol.ao/v1/invoices', {
  headers: {
    'Authorization': 'Bearer YOUR_API_KEY',
    'Content-Type': 'application/json'
  }
});
const data = await response.json();`}</code>
                    </pre>
                  </div>
                  <div>
                    <p className="font-medium mb-2">Python</p>
                    <pre className="bg-muted p-4 rounded-lg overflow-x-auto">
                      <code>{`import requests

headers = {
    'Authorization': 'Bearer YOUR_API_KEY',
    'Content-Type': 'application/json'
}
response = requests.get('https://api.kwanzacontrol.ao/v1/invoices', headers=headers)
data = response.json()`}</code>
                    </pre>
                  </div>
                </div>
              </CardContent>
            </Card>
          </TabsContent>
        </Tabs>
      </div>
    </Layout>
  );
}
