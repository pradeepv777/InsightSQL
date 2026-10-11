export interface VisualizationConfig {
  type: string;
  x: string;
  y: string;
}

export interface ResultSection {
  title: string;
  columns: string[];
  rows: any[][];
  row_count: number;
}

export interface MessageItem {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  sql?: string | null;
  columns?: string[];
  rows?: any[][];
  row_count?: number;
  sections?: ResultSection[] | null;
  visualization?: VisualizationConfig | null;
  status?: string;
  clarification_question?: string | null;
  warnings?: string[];
}

export interface ActiveDatasetInfo {
  id: string;
  name: string;
  rowCount: number;
}
