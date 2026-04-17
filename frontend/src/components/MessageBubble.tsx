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
  ExternalLink,
  MessageCircleWarning,
  Pause,
  Play,
  ThumbsDown,
  ThumbsUp,
  X,
} from 'lucide-react';
import type { Citation, FeedbackPayload, Message } from '../api/client';
import userAvatarDefault from '../assets/user-warm.svg';

const USER_BOT_AVATAR = '/static/assets/img/chatbot/icon_chatbot_circle_final.png';

type BubbleVariant = 'admin' | 'user';
type DeliveryStatus = 'sending' | 'responding' | 'sent';

interface MessageBubbleProps {
  message: Message;
  userAvatar?: string;
  botAvatar?: string;
  isStreaming?: boolean;
  streamingMode?: 'rag' | 'ai';
  deliveryStatus?: DeliveryStatus;
  onFeedback?: (messageId: string, rating: 'like' | 'dislike', payload?: FeedbackPayload) => Promise<string | undefined>;
  variant?: BubbleVariant;
  compact?: boolean;
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
    /(?<!\]\()(?<!href=")(?<!src=")\b((?:https?:\/\/|www\.)[^\s<]+)/gi,
    (raw) => {
      const trimmed = raw.replace(/[),.;!?]+$/g, '');
      const trailing = raw.slice(trimmed.length);
      const normalized = trimmed.startsWith('www.') ? `https://${trimmed}` : trimmed;
      return `<${normalized}>${trailing}`;
    },
  );
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

