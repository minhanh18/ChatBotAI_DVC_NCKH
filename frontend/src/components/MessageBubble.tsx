// cspell:disable

import React, { useEffect, useMemo, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';
import { vscDarkPlus } from 'react-syntax-highlighter/dist/esm/styles/prism';
import {
  Check,
  ChevronDown,
  ChevronUp,
  Copy,
  MessageCircleWarning,
  Pause,
  Play,
  RotateCcw,
  Square,
  ThumbsDown,
  ThumbsUp,
  X,
} from 'lucide-react';

import type { Citation, LegalRef, Message, ServiceLink } from '../api/client';
import { buildDocumentBaseUrl } from '../api/client';
import { PdfViewerModal } from './PdfViewerModal';
import userAvatarDefault from '../assets/user-warm.svg';

const USER_BOT_AVATAR = '/static/assets/img/chatbot/icon_chatbot_circle_final.png';

type BubbleVariant = 'admin' | 'user';
type DeliveryStatus = 'sending' | 'responding' | 'sent';

type FeedbackMeta = {
  issue_type?: string;
  description?: string;
  toggle?: boolean;
};

interface MessageBubbleProps {
  message: Message;
  userAvatar?: string;
  botAvatar?: string;
  isStreaming?: boolean;
  streamingMode?: 'rag' | 'ai' | 'ai_rag';
  deliveryStatus?: DeliveryStatus;
  onFeedback?: (
    messageId: string,
    rating: 'like' | 'dislike',
    payload?: FeedbackMeta,
  ) => Promise<string | undefined>;
  onStop?: () => void;
  onReload?: () => void;
  variant?: BubbleVariant;
  compact?: boolean;
  legalRefs?: LegalRef[];
  serviceLinks?: ServiceLink[];
}

function formatMessageTime(timestamp?: string | null) {
  if (!timestamp) return '';

  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) return '';

  return new Intl.DateTimeFormat('vi-VN', {
    hour: '2-digit',
    minute: '2-digit',
    day: '2-digit',
    month: '2-digit',
  }).format(date);
}

function normalizeMessageLinks(content: string) {
  return content.replace(
    /(?<!<)(?<!\]\()(?<!href=")(?<!src=")\b((?:https?:\/\/|www\.)[^\s<]+)/gi,
    (raw) => {
      const trimmed = raw.replace(/[),.;!?]+$/g, '');
      const trailing = raw.slice(trimmed.length);
      const normalized = trimmed.startsWith('www.') ? `https://${trimmed}` : trimmed;
      return `<${normalized}>${trailing}`;
    },
  );
}

function normalizeCitationHref(url?: string | null) {
  if (!url) return undefined;
  return url.startsWith('www.') ? `https://${url}` : url;
}

