import React, { useState, useEffect, useRef } from 'react';
import { ArrowUp, Plus, ChevronDown, ChevronRight, Copy, Check, UploadCloud, AlertCircle, Loader2, Table } from 'lucide-react';

interface VisualizationConfig {
  type: string;
  x: string;
  y: string;
}

interface ResultSection {
  title: string;
  columns: string[];
  rows: any[][];
  row_count: number;
}

interface MessageItem {
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

interface ActiveDatasetInfo {
  id: string;
  name: string;
  rowCount: number;
}

export default function App() {
  const [messages, setMessages] = useState<MessageItem[]>([]);
  const [input, setInput] = useState('');
  const [conversationId, setConversationId] = useState<string>('');
  const [activeDataset, setActiveDataset] = useState<ActiveDatasetInfo | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [isDragging, setIsDragging] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [openSqlId, setOpenSqlId] = useState<string | null>(null);
  const [isDarkMode, setIsDarkMode] = useState<boolean>(true);

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // Initialize conversation session and restore active dataset if persisted
  useEffect(() => {
    async function initSession() {
      const savedConvId = sessionStorage.getItem('active_conversation_id');
      const savedDatasetStr = sessionStorage.getItem('active_dataset');

      if (savedConvId && savedDatasetStr) {
        try {
          const parsed = JSON.parse(savedDatasetStr) as ActiveDatasetInfo;
          if (parsed && parsed.id) {
            setConversationId(savedConvId);
            setActiveDataset(parsed);

            // Fetch stored messages for this conversation
            const res = await fetch(`/api/v1/conversations/${savedConvId}/messages`);
            if (res.ok) {
              const msgs = await res.json();
              if (Array.isArray(msgs) && msgs.length > 0) {
                const restored: MessageItem[] = msgs.map((m: any) => ({
                  id: m.id,
                  role: m.role,
                  content: m.content,
                  sql: m.metadata?.sql,
                  columns: m.metadata?.columns || [],
                  rows: m.metadata?.rows || [],
                  row_count: m.metadata?.row_count || 0,
                  sections: m.metadata?.sections || null,
                  visualization: m.metadata?.visualization || null,
                  status: m.metadata?.status,
                  clarification_question: m.metadata?.clarification_question,
                  warnings: m.metadata?.warnings || [],
                }));
                setMessages(restored);
              }
            }
            return;
          }
        } catch {
          sessionStorage.removeItem('active_conversation_id');
          sessionStorage.removeItem('active_dataset');
        }
      }

      // No active dataset saved -> create fresh conversation and start in welcome state
      try {
        const res = await fetch('/api/v1/conversations', { method: 'POST' });
        if (res.ok) {
          const data = await res.json();
          setConversationId(data.id);
        } else {
          setConversationId(crypto.randomUUID());
        }
      } catch {
        setConversationId(crypto.randomUUID());
      }
      setActiveDataset(null);
      setMessages([]);
    }

    initSession();
  }, []);

  // Scroll to bottom on new messages
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, isLoading]);

  const handleNewChat = async () => {
    if (isLoading || isUploading) return;
    sessionStorage.removeItem('active_conversation_id');
    sessionStorage.removeItem('active_dataset');
    setActiveDataset(null);
    setMessages([]);
    setInput('');
    setUploadError(null);
    try {
      const res = await fetch('/api/v1/conversations', { method: 'POST' });
      if (res.ok) {
        const data = await res.json();
        setConversationId(data.id);
      } else {
        setConversationId(crypto.randomUUID());
      }
    } catch {
      setConversationId(crypto.randomUUID());
    }
  };

  const handleShowTable = async () => {
    if (isLoading || isUploading || !activeDataset || !conversationId) return;

    const userMsg: MessageItem = {
      id: crypto.randomUUID(),
      role: 'user',
      content: `Show complete table (${activeDataset.name})`,
    };

    setMessages((prev) => [...prev, userMsg]);
    setIsLoading(true);

    try {
      const res = await fetch(`/api/v1/conversations/${conversationId}/messages`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          message: `SELECT * FROM ${activeDataset.name}`,
          dataset_ids: [activeDataset.id],
          include_sql: true,
          visualization: 'auto',
        }),
      });

      if (!res.ok) {
        let errorMsg = 'Failed to load table';
        try {
          const err = await res.json();
          if (typeof err.detail === 'string') {
            errorMsg = err.detail;
          } else if (err.detail && typeof err.detail === 'object') {
            errorMsg = err.detail.message || err.detail.detail || err.detail.error || JSON.stringify(err.detail);
          } else if (err.message) {
            errorMsg = err.message;
          }
        } catch {
          errorMsg = `Request failed with HTTP status ${res.status}`;
        }
        throw new Error(errorMsg);
      }

      const data = await res.json();
      const asstMsg: MessageItem = {
        id: data.message_id || crypto.randomUUID(),
        role: 'assistant',
        content: data.answer,
        sql: data.sql,
        columns: data.columns || [],
        rows: data.rows || [],
        row_count: data.row_count || 0,
        sections: data.sections || null,
        visualization: data.visualization,
        status: data.status,
        clarification_question: data.clarification_question,
        warnings: data.warnings || [],
      };

      setMessages((prev) => [...prev, asstMsg]);
    } catch (err: any) {
      setMessages((prev) => [
        ...prev,
        {
          id: crypto.randomUUID(),
          role: 'assistant',
          content: err.message || 'An unexpected error occurred.',
          status: 'error',
        },
      ]);
    } finally {
      setIsLoading(false);
      setTimeout(() => inputRef.current?.focus(), 100);
    }
  };

  const handleSend = async () => {
    const trimmed = input.trim();
    if (!trimmed || isLoading || isUploading || !conversationId) return;

    const userMsg: MessageItem = {
      id: crypto.randomUUID(),
      role: 'user',
      content: trimmed,
    };

    setMessages((prev) => [...prev, userMsg]);
    setInput('');
    setIsLoading(true);

    try {
      const res = await fetch(`/api/v1/conversations/${conversationId}/messages`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          message: trimmed,
          dataset_ids: activeDataset ? [activeDataset.id] : undefined,
          include_sql: true,
          visualization: 'auto',
        }),
      });

      if (!res.ok) {
        let errorMsg = 'Failed to process request';
        try {
          const err = await res.json();
          if (typeof err.detail === 'string') {
            errorMsg = err.detail;
          } else if (err.detail && typeof err.detail === 'object') {
            errorMsg = err.detail.message || err.detail.detail || err.detail.error || JSON.stringify(err.detail);
          } else if (err.message) {
            errorMsg = err.message;
          }
        } catch {
          errorMsg = `Request failed with HTTP status ${res.status} (${res.statusText || 'Error'})`;
        }
        throw new Error(errorMsg);
      }

      const data = await res.json();
      const asstMsg: MessageItem = {
        id: data.message_id || crypto.randomUUID(),
        role: 'assistant',
        content: data.answer,
        sql: data.sql,
        columns: data.columns || [],
        rows: data.rows || [],
        row_count: data.row_count || 0,
        sections: data.sections || null,
        visualization: data.visualization,
        status: data.status,
        clarification_question: data.clarification_question,
        warnings: data.warnings || [],
      };

      setMessages((prev) => [...prev, asstMsg]);

      // Ensure persistence keys are current
      if (conversationId && activeDataset) {
        sessionStorage.setItem('active_conversation_id', conversationId);
        sessionStorage.setItem('active_dataset', JSON.stringify(activeDataset));
      }
    } catch (err: any) {
      setMessages((prev) => [
        ...prev,
        {
          id: crypto.randomUUID(),
          role: 'assistant',
          content: err.message || 'An unexpected error occurred.',
          status: 'error',
        },
      ]);
    } finally {
      setIsLoading(false);
      setTimeout(() => inputRef.current?.focus(), 100);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const MAX_FILE_SIZE_MB = 100;

  const processFile = async (file: File) => {
    if (!file || isUploading) return;

    if (file.size > MAX_FILE_SIZE_MB * 1024 * 1024) {
      setUploadError(`File size exceeds maximum allowed limit of ${MAX_FILE_SIZE_MB} MB.`);
      return;
    }

    setIsUploading(true);
    setUploadError(null);

    const formData = new FormData();
    formData.append('file', file);

    try {
      const res = await fetch('/api/v1/datasets', {
        method: 'POST',
        body: formData,
      });

      if (!res.ok) {
        let errDetail = 'Upload failed';
        try {
          const err = await res.json();
          errDetail = err.detail || err.message || 'Upload failed';
        } catch {
          errDetail = `Upload failed (HTTP status ${res.status})`;
        }
        throw new Error(errDetail);
      }

      const data = await res.json();
      const newDs: ActiveDatasetInfo = {
        id: data.id,
        name: data.source_filename || data.display_name,
        rowCount: data.row_count,
      };

      // Ingestion confirmed successful: transition to active chat state
      setActiveDataset(newDs);
      sessionStorage.setItem('active_dataset', JSON.stringify(newDs));
      if (conversationId) {
        sessionStorage.setItem('active_conversation_id', conversationId);
      }
      setUploadError(null);

      // If replacement during chat, announce in chat stream
      if (activeDataset && activeDataset.id !== newDs.id) {
        setMessages((prev) => [
          ...prev,
          {
            id: crypto.randomUUID(),
            role: 'system',
            content: `Switched dataset to ${newDs.name} • ${newDs.rowCount.toLocaleString()} rows ready for queries`,
          },
        ]);
      }

      setTimeout(() => inputRef.current?.focus(), 150);
    } catch (err: any) {
      const msg = err.message || 'Upload failed. Please try again.';
      setUploadError(msg);

      // If already in chat, inform user in message stream without replacing active dataset
      if (activeDataset) {
        setMessages((prev) => [
          ...prev,
          {
            id: crypto.randomUUID(),
            role: 'system',
            content: `Upload failed: ${msg}. Previous dataset preserved.`,
            status: 'error',
          },
        ]);
      }
    } finally {
      setIsUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  };

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (!isUploading) setIsDragging(true);
  };

  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(false);
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(false);
    if (isUploading) return;
    const file = e.dataTransfer.files?.[0];
    if (file) {
      processFile(file);
    }
  };

  const copyToClipboard = async (id: string, text: string) => {
    if (!text) return;
    try {
      if (navigator?.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
        setCopiedId(id);
        setTimeout(() => {
          setCopiedId((curr) => (curr === id ? null : curr));
        }, 2000);
        return;
      }
      const textArea = document.createElement('textarea');
      textArea.value = text;
      textArea.style.position = 'fixed';
      textArea.style.left = '-9999px';
      textArea.style.top = '-9999px';
      document.body.appendChild(textArea);
      textArea.focus();
      textArea.select();
      const successful = document.execCommand('copy');
      document.body.removeChild(textArea);
      if (successful) {
        setCopiedId(id);
        setTimeout(() => {
          setCopiedId((curr) => (curr === id ? null : curr));
        }, 2000);
      }
    } catch (err) {
      console.error('Failed to copy to clipboard:', err);
    }
  };

  const formatTableAsTsv = (columns: string[], rows: any[][]): string => {
    const headerLine = columns.join('\t');
    const rowLines = rows.map((row) =>
      row
        .map((cell) => {
          if (cell === null || cell === undefined) return '';
          const str = String(cell);
          if (str.includes('\t') || str.includes('\n') || str.includes('"')) {
            return `"${str.replace(/"/g, '""')}"`;
          }
          return str;
        })
        .join('\t')
    );
    return [headerLine, ...rowLines].join('\n');
  };

  const toggleSql = (id: string) => {
    setOpenSqlId((prev) => (prev === id ? null : id));
  };

  return (
    <div className={`relative w-screen h-screen p-3 md:p-4 transition-colors duration-500 ${isDarkMode ? 'pattern-grid-dark' : 'pattern-grid-light'}`}>
      {/* Outer rounded viewport container */}
      <div className={`relative w-full h-full rounded-2xl border flex flex-col overflow-hidden shadow-2xl transition-colors duration-500 ${isDarkMode ? 'border-[#27272e] pattern-grid-dark' : 'border-[#d4d4d8] pattern-grid-light'
        }`}>

        {/* Top Header Navigation Bar */}
        <div className="w-full px-5 py-3.5 flex items-start justify-between z-30 select-none flex-shrink-0">
          <div className="flex items-start gap-2.5">
            {activeDataset && (
              <div className="flex flex-col gap-1.5">
                <button
                  type="button"
                  onClick={handleNewChat}
                  disabled={isLoading || isUploading}
                  title="Start a new chat"
                  className={`flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-xl border transition-all cursor-pointer ${isDarkMode
                      ? 'border-[#27272e] bg-[#121215]/90 text-zinc-300 hover:text-white hover:border-zinc-600'
                      : 'border-zinc-200 bg-white/90 text-zinc-700 hover:text-zinc-950 hover:border-zinc-400 shadow-sm'
                    }`}
                >
                  <Plus className="w-3.5 h-3.5" />
                  <span>New Chat</span>
                </button>

                <button
                  type="button"
                  onClick={handleShowTable}
                  disabled={isLoading || isUploading}
                  title="Show complete table"
                  className={`flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-xl border transition-all cursor-pointer ${isDarkMode
                      ? 'border-[#27272e] bg-[#121215]/90 text-zinc-300 hover:text-white hover:border-zinc-600'
                      : 'border-zinc-200 bg-white/90 text-zinc-700 hover:text-zinc-950 hover:border-zinc-400 shadow-sm'
                    }`}
                >
                  <Table className="w-3.5 h-3.5" />
                  <span>Show Table</span>
                </button>
              </div>
            )}
          </div>

          {/* Right side: Table name (no green dot) & Animated theme switch toggle */}
          <div className="flex items-center gap-3">
            {activeDataset && (
              <span className={`text-xs font-medium select-none truncate max-w-[200px] sm:max-w-xs ${isDarkMode ? 'text-zinc-300' : 'text-zinc-700'
                }`}>
                {activeDataset.name}
              </span>
            )}
            <label className="theme-switch" title={isDarkMode ? "Switch to Light Mode" : "Switch to Dark Mode"}>
              <input
                type="checkbox"
                className="theme-switch__checkbox"
                checked={isDarkMode}
                onChange={(e) => setIsDarkMode(e.target.checked)}
              />
              <div className="theme-switch__container">
                <div className="theme-switch__clouds"></div>
                <div className="theme-switch__stars-container">
                  <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 144 55" fill="none">
                    <path
                      fillRule="evenodd"
                      clipRule="evenodd"
                      d="M135.831 3.00688C135.055 3.85027 134.111 4.29946 133 4.35447C134.111 4.40947 135.055 4.85867 135.831 5.71123C136.607 6.55462 136.996 7.56303 136.996 8.72727C136.996 7.95722 137.172 7.25134 137.525 6.59129C137.886 5.93124 138.372 5.39954 138.98 5.00535C139.598 4.60199 140.268 4.39114 141 4.35447C139.88 4.2903 138.936 3.85027 138.16 3.00688C137.384 2.16348 136.996 1.16425 136.996 0C136.996 1.16425 136.607 2.16348 135.831 3.00688ZM31 23.3545C32.1114 23.2995 33.0551 22.8503 33.8313 22.0069C34.6075 21.1635 34.9956 20.1642 34.9956 19C34.9956 20.1642 35.3837 21.1635 36.1599 22.0069C36.9361 22.8503 37.8798 23.2903 39 23.3545C38.2679 23.3911 37.5976 23.602 36.9802 24.0053C36.3716 24.3995 35.8864 24.9312 35.5248 25.5913C35.172 26.2513 34.9956 26.9572 34.9956 27.7273C34.9956 26.563 34.6075 25.5546 33.8313 24.7112C33.0551 23.8587 32.1114 23.4095 31 23.3545ZM0 36.3545C1.11136 36.2995 2.05513 35.8503 2.83131 35.0069C3.6075 34.1635 3.99559 33.1642 3.99559 32C3.99559 33.1642 4.38368 34.1635 5.15987 35.0069C5.93605 35.8503 6.87982 36.2903 8 36.3545C7.26792 36.3911 6.59757 36.602 5.98015 37.0053C5.37155 37.3995 4.88644 37.9312 4.52481 38.5913C4.172 39.2513 3.99559 39.9572 3.99559 40.7273C3.99559 39.563 3.6075 38.5546 2.83131 37.7112C2.05513 36.8587 1.11136 36.4095 0 36.3545ZM56.8313 24.0069C56.0551 24.8503 55.1114 25.2995 54 25.3545C55.1114 25.4095 56.0551 25.8587 56.8313 26.7112C57.6075 27.5546 57.9956 28.563 57.9956 29.7273C57.9956 28.9572 58.172 28.2513 58.5248 27.5913C58.8864 26.9312 59.3716 26.3995 59.9802 26.0053C60.5976 25.602 61.2679 25.3911 62 25.3545C60.8798 25.2903 59.9361 24.8503 59.1599 24.0069C58.3837 23.1635 57.9956 22.1642 57.9956 21C57.9956 22.1642 57.6075 23.1635 56.8313 24.0069ZM81 25.3545C82.1114 25.2995 83.0551 24.8503 83.8313 24.0069C84.6075 23.1635 84.9956 22.1642 84.9956 21C84.9956 22.1642 85.3837 23.1635 86.1599 24.0069C86.9361 24.8503 87.8798 25.2903 89 25.3545C88.2679 25.3911 87.5976 25.602 86.9802 26.0053C86.3716 26.3995 85.8864 26.9312 85.5248 27.5913C85.172 28.2513 84.9956 28.9572 84.9956 29.7273C84.9956 28.563 84.6075 27.5546 83.8313 26.7112C83.0551 25.8587 82.1114 25.4095 81 25.3545ZM136 36.3545C137.111 36.2995 138.055 35.8503 138.831 35.0069C139.607 34.1635 139.996 33.1642 139.996 32C139.996 33.1642 140.384 34.1635 141.16 35.0069C141.936 35.8503 142.88 36.2903 144 36.3545C143.268 36.3911 142.598 36.602 141.98 37.0053C141.372 37.3995 140.886 37.9312 140.525 38.5913C140.172 39.2513 139.996 39.9572 139.996 40.7273C139.996 39.563 139.607 38.5546 138.831 37.7112C138.055 36.8587 137.111 36.4095 136 36.3545ZM101.831 49.0069C101.055 49.8503 100.111 50.2995 99 50.3545C100.111 50.4095 101.055 50.8587 101.831 51.7112C102.607 52.5546 102.996 53.563 102.996 54.7273C102.996 53.9572 103.172 53.2513 103.525 52.5913C103.886 51.9312 104.372 51.3995 104.98 51.0053C105.598 50.602 106.268 50.3911 107 50.3545C105.88 50.2903 104.936 49.8503 104.16 49.0069C103.384 48.1635 102.996 47.1642 102.996 46C102.996 47.1642 102.607 48.1635 101.831 49.0069Z"
                      fill="currentColor"
                    />
                  </svg>
                </div>
                <div className="theme-switch__circle-container">
                  <div className="theme-switch__sun-moon-container">
                    <div className="theme-switch__moon">
                      <div className="theme-switch__spot"></div>
                      <div className="theme-switch__spot"></div>
                      <div className="theme-switch__spot"></div>
                    </div>
                  </div>
                </div>
              </div>
            </label>
          </div>
        </div>

        {/* STATE 1: Centered Welcome / Upload Screen when no active dataset */}
        {!activeDataset ? (
          <div className="flex-1 flex flex-col items-center justify-center p-6 text-center max-w-xl mx-auto w-full select-none">
            <h1 className={`text-2xl sm:text-3xl font-semibold tracking-tight mb-2 ${isDarkMode ? 'text-zinc-100' : 'text-zinc-900'
              }`}>
              InsightSQL
            </h1>
            <p className={`text-sm mb-8 ${isDarkMode ? 'text-zinc-400' : 'text-zinc-600'
              }`}>
              Upload a file to get started.
            </p>

            {/* Prominent Centered Upload Area */}
            <div
              onClick={() => !isUploading && fileInputRef.current?.click()}
              onDragOver={handleDragOver}
              onDragLeave={handleDragLeave}
              onDrop={handleDrop}
              role="button"
              tabIndex={0}
              aria-label="Upload dataset"
              onKeyDown={(e) => {
                if ((e.key === 'Enter' || e.key === ' ') && !isUploading) {
                  e.preventDefault();
                  fileInputRef.current?.click();
                }
              }}
              className={`w-full rounded-2xl border-2 border-dashed p-8 sm:p-12 flex flex-col items-center justify-center cursor-pointer transition-all duration-200 select-none ${isDragging
                  ? isDarkMode
                    ? 'border-indigo-400 bg-indigo-950/20'
                    : 'border-indigo-500 bg-indigo-50/50'
                  : isDarkMode
                    ? 'border-[#2c2c34] bg-[#121216]/60 hover:border-zinc-500 hover:bg-[#16161b]'
                    : 'border-zinc-300 bg-white/70 hover:border-zinc-400 hover:bg-white shadow-sm'
                } ${isUploading ? 'opacity-80 pointer-events-none' : ''}`}
            >
              {isUploading ? (
                <div className="flex flex-col items-center space-y-3 py-4">
                  <Loader2 className="w-10 h-10 text-indigo-400 animate-spin" />
                  <span className={`text-sm font-medium ${isDarkMode ? 'text-zinc-200' : 'text-zinc-800'}`}>
                    Uploading and processing dataset...
                  </span>
                  <span className={`text-xs ${isDarkMode ? 'text-zinc-500' : 'text-zinc-500'}`}>
                    Parsing records and cataloging database schema
                  </span>
                </div>
              ) : (
                <>
                  <div className={`w-12 h-12 rounded-xl flex items-center justify-center mb-4 transition-colors ${isDarkMode ? 'bg-[#1e1e24] text-zinc-300' : 'bg-zinc-100 text-zinc-700'
                    }`}>
                    <UploadCloud className="w-6 h-6" />
                  </div>
                  <p className={`text-base font-medium mb-1 ${isDarkMode ? 'text-zinc-200' : 'text-zinc-800'
                    }`}>
                    Please upload your file <span className={`text-xs font-normal ${isDarkMode ? 'text-zinc-400' : 'text-zinc-500'}`}>(Max size allowed: {MAX_FILE_SIZE_MB} MB)</span>
                  </p>
                  <p className={`text-xs mb-3.5 ${isDarkMode ? 'text-zinc-400' : 'text-zinc-500'
                    }`}>
                    Click to browse or drag and drop your dataset here.
                  </p>
                  <span className={`text-[11px] px-3 py-1 rounded-full border ${isDarkMode ? 'text-zinc-500 bg-[#16161a] border-[#222228]' : 'text-zinc-500 bg-zinc-50 border-zinc-200'
                    }`}>
                    Supported formats: CSV, Excel (.xlsx, .xls), Parquet, JSON
                  </span>
                </>
              )}
            </div>

            {/* Error Message with retry / dismiss */}
            {uploadError && (
              <div className={`mt-4 p-3 rounded-xl text-xs flex items-center gap-2 max-w-md w-full border ${isDarkMode ? 'bg-red-950/30 border-red-900/50 text-red-300' : 'bg-red-50 border-red-200 text-red-700'
                }`}>
                <AlertCircle className="w-4 h-4 flex-shrink-0" />
                <span className="flex-1 text-left">{uploadError}</span>
                <button
                  type="button"
                  onClick={(e) => { e.stopPropagation(); setUploadError(null); }}
                  className="underline font-medium hover:opacity-80 ml-2 cursor-pointer"
                >
                  Dismiss
                </button>
              </div>
            )}
          </div>
        ) : (
          /* STATE 2: Active Chat Interface after successful ingestion */
          <>
            {/* Chat message display area */}
            <div className="flex-1 overflow-y-auto px-4 md:px-12 pt-2 pb-4 space-y-4">
              {messages.map((m) => {
                if (m.role === 'system') {
                  return (
                    <div key={m.id} className="flex justify-center my-2 select-none">
                      <span className={`text-xs px-3 py-1 rounded-full border transition-colors duration-300 ${m.status === 'error'
                          ? isDarkMode
                            ? 'text-red-400 bg-red-950/20 border-red-900/40'
                            : 'text-red-700 bg-red-50 border-red-200'
                          : isDarkMode
                            ? 'text-zinc-400 bg-[#111114] border-[#1d1d22]'
                            : 'text-zinc-600 bg-white/80 border-zinc-300 shadow-sm'
                        }`}>
                        {m.content}
                      </span>
                    </div>
                  );
                }

                if (m.role === 'user') {
                  return (
                    <div key={m.id} className="flex flex-col items-end gap-1">
                      <div className={`border text-sm px-4 py-2 rounded-2xl max-w-md shadow-sm select-text transition-colors duration-300 ${isDarkMode ? 'bg-[#18181b] border-[#27272a] text-[#d4d4d8]' : 'bg-[#1e293b] border-slate-700 text-slate-100'
                        }`}>
                        {m.content}
                      </div>
                      <button
                        type="button"
                        onClick={() => copyToClipboard(`user-${m.id}`, m.content)}
                        aria-label="Copy user message"
                        className={`flex items-center gap-1 text-[11px] transition-colors pr-1 select-none cursor-pointer ${isDarkMode ? 'text-zinc-500 hover:text-zinc-300' : 'text-zinc-400 hover:text-zinc-700'
                          }`}
                      >
                        {copiedId === `user-${m.id}` ? (
                          <>
                            <Check className="w-3 h-3 text-emerald-500" />
                            <span className="text-emerald-500">Copied</span>
                          </>
                        ) : (
                          <>
                            <Copy className="w-3 h-3" />
                            <span>Copy</span>
                          </>
                        )}
                      </button>
                    </div>
                  );
                }

                // Assistant response
                return (
                  <div key={m.id} className="flex justify-start">
                    <div className="max-w-2xl w-full space-y-3">
                      {/* Natural language explanation */}
                      <div className={`text-sm leading-relaxed font-normal select-text transition-colors duration-300 ${isDarkMode ? 'text-zinc-200' : 'text-zinc-800'
                        }`}>
                        {m.content}
                      </div>

                      {/* Copy assistant explanation button */}
                      <div className="flex items-center gap-2">
                        <button
                          type="button"
                          onClick={() => copyToClipboard(`asst-${m.id}`, m.content)}
                          aria-label="Copy assistant response"
                          className={`flex items-center gap-1 text-[11px] transition-colors select-none cursor-pointer ${isDarkMode ? 'text-zinc-500 hover:text-zinc-300' : 'text-zinc-400 hover:text-zinc-700'
                            }`}
                        >
                          {copiedId === `asst-${m.id}` ? (
                            <>
                              <Check className="w-3 h-3 text-emerald-500" />
                              <span className="text-emerald-500">Copied</span>
                            </>
                          ) : (
                            <>
                              <Copy className="w-3 h-3" />
                              <span>Copy</span>
                            </>
                          )}
                        </button>
                      </div>

                      {/* Warnings banner if present */}
                      {m.warnings && m.warnings.length > 0 && (
                        <div className={`text-xs p-2.5 rounded-lg select-text transition-colors duration-300 ${isDarkMode
                            ? 'text-amber-300/90 bg-amber-950/20 border border-amber-900/40'
                            : 'text-amber-900 bg-amber-50 border border-amber-200'
                          }`}>
                          {m.warnings.map((w, idx) => (
                            <div key={idx}>{w}</div>
                          ))}
                        </div>
                      )}

                      {/* Clarification prompt if present */}
                      {m.clarification_question && (
                        <div className={`text-xs p-2.5 rounded-lg select-text transition-colors duration-300 ${isDarkMode
                            ? 'text-amber-400/90 bg-amber-950/20 border border-amber-900/40'
                            : 'text-amber-900 bg-amber-50 border border-amber-200'
                          }`}>
                          {m.clarification_question}
                        </div>
                      )}

                      {/* Expandable SQL disclosure */}
                      {m.sql && (
                        <div className={`border rounded-lg overflow-hidden text-xs transition-colors duration-300 ${isDarkMode ? 'border-[#1f1f24] bg-[#0d0d10]' : 'border-zinc-300 bg-white/95 shadow-sm'
                          }`}>
                          <div className={`w-full flex items-center justify-between px-3 py-2 transition-colors ${isDarkMode ? 'text-zinc-400' : 'text-zinc-600'
                            }`}>
                            <button
                              type="button"
                              onClick={() => toggleSql(m.id)}
                              className={`flex items-center gap-1.5 font-mono transition-colors select-none cursor-pointer ${isDarkMode ? 'text-zinc-400 hover:text-zinc-200' : 'text-zinc-600 hover:text-zinc-900'
                                }`}
                            >
                              {openSqlId === m.id ? (
                                <ChevronDown className="w-3.5 h-3.5" />
                              ) : (
                                <ChevronRight className="w-3.5 h-3.5" />
                              )}
                              <span>Generated SQL</span>
                            </button>
                            <button
                              type="button"
                              onClick={() => copyToClipboard(`sql-${m.id}`, m.sql!)}
                              className={`flex items-center gap-1 text-[11px] font-sans transition-colors select-none cursor-pointer ${isDarkMode ? 'text-zinc-400 hover:text-zinc-200' : 'text-zinc-600 hover:text-zinc-900'
                                }`}
                            >
                              {copiedId === `sql-${m.id}` ? (
                                <>
                                  <Check className="w-3 h-3 text-emerald-500" />
                                  <span className="text-emerald-500">Copied</span>
                                </>
                              ) : (
                                <>
                                  <Copy className="w-3 h-3" />
                                  <span>Copy SQL</span>
                                </>
                              )}
                            </button>
                          </div>
                          {openSqlId === m.id && (
                            <pre className={`p-3 font-mono text-[11px] overflow-x-auto border-t select-text ${isDarkMode ? 'border-[#1b1b20] bg-black/40 text-zinc-300' : 'border-zinc-200 bg-zinc-50 text-zinc-800'
                              }`}>
                              <code>{m.sql}</code>
                            </pre>
                          )}
                        </div>
                      )}

                      {/* Multi-Section Result Tables */}
                      {m.sections && m.sections.length > 0 ? (
                        <div className="space-y-4">
                          {m.sections.map((section, secIdx) => (
                            <div
                              key={secIdx}
                              className={`border rounded-lg overflow-hidden text-xs transition-colors duration-300 ${isDarkMode ? 'border-[#1f1f24] bg-[#0c0c0f]' : 'border-zinc-300 bg-white/95 shadow-sm'
                                }`}
                            >
                              <div className={`px-3 py-2 border-b flex items-center justify-between select-none ${isDarkMode ? 'bg-[#141418] border-[#1d1d22]' : 'bg-zinc-100 border-zinc-200'
                                }`}>
                                <span className={`font-medium ${isDarkMode ? 'text-zinc-200' : 'text-zinc-800'}`}>
                                  {section.title}
                                </span>
                                <div className="flex items-center gap-2">
                                  <span className={`text-[11px] ${isDarkMode ? 'text-zinc-500' : 'text-zinc-600'}`}>
                                    {section.row_count || section.rows.length} rows
                                  </span>
                                  {section.columns.length > 0 && section.rows.length > 0 && (
                                    <button
                                      type="button"
                                      onClick={() => copyToClipboard(`sec-${m.id}-${secIdx}`, formatTableAsTsv(section.columns, section.rows))}
                                      className={`flex items-center gap-1 text-[11px] transition-colors select-none cursor-pointer ${isDarkMode ? 'text-zinc-400 hover:text-zinc-200' : 'text-zinc-600 hover:text-zinc-900'
                                        }`}
                                    >
                                      {copiedId === `sec-${m.id}-${secIdx}` ? (
                                        <>
                                          <Check className="w-3 h-3 text-emerald-500" />
                                          <span className="text-emerald-500">Copied</span>
                                        </>
                                      ) : (
                                        <>
                                          <Copy className="w-3 h-3" />
                                          <span>Copy TSV</span>
                                        </>
                                      )}
                                    </button>
                                  )}
                                </div>
                              </div>
                              <div className="overflow-x-auto max-h-80 select-text">
                                <table className="w-full text-left border-collapse">
                                  <thead>
                                    <tr className={`border-b ${isDarkMode ? 'border-[#1a1a20] bg-[#121216]' : 'border-zinc-200 bg-zinc-50'}`}>
                                      {section.columns.map((col, cIdx) => (
                                        <th
                                          key={cIdx}
                                          className={`px-3 py-2 font-medium whitespace-nowrap select-text ${isDarkMode ? 'text-zinc-400' : 'text-zinc-600'}`}
                                        >
                                          {col}
                                        </th>
                                      ))}
                                    </tr>
                                  </thead>
                                  <tbody className={`divide-y ${isDarkMode ? 'divide-[#16161b]' : 'divide-zinc-200'}`}>
                                    {section.rows.slice(0, 15).map((row, rIdx) => (
                                      <tr
                                        key={rIdx}
                                        className={`transition-colors ${isDarkMode ? 'hover:bg-[#16161c]' : 'hover:bg-zinc-50'}`}
                                      >
                                        {row.map((cell, cIdx) => (
                                          <td
                                            key={cIdx}
                                            className={`px-3 py-1.5 whitespace-nowrap select-text ${isDarkMode ? 'text-zinc-300' : 'text-zinc-800'}`}
                                          >
                                            {cell !== null && cell !== undefined ? String(cell) : '—'}
                                          </td>
                                        ))}
                                      </tr>
                                    ))}
                                  </tbody>
                                </table>
                              </div>
                              {section.rows.length > 15 && (
                                <div className={`text-[11px] px-3 py-1.5 border-t select-none ${isDarkMode ? 'text-zinc-500 border-[#18181c]' : 'text-zinc-500 border-zinc-200'
                                  }`}>
                                  Showing 15 of {section.row_count || section.rows.length} rows
                                </div>
                              )}
                            </div>
                          ))}
                        </div>
                      ) : m.columns && m.columns.length > 0 && m.rows && m.rows.length > 0 ? (
                        /* Standard Single Table */
                        <div className={`border rounded-lg overflow-hidden text-xs transition-colors duration-300 ${isDarkMode ? 'border-[#1f1f24] bg-[#0c0c0f]' : 'border-zinc-300 bg-white/95 shadow-sm'
                          }`}>
                          <div className={`px-3 py-2 border-b flex items-center justify-between select-none ${isDarkMode ? 'bg-[#141418] border-[#1d1d22]' : 'bg-zinc-100 border-zinc-200'
                            }`}>
                            <span className={`font-medium ${isDarkMode ? 'text-zinc-300' : 'text-zinc-700'}`}>
                              Results ({m.row_count || m.rows.length} rows)
                            </span>
                            <button
                              type="button"
                              onClick={() => copyToClipboard(`tbl-${m.id}`, formatTableAsTsv(m.columns!, m.rows!))}
                              className={`flex items-center gap-1 text-[11px] transition-colors select-none cursor-pointer ${isDarkMode ? 'text-zinc-400 hover:text-zinc-200' : 'text-zinc-600 hover:text-zinc-900'
                                }`}
                            >
                              {copiedId === `tbl-${m.id}` ? (
                                <>
                                  <Check className="w-3 h-3 text-emerald-500" />
                                  <span className="text-emerald-500">Copied</span>
                                </>
                              ) : (
                                <>
                                  <Copy className="w-3 h-3" />
                                  <span>Copy TSV</span>
                                </>
                              )}
                            </button>
                          </div>
                          <div className="overflow-x-auto max-h-80 select-text">
                            <table className="w-full text-left border-collapse">
                              <thead>
                                <tr className={`border-b ${isDarkMode ? 'border-[#1a1a20] bg-[#121216]' : 'border-zinc-200 bg-zinc-50'}`}>
                                  {m.columns.map((col, cIdx) => (
                                    <th key={cIdx} className={`px-3 py-2 font-medium whitespace-nowrap select-text ${isDarkMode ? 'text-zinc-400' : 'text-zinc-600'}`}>
                                      {col}
                                    </th>
                                  ))}
                                </tr>
                              </thead>
                              <tbody className={`divide-y ${isDarkMode ? 'divide-[#16161b]' : 'divide-zinc-200'}`}>
                                {m.rows.slice(0, 15).map((row, rIdx) => (
                                  <tr key={rIdx} className={`transition-colors ${isDarkMode ? 'hover:bg-[#16161c]' : 'hover:bg-zinc-50'}`}>
                                    {row.map((cell, cIdx) => (
                                      <td key={cIdx} className={`px-3 py-1.5 whitespace-nowrap select-text ${isDarkMode ? 'text-zinc-300' : 'text-zinc-800'}`}>
                                        {cell !== null && cell !== undefined ? String(cell) : '—'}
                                      </td>
                                    ))}
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </div>
                          {m.rows.length > 15 && (
                            <div className={`text-[11px] px-3 py-1.5 border-t select-none ${isDarkMode ? 'text-zinc-500 border-[#18181c]' : 'text-zinc-500 border-zinc-200'
                              }`}>
                              Showing 15 of {m.row_count || m.rows.length} rows
                            </div>
                          )}
                        </div>
                      ) : null}

                      {/* Minimal Result Bar Chart if visualization configured */}
                      {m.visualization && m.visualization.type === 'bar' && m.rows && m.rows.length > 1 && (
                        <div className={`border rounded-lg p-3 text-xs transition-colors duration-300 ${isDarkMode ? 'border-[#1f1f24] bg-[#0c0c0f]' : 'border-zinc-300 bg-white/95 shadow-sm'
                          }`}>
                          <div className={`text-[11px] mb-2 font-mono ${isDarkMode ? 'text-zinc-500' : 'text-zinc-600'}`}>
                            {m.visualization.y} by {m.visualization.x}
                          </div>
                          <div className="space-y-1.5">
                            {m.rows.slice(0, 8).map((r, i) => {
                              const label = String(r[0]);
                              const val = Number(r[1]) || 0;
                              const maxVal = Math.max(...m.rows!.map((row) => Number(row[1]) || 0), 1);
                              const pct = Math.min(Math.round((val / maxVal) * 100), 100);
                              return (
                                <div key={i} className="flex items-center gap-2">
                                  <span className={`w-24 truncate text-[11px] ${isDarkMode ? 'text-zinc-400' : 'text-zinc-600'}`}>{label}</span>
                                  <div className={`flex-1 h-3.5 rounded-sm overflow-hidden ${isDarkMode ? 'bg-[#18181d]' : 'bg-zinc-200'}`}>
                                    <div
                                      className={`h-full rounded-sm transition-all duration-300 ${isDarkMode ? 'bg-zinc-400' : 'bg-slate-700'}`}
                                      style={{ width: `${pct}%` }}
                                    />
                                  </div>
                                  <span className={`w-16 text-right font-mono text-[11px] ${isDarkMode ? 'text-zinc-300' : 'text-zinc-800'}`}>
                                    {val.toLocaleString()}
                                  </span>
                                </div>
                              );
                            })}
                          </div>
                        </div>
                      )}
                    </div>
                  </div>
                );
              })}

              {/* Subtle loading indicator */}
              {isLoading && (
                <div className="flex justify-start">
                  <div className="flex items-center gap-1.5 px-3 py-2 text-zinc-500 text-xs">
                    <span className="w-1.5 h-1.5 rounded-full bg-zinc-500 animate-pulse" />
                    <span className="w-1.5 h-1.5 rounded-full bg-zinc-500 animate-pulse delay-150" />
                    <span className="w-1.5 h-1.5 rounded-full bg-zinc-500 animate-pulse delay-300" />
                  </div>
                </div>
              )}

              <div ref={messagesEndRef} />
            </div>

            {/* Minimal Chat Composer and Disclaimer centered near bottom */}
            <div className="p-3 md:pb-5 flex flex-col items-center w-full flex-shrink-0">
              <div className="flex items-center gap-2 max-w-xl w-full">
                {/* Separate + Upload Button beside composer */}
                <button
                  type="button"
                  onClick={() => fileInputRef.current?.click()}
                  disabled={isUploading}
                  aria-label="Upload replacement data file"
                  title="Upload a new or replacement dataset"
                  className={`w-10 h-10 rounded-full border flex items-center justify-center transition-all disabled:opacity-50 flex-shrink-0 cursor-pointer shadow-xl ${isDarkMode
                      ? 'bg-white border-zinc-200 text-zinc-700 hover:text-zinc-950 hover:bg-zinc-50'
                      : 'bg-[#101013] border-[#222227] text-zinc-400 hover:text-zinc-100 hover:border-zinc-600'
                    }`}
                >
                  {isUploading ? (
                    <Loader2 className={`w-4 h-4 animate-spin ${isDarkMode ? 'text-zinc-700' : 'text-zinc-300'}`} />
                  ) : (
                    <Plus className="w-4 h-4" />
                  )}
                </button>

                {/* Pill input container */}
                <div className={`relative flex-1 flex items-center border rounded-full px-4 py-2.5 transition-colors duration-300 shadow-xl ${isDarkMode
                    ? 'bg-white border-zinc-200 focus-within:border-zinc-400'
                    : 'bg-[#101013] border-[#222227] focus-within:border-zinc-600'
                  }`}>
                  <input
                    ref={inputRef}
                    type="text"
                    value={input}
                    onChange={(e) => setInput(e.target.value)}
                    onKeyDown={handleKeyDown}
                    placeholder="Type your question..."
                    disabled={isLoading || isUploading}
                    className={`w-full bg-transparent text-sm focus:outline-none pr-8 select-text transition-colors duration-300 ${isDarkMode
                        ? 'text-zinc-900 placeholder:text-zinc-500'
                        : 'text-zinc-100 placeholder:text-zinc-500'
                      }`}
                  />
                  <button
                    type="button"
                    onClick={handleSend}
                    disabled={!input.trim() || isLoading || isUploading}
                    aria-label="Send message"
                    className={`absolute right-2 w-7 h-7 rounded-full flex items-center justify-center transition-all disabled:opacity-30 disabled:cursor-not-allowed ${isDarkMode
                        ? 'bg-zinc-900 hover:bg-zinc-800 text-white'
                        : 'bg-zinc-100 hover:bg-white text-zinc-900'
                      }`}
                  >
                    <ArrowUp className="w-4 h-4" />
                  </button>
                </div>
              </div>

              {/* Accuracy disclaimer unobtrusively placed below composer */}
              <p className={`text-[11px] text-center select-none pt-2.5 ${isDarkMode ? 'text-zinc-500' : 'text-zinc-500'
                }`}>
                Results may be inaccurate. Please verify important findings against your data.
              </p>
            </div>
          </>
        )}

        {/* Hidden Native File Picker supporting backend accepted formats */}
        <input
          ref={fileInputRef}
          type="file"
          accept=".csv,.xlsx,.xls,.parquet,.json"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) processFile(f);
          }}
          className="hidden"
        />

      </div>
    </div>
  );
}
