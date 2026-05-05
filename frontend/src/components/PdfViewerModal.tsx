/**
 * PdfViewerModal — hiển thị tài liệu PDF/Word ngay trong chatbot.
 *
 * - PDF: render trực tiếp qua iframe, fragment #page=N được trình duyệt xử lý.
 * - Tài liệu khác: hiển thị thông báo và link mở tab mới.
 * - Đóng bằng nút X, phím Escape, hoặc click vào vùng tối bên ngoài.
 */

import React, { useEffect, useRef, useState } from 'react';
import { ExternalLink, FileText, Loader2, X } from 'lucide-react';

export interface PdfViewerModalProps {
  url: string;
  title: string;
  pageNumber?: number;
  fileType?: string;
  onClose: () => void;
}

export function PdfViewerModal({
  url,
  title,
  pageNumber,
  fileType,
  onClose,
}: PdfViewerModalProps) {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const iframeRef = useRef<HTMLIFrameElement>(null);

  const isPdf =
    fileType?.toLowerCase() === 'pdf' ||
    url.includes('/file') ||
    url.toLowerCase().endsWith('.pdf');

  // Đóng bằng phím Escape
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [onClose]);

  // Ngăn scroll body khi modal mở
  useEffect(() => {
    document.body.style.overflow = 'hidden';
    return () => { document.body.style.overflow = ''; };
  }, []);

  return (
    <div
      className="fixed inset-0 z-[9999] flex items-center justify-center bg-black/50 backdrop-blur-sm p-4"
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <div className="relative flex flex-col bg-white rounded-2xl shadow-2xl w-full max-w-4xl"
           style={{ height: 'min(88vh, 900px)' }}>

        {/* ── Header ─────────────────────────────────────────────── */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-gray-100 shrink-0 rounded-t-2xl bg-[#fdf8f5]">
          <div className="flex items-center gap-2 min-w-0">
            <FileText size={15} className="text-[#b27454] shrink-0" />
            <span className="text-sm font-medium text-[#5a3825] truncate max-w-[calc(100%-2rem)]">
              {title}
            </span>
            {pageNumber && (
              <span className="shrink-0 text-[11px] text-[#9a7060] bg-[#f5e8df] px-2 py-0.5 rounded-full">
                trang {pageNumber}
              </span>
            )}
          </div>

          <div className="flex items-center gap-1 shrink-0 ml-2">
            <a
              href={url}
              target="_blank"
              rel="noopener noreferrer"
              className="p-1.5 rounded-lg text-[#9a7060] hover:text-[#5a3825] hover:bg-[#f0e0d4] transition-colors"
              title="Mở trong tab mới"
            >
              <ExternalLink size={14} />
            </a>
            <button
              onClick={onClose}
              className="p-1.5 rounded-lg text-[#9a7060] hover:text-[#5a3825] hover:bg-[#f0e0d4] transition-colors"
              title="Đóng (Esc)"
            >
              <X size={14} />
            </button>
          </div>
        </div>

        {/* ── Body ───────────────────────────────────────────────── */}
        <div className="relative flex-1 overflow-hidden rounded-b-2xl">

          {/* Loading spinner */}
          {loading && !error && (
            <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 bg-[#fafafa] z-10">
              <Loader2 size={28} className="animate-spin text-[#b27454]" />
              <span className="text-sm text-[#9a7060]">Đang tải tài liệu…</span>
            </div>
          )}

          {/* Error state */}
          {error && (
            <div className="absolute inset-0 flex flex-col items-center justify-center gap-4 bg-[#fafafa] z-10 p-8 text-center">
              <FileText size={40} className="text-[#d4a899]" />
              <div>
                <p className="text-sm font-medium text-[#5a3825] mb-1">
                  Không thể hiển thị tài liệu trong cửa sổ này
                </p>
                <p className="text-xs text-[#9a7060]">
                  Tài liệu có thể ở định dạng không hỗ trợ xem trực tiếp.
                </p>
              </div>
              <a
                href={url}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-2 px-4 py-2 rounded-full bg-[#b27454] text-white text-sm hover:bg-[#9e6040] transition-colors"
              >
                <ExternalLink size={13} />
                Mở trong tab mới
              </a>
            </div>
          )}

          {/* PDF iframe — luôn render để tận dụng cache */}
          {isPdf && (
            <iframe
              ref={iframeRef}
              src={url}
              className="w-full h-full border-0"
              title={title}
              onLoad={() => setLoading(false)}
              onError={() => { setLoading(false); setError(true); }}
              style={{ display: error ? 'none' : 'block' }}
            />
          )}

          {/* Non-PDF: fallback */}
          {!isPdf && !loading && (
            <div className="absolute inset-0 flex flex-col items-center justify-center gap-4 bg-[#fafafa] p-8 text-center">
              <FileText size={40} className="text-[#d4a899]" />
              <div>
                <p className="text-sm font-medium text-[#5a3825] mb-1">
                  Tài liệu này không phải PDF
                </p>
                <p className="text-xs text-[#9a7060]">
                  Chỉ có thể tải về hoặc mở trong tab mới.
                </p>
              </div>
              <a
                href={url}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-2 px-4 py-2 rounded-full bg-[#b27454] text-white text-sm hover:bg-[#9e6040] transition-colors"
                download
              >
                <ExternalLink size={13} />
                Tải về / Mở ngoài
              </a>
            </div>
          )}

          {/* Trigger load for non-PDF */}
          {!isPdf && loading && (
            <div className="hidden">
              <img src={url} alt="" onLoad={() => setLoading(false)} onError={() => setLoading(false)} />
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
