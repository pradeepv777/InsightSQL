export function formatTableAsTsv(columns: string[], rows: any[][]): string {
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
}

export async function copyTextToClipboard(text: string): Promise<boolean> {
  if (!text) return false;
  try {
    if (navigator?.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
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
    return successful;
  } catch {
    return false;
  }
}
