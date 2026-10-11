import React from 'react';
import { ChevronDown, ChevronRight, Copy, Check, AlertCircle } from 'lucide-react';
import type { MessageItem } from '../types';
import { DataTable } from './DataTable';
import { copyTextToClipboard } from '../utils/format';

interface ChatMessageProps {
  message: MessageItem;
  isDarkMode: boolean;
  openSqlId: string | null;
  copiedId: string | null;
  onToggleSql: (id: string) => void;
  onSetCopiedId: (id: string | null) => void;
}

export const ChatMessage: React.FC<ChatMessageProps> = ({
  message,
  isDarkMode,
  openSqlId,
  copiedId,
  onToggleSql,
  onSetCopiedId,
}) => {
  const isUser = message.role === 'user';
  const isSqlOpen = openSqlId === message.id;

  const handleCopySql = async () => {
    if (!message.sql) return;
    const ok = await copyTextToClipboard(message.sql);
    if (ok) {
      onSetCopiedId(`sql-${message.id}`);
      setTimeout(() => onSetCopiedId(null), 2000);
    }
  };

  return (
    <div className={`flex ${isUser ? 'justify-end' : 'justify-start'}`}>
      <div
        className={`max-w-2xl w-full space-y-2.5 ${
          isUser
            ? isDarkMode
              ? 'bg-[#18181d] text-zinc-100 rounded-2xl rounded-tr-sm px-4 py-3 border border-[#272730] shadow-sm ml-auto max-w-lg'
              : 'bg-zinc-100 text-zinc-900 rounded-2xl rounded-tr-sm px-4 py-3 border border-zinc-200 shadow-sm ml-auto max-w-lg'
            : isDarkMode
              ? 'bg-[#101013] text-zinc-200 rounded-2xl rounded-tl-sm p-4 border border-[#1f1f26] shadow-md'
              : 'bg-white text-zinc-800 rounded-2xl rounded-tl-sm p-4 border border-zinc-200 shadow-sm'
        }`}
      >
        {/* Main textual response */}
        <div className="text-sm leading-relaxed whitespace-pre-wrap select-text font-normal">
          {message.content}
        </div>

        {/* Clarification prompt banner */}
        {message.status === 'clarification_needed' && message.clarification_question && (
          <div
            className={`p-3 rounded-lg border text-xs flex items-start gap-2.5 transition-colors ${
              isDarkMode ? 'bg-amber-950/20 border-amber-900/40 text-amber-300' : 'bg-amber-50 border-amber-200 text-amber-800'
            }`}
          >
            <AlertCircle className="w-4 h-4 flex-shrink-0 mt-0.5" />
            <div>
              <span className="font-semibold block mb-0.5">Clarification Required</span>
              <span>{message.clarification_question}</span>
            </div>
          </div>
        )}

        {/* Policy warnings badge */}
        {message.warnings && message.warnings.length > 0 && (
          <div
            className={`p-2.5 rounded-lg border text-xs space-y-1 transition-colors ${
              isDarkMode ? 'bg-amber-950/15 border-amber-900/30 text-amber-400' : 'bg-amber-50 border-amber-200 text-amber-700'
            }`}
          >
            {message.warnings.map((w, idx) => (
              <div key={idx} className="flex items-center gap-1.5">
                <span className="w-1.5 h-1.5 rounded-full bg-amber-400 flex-shrink-0" />
                <span>{w}</span>
              </div>
            ))}
          </div>
        )}

        {/* Expandable SQL disclosure */}
        {message.sql && (
          <div
            className={`border rounded-lg overflow-hidden text-xs transition-colors duration-300 ${
              isDarkMode ? 'border-[#1f1f24] bg-[#0d0d10]' : 'border-zinc-300 bg-white/95 shadow-sm'
            }`}
          >
            <div className={`w-full flex items-center justify-between px-3 py-2 transition-colors ${
              isDarkMode ? 'text-zinc-400' : 'text-zinc-600'
            }`}>
              <button
                type="button"
                onClick={() => onToggleSql(message.id)}
                className={`flex items-center gap-1.5 font-mono transition-colors select-none cursor-pointer ${
                  isDarkMode ? 'text-zinc-400 hover:text-zinc-200' : 'text-zinc-600 hover:text-zinc-900'
                }`}
              >
                {isSqlOpen ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
                <span>Generated SQL</span>
              </button>
              <button
                type="button"
                onClick={handleCopySql}
                className={`flex items-center gap-1 text-[11px] font-sans transition-colors select-none cursor-pointer ${
                  isDarkMode ? 'text-zinc-400 hover:text-zinc-200' : 'text-zinc-600 hover:text-zinc-900'
                }`}
              >
                {copiedId === `sql-${message.id}` ? (
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
            {isSqlOpen && (
              <pre
                className={`p-3 font-mono text-[11px] overflow-x-auto border-t select-text ${
                  isDarkMode ? 'border-[#1b1b20] bg-black/40 text-zinc-300' : 'border-zinc-200 bg-zinc-50 text-zinc-800'
                }`}
              >
                <code>{message.sql}</code>
              </pre>
            )}
          </div>
        )}

        {/* Multi-Section Result Tables */}
        {message.sections && message.sections.length > 0 ? (
          <div className="space-y-4">
            {message.sections.map((sec, idx) => (
              <DataTable
                key={idx}
                id={`sec-${message.id}-${idx}`}
                title={sec.title}
                columns={sec.columns}
                rows={sec.rows}
                rowCount={sec.row_count}
                isDarkMode={isDarkMode}
                copiedId={copiedId}
                onSetCopiedId={onSetCopiedId}
              />
            ))}
          </div>
        ) : message.columns && message.columns.length > 0 && message.rows && message.rows.length > 0 ? (
          <DataTable
            id={`tbl-${message.id}`}
            columns={message.columns}
            rows={message.rows}
            rowCount={message.row_count}
            isDarkMode={isDarkMode}
            copiedId={copiedId}
            onSetCopiedId={onSetCopiedId}
          />
        ) : null}

        {/* Minimal Result Bar Chart if visualization configured */}
        {message.visualization && message.visualization.type === 'bar' && message.rows && message.rows.length > 1 && (
          <div
            className={`border rounded-lg p-3 text-xs transition-colors duration-300 ${
              isDarkMode ? 'border-[#1f1f24] bg-[#0c0c0f]' : 'border-zinc-300 bg-white/95 shadow-sm'
            }`}
          >
            <div className={`text-[11px] mb-2 font-mono ${isDarkMode ? 'text-zinc-500' : 'text-zinc-600'}`}>
              {message.visualization.y} by {message.visualization.x}
            </div>
            <div className="space-y-1.5">
              {message.rows.slice(0, 8).map((r, i) => {
                const label = String(r[0]);
                const val = Number(r[1]) || 0;
                const maxVal = Math.max(...message.rows!.map((row) => Number(row[1]) || 0), 1);
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
};
