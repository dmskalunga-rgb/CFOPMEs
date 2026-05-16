/**
 * Bank Reconciliation Service
 * Serviço de conciliação bancária automática
 */

import { supabase } from '@/integrations/supabase/client';

export interface BankStatement {
  id: string;
  accountId: string;
  date: Date;
  description: string;
  reference: string;
  debit: number;
  credit: number;
  balance: number;
  reconciled: boolean;
  matchedTransactionId?: string;
}

export interface ReconciliationMatch {
  statementId: string;
  transactionId: string;
  matchScore: number;
  matchType: 'exact' | 'fuzzy' | 'manual';
  differences: string[];
}

export interface ReconciliationReport {
  accountId: string;
  period: { start: Date; end: Date };
  openingBalance: number;
  closingBalance: number;
  totalDebits: number;
  totalCredits: number;
  reconciledItems: number;
  unreconciledItems: number;
  discrepancies: ReconciliationDiscrepancy[];
  matchedTransactions: ReconciliationMatch[];
}

export interface ReconciliationDiscrepancy {
  id: string;
  type: 'missing_transaction' | 'duplicate' | 'amount_mismatch' | 'date_mismatch';
  severity: 'low' | 'medium' | 'high';
  description: string;
  suggestedAction: string;
  amount?: number;
}

class BankReconciliationService {
  /**
   * Importar extrato bancário
   */
  async importBankStatement(
    accountId: string,
    statements: Omit<BankStatement, 'id' | 'reconciled'>[]
  ): Promise<BankStatement[]> {
    try {
      const { data, error } = await supabase
        .from('bank_statements')
        .insert(
          statements.map((stmt) => ({
            account_id: accountId,
            date: stmt.date,
            description: stmt.description,
            reference: stmt.reference,
            debit: stmt.debit,
            credit: stmt.credit,
            balance: stmt.balance,
            reconciled: false,
          }))
        )
        .select();

      if (error) throw error;

      return (data || []).map(this.mapBankStatement);
    } catch (error) {
      console.error('Erro ao importar extrato:', error);
      throw error;
    }
  }

  /**
   * Conciliar automaticamente
   */
  async autoReconcile(accountId: string, period: { start: Date; end: Date }): Promise<ReconciliationMatch[]> {
    try {
      // Buscar extratos não conciliados
      const { data: statements, error: stmtError } = await supabase
        .from('bank_statements')
        .select('*')
        .eq('account_id', accountId)
        .eq('reconciled', false)
        .gte('date', period.start.toISOString())
        .lte('date', period.end.toISOString());

      if (stmtError) throw stmtError;

      // Buscar transações não conciliadas
      const { data: transactions, error: txnError } = await supabase
        .from('transactions')
        .select('*')
        .eq('reconciled', false)
        .gte('date', period.start.toISOString())
        .lte('date', period.end.toISOString());

      if (txnError) throw txnError;

      const matches: ReconciliationMatch[] = [];

      // Algoritmo de matching
      (statements || []).forEach((stmt: any) => {
        const match = this.findBestMatch(stmt, transactions || []);
        if (match) {
          matches.push(match);
        }
      });

      // Marcar como conciliados
      for (const match of matches) {
        await this.markAsReconciled(match.statementId, match.transactionId);
      }

      return matches;
    } catch (error) {
      console.error('Erro na conciliação automática:', error);
      return [];
    }
  }

  /**
   * Conciliar manualmente
   */
  async manualReconcile(statementId: string, transactionId: string): Promise<ReconciliationMatch> {
    try {
      await this.markAsReconciled(statementId, transactionId);

      return {
        statementId,
        transactionId,
        matchScore: 1.0,
        matchType: 'manual',
        differences: [],
      };
    } catch (error) {
      console.error('Erro na conciliação manual:', error);
      throw error;
    }
  }

