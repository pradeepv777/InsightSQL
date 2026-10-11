import React, { type RefObject } from 'react';
import { ArrowUp, Plus, Loader2 } from 'lucide-react';

interface ChatInputProps {
  input: string;
  isDarkMode: boolean;
  isLoading: boolean;
  isUploading: boolean;
  inputRef: RefObject<HTMLInputElement | null>;
  onChangeInput: (val: string) => void;
  onSend: () => void;
  onKeyDown: (e: React.KeyboardEvent<HTMLInputElement>) => void;
  onUploadClick: () => void;
}

export const ChatInput: React.FC<ChatInputProps> = ({
  input,
  isDarkMode,
  isLoading,
  isUploading,
  inputRef,
  onChangeInput,
  onSend,
  onKeyDown,
  onUploadClick,
}) => {
  return (
    <div className="p-3 md:pb-5 flex flex-col items-center w-full flex-shrink-0">
      <div className="flex items-center gap-2 max-w-xl w-full">
        {/* Inverted + Upload Button beside composer */}
        <button
          type="button"
          onClick={onUploadClick}
          disabled={isUploading}
          aria-label="Upload replacement data file"
          title="Upload a new or replacement dataset"
          className={`w-10 h-10 rounded-full border flex items-center justify-center transition-all disabled:opacity-50 flex-shrink-0 cursor-pointer shadow-xl ${
            isDarkMode
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

        {/* Inverted Pill input container */}
        <div
          className={`relative flex-1 flex items-center border rounded-full px-4 py-2.5 transition-colors duration-300 shadow-xl ${
            isDarkMode
              ? 'bg-white border-zinc-200 focus-within:border-zinc-400'
              : 'bg-[#101013] border-[#222227] focus-within:border-zinc-600'
          }`}
        >
          <input
            ref={inputRef}
            type="text"
            value={input}
            onChange={(e) => onChangeInput(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder="Type your question..."
            disabled={isLoading || isUploading}
            className={`w-full bg-transparent text-sm focus:outline-none pr-8 select-text transition-colors duration-300 ${
              isDarkMode
                ? 'text-zinc-900 placeholder:text-zinc-500'
                : 'text-zinc-100 placeholder:text-zinc-500'
            }`}
          />
          <button
            type="button"
            onClick={onSend}
            disabled={!input.trim() || isLoading || isUploading}
            aria-label="Send message"
            className={`absolute right-2 w-7 h-7 rounded-full flex items-center justify-center transition-all disabled:opacity-30 disabled:cursor-not-allowed ${
              isDarkMode
                ? 'bg-zinc-900 hover:bg-zinc-800 text-white'
                : 'bg-zinc-100 hover:bg-white text-zinc-900'
            }`}
          >
            <ArrowUp className="w-4 h-4" />
          </button>
        </div>
      </div>

      {/* Accuracy disclaimer unobtrusively placed below composer */}
      <p className="text-[11px] text-center select-none pt-2.5 text-zinc-500">
        Results may be inaccurate. Please verify important findings against your data.
      </p>
    </div>
  );
};