export function MessageBubble({
  message,
  userAvatar = userAvatarDefault,
  botAvatar,
  isStreaming = false,
  streamingMode,
  deliveryStatus = 'sent',
  onFeedback,
  variant = 'admin',
  compact = false,
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
  const [sourcesOpen, setSourcesOpen] = useState(false);
  const utteranceRef = useRef<SpeechSynthesisUtterance | null>(null);
  const progressOffsetRef = useRef(0);
  const speechTextRef = useRef('');

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
      setTimeout(() => setCopiedAll(false), 2000);
    } catch {
      // ignore
    }
  };

  const submitFeedback = async (rating: 'like' | 'dislike', payload?: { issue_type?: string; description?: string; toggle?: boolean }) => {
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
      setTimeout(() => setThanksMessage(''), 2400);
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
    if (resumeFrom === 0) setTtsProgress(0);
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
    deliveryStatus === 'sending' ? 'Đang gửi' : deliveryStatus === 'responding' ? 'Đang phản hồi' : 'Đã gửi';

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

          <div className={`min-w-0 ${isUser ? (compactWarm ? 'max-w-[78%]' : 'max-w-[760px]') : (compactWarm ? 'flex-1 max-w-[calc(100%-56px)]' : 'flex-1 max-w-[760px]')}`}>
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
                <div className={`mb-3 rounded-2xl border ${isWarm ? 'border-[#ead8cf] bg-[#fff7f2]' : 'border-slate-200 bg-slate-50'} ${compactWarm ? 'px-3 py-2.5' : 'px-3 py-2.5'}`}>
                  <div className="flex items-center gap-2.5">
                    <button
                      onClick={toggleSpeech}
                      className={`shrink-0 inline-flex items-center justify-center rounded-full ${compactWarm ? 'w-8 h-8' : 'w-9 h-9'} ${isWarm ? 'bg-[#b2694c] text-white' : 'bg-indigo-600 text-white'}`}
                      title={ttsSpeaking ? 'Tạm dừng phát' : 'Phát nội dung'}
                    >
                      {ttsSpeaking ? <Pause size={compactWarm ? 14 : 15} /> : <Play size={compactWarm ? 14 : 15} className="translate-x-[1px]" />}
                    </button>
                    <input
                      type="range"
                      min={0}
                      max={100}
                      step={1}
                      value={Number.isFinite(ttsProgress) ? ttsProgress : 0}
                      onChange={(e) => {
                        const next = Number(e.target.value);
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
                <MarkdownContent content={normalizedMarkdown} variant={variant} compact={compactWarm} />
              )}

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

            <div className={`mt-2 flex flex-wrap items-center gap-2 ${compactWarm ? 'text-[10px]' : 'text-[11px]'} ${isWarm ? 'text-[#a08a80]' : 'text-slate-400'} ${isUser ? 'justify-end mr-1' : 'justify-start ml-1'}`}>
              {deliveryStatus !== 'sent' && isUser && <span>{deliveryText}</span>}
              {timeLabel && <span>{timeLabel}</span>}

              {!isUser && (
                <button
                  onClick={copyFull}
                  className={`inline-flex items-center gap-1 rounded-full border transition-colors ${compactWarm ? 'px-2 py-0.5' : 'px-2.5 py-1'} border-[#ead5c9] bg-white text-[#8c533f] hover:text-[#734232] hover:border-[#d8b6a6]`}
                >
                  {copiedAll ? <Check size={compactWarm ? 10 : 11} className="text-emerald-500" /> : <Copy size={compactWarm ? 10 : 11} />}
                  {copiedAll ? 'Đã chép' : 'Copy'}
                </button>
              )}

              {!isUser && !isStreaming && onFeedback && !message.id.startsWith('streaming') && (
                <>
                  <button
                    onClick={() => submitFeedback('like', feedbackType === 'like' ? { toggle: true } : undefined)}
                    disabled={feedbackLoading}
                    className={`inline-flex items-center gap-1 rounded-full border transition-colors ${compactWarm ? 'px-2 py-0.5' : 'px-2.5 py-1'} ${
                      feedbackType === 'like'
                        ? 'border-[#d8b49e] bg-[#fbefe8] text-[#8c533f]'
                        : 'border-[#ead5c9] bg-white text-[#8c533f] hover:border-[#d8b6a6]'
                    }`}
                  >
                    <ThumbsUp size={compactWarm ? 10 : 11} /> Hữu ích
                  </button>
                  <button
                    onClick={() => {
                      if (feedbackType === 'dislike') {
                        submitFeedback('dislike', { toggle: true });
                      } else {
                        setFeedbackOpen(true);
                      }
                    }}
                    disabled={feedbackLoading}
                    className={`inline-flex items-center gap-1 rounded-full border transition-colors ${compactWarm ? 'px-2 py-0.5' : 'px-2.5 py-1'} ${
                      feedbackType === 'dislike'
                        ? 'border-[#d8b49e] bg-[#fbefe8] text-[#8c533f]'
                        : 'border-[#ead5c9] bg-white text-[#8c533f] hover:border-[#d8b6a6]'
                    }`}
                  >
                    <ThumbsDown size={compactWarm ? 10 : 11} /> Chưa ổn
                  </button>
                </>
              )}
            </div>

            {thanksMessage && !isStreaming && !isUser && (
              <div className={`mt-2 ml-1 inline-flex items-center gap-1.5 rounded-full border ${compactWarm ? 'px-2.5 py-0.5 text-[10px]' : 'px-3 py-1 text-[11px]'} border-[#e6d5c7] bg-[#fff7f2] text-[#8c533f]`}>
                <Check size={compactWarm ? 10 : 11} /> {thanksMessage}
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

      {feedbackOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center px-4" role="dialog" aria-modal="true">
          <button
            type="button"
            aria-label="Đóng"
            className="absolute inset-0 bg-slate-900/25 backdrop-blur-[1px]"
            onClick={() => setFeedbackOpen(false)}
          />
          <div className={`relative w-full max-w-3xl rounded-3xl border bg-white shadow-[0_24px_80px_rgba(15,23,42,0.18)] ${isWarm ? 'border-[#ead5c9]' : 'border-[#ead5c9]'}`}>
            <div className={`flex items-center justify-between gap-3 px-5 py-4 border-b ${isWarm ? 'border-[#f1e4db]' : 'border-[#f1e4db]'}`}>
              <div className="flex items-center gap-2 text-sm font-medium text-slate-700">
                <MessageCircleWarning size={16} className="text-[#b2694c]" />
                Ghi nhận đánh giá
              </div>
              <button onClick={() => setFeedbackOpen(false)} className="text-slate-400 hover:text-slate-600">
                <X size={18} />
              </button>
            </div>
            <div className="px-5 py-5 space-y-4">
              <div>
                <label className="block text-xs font-medium text-slate-600 mb-2">Vấn đề gặp phải</label>
                <select
                  value={issueType}
                  onChange={(e) => setIssueType(e.target.value)}
                  className="w-full rounded-2xl border px-4 py-3 text-sm text-slate-700 focus:outline-none border-[#ead5c9] focus:border-[#c89279]"
                >
                  <option>Thông tin chưa chính xác</option>
                  <option>Thông tin còn cũ</option>
                  <option>Thiếu căn cứ / thiếu nguồn</option>
                  <option>Không đúng trọng tâm câu hỏi</option>
                  <option>Trình bày khó hiểu</option>
                  <option>Khác</option>
                </select>
              </div>
              <div>
                <label className="block text-xs font-medium text-slate-600 mb-2">Mô tả vấn đề</label>
                <textarea
                  value={feedbackText}
                  onChange={(e) => setFeedbackText(e.target.value)}
                  rows={5}
                  placeholder="Ví dụ: Bạn đang hỏi tạm trú nhưng bot lại lấy mức phí của thường trú..."
                  className="w-full rounded-2xl border px-4 py-3 text-sm text-slate-700 placeholder-slate-400 focus:outline-none resize-none border-[#ead5c9] focus:border-[#c89279]"
                />
              </div>
              <div className="flex items-center justify-end gap-3">
                <button
                  onClick={() => setFeedbackOpen(false)}
                  className="rounded-2xl border border-slate-200 px-4 py-2.5 text-sm text-slate-600 hover:bg-slate-50"
                >
                  Đóng
                </button>
                <button
                  onClick={() =>
                    submitFeedback('dislike', {
                      issue_type: issueType,
                      description: feedbackText.trim() || undefined,
                    })
                  }
                  disabled={feedbackLoading}
                  className="rounded-2xl px-4 py-2.5 text-sm text-white bg-[#b2694c] hover:bg-[#9b563d] disabled:bg-[#d8b6a6]"
                >
                  {feedbackLoading ? 'Đang gửi...' : 'Gửi đánh giá'}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function MarkdownContent({ content, variant, compact }: { content: string; variant: BubbleVariant; compact: boolean }) {
  const isWarm = variant === 'user';
  return (
    <div
      className={`prose prose-sm max-w-none ${
        isWarm
          ? 'prose-headings:text-[#734232] prose-p:text-slate-700 prose-a:text-[#b2694c] prose-strong:text-[#734232] prose-code:text-[#8c533f] prose-code:bg-[#fff1e8] prose-blockquote:border-l-[#d8b6a6] prose-blockquote:text-[#7d5a49] prose-li:text-slate-700 prose-th:bg-[#fff7f2]'
          : 'prose-slate prose-headings:text-slate-800 prose-p:text-slate-700 prose-a:text-indigo-600 prose-strong:text-slate-800 prose-code:text-pink-600 prose-code:bg-pink-50 prose-blockquote:border-l-indigo-400 prose-blockquote:text-slate-600 prose-li:text-slate-700 prose-th:bg-slate-50'
      } ${compact ? 'text-[13px] leading-6' : 'text-[14px] leading-[1.68]'} prose-headings:font-semibold prose-p:leading-[1.8] prose-p:my-2.5 prose-a:no-underline hover:prose-a:underline prose-code:px-1 prose-code:rounded prose-pre:p-0 prose-pre:bg-transparent prose-ol:my-3 prose-ul:my-3 prose-table:text-sm`}
    >
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a({ href, children, ...props }: any) {
            const safeHref = typeof href === 'string' && href.startsWith('www.') ? `https://${href}` : href;
            return (
              <a href={safeHref} target="_blank" rel="noreferrer" {...props}>
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
        {content}
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
  return (
    <div className={`mt-4 rounded-2xl border ${isWarm ? 'border-[#ead8cf] bg-[#fff7f2]' : 'border-slate-200 bg-slate-50'} ${compact ? 'px-3 py-3' : 'px-4 py-4'}`}>
      <button
        type="button"
        onClick={onToggle}
        className={`w-full flex items-center justify-between gap-3 text-left rounded-xl transition ${isWarm ? 'hover:bg-[#fff3ed]' : 'hover:bg-white/70'} ${compact ? 'px-1 py-0.5' : 'px-1 py-1'}`}
      >
        <div className={`font-semibold ${compact ? 'text-[11px]' : 'text-xs'} ${isWarm ? 'text-[#8c533f]' : 'text-slate-600'}`}>
          Tham khảo thêm
        </div>
        <span className={`inline-flex items-center gap-1 rounded-full border ${compact ? 'px-2 py-0.5 text-[10px]' : 'px-2.5 py-1 text-[11px]'} ${isWarm ? 'border-[#dfc2b4] text-[#9a624a] bg-white' : 'border-slate-200 text-slate-600 bg-white'}`}>
          {open ? 'Thu gọn' : 'Xem nguồn'}
          {open ? <ChevronUp size={compact ? 10 : 11} /> : <ChevronDown size={compact ? 10 : 11} />}
        </span>
      </button>

      {open && (
        <div className="mt-3 space-y-2.5">
          {citations.map((citation, index) => {
            const href = citation.url
              ? citation.url.startsWith('www.')
                ? `https://${citation.url}`
                : citation.url
              : undefined;
            return (
              <div key={`${citation.segment_id || citation.document_name}-${index}`} className={`rounded-2xl border ${isWarm ? 'border-[#ead8cf] bg-white' : 'border-slate-200 bg-white'} ${compact ? 'px-3 py-2.5' : 'px-3.5 py-3'}`}>
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className={`font-medium ${compact ? 'text-[11px]' : 'text-xs'} ${isWarm ? 'text-[#734232]' : 'text-slate-700'}`}>
                      {citation.document_name || `Nguồn ${index + 1}`}
                    </div>
                    {citation.content && (
                      <div className={`mt-1 line-clamp-3 ${compact ? 'text-[10px]' : 'text-[11px]'} ${isWarm ? 'text-[#8a6a5b]' : 'text-slate-500'}`}>
                        {citation.content}
                      </div>
                    )}
                  </div>
                  {href && (
                    <a
                      href={href}
                      target="_blank"
                      rel="noreferrer"
                      className={`shrink-0 inline-flex items-center gap-1 rounded-full border ${compact ? 'px-2 py-1 text-[10px]' : 'px-2.5 py-1 text-[11px]'} ${isWarm ? 'border-[#dfc2b4] text-[#9a624a] hover:bg-[#fff7f2]' : 'border-slate-200 text-slate-600 hover:bg-slate-50'}`}
                    >
                      Mở link
                      <ExternalLink size={compact ? 10 : 11} />
                    </a>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function ThinkingDots({ compact }: { compact: boolean }) {
  return (
    <div className={`flex items-center gap-2 ${compact ? 'py-1' : 'py-1.5'}`} aria-label="Đang phản hồi">
      <span className={`rounded-full bg-[#b27454] animate-pulse ${compact ? 'w-2 h-2' : 'w-2.5 h-2.5'}`} style={{ animationDelay: '0ms' }} />
      <span className={`rounded-full bg-[#b27454] animate-pulse ${compact ? 'w-2 h-2' : 'w-2.5 h-2.5'}`} style={{ animationDelay: '180ms' }} />
      <span className={`rounded-full bg-[#b27454] animate-pulse ${compact ? 'w-2 h-2' : 'w-2.5 h-2.5'}`} style={{ animationDelay: '360ms' }} />
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
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // ignore
    }
  };

  return (
    <div className={`relative group rounded-xl overflow-hidden border my-3 ${isWarm ? 'border-[#d9b6a7]' : 'border-slate-700'}`}>
      <div className={`flex items-center justify-between px-4 py-1.5 ${isWarm ? 'bg-[#7d4f3b]' : 'bg-slate-800'}`}>
        <span className="text-xs text-slate-200 font-mono">{language}</span>
        <button onClick={copy} className="flex items-center gap-1.5 text-xs text-slate-200 hover:text-white transition-colors">
          {copied ? <Check size={12} className="text-green-300" /> : <Copy size={12} />}
          {copied ? 'Đã chép' : 'Copy'}
        </button>
      </div>
      <SyntaxHighlighter
        style={vscDarkPlus}
        language={language}
        PreTag="div"
        customStyle={{ margin: 0, borderRadius: 0, fontSize: '0.8rem' }}
      >
        {code}
      </SyntaxHighlighter>
    </div>
  );
}