function isUsableCitationUrl(url?: string | null) {
  if (!url) return false;

  const normalized = normalizeCitationHref(url);
  if (!normalized || !/^https?:\/\//i.test(normalized)) return false;

  const lower = normalized.toLowerCase();
  const blocked = [
    '/404',
    '/404.html',
    'page/tim-van-ban.aspx',
    'vbpqtimkiem.aspx',
    'portal.aspx?requesturl=',
    'requesturl=https://vbpl.vn/',
  ];

  return !blocked.some((item) => lower.includes(item));
}

const WEB_SOURCE_PRIORITY = [
  'dichvucong.gov.vn',
  'bocongan.gov.vn',
  'chinhphu.vn',
  'baohiemxahoi.gov.vn',
  'vbpl.vn',
  'luatvietnam.vn',
  'thuvienphapluat.vn',
];

function webPriority(url?: string | null) {
  const href = normalizeCitationHref(url) || '';
  const lower = href.toLowerCase();
  const index = WEB_SOURCE_PRIORITY.findIndex((domain) => lower.includes(domain));
  return index === -1 ? 999 : index;
}

function sanitizeCitations(citations: Citation[]) {
  const seen = new Set<string>();

  const filtered = citations.filter((citation) => {
    const href = normalizeCitationHref(citation.url);

    if (citation.source_type === 'web') {
      if (!isUsableCitationUrl(href)) return false;

      const key = String(href || citation.document_name).toLowerCase().trim();
      if (!key || seen.has(key)) return false;

      seen.add(key);
      return true;
    }

    const key = String(href || citation.document_id || citation.segment_id || citation.document_name)
      .toLowerCase()
      .trim();

    if (!key || seen.has(key)) return false;

    seen.add(key);
    return true;
  });

  return filtered.sort((a, b) => {
    if (a.source_type === 'web' && b.source_type === 'web') {
      return webPriority(a.url) - webPriority(b.url);
    }

    if (a.source_type === 'web') return 1;
    if (b.source_type === 'web') return -1;

    return 0;
  });
}

function stripMarkdownForSpeech(content: string) {
  return content
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/!\[[^\]]*\]\([^)]*\)/g, ' ')
    .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '$1')
    .replace(/^>\s?/gm, '')
    .replace(/[#*_~-]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

function nthDocumentCitation(citations: Citation[], n: number) {
  const docs = citations.filter((citation) => citation.source_type !== 'web' && citation.document_id);
  return docs[n - 1] ?? null;
}

function linkifyInlinePageRefs(content: string, citations: Citation[]) {
  const docCitations = citations.filter((citation) => citation.source_type !== 'web' && citation.document_id);
  const webCitations = citations.filter((citation) => citation.source_type === 'web');
  const docCount = docCitations.length;

  if (!docCount && !webCitations.length) return content;

  let result = content;

  result = result.replace(/\(\[(\d+)\],\s*trang\s*(\d+)(?:\s*-\s*\d+)?\)/gi, (match, nStr, pageStr) => {
    const n = Number.parseInt(nStr, 10);
    const page = Number.parseInt(pageStr, 10);
    const doc = nthDocumentCitation(citations, n);

    if (!doc?.document_id || Number.isNaN(page)) return match;

    return `[([${n}], trang ${page})](/api/documents/${doc.document_id}/file#page=${page})`;
  });

  if (docCitations[0]?.document_id) {
    const firstDocId = docCitations[0].document_id;

    result = result.replace(
      /(?<!\])\((?:Theo\s+[^,()]{1,60},\s*)?trang\s+(\d+)(?:\s*-\s*\d+)?(?:[,\s][^()]{0,60})?\)/gi,
      (match, pageStr) => {
        const page = Number.parseInt(pageStr, 10);
        if (Number.isNaN(page)) return match;

        return `[${match}](/api/documents/${firstDocId}/file#page=${page})`;
      },
    );
  }

  if (webCitations.length > 0) {
    let webIndex = 0;

    result = result.replace(/\(nguồn\)/gi, () => {
      const index = webIndex % webCitations.length;
      const web = webCitations[index];
      const number = docCount + index + 1;
      webIndex += 1;

      if (!web?.url) return `[${number}]`;

      return `[[${number}]](${web.url})`;
    });
  }

  result = result.replace(/(?<!\[)\(\[(\d+)\],?\s*\)(?!\()/g, (match, nStr) => {
    const n = Number.parseInt(nStr, 10);

    if (n <= docCount) {
      const doc = nthDocumentCitation(citations, n);
      if (!doc?.document_id) return '';

      return `[([${n}])](/api/documents/${doc.document_id}/file)`;
    }

    const web = webCitations[n - docCount - 1];
    if (!web?.url) return '';

    return `[([${n}])](${web.url})`;
  });

  result = result.replace(/\s*\(\[\d+\],?\s*\)(?!\s*trang)/gi, '');

  return result;
}

function pageFromDocumentHref(href?: string) {
  const match = String(href || '').match(/#page=(\d+)/i);
  return match ? Number(match[1]) : undefined;
}

function MarkdownContent({
  content,
  variant,
  compact,
  citations,
  onOpenDocumentPage,
}: {
  content: string;
  variant: BubbleVariant;
  compact: boolean;
  citations: Citation[];
  onOpenDocumentPage?: (url: string, page?: number) => void;
}) {
  const isWarm = variant === 'user';
  const processedContent = useMemo(() => linkifyInlinePageRefs(content, citations), [content, citations]);

  return (
    <div
      className={`prose prose-sm max-w-none ${
        isWarm
          ? 'prose-headings:text-[#734232] prose-p:text-slate-700 prose-a:text-[#b2694c] prose-strong:text-[#734232] prose-code:text-[#8c533f] prose-code:bg-[#fff1e8] prose-blockquote:border-l-[#d8b6a6] prose-blockquote:text-[#7d5a49] prose-li:text-slate-700 prose-th:bg-[#fff7f2]'
          : 'prose-slate prose-headings:text-slate-800 prose-p:text-slate-700 prose-a:text-indigo-600 prose-strong:text-slate-800 prose-code:text-pink-600 prose-code:bg-pink-50 prose-blockquote:border-l-indigo-400 prose-blockquote:text-slate-600 prose-li:text-slate-700 prose-th:bg-slate-50'
      } ${
        compact ? 'text-[13px] leading-6' : 'text-[14px] leading-[1.68]'
      } prose-headings:font-semibold prose-p:leading-[1.8] prose-p:my-2.5 prose-a:no-underline hover:prose-a:underline prose-code:px-1 prose-code:rounded prose-pre:p-0 prose-pre:bg-transparent prose-ol:my-3 prose-ul:my-3 prose-table:text-sm`}
    >
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a({ href, children, ...props }: any) {
            const safeHref = typeof href === 'string' && href.startsWith('www.') ? `https://${href}` : href;
            const isInternalDoc = typeof safeHref === 'string' && safeHref.startsWith('/api/documents/');

            return (
              <a
                href={safeHref}
                target={isInternalDoc ? undefined : '_blank'}
                rel={isInternalDoc ? undefined : 'noreferrer'}
                onClick={
                  isInternalDoc && onOpenDocumentPage
                    ? (event) => {
                        event.preventDefault();
                        onOpenDocumentPage(safeHref, pageFromDocumentHref(safeHref));
                      }
                    : undefined
                }
                {...props}
              >
                {children}
              </a>
            );
          },
          code({ inline, className, children, ...props }: any) {
            const match = /language-(\w+)/.exec(className || '');
            const codeString = String(children).replace(/\n$/, '');

            if (!inline && match) {
              return <CodeBlock language={match[1]} code={codeString} variant={variant} />;
            }

            return (
              <code className={className} {...props}>
                {children}
              </code>
            );
          },
        }}
      >
        {processedContent}
      </ReactMarkdown>
    </div>
  );
}

function CitationsPanel({
  citations,
  variant,
  compact,
  open,
  onToggle,
}: {
  citations: Citation[];
  variant: BubbleVariant;
  compact: boolean;
  open: boolean;
  onToggle: () => void;
}) {
  const isWarm = variant === 'user';
  const visibleCitations = sanitizeCitations(citations);
  const [pdfModal, setPdfModal] = useState<{
    url: string;
    title: string;
    pageNumber?: number;
  } | null>(null);

  if (visibleCitations.length === 0) return null;

  const documentCount = visibleCitations.filter((citation) => citation.source_type !== 'web').length;

  return (
    <div
      className={`mt-4 rounded-2xl border ${
        isWarm ? 'border-[#ead8cf] bg-[#fff7f2]' : 'border-slate-200 bg-slate-50'
      } ${compact ? 'px-3 py-3' : 'px-4 py-4'}`}
    >
      <button
        type="button"
        onClick={onToggle}
        className={`w-full flex items-center justify-between gap-3 text-left rounded-xl transition ${
          isWarm ? 'hover:bg-[#fff3ed]' : 'hover:bg-white/70'
        } ${compact ? 'px-1 py-0.5' : 'px-1 py-1'}`}
      >
        <div className={`font-semibold ${compact ? 'text-[11px]' : 'text-xs'} ${isWarm ? 'text-[#8c533f]' : 'text-slate-600'}`}>
          Tham khảo thêm
        </div>

        <span
          className={`inline-flex items-center gap-1 rounded-full border ${
            compact ? 'px-2 py-0.5 text-[10px]' : 'px-2.5 py-1 text-[11px]'
          } ${isWarm ? 'border-[#dfc2b4] text-[#9a624a] bg-white' : 'border-slate-200 text-slate-600 bg-white'}`}
        >
          {open ? 'Thu gọn' : 'Xem nguồn'}
          {open ? <ChevronUp size={compact ? 10 : 11} /> : <ChevronDown size={compact ? 10 : 11} />}
        </span>
      </button>

      {open && (
        <div className="mt-3 space-y-2.5">
          {visibleCitations.map((citation, index) => {
            const href = normalizeCitationHref(citation.url);
            const isDoc = citation.source_type !== 'web';

            const docIndex = visibleCitations.filter((item) => item.source_type !== 'web').indexOf(citation);
            const webIndex = visibleCitations.filter((item) => item.source_type === 'web').indexOf(citation);
            const citationNumber = isDoc ? docIndex + 1 : documentCount + webIndex + 1;

            const title = citation.document_name || `Nguồn ${citationNumber}`;
            const docPageUrl = isDoc ? buildDocumentBaseUrl(citation) : null;
            const canOpenDoc = isDoc && !!docPageUrl;

            return (
              <div
                key={`${citation.segment_id || citation.document_name || citation.url || index}-${index}`}
                className={`rounded-2xl border ${
                  isWarm ? 'border-[#ead8cf] bg-white' : 'border-slate-200 bg-white'
                } ${compact ? 'px-3 py-2.5' : 'px-3.5 py-3'}`}
              >
                <div className="flex items-start gap-2 min-w-0">
                  <span
                    className={`shrink-0 inline-flex items-center justify-center rounded-full font-semibold ${
                      compact ? 'w-4 h-4 text-[9px]' : 'w-5 h-5 text-[10px]'
                    } ${
                      isDoc
                        ? isWarm
                          ? 'bg-[#f5ddd1] text-[#734232]'
                          : 'bg-indigo-100 text-indigo-700'
                        : isWarm
                          ? 'bg-[#fef3ec] text-[#9a624a]'
                          : 'bg-slate-100 text-slate-600'
                    }`}
                  >
                    {citationNumber}
                  </span>

                  <div className="min-w-0 flex-1">
                    {canOpenDoc ? (
                      <button
                        type="button"
                        onClick={() =>
                          setPdfModal({
                            url: docPageUrl,
                            title,
                            pageNumber: undefined, // Fix #1: panel nguồn luôn mở ở trang đầu, không nhảy trang
                          })
                        }
                        className={`font-medium text-left hover:underline underline-offset-2 ${
                          compact ? 'text-[11px]' : 'text-xs'
                        } ${isWarm ? 'text-[#734232]' : 'text-slate-700'}`}
                      >
                        {title}
                      </button>
                    ) : isUsableCitationUrl(href) ? (
                      <a
                        href={href}
                        target="_blank"
                        rel="noreferrer"
                        className={`font-medium underline-offset-2 hover:underline ${
                          compact ? 'text-[11px]' : 'text-xs'
                        } ${isWarm ? 'text-[#734232]' : 'text-slate-700'}`}
                      >
                        {title}
                      </a>
                    ) : (
                      <div className={`font-medium ${compact ? 'text-[11px]' : 'text-xs'} ${isWarm ? 'text-[#734232]' : 'text-slate-700'}`}>
                        {title}
                      </div>
                    )}

                    {!isDoc && citation.domain && (
                      <div className={`mt-0.5 truncate ${compact ? 'text-[9px]' : 'text-[10px]'} text-slate-400`}>
                        {citation.domain}
                      </div>
                    )}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {pdfModal && (
        <PdfViewerModal
          url={pdfModal.url}
          title={pdfModal.title}
          pageNumber={pdfModal.pageNumber}
          onClose={() => setPdfModal(null)}
        />
      )}
    </div>
  );
}

function ThinkingDots({ compact }: { compact: boolean }) {
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    const start = Date.now();
    const id = window.setInterval(() => setElapsed(Math.floor((Date.now() - start) / 1000)), 500);

    return () => window.clearInterval(id);
  }, []);

  if (elapsed >= 30) {
    const dots = '.'.repeat((Math.floor(elapsed / 1.5) % 3) + 1);

    return (
      <div
        className={`flex items-center gap-1.5 text-[#b27454] ${compact ? 'text-xs' : 'text-sm'}`}
        style={{ animation: 'pulse 1.5s ease-in-out infinite' }}
      >
        <span className="opacity-80">Tôi đang cố gắng truy xuất thông tin, vui lòng chờ thêm giây lát ...</span>
        <span className="font-mono tracking-widest w-6 inline-block">{dots}</span>
      </div>
    );
  }

  return (
    <div className={`flex items-center gap-2 ${compact ? 'py-1' : 'py-1.5'}`} aria-label="Đang phản hồi">
      <span className={`rounded-full bg-[#b27454] animate-pulse ${compact ? 'w-1.5 h-1.5' : 'w-2 h-2'}`} style={{ animationDelay: '0ms' }} />
      <span className={`rounded-full bg-[#b27454] animate-pulse ${compact ? 'w-1.5 h-1.5' : 'w-2 h-2'}`} style={{ animationDelay: '180ms' }} />
      <span className={`rounded-full bg-[#b27454] animate-pulse ${compact ? 'w-1.5 h-1.5' : 'w-2 h-2'}`} style={{ animationDelay: '360ms' }} />
    </div>
  );
}

function LegalRefsPanel({ refs }: { refs: LegalRef[] }) {
  if (!refs?.length) return null;

  return (
    <div className="mt-3 pt-3 border-t border-[#e8d5c8]">
      <p className="text-xs font-semibold text-[#8c6a5b] mb-1.5">⚖️ Căn cứ pháp lý</p>

      <div className="flex flex-col gap-1">
        {refs.map((ref, index) => (
          <div key={`${ref.label}-${index}`} className="flex items-center gap-2 text-xs text-[#734232]">
            {ref.url ? (
              <a href={ref.url} target="_blank" rel="noopener noreferrer" className="underline underline-offset-2 hover:text-[#4a2010]">
                {ref.label}
              </a>
            ) : (
              <span>{ref.label}</span>
            )}

            {ref.status && (
              <span
                className={`px-1.5 py-0.5 rounded-full text-[10px] font-medium ${
                  ref.status.includes('hết') ? 'bg-red-50 text-red-600' : 'bg-green-50 text-green-700'
                }`}
              >
                {ref.status}
              </span>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

function CodeBlock({ language, code, variant }: { language: string; code: string; variant: BubbleVariant }) {
  const [copied, setCopied] = useState(false);
  const isWarm = variant === 'user';

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      // ignore
    }
  };

  return (
    <div className={`relative group rounded-xl overflow-hidden border my-3 ${isWarm ? 'border-[#d9b6a7]' : 'border-slate-700'}`}>
      <div className={`flex items-center justify-between px-4 py-1.5 ${isWarm ? 'bg-[#7d4f3b]' : 'bg-slate-800'}`}>
        <span className="text-xs text-slate-200 font-mono">{language}</span>

        <button type="button" onClick={copy} className="flex items-center gap-1.5 text-xs text-slate-200 hover:text-white transition-colors">
          {copied ? <Check size={12} className="text-green-300" /> : <Copy size={12} />}
          {copied ? 'Đã chép' : 'Copy'}
        </button>
      </div>

      <SyntaxHighlighter
        style={vscDarkPlus}
        language={language}
        PreTag="div"
        customStyle={{
          margin: 0,
          borderRadius: 0,
          fontSize: '0.8rem',
        }}
      >
        {code}
      </SyntaxHighlighter>
    </div>
  );
}

function ServiceLinksPanel({ links, compact }: { links: ServiceLink[]; compact: boolean }) {
  const visible = (links || []).filter((link) => link && link.url).slice(0, 6);

  if (!visible.length) return null;

  return (
    <div className={`mt-3 rounded-2xl border border-[#ead8cf] bg-[#fff7f2] ${compact ? 'p-2.5' : 'p-3'}`}>
      <div className={`${compact ? 'text-[12px]' : 'text-sm'} font-semibold text-[#7d4f3b] mb-2`}>
        Đường link thao tác / hồ sơ
      </div>

      <div className="flex flex-col gap-2">
        {visible.map((link, index) => {
          const label = link.label || link.title || 'Mở đường link';

          return (
            <a
              key={`${link.url}-${index}`}
              href={link.url}
              target="_blank"
              rel="noreferrer noopener"
              className={`${
                compact ? 'text-[12px]' : 'text-sm'
              } inline-flex items-center justify-between gap-2 rounded-xl border border-[#e1c5b7] bg-white px-3 py-2 text-[#8a563f] hover:bg-[#fff1ea] hover:border-[#c8957d] transition-colors`}
            >
              <span className="truncate">{label}</span>
              <span className="shrink-0">↗</span>
            </a>
          );
        })}
      </div>
    </div>
  );
}

function AnswerModeBadge({
  mode,
  compact,
  warm,
}: {
  mode?: 'rag' | 'ai' | 'ai_rag';
  compact: boolean;
  warm: boolean;
}) {
  if (!mode) return null;

  const label =
    mode === 'rag'
      ? 'Tra cứu tài liệu'
      : mode === 'ai_rag'
        ? 'AI + tra cứu'
        : 'AI';

  return (
    <span
      className={`inline-flex items-center rounded-full border ${
        compact ? 'px-2 py-0.5 text-[10px]' : 'px-2.5 py-1 text-[11px]'
      } ${
        warm
          ? 'border-[#e1c5b7] bg-[#fff7f2] text-[#9a624a]'
          : 'border-slate-200 bg-slate-50 text-slate-500'
      }`}
    >
      {label}
    </span>
  );
}

export function MessageBubble({
  message,
  userAvatar = userAvatarDefault,
  botAvatar,
  isStreaming = false,
  streamingMode,
  deliveryStatus = 'sent',
  onFeedback,
  onStop,
  onReload,
  variant = 'admin',
  compact = false,
  legalRefs,
  serviceLinks,
}: MessageBubbleProps) {
  const isUser = message.role === 'user';
  const isWarm = variant === 'user';
  const compactWarm = compact && isWarm;
  const actualBotAvatar = botAvatar || USER_BOT_AVATAR;
  const content = message.content || '';
  const citations = message.citations || [];

  const timeLabel = useMemo(() => formatMessageTime(message.created_at), [message.created_at]);
  const normalizedMarkdown = useMemo(() => normalizeMessageLinks(content), [content]);

  const [copiedAll, setCopiedAll] = useState(false);
  const [feedbackOpen, setFeedbackOpen] = useState(false);
  const [feedbackLoading, setFeedbackLoading] = useState(false);
  const [feedbackType, setFeedbackType] = useState<'like' | 'dislike' | null>(message.feedback ?? null);
  const [thanksMessage, setThanksMessage] = useState('');
  const [issueType, setIssueType] = useState('Thông tin chưa chính xác');
  const [feedbackText, setFeedbackText] = useState('');

  const [ttsProgress, setTtsProgress] = useState(0);
  const [ttsSpeaking, setTtsSpeaking] = useState(false);
  // Panel nguồn tham khảo mặc định ĐÓNG — user phải bấm "Xem nguồn" để mở.
  const [sourcesOpen, setSourcesOpen] = useState(false);

  // Reset về đóng khi bubble chuyển từ streaming sang DB message (message.id thay đổi).
  useEffect(() => {
    setSourcesOpen(false);
  }, [message.id]); // eslint-disable-line react-hooks/exhaustive-deps
  const [inlinePdfModal, setInlinePdfModal] = useState<{
    url: string;
    title: string;
    pageNumber?: number;
  } | null>(null);

  const utteranceRef = useRef<SpeechSynthesisUtterance | null>(null);
  const progressOffsetRef = useRef(0);
  const speechTextRef = useRef('');
  const voicesRef = useRef<SpeechSynthesisVoice[]>([]);

  useEffect(() => {
    if (typeof window === 'undefined' || !('speechSynthesis' in window)) return;

    const loadVoices = () => {
      const list = window.speechSynthesis.getVoices();
      if (list.length > 0) voicesRef.current = list;
    };

    loadVoices();
    window.speechSynthesis.addEventListener('voiceschanged', loadVoices);

    return () => window.speechSynthesis.removeEventListener('voiceschanged', loadVoices);
  }, []);

  useEffect(() => {
    setFeedbackType(message.feedback ?? null);
  }, [message.feedback]);

  useEffect(() => {
    speechTextRef.current = stripMarkdownForSpeech(content);

    return () => {
      if (utteranceRef.current && typeof window !== 'undefined' && 'speechSynthesis' in window) {
        window.speechSynthesis.cancel();
        utteranceRef.current = null;
      }
    };
  }, [content]);

  const copyFull = async () => {
    try {
      await navigator.clipboard.writeText(content);
      setCopiedAll(true);
      window.setTimeout(() => setCopiedAll(false), 2000);
    } catch {
      // ignore
    }
  };

  const submitFeedback = async (rating: 'like' | 'dislike', payload?: FeedbackMeta) => {
    if (!onFeedback) return;

    setFeedbackLoading(true);

    try {
      const messageText = await onFeedback(message.id, rating, payload);
      const nextFeedback = payload?.toggle ? null : rating;

      setFeedbackType(nextFeedback);
      setThanksMessage(messageText || (nextFeedback ? 'Cảm ơn bạn đã gửi đánh giá.' : 'Đã hủy đánh giá.'));

      if (rating === 'dislike' && !payload?.toggle) {
        setFeedbackOpen(false);
        setFeedbackText('');
        setIssueType('Thông tin chưa chính xác');
      }

      window.setTimeout(() => setThanksMessage(''), 2400);
    } finally {
      setFeedbackLoading(false);
    }
  };

  const ttsAvailable = !isUser && !isStreaming && typeof window !== 'undefined' && 'speechSynthesis' in window;

  const stopSpeech = () => {
    if (!ttsAvailable) return;

    window.speechSynthesis.cancel();
    utteranceRef.current = null;
    setTtsSpeaking(false);
  };

  const startSpeech = (progressPercent?: number) => {
    if (!ttsAvailable) return;

    const text = speechTextRef.current;
    if (!text) return;

    window.speechSynthesis.cancel();

    const percent = typeof progressPercent === 'number' ? progressPercent : ttsProgress;
    const startIndex = Math.max(0, Math.min(text.length - 1, Math.floor((percent / 100) * text.length)));

    progressOffsetRef.current = startIndex;

    const utterance = new SpeechSynthesisUtterance(text.slice(startIndex));
    utterance.lang = 'vi-VN';
    utterance.rate = 1;
    utterance.pitch = 1;

    // Fix #8: đảm bảo voices đã load; ưu tiên giọng nam tiếng Việt
    const freshVoices = window.speechSynthesis.getVoices();
    if (freshVoices.length > 0) voicesRef.current = freshVoices;
    const allVoices = voicesRef.current;

    const viVoices = allVoices.filter(
      (v) => v.lang === 'vi-VN' || v.lang.startsWith('vi'),
    );

    // Tên giọng nam tiếng Việt phổ biến trên Windows/Chrome
    const MALE_SIGNALS = ['nam', 'male', 'hung', 'son', 'quoc'];
    const FEMALE_SIGNALS = ['lan', 'linh', 'mai', 'nhi', 'thu', 'female', 'woman'];

    const maleVoice = viVoices.find((v) => {
      const n = v.name.toLowerCase();
      return MALE_SIGNALS.some((m) => n.includes(m));
    });
    const nonFemaleVoice = viVoices.find((v) => {
      const n = v.name.toLowerCase();
      return !FEMALE_SIGNALS.some((f) => n.includes(f));
    });
    const viVoice = maleVoice ?? nonFemaleVoice ?? viVoices[0] ?? null;

    if (viVoice) utterance.voice = viVoice;

    utterance.onstart = () => setTtsSpeaking(true);

    utterance.onboundary = (event: SpeechSynthesisEvent) => {
      if (event.name && event.name !== 'word' && event.name !== 'sentence') return;

      const total = text.length || 1;
      const current = Math.min(total, progressOffsetRef.current + (event.charIndex || 0));

      setTtsProgress((current / total) * 100);
    };

    utterance.onend = () => {
      utteranceRef.current = null;
      setTtsSpeaking(false);
      setTtsProgress(100);
    };

    utterance.onerror = () => {
      utteranceRef.current = null;
      setTtsSpeaking(false);
    };

    utteranceRef.current = utterance;
    window.speechSynthesis.speak(utterance);
  };

  const toggleSpeech = () => {
    if (!ttsAvailable) return;

    if (ttsSpeaking) {
      stopSpeech();
      return;
    }

    const resumeFrom = ttsProgress >= 99 ? 0 : ttsProgress;

    if (resumeFrom === 0) {
      setTtsProgress(0);
    }

    startSpeech(resumeFrom);
  };

  const bubbleBorderRadius = isUser
    ? compactWarm
      ? 'rounded-[20px] rounded-tr-[12px]'
      : 'rounded-[26px] rounded-tr-[14px]'
    : compactWarm
      ? 'rounded-[20px] rounded-tl-[12px]'
      : 'rounded-[28px] rounded-tl-[14px]';

  const deliveryText =
    deliveryStatus === 'sending'
      ? 'Đang gửi'
      : deliveryStatus === 'responding'
        ? 'Đang phản hồi'
        : 'Đã gửi';

  return (
    <div className={`flex ${isUser ? 'justify-end' : 'justify-start'} ${compactWarm ? 'mb-4' : 'mb-6'}`}>
      <div className={`w-full ${compactWarm ? 'max-w-[94%]' : 'max-w-[920px]'}`}>
        <div className={`flex items-start gap-3 ${isUser ? 'justify-end' : 'justify-start'}`}>
          {!isUser && (
            <img
              src={actualBotAvatar}
              alt="Bot"
              className={`${compactWarm ? 'w-10 h-10 mt-1' : 'w-12 h-12 mt-1'} rounded-full object-cover shrink-0`}
            />
          )}

          <div
            className={`min-w-0 ${
              isUser
                ? compactWarm
                  ? 'max-w-[78%]'
                  : 'max-w-[760px]'
                : compactWarm
                  ? 'flex-1 max-w-[calc(100%-56px)]'
                  : 'flex-1 max-w-[760px]'
            }`}
          >
            <div
              className={[
                'border shadow-[0_4px_14px_rgba(116,76,56,0.04)]',
                bubbleBorderRadius,
                compactWarm ? 'px-4 py-3.5' : 'px-3.5 py-2.5',
                isUser
                  ? 'bg-[#b27454] border-[#b27454] text-white ml-auto'
                  : isWarm
                    ? 'bg-[#fffdfa] border-[#ead8cf] text-slate-700'
                    : 'bg-white border-slate-200 text-slate-700',
              ].join(' ')}
            >
              {!isUser && ttsAvailable && (
                <div
                  className={`mb-3 rounded-2xl border ${
                    isWarm ? 'border-[#ead8cf] bg-[#fff7f2]' : 'border-slate-200 bg-slate-50'
                  } ${compactWarm ? 'px-2.5 py-1.5' : 'px-2.5 py-2'}`}
                >
                  <div className="flex items-center gap-2">
                    <button
                      type="button"
                      onClick={toggleSpeech}
                      className={`shrink-0 inline-flex items-center justify-center rounded-full ${
                        compactWarm ? 'w-7 h-7' : 'w-8 h-8'
                      } ${isWarm ? 'bg-[#b2694c] text-white' : 'bg-indigo-600 text-white'}`}
                      title={ttsSpeaking ? 'Tạm dừng phát' : 'Phát nội dung'}
                    >
                      {ttsSpeaking ? (
                        <Pause size={compactWarm ? 12 : 13} />
                      ) : (
                        <Play size={compactWarm ? 12 : 13} className="translate-x-[0.5px]" />
                      )}
                    </button>

                    <input
                      type="range"
                      min={0}
                      max={100}
                      step={1}
                      value={Number.isFinite(ttsProgress) ? ttsProgress : 0}
                      onChange={(event) => {
                        const next = Number(event.target.value);
                        setTtsProgress(next);
                        stopSpeech();
                      }}
                      onMouseUp={() => startSpeech(ttsProgress)}
                      onTouchEnd={() => startSpeech(ttsProgress)}
                      className="flex-1 accent-[#b2694c] h-1.5"
                      aria-label="Thanh phát nội dung"
                    />
                  </div>
                </div>
              )}

              {isUser ? (
                <div className={`${compactWarm ? 'text-[14px] leading-6' : 'text-[15px] leading-[1.7]'} whitespace-pre-wrap break-words text-left`}>
                  {content}
                </div>
              ) : isStreaming && !content.trim() ? (
                <ThinkingDots compact={compactWarm} />
              ) : (
                <MarkdownContent
                  content={normalizedMarkdown}
                  variant={variant}
                  compact={compactWarm}
                  citations={citations}
                  onOpenDocumentPage={(url, pageNumber) =>
                    setInlinePdfModal({
                      url,
                      title: 'Tài liệu nguồn',
                      pageNumber,
                    })
                  }
                />
              )}

              {serviceLinks && serviceLinks.length > 0 && <ServiceLinksPanel links={serviceLinks} compact={compactWarm} />}

              {!isStreaming && legalRefs && legalRefs.length > 0 && <LegalRefsPanel refs={legalRefs} />}

              {citations.length > 0 && (
                <CitationsPanel
                  citations={citations}
                  variant={variant}
                  compact={compactWarm}
                  open={sourcesOpen}
                  onToggle={() => setSourcesOpen((prev) => !prev)}
                />
              )}
            </div>

            <div
              className={`mt-1.5 flex flex-wrap items-center ${compactWarm ? 'gap-1' : 'gap-1.5'} ${
                compactWarm ? 'text-[10px]' : 'text-[11px]'
              } ${isWarm ? 'text-[#a08a80]' : 'text-slate-400'} ${isUser ? 'justify-end mr-1' : 'justify-start ml-1'}`}
            >
              {deliveryStatus !== 'sent' && isUser && <span>{deliveryText}</span>}

              {!isStreaming && timeLabel && <span>{timeLabel}</span>}

              {isStreaming && !isUser && (
                <span className={`inline-flex items-center gap-1.5 ${isWarm ? 'text-[#9a624a]' : 'text-slate-500'}`}>
                  Đang phản hồi ...
                </span>
              )}

              {!isUser && !isStreaming && <AnswerModeBadge mode={streamingMode} compact={compactWarm} warm={isWarm} />}

              {!isUser && !isStreaming && (
                <>
                  <button
                    type="button"
                    onClick={copyFull}
                    className={`inline-flex items-center gap-1 rounded-full border transition-colors ${
                      compactWarm ? 'px-2 py-0.5' : 'px-2.5 py-1'
                    } ${isWarm ? 'border-[#ead5c9] bg-white text-[#8c533f] hover:border-[#d8b6a6]' : 'border-slate-200 bg-white text-slate-500 hover:text-slate-700'}`}
                  >
                    {copiedAll ? <Check size={compactWarm ? 10 : 11} /> : <Copy size={compactWarm ? 10 : 11} />}
                    {copiedAll ? 'Đã chép' : 'Sao chép'}
                  </button>

                  {onReload && (
                    <button
                      type="button"
                      onClick={onReload}
                      className={`inline-flex items-center gap-1 rounded-full border transition-colors ${
                        compactWarm ? 'px-2 py-0.5' : 'px-2.5 py-1'
                      } ${isWarm ? 'border-[#ead5c9] bg-white text-[#8c533f] hover:border-[#d8b6a6]' : 'border-slate-200 bg-white text-slate-500 hover:text-slate-700'}`}
                    >
                      <RotateCcw size={compactWarm ? 10 : 11} />
                      Tạo lại
                    </button>
                  )}
                </>
              )}

              {/* Fix #6: Nút Dừng đã có ở thanh input ChatWindow — không hiện thêm ở đây */}

              {!isUser && !isStreaming && onFeedback && (
                <>
                  <button
                    type="button"
                    onClick={() => submitFeedback('like', feedbackType === 'like' ? { toggle: true } : undefined)}
                    disabled={feedbackLoading}
                    className={`inline-flex items-center gap-1 rounded-full border transition-colors ${
                      compactWarm ? 'px-1.5 py-0.5' : 'px-2.5 py-1'
                    } ${
                      feedbackType === 'like'
                        ? 'border-[#d8b49e] bg-[#fbefe8] text-[#8c533f]'
                        : 'border-[#ead5c9] bg-white text-[#8c533f] hover:border-[#d8b6a6]'
                    } whitespace-nowrap`}
                  >
                    <ThumbsUp size={compactWarm ? 10 : 11} />
                    Hữu ích
                  </button>

                  <button
                    type="button"
                    onClick={() => {
                      if (feedbackType === 'dislike') {
                        submitFeedback('dislike', { toggle: true });
                      } else {
                        setFeedbackOpen(true);
                      }
                    }}
                    disabled={feedbackLoading}
                    className={`inline-flex items-center gap-1 rounded-full border transition-colors ${
                      compactWarm ? 'px-1.5 py-0.5' : 'px-2.5 py-1'
                    } ${
                      feedbackType === 'dislike'
                        ? 'border-[#d8b49e] bg-[#fbefe8] text-[#8c533f]'
                        : 'border-[#ead5c9] bg-white text-[#8c533f] hover:border-[#d8b6a6]'
                    } whitespace-nowrap`}
                  >
                    <ThumbsDown size={compactWarm ? 10 : 11} />
                    Chưa đúng
                  </button>
                </>
              )}
            </div>

            {thanksMessage && !isStreaming && !isUser && (
              <div
                className={`mt-2 ml-1 inline-flex items-center gap-1.5 rounded-full border ${
                  compactWarm ? 'px-2.5 py-0.5 text-[10px]' : 'px-3 py-1 text-[11px]'
                } border-[#e6d5c7] bg-[#fff7f2] text-[#8c533f]`}
              >
                <Check size={compactWarm ? 10 : 11} />
                {thanksMessage}
              </div>
            )}
          </div>

          {isUser && (
            <img
              src={userAvatar}
              alt="User"
              className={`${compactWarm ? 'w-10 h-10 mt-1' : 'w-12 h-12 mt-1'} rounded-full object-cover shrink-0`}
            />
          )}
        </div>
      </div>

      {inlinePdfModal && (
        <PdfViewerModal
          url={inlinePdfModal.url}
          title={inlinePdfModal.title}
          pageNumber={inlinePdfModal.pageNumber}
          onClose={() => setInlinePdfModal(null)}
        />
      )}

      {feedbackOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center px-4" role="dialog" aria-modal="true">
          <button
            type="button"
            aria-label="Đóng"
            className="absolute inset-0 bg-slate-900/25 backdrop-blur-[1px]"
            onClick={() => setFeedbackOpen(false)}
          />

          <div className="relative w-full max-w-3xl rounded-3xl border border-[#ead5c9] bg-white shadow-[0_24px_80px_rgba(15,23,42,0.18)]">
            <div className="flex items-center justify-between gap-3 px-5 py-4 border-b border-[#ead5c9]">
              <div className="flex items-center gap-3">
                <div className="w-10 h-10 rounded-2xl bg-[#fff3ec] text-[#8c533f] flex items-center justify-center">
                  <MessageCircleWarning size={18} />
                </div>

                <div>
                  <div className="font-semibold text-[#734232]">Phản hồi về câu trả lời</div>
                  <div className="text-sm text-[#9a7b6d]">Bạn có thể cho biết phần nào chưa phù hợp để hệ thống cải thiện.</div>
                </div>
              </div>

              <button
                type="button"
                onClick={() => setFeedbackOpen(false)}
                className="w-9 h-9 rounded-xl border border-[#ead5c9] text-[#8c533f] flex items-center justify-center hover:bg-[#fff7f2]"
                aria-label="Đóng"
              >
                <X size={16} />
              </button>
            </div>

            <div className="px-5 py-5 space-y-4">
              <div>
                <label className="block text-sm font-medium text-[#734232] mb-2">Vấn đề gặp phải</label>

                <select
                  value={issueType}
                  onChange={(event) => setIssueType(event.target.value)}
                  className="w-full rounded-2xl border border-[#ead5c9] bg-white px-4 py-3 text-sm text-slate-700 outline-none focus:border-[#c8957d]"
                >
                  <option>Thông tin chưa chính xác</option>
                  <option>Thiếu căn cứ / nguồn tham khảo</option>
                  <option>Câu trả lời chưa rõ ràng</option>
                  <option>Không đúng trọng tâm câu hỏi</option>
                  <option>Khác</option>
                </select>
              </div>

              <div>
                <label className="block text-sm font-medium text-[#734232] mb-2">Mô tả thêm</label>

                <textarea
                  value={feedbackText}
                  onChange={(event) => setFeedbackText(event.target.value)}
                  rows={5}
                  placeholder="Nhập góp ý của bạn..."
                  className="w-full resize-none rounded-2xl border border-[#ead5c9] bg-white px-4 py-3 text-sm text-slate-700 outline-none focus:border-[#c8957d]"
                />
              </div>
            </div>

            <div className="flex items-center justify-end gap-3 px-5 py-4 border-t border-[#ead5c9]">
              <button
                type="button"
                onClick={() => setFeedbackOpen(false)}
                className="rounded-2xl border border-[#ead5c9] bg-white px-4 py-2 text-sm text-[#8c533f] hover:bg-[#fff7f2]"
              >
                Hủy
              </button>

              <button
                type="button"
                disabled={feedbackLoading}
                onClick={() =>
                  submitFeedback('dislike', {
                    issue_type: issueType,
                    description: feedbackText.trim() || undefined,
                  })
                }
                className="rounded-2xl bg-[#b27454] px-4 py-2 text-sm font-medium text-white hover:bg-[#9d6248] disabled:opacity-60"
              >
                Gửi phản hồi
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}