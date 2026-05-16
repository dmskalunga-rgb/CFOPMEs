// Export Utilities - CSV, Excel, PDF
import { toast } from 'sonner';

// ============================================
// CSV EXPORT
// ============================================
export function exportToCSV<T extends Record<string, any>>(
  data: T[],
  filename: string,
  columns?: { key: keyof T; label: string }[]
) {
  try {
    if (data.length === 0) {
      toast.error('Nenhum dado para exportar');
      return;
    }

    // Determine columns
    const cols = columns || Object.keys(data[0]).map(key => ({ key, label: key }));

    // Create CSV header
    const header = cols.map(col => col.label).join(',');

    // Create CSV rows
    const rows = data.map(item =>
      cols.map(col => {
        const value = item[col.key];
        // Handle values with commas, quotes, or newlines
        if (value === null || value === undefined) return '';
        const stringValue = String(value);
        if (stringValue.includes(',') || stringValue.includes('"') || stringValue.includes('\n')) {
          return `"${stringValue.replace(/"/g, '""')}"`;
        }
        return stringValue;
      }).join(',')
    );

    // Combine header and rows
    const csv = [header, ...rows].join('\n');

    // Create blob and download
    const blob = new Blob(['\ufeff' + csv], { type: 'text/csv;charset=utf-8;' });
    const link = document.createElement('a');
    const url = URL.createObjectURL(blob);
    link.setAttribute('href', url);
    link.setAttribute('download', `${filename}.csv`);
    link.style.visibility = 'hidden';
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);

    toast.success('CSV exportado com sucesso!');
  } catch (error) {
    console.error('Error exporting CSV:', error);
    toast.error('Erro ao exportar CSV');
  }
}

// ============================================
// EXCEL EXPORT (using HTML table method)
// ============================================
export function exportToExcel<T extends Record<string, any>>(
  data: T[],
  filename: string,
  columns?: { key: keyof T; label: string }[]
) {
  try {
    if (data.length === 0) {
      toast.error('Nenhum dado para exportar');
      return;
    }

    // Determine columns
    const cols = columns || Object.keys(data[0]).map(key => ({ key, label: key }));

    // Create HTML table
    let html = '<html><head><meta charset="utf-8"></head><body><table border="1">';
    
    // Header
    html += '<thead><tr>';
    cols.forEach(col => {
      html += `<th>${col.label}</th>`;
    });
    html += '</tr></thead>';

    // Body
    html += '<tbody>';
    data.forEach(item => {
      html += '<tr>';
      cols.forEach(col => {
        const value = item[col.key];
        html += `<td>${value !== null && value !== undefined ? value : ''}</td>`;
      });
      html += '</tr>';
    });
    html += '</tbody></table></body></html>';

    // Create blob and download
    const blob = new Blob([html], { type: 'application/vnd.ms-excel' });
    const link = document.createElement('a');
    const url = URL.createObjectURL(blob);
    link.setAttribute('href', url);
    link.setAttribute('download', `${filename}.xls`);
    link.style.visibility = 'hidden';
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);

    toast.success('Excel exportado com sucesso!');
  } catch (error) {
    console.error('Error exporting Excel:', error);
    toast.error('Erro ao exportar Excel');
  }
}

// ============================================
// PDF EXPORT (using HTML to PDF conversion)
// ============================================
export function exportToPDF<T extends Record<string, any>>(
  data: T[],
  filename: string,
  title: string,
  columns?: { key: keyof T; label: string }[]
) {
  try {
    if (data.length === 0) {
      toast.error('Nenhum dado para exportar');
      return;
    }

    // Determine columns
    const cols = columns || Object.keys(data[0]).map(key => ({ key, label: key }));

    // Create HTML content
    const html = `
<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <style>
    body {
      font-family: Arial, sans-serif;
      margin: 40px;
    }
    .header {
      text-align: center;
      margin-bottom: 30px;
    }
    .company {
      font-size: 24px;
      font-weight: bold;
      color: #2563eb;
    }
    h1 {
      color: #333;
      margin: 20px 0;
    }
    table {
      width: 100%;
      border-collapse: collapse;
      margin: 20px 0;
    }
    th, td {
      border: 1px solid #ddd;
      padding: 10px;
      text-align: left;
    }
    th {
      background-color: #2563eb;
      color: white;
      font-weight: bold;
    }
    tr:nth-child(even) {
      background-color: #f9fafb;
    }
    .footer {
      margin-top: 50px;
      text-align: center;
      color: #666;
      font-size: 12px;
    }
  </style>
</head>
<body>
  <div class="header">
    <div class="company">KwanzaControl</div>
    <div>Sistema de Gestão Empresarial</div>
  </div>

  <h1>${title}</h1>
  <p>Data de Geração: ${new Date().toLocaleString('pt-AO')}</p>

  <table>
    <thead>
      <tr>
        ${cols.map(col => `<th>${col.label}</th>`).join('')}
      </tr>
    </thead>
    <tbody>
      ${data.map(item => `
        <tr>
          ${cols.map(col => {
            const value = item[col.key];
            return `<td>${value !== null && value !== undefined ? value : ''}</td>`;
          }).join('')}
        </tr>
      `).join('')}
    </tbody>
  </table>

  <div class="footer">
    <p>© 2026 KwanzaControl - Todos os direitos reservados</p>
    <p>Total de registros: ${data.length}</p>
  </div>
</body>
</html>
    `;

    // Open print dialog
    const printWindow = window.open('', '_blank');
    if (printWindow) {
      printWindow.document.write(html);
      printWindow.document.close();
      printWindow.focus();
      
      // Wait for content to load then print
      setTimeout(() => {
        printWindow.print();
        toast.success('PDF gerado! Use "Salvar como PDF" na janela de impressão.');
      }, 250);
    } else {
      toast.error('Erro ao abrir janela de impressão. Verifique se pop-ups estão bloqueados.');
    }
  } catch (error) {
    console.error('Error exporting PDF:', error);
    toast.error('Erro ao exportar PDF');
  }
}

// ============================================
// EXPORT BUTTON COMPONENT
// ============================================
import { Button } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Download, FileSpreadsheet, FileText, File } from 'lucide-react';

interface ExportButtonProps<T extends Record<string, any>> {
  data: T[];
  filename: string;
  title?: string;
  columns?: { key: keyof T; label: string }[];
  variant?: 'default' | 'outline' | 'ghost';
  size?: 'default' | 'sm' | 'lg';
}

export function ExportButton<T extends Record<string, any>>({
  data,
  filename,
  title = 'Relatório',
  columns,
  variant = 'outline',
  size = 'sm',
}: ExportButtonProps<T>) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant={variant} size={size}>
          <Download className="h-4 w-4 mr-2" />
          Exportar
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuItem onClick={() => exportToCSV(data, filename, columns)}>
          <File className="h-4 w-4 mr-2" />
          Exportar CSV
        </DropdownMenuItem>
        <DropdownMenuItem onClick={() => exportToExcel(data, filename, columns)}>
          <FileSpreadsheet className="h-4 w-4 mr-2" />
          Exportar Excel
        </DropdownMenuItem>
        <DropdownMenuItem onClick={() => exportToPDF(data, filename, title, columns)}>
          <FileText className="h-4 w-4 mr-2" />
          Exportar PDF
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
