// =====================================================
// KWANZACONTROL - Export Service
// Serviço para exportação de dados (PDF/Excel)
// Data: 2026-04-04
// =====================================================

import jsPDF from 'jspdf';
import autoTable from 'jspdf-autotable';
import * as XLSX from 'xlsx';

export interface ExportData {
  headers: string[];
  rows: any[][];
  title: string;
  filename: string;
}

export const exportService = {
  /**
   * Exportar para PDF
   */
  exportToPDF(data: ExportData) {
    const doc = new jsPDF();
    
    // Título
    doc.setFontSize(18);
    doc.text(data.title, 14, 20);
    
    // Data de exportação
    doc.setFontSize(10);
    doc.text(`Exportado em: ${new Date().toLocaleString('pt-AO')}`, 14, 28);
    
    // Tabela
    autoTable(doc, {
      head: [data.headers],
      body: data.rows,
      startY: 35,
      styles: {
        fontSize: 8,
        cellPadding: 3,
      },
      headStyles: {
        fillColor: [41, 128, 185],
        textColor: 255,
        fontStyle: 'bold',
      },
      alternateRowStyles: {
        fillColor: [245, 245, 245],
      },
    });
    
    // Rodapé
    const pageCount = (doc as any).internal.getNumberOfPages();
    for (let i = 1; i <= pageCount; i++) {
      doc.setPage(i);
      doc.setFontSize(8);
      doc.text(
        `Página ${i} de ${pageCount}`,
        doc.internal.pageSize.getWidth() / 2,
        doc.internal.pageSize.getHeight() - 10,
        { align: 'center' }
      );
      doc.text(
        '© 2026 KWANZACONTROL',
        14,
        doc.internal.pageSize.getHeight() - 10
      );
    }
    
    // Download
    doc.save(`${data.filename}.pdf`);
  },

  /**
   * Exportar para Excel
   */
  exportToExcel(data: ExportData) {
    // Criar workbook
    const wb = XLSX.utils.book_new();
    
    // Criar worksheet com headers e dados
    const wsData = [data.headers, ...data.rows];
    const ws = XLSX.utils.aoa_to_sheet(wsData);
    
    // Estilizar headers (largura das colunas)
    const colWidths = data.headers.map(() => ({ wch: 20 }));
    ws['!cols'] = colWidths;
    
    // Adicionar worksheet ao workbook
    XLSX.utils.book_append_sheet(wb, ws, 'Dados');
    
    // Adicionar sheet de informações
    const infoData = [
      ['Título', data.title],
      ['Data de Exportação', new Date().toLocaleString('pt-AO')],
      ['Total de Registros', data.rows.length.toString()],
      ['Sistema', 'KWANZACONTROL'],
      ['Versão', '1.0.0'],
    ];
    const wsInfo = XLSX.utils.aoa_to_sheet(infoData);
    XLSX.utils.book_append_sheet(wb, wsInfo, 'Informações');
    
    // Download
    XLSX.writeFile(wb, `${data.filename}.xlsx`);
  },

  /**
   * Exportar para CSV
   */
  exportToCSV(data: ExportData) {
    // Criar CSV
    const csvContent = [
      data.headers.join(','),
      ...data.rows.map(row => row.map(cell => `"${cell}"`).join(',')),
    ].join('\n');
    
    // Criar blob e download
    const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = `${data.filename}.csv`;
    link.click();
  },

  /**
   * Preparar dados de auditoria para exportação
   */
  prepareAuditData(logs: any[]): ExportData {
    return {
      title: 'Logs de Auditoria - KWANZACONTROL',
      filename: `audit_logs_${new Date().toISOString().split('T')[0]}`,
      headers: ['Data/Hora', 'Utilizador', 'Ação', 'Recurso', 'IP', 'Status'],
      rows: logs.map(log => [
        new Date(log.created_at).toLocaleString('pt-AO'),
        log.user_email || log.user_id,
        log.action,
        log.resource_type || '-',
        log.ip_address || '-',
        log.status || 'success',
      ]),
    };
  },

  /**
   * Preparar dados de utilizadores para exportação
   */
  prepareUsersData(users: any[]): ExportData {
    return {
      title: 'Utilizadores - KWANZACONTROL',
      filename: `users_${new Date().toISOString().split('T')[0]}`,
      headers: ['Nome', 'Email', 'Roles', 'Status', 'Criado em'],
      rows: users.map(user => [
        user.full_name,
        user.email,
        user.user_roles?.map((ur: any) => ur.roles?.display_name).join(', ') || 'Sem roles',
        user.is_active ? 'Ativo' : 'Inativo',
        new Date(user.created_at).toLocaleDateString('pt-AO'),
      ]),
    };
  },

  /**
   * Preparar dados de aprovações para exportação
   */
  prepareApprovalsData(approvals: any[]): ExportData {
    return {
      title: 'Aprovações - KWANZACONTROL',
      filename: `approvals_${new Date().toISOString().split('T')[0]}`,
      headers: ['Data', 'Solicitante', 'Ação', 'Status', 'Aprovador', 'Comentário'],
      rows: approvals.map(approval => [
        new Date(approval.created_at).toLocaleString('pt-AO'),
        approval.requester_email || approval.requester_id,
        approval.action_type,
        approval.status,
        approval.approver_email || '-',
        approval.comment || '-',
      ]),
    };
  },
};
