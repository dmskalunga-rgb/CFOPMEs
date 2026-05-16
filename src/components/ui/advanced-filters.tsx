// Advanced Filters Component
import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover';
import { Filter, X } from 'lucide-react';
import { Badge } from '@/components/ui/badge';

export interface FilterConfig {
  key: string;
  label: string;
  type: 'text' | 'select' | 'date' | 'number';
  options?: { label: string; value: string }[];
  placeholder?: string;
}

interface AdvancedFiltersProps {
  filters: FilterConfig[];
  onFiltersChange: (filters: Record<string, string>) => void;
  activeFilters: Record<string, string>;
}

export function AdvancedFilters({
  filters,
  onFiltersChange,
  activeFilters,
}: AdvancedFiltersProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [localFilters, setLocalFilters] = useState(activeFilters);

  const activeFilterCount = Object.keys(activeFilters).filter(
    (key) => activeFilters[key]
  ).length;

  const handleApply = () => {
    onFiltersChange(localFilters);
    setIsOpen(false);
  };

  const handleClear = () => {
    setLocalFilters({});
    onFiltersChange({});
    setIsOpen(false);
  };

  const handleRemoveFilter = (key: string) => {
    const newFilters = { ...activeFilters };
    delete newFilters[key];
    onFiltersChange(newFilters);
    setLocalFilters(newFilters);
  };

  return (
    <div className="flex items-center gap-2">
      <Popover open={isOpen} onOpenChange={setIsOpen}>
        <PopoverTrigger asChild>
          <Button variant="outline" size="sm">
            <Filter className="h-4 w-4 mr-2" />
            Filtros
            {activeFilterCount > 0 && (
              <Badge variant="secondary" className="ml-2">
                {activeFilterCount}
              </Badge>
            )}
          </Button>
        </PopoverTrigger>
        <PopoverContent className="w-80" align="start">
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <h4 className="font-medium">Filtros Avançados</h4>
              <Button variant="ghost" size="sm" onClick={handleClear}>
                Limpar
              </Button>
            </div>

            <div className="space-y-3">
              {filters.map((filter) => (
                <div key={filter.key} className="space-y-2">
                  <Label htmlFor={filter.key}>{filter.label}</Label>
                  {filter.type === 'text' && (
                    <Input
                      id={filter.key}
                      placeholder={filter.placeholder}
                      value={localFilters[filter.key] || ''}
                      onChange={(e) =>
                        setLocalFilters({ ...localFilters, [filter.key]: e.target.value })
                      }
                    />
                  )}
                  {filter.type === 'select' && filter.options && (
                    <Select
                      value={localFilters[filter.key] || ''}
                      onValueChange={(value) =>
                        setLocalFilters({ ...localFilters, [filter.key]: value })
                      }
                    >
                      <SelectTrigger id={filter.key}>
                        <SelectValue placeholder={filter.placeholder} />
                      </SelectTrigger>
                      <SelectContent>
                        {filter.options.map((option) => (
                          <SelectItem key={option.value} value={option.value}>
                            {option.label}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  )}
                  {filter.type === 'date' && (
                    <Input
                      id={filter.key}
                      type="date"
                      value={localFilters[filter.key] || ''}
                      onChange={(e) =>
                        setLocalFilters({ ...localFilters, [filter.key]: e.target.value })
                      }
                    />
                  )}
                  {filter.type === 'number' && (
                    <Input
                      id={filter.key}
                      type="number"
                      placeholder={filter.placeholder}
                      value={localFilters[filter.key] || ''}
                      onChange={(e) =>
                        setLocalFilters({ ...localFilters, [filter.key]: e.target.value })
                      }
                    />
                  )}
                </div>
              ))}
            </div>

            <Button onClick={handleApply} className="w-full">
              Aplicar Filtros
            </Button>
          </div>
        </PopoverContent>
      </Popover>

      {/* Active Filters Display */}
      {activeFilterCount > 0 && (
        <div className="flex items-center gap-2 flex-wrap">
          {Object.entries(activeFilters)
            .filter(([_, value]) => value)
            .map(([key, value]) => {
              const filter = filters.find((f) => f.key === key);
              const label = filter?.label || key;
              const displayValue =
                filter?.type === 'select'
                  ? filter.options?.find((o) => o.value === value)?.label || value
                  : value;

              return (
                <Badge key={key} variant="secondary" className="gap-1">
                  <span className="text-xs">
                    {label}: {displayValue}
                  </span>
                  <button
                    onClick={() => handleRemoveFilter(key)}
                    className="ml-1 hover:bg-secondary-foreground/20 rounded-full p-0.5"
                  >
                    <X className="h-3 w-3" />
                  </button>
                </Badge>
              );
            })}
        </div>
      )}
    </div>
  );
}

// Hook for filters
export function useFilters<T>(
  data: T[],
  filterFn: (item: T, filters: Record<string, string>) => boolean
) {
  const [filters, setFilters] = useState<Record<string, string>>({});

  const filteredData = data.filter((item) => filterFn(item, filters));

  return {
    filters,
    setFilters,
    filteredData,
  };
}
