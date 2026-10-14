import { useState, useEffect, useRef } from 'react';
import { Plus, Table } from 'lucide-react';
import type { MessageItem, ActiveDatasetInfo } from './types';
import { ThemeToggle } from './components/ThemeToggle';
import { WelcomeUpload } from './components/WelcomeUpload';
import { ChatMessage } from './components/ChatMessage';
import { ChatInput } from './components/ChatInput';

const MAX_FILE_SIZE_MB = 100;

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
          errorMsg = (typeof err.detail === 'string' ? err.detail : err.message) || errorMsg;
        } catch {
          errorMsg = `Request failed with HTTP status ${res.status}`;
        }
        setMessages((prev) => [
          ...prev,
          { id: crypto.randomUUID(), role: 'assistant', content: `Error: ${errorMsg}` },
        ]);
        return;
      }

      const data = await res.json();
      const assistantMsg: MessageItem = {
        id: data.id || crypto.randomUUID(),
        role: 'assistant',
        content: data.answer || 'Here is the complete table data.',
        sql: data.sql,
        columns: data.columns || [],
        rows: data.rows || [],
        row_count: data.row_count || 0,
        sections: data.sections || null,
        visualization: data.visualization || null,
        status: data.status,
        warnings: data.warnings || [],
      };
      setMessages((prev) => [...prev, assistantMsg]);
    } catch (err: any) {
      setMessages((prev) => [
        ...prev,
        { id: crypto.randomUUID(), role: 'assistant', content: `Network error: ${err.message || 'Server unreachable'}` },
      ]);
    } finally {
      setIsLoading(false);
    }
  };

  const handleSend = async () => {
    if (!input.trim() || isLoading || isUploading || !conversationId) return;

    const userMsg: MessageItem = {
      id: crypto.randomUUID(),
      role: 'user',
      content: input.trim(),
    };

    setMessages((prev) => [...prev, userMsg]);
    setInput('');
    setIsLoading(true);

    try {
      const res = await fetch(`/api/v1/conversations/${conversationId}/messages`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          message: userMsg.content,
          dataset_ids: activeDataset ? [activeDataset.id] : [],
          include_sql: true,
          visualization: 'auto',
        }),
      });

      if (!res.ok) {
        let errorMsg = 'Failed to process question';
        try {
          const err = await res.json();
          errorMsg = (typeof err.detail === 'string' ? err.detail : err.message) || errorMsg;
        } catch {
          errorMsg = `Request failed with HTTP status ${res.status}`;
        }
        setMessages((prev) => [
          ...prev,
          { id: crypto.randomUUID(), role: 'assistant', content: `Error: ${errorMsg}` },
        ]);
        return;
      }

      const data = await res.json();
      const assistantMsg: MessageItem = {
        id: data.id || crypto.randomUUID(),
        role: 'assistant',
        content: data.answer,
        sql: data.sql,
        columns: data.columns || [],
        rows: data.rows || [],
        row_count: data.row_count || 0,
        sections: data.sections || null,
        visualization: data.visualization || null,
        status: data.status,
        clarification_question: data.clarification_question,
        warnings: data.warnings || [],
      };
      setMessages((prev) => [...prev, assistantMsg]);
    } catch (err: any) {
      setMessages((prev) => [
        ...prev,
        { id: crypto.randomUUID(), role: 'assistant', content: `Network error: ${err.message || 'Server unreachable'}` },
      ]);
    } finally {
      setIsLoading(false);
      setTimeout(() => inputRef.current?.focus(), 50);
    }
  };

  const processFile = async (file: File) => {
    const ext = file.name.substring(file.name.lastIndexOf('.')).toLowerCase();
    const validExts = ['.csv', '.xlsx', '.xls', '.parquet', '.json'];
    if (!validExts.includes(ext)) {
      setUploadError(`Unsupported file format. Please upload ${validExts.join(', ')}`);
      return;
    }

    if (file.size > MAX_FILE_SIZE_MB * 1024 * 1024) {
      setUploadError(`File is too large (${(file.size / (1024 * 1024)).toFixed(1)} MB). Limit is ${MAX_FILE_SIZE_MB} MB.`);
      return;
    }

    setUploadError(null);
    setIsUploading(true);

    try {
      const formData = new FormData();
      formData.append('file', file);

      const res = await fetch('/api/v1/datasets/upload', {
        method: 'POST',
        body: formData,
      });

      if (!res.ok) {
        let errDesc = 'Upload failed';
        try {
          const err = await res.json();
          errDesc = (typeof err.detail === 'string' ? err.detail : err.message) || errDesc;
        } catch {
          errDesc = `Upload failed with status ${res.status}`;
        }
        setUploadError(errDesc);
        return;
      }

      const dataset = await res.json();
      const datasetName = dataset.name || dataset.display_name || 'Dataset';
      const datasetInfo: ActiveDatasetInfo = {
        id: dataset.id,
        name: datasetName,
        rowCount: dataset.row_count,
      };

      sessionStorage.setItem('active_dataset', JSON.stringify(datasetInfo));
      sessionStorage.setItem('active_conversation_id', conversationId);
      setActiveDataset(datasetInfo);

      setMessages([
        {
          id: crypto.randomUUID(),
          role: 'system',
          content: `Dataset "${datasetName}" ingested successfully (${dataset.row_count?.toLocaleString() || 0} rows). Ask any analytical question.`,
          status: 'success',
        },
      ]);
    } catch (err: any) {
      setUploadError(`Upload error: ${err.message || 'Network disconnected'}`);
    } finally {
      setIsUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  };

  return (
    <div className={`relative w-screen h-screen p-3 md:p-4 transition-colors duration-500 ${isDarkMode ? 'pattern-grid-dark' : 'pattern-grid-light'}`}>
      <div className={`relative w-full h-full rounded-2xl border flex flex-col overflow-hidden shadow-2xl transition-colors duration-500 ${
        isDarkMode ? 'border-[#27272e] pattern-grid-dark' : 'border-[#d4d4d8] pattern-grid-light'
      }`}>
        {/* Top Header Navigation Bar */}
        <div className="w-full px-5 py-3.5 flex items-start justify-between z-30 select-none flex-shrink-0">
          <div className="flex items-start gap-2.5">
            {activeDataset && (
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={handleNewChat}
                  disabled={isLoading || isUploading}
                  title="Start a new chat"
                  className={`flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-xl border transition-all cursor-pointer shadow-md disabled:opacity-50 ${
                    isDarkMode
                      ? 'border-zinc-200 bg-white text-zinc-800 hover:text-black hover:bg-zinc-50 hover:border-zinc-300'
                      : 'border-[#222227] bg-[#101013] text-zinc-300 hover:text-white hover:bg-[#18181d] hover:border-zinc-600'
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
                  className={`flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-xl border transition-all cursor-pointer shadow-md disabled:opacity-50 ${
                    isDarkMode
                      ? 'border-zinc-200 bg-white text-zinc-800 hover:text-black hover:bg-zinc-50 hover:border-zinc-300'
                      : 'border-[#222227] bg-[#101013] text-zinc-300 hover:text-white hover:bg-[#18181d] hover:border-zinc-600'
                  }`}
                >
                  <Table className="w-3.5 h-3.5" />
                  <span>Show Table</span>
                </button>
              </div>
            )}
          </div>

          {/* Right side: Table name & Animated theme switch toggle */}
          <div className="flex items-center gap-3">
            {activeDataset && (
              <span className={`text-xs font-medium select-none truncate max-w-[200px] sm:max-w-xs ${
                isDarkMode ? 'text-zinc-300' : 'text-zinc-700'
              }`}>
                {activeDataset.name}
              </span>
            )}
            <ThemeToggle isDarkMode={isDarkMode} onToggle={setIsDarkMode} />
          </div>
        </div>

        {/* STATE 1: Centered Welcome / Upload Screen */}
        {!activeDataset ? (
          <WelcomeUpload
            isDarkMode={isDarkMode}
            isDragging={isDragging}
            isUploading={isUploading}
            uploadError={uploadError}
            maxFileSizeMb={MAX_FILE_SIZE_MB}
            onBrowseClick={() => fileInputRef.current?.click()}
            onDragOver={(e) => { e.preventDefault(); e.stopPropagation(); if (!isUploading) setIsDragging(true); }}
            onDragLeave={(e) => { e.preventDefault(); e.stopPropagation(); setIsDragging(false); }}
            onDrop={(e) => {
              e.preventDefault();
              e.stopPropagation();
              setIsDragging(false);
              if (!isUploading && e.dataTransfer.files?.[0]) processFile(e.dataTransfer.files[0]);
            }}
            onDismissError={() => setUploadError(null)}
          />
        ) : (
          /* STATE 2: Active Chat Interface */
          <>
            <div className="flex-1 overflow-y-auto px-4 md:px-12 pt-2 pb-4 space-y-4">
              {messages.map((m) =>
                m.role === 'system' ? (
                  <div key={m.id} className="flex justify-center my-2 select-none">
                    <span className={`text-xs px-3 py-1 rounded-full border transition-colors duration-300 ${
                      m.status === 'error'
                        ? isDarkMode
                          ? 'text-red-400 bg-red-950/20 border-red-900/40'
                          : 'text-red-700 bg-red-50 border-red-200'
                        : isDarkMode
                          ? 'text-zinc-400 bg-[#141418] border-[#222228]'
                          : 'text-zinc-600 bg-zinc-100 border-zinc-200'
                    }`}>
                      {m.content}
                    </span>
                  </div>
                ) : (
                  <ChatMessage
                    key={m.id}
                    message={m}
                    isDarkMode={isDarkMode}
                    openSqlId={openSqlId}
                    copiedId={copiedId}
                    onToggleSql={(id) => setOpenSqlId((prev) => (prev === id ? null : id))}
                    onSetCopiedId={setCopiedId}
                  />
                )
              )}

              {/* Loading indicator */}
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

            <ChatInput
              input={input}
              isDarkMode={isDarkMode}
              isLoading={isLoading}
              isUploading={isUploading}
              inputRef={inputRef}
              onChangeInput={setInput}
              onSend={handleSend}
              onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend(); } }}
              onUploadClick={() => fileInputRef.current?.click()}
            />
          </>
        )}

        {/* Hidden File Input */}
        <input
          ref={fileInputRef}
          type="file"
          accept=".csv,.xlsx,.xls,.parquet,.json"
          onChange={(e) => { const f = e.target.files?.[0]; if (f) processFile(f); }}
          className="hidden"
          aria-hidden="true"
        />
      </div>
    </div>
  );
}
