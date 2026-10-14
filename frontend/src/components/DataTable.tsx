import React, { useState } from 'react';
import { Copy, Check } from 'lucide-react';
import { formatTableAsTsv, copyTextToClipboard } from '../utils/format';

interface DataTableProps {
  id: string;
  title?: string;
  columns: string[];
  rows: any[][];
  rowCount?: number;
  isDarkMode: boolean;
  copiedId: string | null;
  onSetCopiedId: (id: string | null) => void;
}

export const DataTable: React.FC<DataTableProps> = ({
  id,
  title,
  columns,
  rows,
  rowCount,
  isDarkMode,
  copiedId,
  onSetCopiedId,
}) => {
  const [showAll, setShowAll] = useState(false);

  if (!columns.length || !rows.length) return null;

  const totalRows = rowCount || rows.length;
  const isCopied = copiedId === id;

  const handleCopy = async () => {
    const tsv = formatTableAsTsv(columns, rows);
    const ok = await copyTextToClipboard(tsv);
    if (ok) {
      onSetCopiedId(id);
      setTimeout(() => onSetCopiedId(null), 2000);
    }
  };

  return (
    <div
      className={`border rounded-lg overflow-hidden text-xs transition-colors duration-300 ${
        isDarkMode ? 'border-[#1f1f24] bg-[#0c0c0f]' : 'border-zinc-300 bg-white/95 shadow-sm'
      }`}
    >
      <div
        className={`px-3 py-2 border-b flex items-center justify-between select-none ${
          isDarkMode ? 'bg-[#141418] border-[#1d1d22]' : 'bg-zinc-100 border-zinc-200'
        }`}
      >
        <span className={`font-medium ${isDarkMode ? 'text-zinc-200' : 'text-zinc-800'}`}>
          {title || `Results (${totalRows} rows)`}
        </span>
        <div className="flex items-center gap-2">
          {title && (
            <span className={`text-[11px] ${isDarkMode ? 'text-zinc-500' : 'text-zinc-600'}`}>
              {totalRows} rows
            </span>
          )}
          <button
            type="button"
            onClick={handleCopy}
            className={`flex items-center gap-1 text-[11px] transition-colors select-none cursor-pointer ${
              isDarkMode ? 'text-zinc-400 hover:text-zinc-200' : 'text-zinc-600 hover:text-zinc-900'
            }`}
          >
            {isCopied ? (
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
      </div>
      <div className="overflow-x-auto max-h-80 select-text">
        <table className="w-full text-left border-collapse">
          <thead>
            <tr className={`border-b ${isDarkMode ? 'border-[#1a1a20] bg-[#121216]' : 'border-zinc-200 bg-zinc-50'}`}>
              {columns.map((col, idx) => (
                <th
                  key={idx}
                  className={`px-3 py-2 font-medium whitespace-nowrap select-text ${
                    isDarkMode ? 'text-zinc-400' : 'text-zinc-600'
                  }`}
                >
                  {col}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className={`divide-y ${isDarkMode ? 'divide-[#16161b]' : 'divide-zinc-200'}`}>
            {(showAll ? rows : rows.slice(0, 15)).map((row, rIdx) => (
              <tr
                key={rIdx}
                className={`transition-colors ${isDarkMode ? 'hover:bg-[#16161c]' : 'hover:bg-zinc-50'}`}
              >
                {row.map((cell, cIdx) => (
                  <td
                    key={cIdx}
                    className={`px-3 py-1.5 whitespace-nowrap select-text ${
                      isDarkMode ? 'text-zinc-300' : 'text-zinc-800'
                    }`}
                  >
                    {cell !== null && cell !== undefined ? String(cell) : '—'}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {rows.length > 15 && (
        <div
          className={`text-[11px] px-3 py-1.5 border-t select-none flex items-center justify-between ${
            isDarkMode ? 'text-zinc-500 border-[#18181c]' : 'text-zinc-500 border-zinc-200'
          }`}
        >
          <span>Showing {showAll ? rows.length : 15} of {totalRows} rows</span>
          <button
            type="button"
            onClick={() => setShowAll((prev) => !prev)}
            className={`cursor-pointer underline transition-colors ${
              isDarkMode ? 'text-zinc-400 hover:text-zinc-200' : 'text-zinc-600 hover:text-zinc-900'
            }`}
          >
            {showAll ? 'Show less' : `Show all ${rows.length} rows`}
          </button>
        </div>
      )}
    </div>
  );
};
