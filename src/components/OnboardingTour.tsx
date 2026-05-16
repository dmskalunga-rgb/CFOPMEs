// Onboarding Tour - Tour guiado para novos usuários
import { useState, useEffect } from 'react';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { X, ChevronLeft, ChevronRight } from 'lucide-react';
import { cn } from '@/lib/utils';

interface TourStep {
  target: string; // CSS selector
  title: string;
  description: string;
  position?: 'top' | 'bottom' | 'left' | 'right';
}

interface OnboardingTourProps {
  steps: TourStep[];
  onComplete: () => void;
  onSkip: () => void;
}

export function OnboardingTour({ steps, onComplete, onSkip }: OnboardingTourProps) {
  const [currentStep, setCurrentStep] = useState(0);
  const [position, setPosition] = useState({ top: 0, left: 0 });
  const [isVisible, setIsVisible] = useState(true);

  const step = steps[currentStep];

  useEffect(() => {
    if (!step) return;

    const element = document.querySelector(step.target);
    if (!element) return;

    const rect = element.getBoundingClientRect();
    const scrollTop = window.pageYOffset || document.documentElement.scrollTop;
    const scrollLeft = window.pageXOffset || document.documentElement.scrollLeft;

    // Highlight element
    element.classList.add('onboarding-highlight');

    // Calculate position
    let top = rect.top + scrollTop;
    let left = rect.left + scrollLeft;

    switch (step.position || 'bottom') {
      case 'top':
        top = rect.top + scrollTop - 200;
        left = rect.left + scrollLeft + rect.width / 2 - 150;
        break;
      case 'bottom':
        top = rect.bottom + scrollTop + 20;
        left = rect.left + scrollLeft + rect.width / 2 - 150;
        break;
      case 'left':
        top = rect.top + scrollTop + rect.height / 2 - 100;
        left = rect.left + scrollLeft - 320;
        break;
      case 'right':
        top = rect.top + scrollTop + rect.height / 2 - 100;
        left = rect.right + scrollLeft + 20;
        break;
    }

    setPosition({ top, left });

    // Scroll to element
    element.scrollIntoView({ behavior: 'smooth', block: 'center' });

    return () => {
      element.classList.remove('onboarding-highlight');
    };
  }, [currentStep, step]);

  const handleNext = () => {
    if (currentStep < steps.length - 1) {
      setCurrentStep(currentStep + 1);
    } else {
      handleComplete();
    }
  };

  const handlePrevious = () => {
    if (currentStep > 0) {
      setCurrentStep(currentStep - 1);
    }
  };

  const handleComplete = () => {
    setIsVisible(false);
    onComplete();
  };

  const handleSkip = () => {
    setIsVisible(false);
    onSkip();
  };

  if (!isVisible || !step) return null;

  return (
    <>
      {/* Overlay */}
      <div className="fixed inset-0 bg-black/50 z-40" />

      {/* Tour Card */}
      <Card
        className="fixed z-50 w-80 shadow-lg"
        style={{ top: position.top, left: position.left }}
      >
        <CardContent className="p-6">
          <div className="flex items-start justify-between mb-4">
            <div className="flex-1">
              <h3 className="font-semibold text-lg mb-1">{step.title}</h3>
              <p className="text-sm text-muted-foreground">{step.description}</p>
            </div>
            <Button
              variant="ghost"
              size="icon"
              className="h-6 w-6 -mt-1 -mr-1"
              onClick={handleSkip}
            >
              <X className="h-4 w-4" />
            </Button>
          </div>

          <div className="flex items-center justify-between">
            <div className="flex gap-1">
              {steps.map((_, index) => (
                <div
                  key={index}
                  className={cn(
                    'h-1.5 w-8 rounded-full transition-colors',
                    index === currentStep ? 'bg-primary' : 'bg-muted'
                  )}
                />
              ))}
            </div>

            <div className="flex gap-2">
              {currentStep > 0 && (
                <Button variant="outline" size="sm" onClick={handlePrevious}>
                  <ChevronLeft className="h-4 w-4" />
                </Button>
              )}
              <Button size="sm" onClick={handleNext}>
                {currentStep < steps.length - 1 ? (
                  <>
                    Próximo
                    <ChevronRight className="h-4 w-4 ml-1" />
                  </>
                ) : (
                  'Concluir'
                )}
              </Button>
            </div>
          </div>

          <div className="mt-4 text-center">
            <button
              onClick={handleSkip}
              className="text-xs text-muted-foreground hover:text-foreground transition-colors"
            >
              Pular tour
            </button>
          </div>
        </CardContent>
      </Card>

      {/* CSS for highlight */}
      <style>{`
        .onboarding-highlight {
          position: relative;
          z-index: 45;
          box-shadow: 0 0 0 4px rgba(59, 130, 246, 0.5);
          border-radius: 8px;
        }
      `}</style>
    </>
  );
}

// Hook to manage onboarding
export function useOnboarding(tourId: string, steps: TourStep[]) {
  const [showTour, setShowTour] = useState(false);

  useEffect(() => {
    const hasCompletedTour = localStorage.getItem(`onboarding-${tourId}`);
    if (!hasCompletedTour) {
      // Show tour after a short delay
      const timer = setTimeout(() => setShowTour(true), 1000);
      return () => clearTimeout(timer);
    }
  }, [tourId]);

  const handleComplete = () => {
    localStorage.setItem(`onboarding-${tourId}`, 'true');
    setShowTour(false);
  };

  const handleSkip = () => {
    localStorage.setItem(`onboarding-${tourId}`, 'true');
    setShowTour(false);
  };

  const resetTour = () => {
    localStorage.removeItem(`onboarding-${tourId}`);
    setShowTour(true);
  };

  return {
    showTour,
    handleComplete,
    handleSkip,
    resetTour,
    TourComponent: showTour ? (
      <OnboardingTour steps={steps} onComplete={handleComplete} onSkip={handleSkip} />
    ) : null,
  };
}
