import { motion } from "framer-motion";
import { Link } from "react-router-dom";
import { ArrowRight, CheckCircle2, Zap, Shield, TrendingUp, FileText, Users, BarChart3, Brain, Lock, Clock, Globe } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { ROUTE_PATHS } from "@/lib/index";
import { springPresets, fadeInUp, staggerContainer, staggerItem, hoverLift } from "@/lib/motion";
import { IMAGES } from "@/assets/images";

export default function Home() {
  const features = [
    {
      icon: FileText,
      title: "Faturação Eletrónica AGT",
      description: "Integração nativa com AGT (FISCALIZA). Assinatura digital JWS, validação em tempo real e conformidade total com a legislação angolana 2026.",
    },
    {
      icon: Users,
      title: "Payroll Automático",
      description: "Cálculo automático de IRT (12 escalões) e INSS (3% + 8%). Geração de recibos de salário e relatórios mensais com precisão fiscal.",
    },
    {
      icon: Brain,
      title: "IA Financeira",
      description: "Classificação automática de transações, previsão de fluxo de caixa, detecção de anomalias e chatbot financeiro com PLN avançado.",
    },
    {
      icon: Shield,
      title: "Conformidade 2026",
      description: "Totalmente adaptado às novas regras fiscais angolanas. SAF-T XML, relatórios AGT, mapas de IRT/INSS e auditoria completa.",
    },
  ];

  const benefits = [
    { icon: Zap, text: "Automação total de processos financeiros" },
    { icon: Clock, text: "Economize até 20 horas por mês" },
    { icon: TrendingUp, text: "Aumente a precisão fiscal em 99.9%" },
    { icon: Lock, text: "Segurança bancária com criptografia AES-256" },
    { icon: BarChart3, text: "Insights em tempo real com dashboards inteligentes" },
    { icon: Globe, text: "Acesso remoto 24/7 de qualquer dispositivo" },
  ];

  const pricingPlans = [
    {
      name: "Starter",
      price: "49.900",
      period: "mês",
      features: [
        "Até 50 faturas/mês",
        "5 funcionários",
        "Integração AGT",
        "Suporte por email",
      ],
    },
    {
      name: "Professional",
      price: "99.900",
      period: "mês",
      features: [
        "Faturas ilimitadas",
        "20 funcionários",
        "IA Financeira",
        "Suporte prioritário",
        "Relatórios avançados",
      ],
      popular: true,
    },
    {
      name: "Enterprise",
      price: "Personalizado",
      period: "",
      features: [
        "Tudo do Professional",
        "Funcionários ilimitados",
        "API dedicada",
        "Gestor de conta",
        "SLA 99.9%",
      ],
    },
  ];

  return (
    <div className="min-h-screen bg-background">
      <header className="fixed top-0 left-0 right-0 z-50 border-b border-border bg-background/80 backdrop-blur supports-[backdrop-filter]:bg-background/60">
        <div className="container mx-auto px-4">
          <div className="flex h-16 items-center justify-between">
            <motion.div
              initial={{ opacity: 0, x: -20 }}
              animate={{ opacity: 1, x: 0 }}
              transition={springPresets.gentle}
              className="flex items-center gap-2"
            >
              <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-gradient-to-br from-primary to-primary/80">
                <BarChart3 className="h-6 w-6 text-primary-foreground" />
              </div>
              <span className="text-xl font-bold tracking-tight">KWANZACONTROL</span>
            </motion.div>

            <motion.nav
              initial={{ opacity: 0, y: -10 }}
              animate={{ opacity: 1, y: 0 }}
              transition={springPresets.gentle}
              className="hidden md:flex items-center gap-6"
            >
              <a href="#features" className="text-sm font-medium text-muted-foreground hover:text-foreground transition-colors">
                Funcionalidades
              </a>
              <a href="#benefits" className="text-sm font-medium text-muted-foreground hover:text-foreground transition-colors">
                Benefícios
              </a>
              <a href="#pricing" className="text-sm font-medium text-muted-foreground hover:text-foreground transition-colors">
                Preços
              </a>
              <Link to={ROUTE_PATHS.LOGIN}>
                <Button variant="ghost" size="sm">
                  Entrar
                </Button>
              </Link>
              <Link to={ROUTE_PATHS.LOGIN}>
                <Button size="sm" className="gap-2">
                  Começar Agora
                  <ArrowRight className="h-4 w-4" />
                </Button>
              </Link>
            </motion.nav>
          </div>
        </div>
      </header>

      <main className="pt-16">
        <section className="relative overflow-hidden py-24 md:py-32">
          <div className="absolute inset-0 z-0 opacity-30">
            <img
              src={IMAGES.DASHBOARD_INSPIRATION_1}
              alt=""
              className="h-full w-full object-cover"
            />
          </div>
          <div className="absolute inset-0 bg-gradient-to-b from-background/50 via-transparent to-background/70" />

          <div className="container relative z-10 mx-auto px-4">
            <motion.div
              initial="hidden"
              animate="visible"
              variants={staggerContainer}
              className="mx-auto max-w-4xl text-center"
            >
              <motion.div variants={staggerItem} className="mb-6">
                <span className="inline-flex items-center gap-2 rounded-full bg-primary/10 px-4 py-1.5 text-sm font-medium text-primary">
                  <Zap className="h-4 w-4" />
                  Conformidade AGT 2026
                </span>
              </motion.div>

              <motion.h1
                variants={staggerItem}
                className="mb-6 text-4xl font-bold tracking-tight md:text-6xl lg:text-7xl"
              >
                KWANZACONTROL
                <span className="block bg-gradient-to-r from-primary to-accent bg-clip-text text-transparent">
                  CFO Digital Inteligente
                </span>
                <span className="block text-3xl md:text-4xl lg:text-5xl mt-2">para PMEs Angolanas</span>
              </motion.h1>

              <motion.p
                variants={staggerItem}
                className="mb-8 text-lg text-muted-foreground md:text-xl"
              >
                Automatize faturação eletrónica, payroll, contabilidade e controlo financeiro com IA.
                Conformidade total com AGT (FISCALIZA), cálculos fiscais precisos (IRT + INSS) e insights em tempo real.
              </motion.p>

              <motion.div variants={staggerItem} className="flex flex-col sm:flex-row items-center justify-center gap-4">
                <Link to={ROUTE_PATHS.LOGIN}>
                  <Button size="lg" className="gap-2 text-base">
                    Começar Agora
                    <ArrowRight className="h-5 w-5" />
                  </Button>
                </Link>
                <Button size="lg" variant="outline" className="gap-2 text-base">
                  Ver Demonstração
                </Button>
              </motion.div>

              <motion.div
                variants={staggerItem}
                className="mt-12 flex flex-wrap items-center justify-center gap-8 text-sm text-muted-foreground"
              >
                <div className="flex items-center gap-2">
                  <CheckCircle2 className="h-5 w-5 text-primary" />
                  <span>Sem cartão de crédito</span>
                </div>
                <div className="flex items-center gap-2">
                  <CheckCircle2 className="h-5 w-5 text-primary" />
                  <span>14 dias grátis</span>
                </div>
                <div className="flex items-center gap-2">
                  <CheckCircle2 className="h-5 w-5 text-primary" />
                  <span>Cancele quando quiser</span>
                </div>
              </motion.div>
            </motion.div>
          </div>
        </section>

        <section id="features" className="py-24 bg-muted/30">
          <div className="container mx-auto px-4">
            <motion.div
              initial="hidden"
              whileInView="visible"
              viewport={{ once: true, margin: "-100px" }}
              variants={staggerContainer}
              className="mx-auto max-w-6xl"
            >
              <motion.div variants={staggerItem} className="mb-16 text-center">
                <h2 className="mb-4 text-3xl font-bold tracking-tight md:text-4xl">
                  Funcionalidades Poderosas
                </h2>
                <p className="text-lg text-muted-foreground">
                  Tudo o que precisa para gerir as finanças da sua empresa com eficiência e conformidade
                </p>
              </motion.div>

              <div className="grid gap-8 md:grid-cols-2">
                {features.map((feature, index) => {
                  const Icon = feature.icon;
                  return (
                    <motion.div key={index} variants={staggerItem}>
                      <Card
                        className="h-full transition-all hover:shadow-lg"
                        style={{
                          boxShadow: "0 8px 30px -6px color-mix(in srgb, var(--primary) 15%, transparent)",
                        }}
                      >
                        <CardContent className="p-8">
                          <div className="mb-4 flex h-14 w-14 items-center justify-center rounded-xl bg-primary/10">
                            <Icon className="h-7 w-7 text-primary" />
                          </div>
                          <h3 className="mb-3 text-xl font-semibold">{feature.title}</h3>
                          <p className="text-muted-foreground">{feature.description}</p>
                        </CardContent>
                      </Card>
                    </motion.div>
                  );
                })}
              </div>
            </motion.div>
          </div>
        </section>

        <section id="benefits" className="py-24">
          <div className="container mx-auto px-4">
            <motion.div
              initial="hidden"
              whileInView="visible"
              viewport={{ once: true, margin: "-100px" }}
              variants={staggerContainer}
              className="mx-auto max-w-6xl"
            >
              <motion.div variants={staggerItem} className="mb-16 text-center">
                <h2 className="mb-4 text-3xl font-bold tracking-tight md:text-4xl">
                  Por Que Escolher KWANZACONTROL?
                </h2>
                <p className="text-lg text-muted-foreground">
                  Transforme a gestão financeira da sua empresa com tecnologia de ponta
                </p>
              </motion.div>

              <div className="grid gap-6 md:grid-cols-2 lg:grid-cols-3">
                {benefits.map((benefit, index) => {
                  const Icon = benefit.icon;
                  return (
                    <motion.div
                      key={index}
                      variants={staggerItem}
                      whileHover={{ scale: 1.02 }}
                      transition={springPresets.snappy}
                    >
                      <Card className="h-full">
                        <CardContent className="flex items-start gap-4 p-6">
                          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-accent/10">
                            <Icon className="h-5 w-5 text-accent" />
                          </div>
                          <p className="font-medium">{benefit.text}</p>
                        </CardContent>
                      </Card>
                    </motion.div>
                  );
                })}
              </div>

              <motion.div variants={staggerItem} className="mt-16 relative overflow-hidden rounded-2xl">
                <img
                  src={IMAGES.ANALYTICS_VISUAL_1}
                  alt="Dashboard Analytics"
                  className="w-full h-auto object-cover"
                  style={{
                    boxShadow: "0 20px 60px -12px color-mix(in srgb, var(--primary) 25%, transparent)",
                  }}
                />
              </motion.div>
            </motion.div>
          </div>
        </section>

        <section id="pricing" className="py-24 bg-muted/30">
          <div className="container mx-auto px-4">
            <motion.div
              initial="hidden"
              whileInView="visible"
              viewport={{ once: true, margin: "-100px" }}
              variants={staggerContainer}
              className="mx-auto max-w-6xl"
            >
              <motion.div variants={staggerItem} className="mb-16 text-center">
                <h2 className="mb-4 text-3xl font-bold tracking-tight md:text-4xl">
                  Planos Transparentes
                </h2>
                <p className="text-lg text-muted-foreground">
                  Escolha o plano ideal para o tamanho da sua empresa
                </p>
              </motion.div>

              <div className="grid gap-8 md:grid-cols-3">
                {pricingPlans.map((plan, index) => (
                  <motion.div key={index} variants={staggerItem}>
                    <Card
                      className={`relative h-full ${plan.popular ? "border-primary shadow-lg" : ""}`}
                      style={{
                        boxShadow: plan.popular
                          ? "0 12px 40px -8px color-mix(in srgb, var(--primary) 30%, transparent)"
                          : "0 8px 30px -6px color-mix(in srgb, var(--primary) 15%, transparent)",
                      }}
                    >
                      {plan.popular && (
                        <div className="absolute -top-4 left-1/2 -translate-x-1/2">
                          <span className="inline-flex items-center gap-1 rounded-full bg-primary px-4 py-1 text-xs font-semibold text-primary-foreground">
                            <Zap className="h-3 w-3" />
                            Mais Popular
                          </span>
                        </div>
                      )}
                      <CardContent className="p-8">
                        <h3 className="mb-2 text-2xl font-bold">{plan.name}</h3>
                        <div className="mb-6">
                          <span className="text-4xl font-bold">{plan.price}</span>
                          {plan.period && (
                            <span className="text-muted-foreground"> Kz/{plan.period}</span>
                          )}
                        </div>
                        <ul className="mb-8 space-y-3">
                          {plan.features.map((feature, fIndex) => (
                            <li key={fIndex} className="flex items-start gap-2">
                              <CheckCircle2 className="h-5 w-5 shrink-0 text-primary" />
                              <span className="text-sm">{feature}</span>
                            </li>
                          ))}
                        </ul>
                        <Link to={ROUTE_PATHS.LOGIN}>
                          <Button
                            className="w-full"
                            variant={plan.popular ? "default" : "outline"}
                          >
                            Começar Agora
                          </Button>
                        </Link>
                      </CardContent>
                    </Card>
                  </motion.div>
                ))}
              </div>
            </motion.div>
          </div>
        </section>

        <section className="py-24">
          <div className="container mx-auto px-4">
            <motion.div
              initial="hidden"
              whileInView="visible"
              viewport={{ once: true, margin: "-100px" }}
              variants={fadeInUp}
              className="mx-auto max-w-4xl rounded-2xl bg-gradient-to-br from-primary to-primary/80 p-12 text-center text-primary-foreground"
              style={{
                boxShadow: "0 20px 60px -12px color-mix(in srgb, var(--primary) 40%, transparent)",
              }}
            >
              <h2 className="mb-4 text-3xl font-bold md:text-4xl">
                Pronto para Transformar a Sua Gestão Financeira?
              </h2>
              <p className="mb-8 text-lg opacity-90">
                Junte-se a centenas de PMEs angolanas que já automatizaram suas finanças com KWANZACONTROL
              </p>
              <Link to={ROUTE_PATHS.LOGIN}>
                <Button size="lg" variant="secondary" className="gap-2 text-base">
                  Começar Teste Grátis
                  <ArrowRight className="h-5 w-5" />
                </Button>
              </Link>
            </motion.div>
          </div>
        </section>
      </main>

      <footer className="border-t border-border bg-muted/30 py-12">
        <div className="container mx-auto px-4">
          <div className="grid gap-8 md:grid-cols-4">
            <div>
              <div className="mb-4 flex items-center gap-2">
                <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-gradient-to-br from-primary to-primary/80">
                  <BarChart3 className="h-5 w-5 text-primary-foreground" />
                </div>
                <span className="font-bold">KWANZACONTROL</span>
              </div>
              <p className="text-sm text-muted-foreground">
                CFO Digital Inteligente para PMEs Angolanas
              </p>
            </div>

            <div>
              <h4 className="mb-4 font-semibold">Produto</h4>
              <ul className="space-y-2 text-sm text-muted-foreground">
                <li>
                  <a href="#features" className="hover:text-foreground transition-colors">
                    Funcionalidades
                  </a>
                </li>
                <li>
                  <a href="#pricing" className="hover:text-foreground transition-colors">
                    Preços
                  </a>
                </li>
                <li>
                  <a href="#" className="hover:text-foreground transition-colors">
                    Demonstração
                  </a>
                </li>
              </ul>
            </div>

            <div>
              <h4 className="mb-4 font-semibold">Empresa</h4>
              <ul className="space-y-2 text-sm text-muted-foreground">
                <li>
                  <a href="#" className="hover:text-foreground transition-colors">
                    Sobre Nós
                  </a>
                </li>
                <li>
                  <a href="#" className="hover:text-foreground transition-colors">
                    Contacto
                  </a>
                </li>
                <li>
                  <a href="#" className="hover:text-foreground transition-colors">
                    Carreiras
                  </a>
                </li>
              </ul>
            </div>

            <div>
              <h4 className="mb-4 font-semibold">Legal</h4>
              <ul className="space-y-2 text-sm text-muted-foreground">
                <li>
                  <a href="#" className="hover:text-foreground transition-colors">
                    Privacidade
                  </a>
                </li>
                <li>
                  <a href="#" className="hover:text-foreground transition-colors">
                    Termos de Uso
                  </a>
                </li>
                <li>
                  <a href="#" className="hover:text-foreground transition-colors">
                    Conformidade AGT
                  </a>
                </li>
              </ul>
            </div>
          </div>

          <div className="mt-12 border-t border-border pt-8 text-center text-sm text-muted-foreground">
            <p>© 2026 KWANZACONTROL. Todos os direitos reservados.</p>
          </div>
        </div>
      </footer>
    </div>
  );
}