  /**
   * Gerar relatório de conciliação
   */
  async generateReconciliationReport(
    accountId: string,
    period: { start: Date; end: Date }
  ): Promise<ReconciliationReport> {
    try {
      // Buscar extratos do período
      const { data: statements, error: stmtError } = await supabase
        .from('bank_statements')
        .select('*')
        .eq('account_id', accountId)
        .gte('date', period.start.toISOString())
        .lte('date', period.end.toISOString())
        .order('date', { ascending: true });

      if (stmtError) throw stmtError;

      const stmts = statements || [];
      const openingBalance = stmts[0]?.balance || 0;
      const closingBalance = stmts[stmts.length - 1]?.balance || 0;
      const totalDebits = stmts.reduce((sum: number, s: any) => sum + s.debit, 0);
      const totalCredits = stmts.reduce((sum: number, s: any) => sum + s.credit, 0);
      const reconciledItems = stmts.filter((s: any) => s.reconciled).length;
      const unreconciledItems = stmts.length - reconciledItems;

      // Detectar discrepâncias
      const discrepancies = await this.detectDiscrepancies(accountId, period);

      // Buscar matches
      const matches = stmts
        .filter((s: any) => s.reconciled && s.matched_transaction_id)
        .map((s: any): ReconciliationMatch => ({
          statementId: s.id,
          transactionId: s.matched_transaction_id,
          matchScore: 1.0,
          matchType: 'exact' as const,
          differences: [],
        }));

      return {
        accountId,
        period,
        openingBalance,
        closingBalance,
        totalDebits,
        totalCredits,
        reconciledItems,
        unreconciledItems,
        discrepancies,
        matchedTransactions: matches,
      };
    } catch (error) {
      console.error('Erro ao gerar relatório:', error);
      throw error;
    }
  }

  /**
   * Detectar discrepâncias
   */
  private async detectDiscrepancies(
    accountId: string,
    period: { start: Date; end: Date }
  ): Promise<ReconciliationDiscrepancy[]> {
    const discrepancies: ReconciliationDiscrepancy[] = [];

    try {
      // Buscar extratos não conciliados
      const { data: unreconciledStatements } = await supabase
        .from('bank_statements')
        .select('*')
        .eq('account_id', accountId)
        .eq('reconciled', false)
        .gte('date', period.start.toISOString())
        .lte('date', period.end.toISOString());

      (unreconciledStatements || []).forEach((stmt: any) => {
        discrepancies.push({
          id: `disc-${stmt.id}`,
          type: 'missing_transaction',
          severity: stmt.debit > 100000 || stmt.credit > 100000 ? 'high' : 'medium',
          description: `Transação bancária sem correspondência: ${stmt.description}`,
          suggestedAction: 'Verificar se a transação foi registrada no sistema',
          amount: stmt.debit || stmt.credit,
        });
      });

      // Buscar transações não conciliadas
      const { data: unreconciledTransactions } = await supabase
        .from('transactions')
        .select('*')
        .eq('reconciled', false)
        .gte('date', period.start.toISOString())
        .lte('date', period.end.toISOString());

      (unreconciledTransactions || []).forEach((txn: any) => {
        discrepancies.push({
          id: `disc-txn-${txn.id}`,
          type: 'missing_transaction',
          severity: txn.amount > 100000 ? 'high' : 'medium',
          description: `Transação do sistema sem correspondência bancária: ${txn.description}`,
          suggestedAction: 'Verificar se a transação foi processada pelo banco',
          amount: txn.amount,
        });
      });
    } catch (error) {
      console.error('Erro ao detectar discrepâncias:', error);
    }

    return discrepancies;
  }

  /**
   * Encontrar melhor correspondência
   */
  private findBestMatch(statement: any, transactions: any[]): ReconciliationMatch | null {
    let bestMatch: ReconciliationMatch | null = null;
    let bestScore = 0;

    transactions.forEach((txn) => {
      const score = this.calculateMatchScore(statement, txn);
      if (score > bestScore && score >= 0.8) {
        bestScore = score;
        bestMatch = {
          statementId: statement.id,
          transactionId: txn.id,
          matchScore: score,
          matchType: score === 1.0 ? 'exact' : 'fuzzy',
          differences: this.findDifferences(statement, txn),
        };
      }
    });

    return bestMatch;
  }

