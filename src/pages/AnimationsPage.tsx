// Animações e Transições - Página Completa e Funcional
import { useState } from 'react';
import { Layout } from '@/components/Layout';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { motion, AnimatePresence } from 'framer-motion';
import { 
  Sparkles, 
  Zap,
  Heart,
  Star,
  Bell,
  Check,
  X,
  ArrowRight,
  Loader2,
  Play,
  RotateCcw,
  MousePointer2,
} from 'lucide-react';

export default function AnimationsPage() {
  const [showNotification, setShowNotification] = useState(false);
  const [likeCount, setLikeCount] = useState(42);
  const [isLiked, setIsLiked] = useState(false);
  const [items, setItems] = useState([1, 2, 3, 4]);
  const [counter, setCounter] = useState(0);
  const [isLoading, setIsLoading] = useState(false);
  const [showSuccess, setShowSuccess] = useState(false);

  const handleLike = () => {
    setIsLiked(!isLiked);
    setLikeCount(isLiked ? likeCount - 1 : likeCount + 1);
  };

  const addItem = () => {
    setItems([...items, items.length + 1]);
  };

  const removeItem = (id: number) => {
    setItems(items.filter(item => item !== id));
  };

  const simulateLoading = () => {
    setIsLoading(true);
    setShowSuccess(false);
    setTimeout(() => {
      setIsLoading(false);
      setShowSuccess(true);
      setTimeout(() => setShowSuccess(false), 2000);
    }, 2000);
  };

  return (
    <Layout>
      <div className="space-y-6">
        {/* Header */}
        <motion.div
          initial={{ opacity: 0, y: -20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5 }}
        >
          <h1 className="text-3xl font-bold flex items-center gap-2">
            <Sparkles className="h-8 w-8 text-primary" />
            Animações e Transições
          </h1>
          <p className="text-muted-foreground">
            Micro-interações e efeitos visuais para melhor feedback
          </p>
        </motion.div>

        {/* Stats */}
        <div className="grid gap-4 md:grid-cols-4">
          {[
            { label: 'Hover Effects', value: '12+', icon: MousePointer2 },
            { label: 'Animações', value: '20+', icon: Zap },
            { label: 'Transições', value: '15+', icon: ArrowRight },
            { label: 'Micro-interações', value: '8+', icon: Sparkles },
          ].map((stat, index) => (
            <motion.div
              key={stat.label}
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: index * 0.1 }}
            >
              <Card>
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm font-medium text-muted-foreground flex items-center gap-2">
                    <stat.icon className="h-4 w-4" />
                    {stat.label}
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  <div className="text-2xl font-bold">{stat.value}</div>
                </CardContent>
              </Card>
            </motion.div>
          ))}
        </div>

        <Tabs defaultValue="hover" className="space-y-4">
          <TabsList className="grid w-full grid-cols-5">
            <TabsTrigger value="hover">Hover Effects</TabsTrigger>
            <TabsTrigger value="buttons">Botões</TabsTrigger>
            <TabsTrigger value="cards">Cards</TabsTrigger>
            <TabsTrigger value="lists">Listas</TabsTrigger>
            <TabsTrigger value="advanced">Avançado</TabsTrigger>
          </TabsList>

          {/* HOVER EFFECTS TAB */}
          <TabsContent value="hover" className="space-y-4">
            <Card>
              <CardHeader>
                <CardTitle>Hover Effects</CardTitle>
                <CardDescription>
                  Efeitos visuais ao passar o mouse sobre os elementos
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-6">
                {/* Scale on Hover */}
                <div>
                  <h3 className="font-semibold mb-3 flex items-center gap-2">
                    <Zap className="h-4 w-4" />
                    Scale on Hover
                  </h3>
                  <div className="flex flex-wrap gap-4">
                    <Button className="transition-transform hover:scale-105 active:scale-95">
                      Scale 105%
                    </Button>
                    <Button className="transition-transform hover:scale-110 active:scale-95">
                      Scale 110%
                    </Button>
                    <Button className="transition-transform hover:scale-125 active:scale-95">
                      Scale 125%
                    </Button>
                    <motion.button
                      whileHover={{ scale: 1.1 }}
                      whileTap={{ scale: 0.95 }}
                      className="px-4 py-2 bg-primary text-primary-foreground rounded-md"
                    >
                      Framer Motion Scale
                    </motion.button>
                  </div>
                </div>

                {/* Shadow on Hover */}
                <div>
                  <h3 className="font-semibold mb-3 flex items-center gap-2">
                    <Sparkles className="h-4 w-4" />
                    Shadow on Hover
                  </h3>
                  <div className="flex flex-wrap gap-4">
                    <Button className="transition-all hover:shadow-md">
                      Shadow MD
                    </Button>
                    <Button className="transition-all hover:shadow-lg">
                      Shadow LG
                    </Button>
                    <Button className="transition-all hover:shadow-xl">
                      Shadow XL
                    </Button>
                    <Button className="transition-all hover:shadow-2xl hover:shadow-primary/50">
                      Shadow Colored
                    </Button>
                  </div>
                </div>

                {/* Color Transition */}
                <div>
                  <h3 className="font-semibold mb-3 flex items-center gap-2">
                    <Star className="h-4 w-4" />
                    Color Transition
                  </h3>
                  <div className="flex flex-wrap gap-4">
                    <Button className="transition-colors duration-300 hover:bg-primary/90">
                      Subtle Transition
                    </Button>
                    <Button className="transition-colors duration-300 hover:bg-green-600">
                      Green Hover
                    </Button>
                    <Button className="transition-colors duration-300 hover:bg-blue-600">
                      Blue Hover
                    </Button>
                    <Button className="transition-all duration-300 hover:bg-gradient-to-r hover:from-purple-600 hover:to-pink-600">
                      Gradient Hover
                    </Button>
                  </div>
                </div>

                {/* Border & Glow */}
                <div>
                  <h3 className="font-semibold mb-3">Border & Glow Effects</h3>
                  <div className="flex flex-wrap gap-4">
                    <Button className="transition-all hover:border-primary hover:border-2" variant="outline">
                      Border Highlight
                    </Button>
                    <Button className="transition-all hover:ring-2 hover:ring-primary hover:ring-offset-2" variant="outline">
                      Ring Effect
                    </Button>
                    <Button className="transition-all hover:shadow-[0_0_20px_rgba(59,130,246,0.5)]" variant="outline">
                      Glow Effect
                    </Button>
                  </div>
                </div>

                {/* Rotate & Skew */}
                <div>
                  <h3 className="font-semibold mb-3">Transform Effects</h3>
                  <div className="flex flex-wrap gap-4">
                    <Button className="transition-transform hover:rotate-3">
                      Rotate 3°
                    </Button>
                    <Button className="transition-transform hover:-rotate-3">
                      Rotate -3°
                    </Button>
                    <Button className="transition-transform hover:skew-x-3">
                      Skew X
                    </Button>
                    <motion.button
                      whileHover={{ rotate: 360 }}
                      transition={{ duration: 0.5 }}
                      className="px-4 py-2 bg-primary text-primary-foreground rounded-md"
                    >
                      Rotate 360°
                    </motion.button>
                  </div>
                </div>

                {/* Interactive Cards */}
                <div>
                  <h3 className="font-semibold mb-3">Interactive Cards</h3>
                  <div className="grid gap-4 md:grid-cols-3">
                    <motion.div
                      whileHover={{ scale: 1.05, y: -5 }}
                      transition={{ type: "spring", stiffness: 300 }}
                    >
                      <Card className="cursor-pointer">
                        <CardContent className="p-6">
                          <Heart className="h-8 w-8 mb-2 text-red-500" />
                          <h4 className="font-semibold">Lift & Scale</h4>
                          <p className="text-sm text-muted-foreground">Hover para ver o efeito</p>
                        </CardContent>
                      </Card>
                    </motion.div>

                    <motion.div
                      whileHover={{ rotateY: 5, rotateX: 5 }}
                      transition={{ type: "spring", stiffness: 300 }}
                      style={{ transformStyle: "preserve-3d" }}
                    >
                      <Card className="cursor-pointer">
                        <CardContent className="p-6">
                          <Star className="h-8 w-8 mb-2 text-yellow-500" />
                          <h4 className="font-semibold">3D Tilt</h4>
                          <p className="text-sm text-muted-foreground">Efeito 3D no hover</p>
                        </CardContent>
                      </Card>
                    </motion.div>

                    <Card className="cursor-pointer transition-all hover:shadow-xl hover:border-primary">
                      <CardContent className="p-6">
                        <Bell className="h-8 w-8 mb-2 text-blue-500" />
                        <h4 className="font-semibold">Shadow & Border</h4>
                        <p className="text-sm text-muted-foreground">CSS transitions</p>
                      </CardContent>
                    </Card>
                  </div>
                </div>
              </CardContent>
            </Card>
          </TabsContent>

          {/* BUTTONS TAB */}
          <TabsContent value="buttons" className="space-y-4">
            <Card>
              <CardHeader>
                <CardTitle>Botões Animados</CardTitle>
                <CardDescription>
                  Diferentes estilos de animação para botões
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-6">
                {/* Loading Buttons */}
                <div>
                  <h3 className="font-semibold mb-3">Loading States</h3>
                  <div className="flex flex-wrap gap-4">
                    <Button onClick={simulateLoading} disabled={isLoading}>
                      {isLoading ? (
                        <>
                          <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                          Carregando...
                        </>
                      ) : (
                        'Simular Loading'
                      )}
                    </Button>
                    
                    <AnimatePresence mode="wait">
                      {showSuccess ? (
                        <motion.div
                          initial={{ scale: 0 }}
                          animate={{ scale: 1 }}
                          exit={{ scale: 0 }}
                        >
                          <Button variant="outline" className="bg-green-500/10 border-green-500">
                            <Check className="mr-2 h-4 w-4 text-green-500" />
                            Sucesso!
                          </Button>
                        </motion.div>
                      ) : null}
                    </AnimatePresence>
                  </div>
                </div>

                {/* Pulse & Bounce */}
                <div>
                  <h3 className="font-semibold mb-3">Pulse & Bounce</h3>
                  <div className="flex flex-wrap gap-4">
                    <Button className="animate-pulse">
                      Pulse Animation
                    </Button>
                    <Button className="animate-bounce">
                      Bounce Animation
                    </Button>
                    <motion.button
                      animate={{ scale: [1, 1.1, 1] }}
                      transition={{ repeat: Infinity, duration: 1 }}
                      className="px-4 py-2 bg-primary text-primary-foreground rounded-md"
                    >
                      Heartbeat
                    </motion.button>
                  </div>
                </div>

                {/* Like Button */}
                <div>
                  <h3 className="font-semibold mb-3">Interactive Like Button</h3>
                  <motion.button
                    whileTap={{ scale: 0.9 }}
                    onClick={handleLike}
                    className="flex items-center gap-2 px-6 py-3 rounded-lg border-2 transition-colors"
                    style={{
                      borderColor: isLiked ? '#ef4444' : '#e5e7eb',
                      backgroundColor: isLiked ? '#fef2f2' : 'transparent',
                    }}
                  >
                    <motion.div
                      animate={isLiked ? { scale: [1, 1.3, 1] } : {}}
                      transition={{ duration: 0.3 }}
                    >
                      <Heart
                        className="h-6 w-6"
                        fill={isLiked ? '#ef4444' : 'none'}
                        color={isLiked ? '#ef4444' : 'currentColor'}
                      />
                    </motion.div>
                    <span className="font-semibold">{likeCount}</span>
                  </motion.button>
                </div>

                {/* Ripple Effect */}
                <div>
                  <h3 className="font-semibold mb-3">Ripple Effect</h3>
                  <Button className="relative overflow-hidden group">
                    <span className="relative z-10">Hover Me</span>
                    <span className="absolute inset-0 bg-white/20 scale-0 group-hover:scale-100 transition-transform duration-500 rounded-full"></span>
                  </Button>
                </div>

                {/* Slide & Reveal */}
                <div>
                  <h3 className="font-semibold mb-3">Slide & Reveal</h3>
                  <div className="flex flex-wrap gap-4">
                    <Button className="relative overflow-hidden group">
                      <span className="relative z-10 group-hover:text-white transition-colors">
                        Slide Right
                      </span>
                      <span className="absolute inset-0 bg-primary translate-x-[-100%] group-hover:translate-x-0 transition-transform duration-300"></span>
                    </Button>
                    
                    <Button className="relative overflow-hidden group" variant="outline">
                      <span className="relative z-10">Slide Up</span>
                      <span className="absolute inset-0 bg-primary translate-y-[100%] group-hover:translate-y-0 transition-transform duration-300"></span>
                    </Button>
                  </div>
                </div>
              </CardContent>
            </Card>
          </TabsContent>

          {/* CARDS TAB */}
          <TabsContent value="cards" className="space-y-4">
            <Card>
              <CardHeader>
                <CardTitle>Cards Animados</CardTitle>
                <CardDescription>
                  Animações de entrada e interação para cards
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-6">
                {/* Fade In */}
                <div>
                  <h3 className="font-semibold mb-3">Fade In</h3>
                  <motion.div
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    transition={{ duration: 1 }}
                  >
                    <Card>
                      <CardContent className="p-6">
                        <p>Este card aparece com fade in suave</p>
                      </CardContent>
                    </Card>
                  </motion.div>
                </div>

                {/* Slide In */}
                <div>
                  <h3 className="font-semibold mb-3">Slide In</h3>
                  <div className="grid gap-4 md:grid-cols-3">
                    <motion.div
                      initial={{ x: -100, opacity: 0 }}
                      animate={{ x: 0, opacity: 1 }}
                      transition={{ duration: 0.5 }}
                    >
                      <Card>
                        <CardContent className="p-6">
                          <p className="text-sm">← Da Esquerda</p>
                        </CardContent>
                      </Card>
                    </motion.div>

                    <motion.div
                      initial={{ y: 100, opacity: 0 }}
                      animate={{ y: 0, opacity: 1 }}
                      transition={{ duration: 0.5 }}
                    >
                      <Card>
                        <CardContent className="p-6">
                          <p className="text-sm">↑ De Baixo</p>
                        </CardContent>
                      </Card>
                    </motion.div>

                    <motion.div
                      initial={{ x: 100, opacity: 0 }}
                      animate={{ x: 0, opacity: 1 }}
                      transition={{ duration: 0.5 }}
                    >
                      <Card>
                        <CardContent className="p-6">
                          <p className="text-sm">→ Da Direita</p>
                        </CardContent>
                      </Card>
                    </motion.div>
                  </div>
                </div>

                {/* Scale & Rotate */}
                <div>
                  <h3 className="font-semibold mb-3">Scale & Rotate</h3>
                  <div className="grid gap-4 md:grid-cols-2">
                    <motion.div
                      initial={{ scale: 0 }}
                      animate={{ scale: 1 }}
                      transition={{ type: "spring", stiffness: 260, damping: 20 }}
                    >
                      <Card>
                        <CardContent className="p-6">
                          <p className="text-sm">Scale com Spring</p>
                        </CardContent>
                      </Card>
                    </motion.div>

                    <motion.div
                      initial={{ rotate: -180, scale: 0 }}
                      animate={{ rotate: 0, scale: 1 }}
                      transition={{ duration: 0.5 }}
                    >
                      <Card>
                        <CardContent className="p-6">
                          <p className="text-sm">Rotate & Scale</p>
                        </CardContent>
                      </Card>
                    </motion.div>
                  </div>
                </div>

                {/* Stagger Children */}
                <div>
                  <h3 className="font-semibold mb-3">Stagger Animation</h3>
                  <motion.div
                    initial="hidden"
                    animate="visible"
                    variants={{
                      visible: {
                        transition: {
                          staggerChildren: 0.1
                        }
                      }
                    }}
                    className="grid gap-4 md:grid-cols-4"
                  >
                    {[1, 2, 3, 4].map((i) => (
                      <motion.div
                        key={i}
                        variants={{
                          hidden: { opacity: 0, y: 20 },
                          visible: { opacity: 1, y: 0 }
                        }}
                      >
                        <Card>
                          <CardContent className="p-6">
                            <p className="text-sm">Card {i}</p>
                          </CardContent>
                        </Card>
                      </motion.div>
                    ))}
                  </motion.div>
                </div>
              </CardContent>
            </Card>
          </TabsContent>

          {/* LISTS TAB */}
          <TabsContent value="lists" className="space-y-4">
            <Card>
              <CardHeader>
                <CardTitle>Listas Animadas</CardTitle>
                <CardDescription>
                  Animações para adicionar e remover itens
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-6">
                <div className="flex gap-2">
                  <Button onClick={addItem}>
                    <Play className="mr-2 h-4 w-4" />
                    Adicionar Item
                  </Button>
                  <Button variant="outline" onClick={() => setItems([1, 2, 3, 4])}>
                    <RotateCcw className="mr-2 h-4 w-4" />
                    Resetar
                  </Button>
                </div>

                <AnimatePresence mode="popLayout">
                  <motion.div className="space-y-2">
                    {items.map((item) => (
                      <motion.div
                        key={item}
                        initial={{ opacity: 0, x: -50 }}
                        animate={{ opacity: 1, x: 0 }}
                        exit={{ opacity: 0, x: 50 }}
                        layout
                        className="flex items-center justify-between p-4 border rounded-lg bg-card"
                      >
                        <span className="font-medium">Item #{item}</span>
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => removeItem(item)}
                        >
                          <X className="h-4 w-4" />
                        </Button>
                      </motion.div>
                    ))}
                  </motion.div>
                </AnimatePresence>

                {items.length === 0 && (
                  <motion.div
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    className="text-center py-8 text-muted-foreground"
                  >
                    Nenhum item na lista. Clique em "Adicionar Item" para começar.
                  </motion.div>
                )}
              </CardContent>
            </Card>
          </TabsContent>

          {/* ADVANCED TAB */}
          <TabsContent value="advanced" className="space-y-4">
            <Card>
              <CardHeader>
                <CardTitle>Animações Avançadas</CardTitle>
                <CardDescription>
                  Efeitos complexos e micro-interações
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-6">
                {/* Counter Animation */}
                <div>
                  <h3 className="font-semibold mb-3">Counter Animation</h3>
                  <div className="flex items-center gap-4">
                    <Button onClick={() => setCounter(counter + 1)}>
                      Incrementar
                    </Button>
                    <motion.div
                      key={counter}
                      initial={{ scale: 1.5, color: '#3b82f6' }}
                      animate={{ scale: 1, color: '#000000' }}
                      className="text-4xl font-bold"
                    >
                      {counter}
                    </motion.div>
                  </div>
                </div>

                {/* Notification Toast */}
                <div>
                  <h3 className="font-semibold mb-3">Notification Toast</h3>
                  <Button onClick={() => setShowNotification(true)}>
                    Mostrar Notificação
                  </Button>
                  
                  <AnimatePresence>
                    {showNotification && (
                      <motion.div
                        initial={{ opacity: 0, y: -50, scale: 0.3 }}
                        animate={{ opacity: 1, y: 0, scale: 1 }}
                        exit={{ opacity: 0, scale: 0.5, transition: { duration: 0.2 } }}
                        className="fixed top-4 right-4 z-50"
                      >
                        <Card className="w-80 shadow-lg border-green-500">
                          <CardContent className="p-4 flex items-center justify-between">
                            <div className="flex items-center gap-3">
                              <Check className="h-5 w-5 text-green-500" />
                              <div>
                                <p className="font-semibold">Sucesso!</p>
                                <p className="text-sm text-muted-foreground">
                                  Operação concluída com sucesso
                                </p>
                              </div>
                            </div>
                            <Button
                              size="sm"
                              variant="ghost"
                              onClick={() => setShowNotification(false)}
                            >
                              <X className="h-4 w-4" />
                            </Button>
                          </CardContent>
                        </Card>
                      </motion.div>
                    )}
                  </AnimatePresence>
                </div>

                {/* Parallax Cards */}
                <div>
                  <h3 className="font-semibold mb-3">Parallax Effect</h3>
                  <div className="grid gap-4 md:grid-cols-3">
                    {[1, 2, 3].map((i) => (
                      <motion.div
                        key={i}
                        whileHover={{ y: -10 }}
                        transition={{ type: "spring", stiffness: 300 }}
                      >
                        <Card className="cursor-pointer">
                          <CardContent className="p-6">
                            <div className="h-32 bg-gradient-to-br from-primary/20 to-primary/5 rounded-lg mb-4"></div>
                            <h4 className="font-semibold">Card {i}</h4>
                            <p className="text-sm text-muted-foreground">
                              Hover para ver o efeito parallax
                            </p>
                          </CardContent>
                        </Card>
                      </motion.div>
                    ))}
                  </div>
                </div>

                {/* Morphing Shape */}
                <div>
                  <h3 className="font-semibold mb-3">Morphing Shape</h3>
                  <motion.div
                    animate={{
                      borderRadius: ["20%", "50%", "20%"],
                      rotate: [0, 180, 360],
                    }}
                    transition={{
                      duration: 3,
                      repeat: Infinity,
                      ease: "easeInOut"
                    }}
                    className="w-32 h-32 bg-gradient-to-br from-blue-500 to-purple-600"
                  />
                </div>

                {/* Typing Effect */}
                <div>
                  <h3 className="font-semibold mb-3">Typing Effect</h3>
                  <motion.div
                    initial={{ width: 0 }}
                    animate={{ width: "100%" }}
                    transition={{ duration: 2, repeat: Infinity }}
                    className="overflow-hidden whitespace-nowrap"
                  >
                    <p className="text-lg font-mono">
                      Este texto aparece como se estivesse sendo digitado...
                    </p>
                  </motion.div>
                </div>
              </CardContent>
            </Card>
          </TabsContent>
        </Tabs>
      </div>
    </Layout>
  );
}
