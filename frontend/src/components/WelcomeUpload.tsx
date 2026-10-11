import React from 'react';
import { UploadCloud, AlertCircle, Loader2 } from 'lucide-react';

interface WelcomeUploadProps {
  isDarkMode: boolean;
  isDragging: boolean;
  isUploading: boolean;
  uploadError: string | null;
  maxFileSizeMb: number;
  onBrowseClick: () => void;
  onDragOver: (e: React.DragEvent) => void;
  onDragLeave: (e: React.DragEvent) => void;
  onDrop: (e: React.DragEvent) => void;
  onDismissError: () => void;
}

export const WelcomeUpload: React.FC<WelcomeUploadProps> = ({
  isDarkMode,
  isDragging,
  isUploading,
  uploadError,
  maxFileSizeMb,
  onBrowseClick,
  onDragOver,
  onDragLeave,
  onDrop,
  onDismissError,
}) => {
  return (
    <div className="flex-1 flex flex-col items-center justify-center p-6 text-center max-w-xl mx-auto w-full select-none">
      <h1 className={`text-2xl sm:text-3xl font-semibold tracking-tight mb-2 ${isDarkMode ? 'text-zinc-100' : 'text-zinc-900'}`}>
        InsightSQL
      </h1>
      <p className={`text-sm mb-8 ${isDarkMode ? 'text-zinc-400' : 'text-zinc-600'}`}>
        Upload a file to get started.
      </p>

      {/* Prominent Centered Upload Area */}
      <div
        onClick={() => !isUploading && onBrowseClick()}
        onDragOver={onDragOver}
        onDragLeave={onDragLeave}
        onDrop={onDrop}
        role="button"
        tabIndex={0}
        aria-label="Upload dataset"
        onKeyDown={(e) => {
          if ((e.key === 'Enter' || e.key === ' ') && !isUploading) {
            e.preventDefault();
            onBrowseClick();
          }
        }}
        className={`w-full rounded-2xl border-2 border-dashed p-8 sm:p-12 flex flex-col items-center justify-center cursor-pointer transition-all duration-200 select-none ${
          isDragging
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
            <div
              className={`w-12 h-12 rounded-xl flex items-center justify-center mb-4 transition-colors ${
                isDarkMode ? 'bg-[#1e1e24] text-zinc-300' : 'bg-zinc-100 text-zinc-700'
              }`}
            >
              <UploadCloud className="w-6 h-6" />
            </div>
            <p className={`text-base font-medium mb-1 ${isDarkMode ? 'text-zinc-200' : 'text-zinc-800'}`}>
              Please upload your file{' '}
              <span className={`text-xs font-normal ${isDarkMode ? 'text-zinc-400' : 'text-zinc-500'}`}>
                (Max size allowed: {maxFileSizeMb} MB)
              </span>
            </p>
            <p className={`text-xs mb-3.5 ${isDarkMode ? 'text-zinc-400' : 'text-zinc-500'}`}>
              Click to browse or drag and drop your dataset here.
            </p>
            <span
              className={`text-[11px] px-3 py-1 rounded-full border ${
                isDarkMode ? 'text-zinc-500 bg-[#16161a] border-[#222228]' : 'text-zinc-500 bg-zinc-50 border-zinc-200'
              }`}
            >
              Supported formats: CSV, Excel (.xlsx, .xls), Parquet, JSON
            </span>
          </>
        )}
      </div>

      {/* Error Message with dismiss */}
      {uploadError && (
        <div
          className={`mt-4 p-3 rounded-xl text-xs flex items-center gap-2 max-w-md w-full border ${
            isDarkMode ? 'bg-red-950/30 border-red-900/50 text-red-300' : 'bg-red-50 border-red-200 text-red-700'
          }`}
        >
          <AlertCircle className="w-4 h-4 flex-shrink-0" />
          <span className="flex-1 text-left">{uploadError}</span>
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              onDismissError();
            }}
            className="underline font-medium hover:opacity-80 ml-2 cursor-pointer"
          >
            Dismiss
          </button>
        </div>
      )}
    </div>
  );
};