  /**
   * Calcular score de correspondência
   */
  private calculateMatchScore(statement: any, transaction: any): number {
    let score = 0;
    let factors = 0;

    // Comparar valor
    const stmtAmount = statement.debit || statement.credit;
    if (Math.abs(stmtAmount - transaction.amount) < 0.01) {
      score += 0.5;
    }
    factors++;

    // Comparar data (±3 dias)
    const stmtDate = new Date(statement.date);
    const txnDate = new Date(transaction.date);
    const daysDiff = Math.abs((stmtDate.getTime() - txnDate.getTime()) / (1000 * 60 * 60 * 24));
    if (daysDiff <= 3) {
      score += 0.3 * (1 - daysDiff / 3);
    }
    factors++;

    // Comparar descrição (similaridade)
    const similarity = this.calculateStringSimilarity(
      statement.description.toLowerCase(),
      transaction.description.toLowerCase()
    );
    score += 0.2 * similarity;
    factors++;

    return score / factors;
  }

  /**
   * Calcular similaridade entre strings
   */
  private calculateStringSimilarity(str1: string, str2: string): number {
    const longer = str1.length > str2.length ? str1 : str2;
    const shorter = str1.length > str2.length ? str2 : str1;

    if (longer.length === 0) return 1.0;

    const editDistance = this.levenshteinDistance(longer, shorter);
    return (longer.length - editDistance) / longer.length;
  }

  /**
   * Distância de Levenshtein
   */
  private levenshteinDistance(str1: string, str2: string): number {
    const matrix: number[][] = [];

    for (let i = 0; i <= str2.length; i++) {
      matrix[i] = [i];
    }

    for (let j = 0; j <= str1.length; j++) {
      matrix[0][j] = j;
    }

    for (let i = 1; i <= str2.length; i++) {
      for (let j = 1; j <= str1.length; j++) {
        if (str2.charAt(i - 1) === str1.charAt(j - 1)) {
          matrix[i][j] = matrix[i - 1][j - 1];
        } else {
          matrix[i][j] = Math.min(
            matrix[i - 1][j - 1] + 1,
            matrix[i][j - 1] + 1,
            matrix[i - 1][j] + 1
          );
        }
      }
    }

    return matrix[str2.length][str1.length];
  }

  /**
   * Encontrar diferenças
   */
  private findDifferences(statement: any, transaction: any): string[] {
    const differences: string[] = [];

    const stmtAmount = statement.debit || statement.credit;
    if (Math.abs(stmtAmount - transaction.amount) >= 0.01) {
      differences.push(`Valor: ${stmtAmount} vs ${transaction.amount}`);
    }

    const stmtDate = new Date(statement.date).toISOString().slice(0, 10);
    const txnDate = new Date(transaction.date).toISOString().slice(0, 10);
    if (stmtDate !== txnDate) {
      differences.push(`Data: ${stmtDate} vs ${txnDate}`);
    }

    return differences;
  }

  /**
   * Marcar como conciliado
   */
  private async markAsReconciled(statementId: string, transactionId: string): Promise<void> {
    // Atualizar extrato
    await supabase
      .from('bank_statements')
      .update({
        reconciled: true,
        matched_transaction_id: transactionId,
      })
      .eq('id', statementId);

    // Atualizar transação
    await supabase
      .from('transactions')
      .update({
        reconciled: true,
        matched_statement_id: statementId,
      })
      .eq('id', transactionId);
  }

  private mapBankStatement(data: any): BankStatement {
    return {
      id: data.id,
      accountId: data.account_id,
      date: new Date(data.date),
      description: data.description,
      reference: data.reference,
      debit: data.debit,
      credit: data.credit,
      balance: data.balance,
      reconciled: data.reconciled,
      matchedTransactionId: data.matched_transaction_id,
    };
  }
}

export const bankReconciliationService = new BankReconciliationService();
