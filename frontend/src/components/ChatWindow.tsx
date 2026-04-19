import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertCircle,
  ImagePlus,
  Menu,
  Mic,
  Minimize2,
  Pin,
  PinOff,
  Plus,
  Search,
  Send,
  Trash2,
  X,
} from 'lucide-react';
import { MessageBubble } from './MessageBubble';
import type { Citation, Conversation, Message } from '../api/client';
import {
  deleteConversation,
  getConversations,
  getMessages,
  streamChat,
  streamChatWithImage,
  submitMessageFeedback,
} from '../api/client';

const USER_BOT_AVATAR = '/static/assets/img/chatbot/icon_chatbot_circle_final.png';
const STREAM_IDLE: StreamState = { active: false, text: '', citations: [] };

function ensureSessionKey(storageKey: string, sessionScope: 'user' | 'admin') {
  const existing = sessionStorage.getItem(storageKey);
  if (existing) {
    if (sessionScope === 'admin' && !existing.startsWith('admin::')) {
      const upgraded = `admin::${existing}`;
      sessionStorage.setItem(storageKey, upgraded);
      return upgraded;
    }
    return existing;
  }
  const created = `${sessionScope === 'admin' ? 'admin::' : ''}sk_${Math.random().toString(36).slice(2)}`;
  sessionStorage.setItem(storageKey, created);
  return created;
}

function mergeCitations(current: Citation[], incoming: Citation[]) {
  const merged: Citation[] = [];
  const seen = new Set<string>();
  for (const citation of [...current, ...incoming]) {
    const key = `${citation.url || citation.segment_id || citation.document_name}`.trim().toLowerCase();
    if (!key || seen.has(key)) continue;
    seen.add(key);
    merged.push(citation);
  }
  return merged;
}

function normalizeConversationTitle(title: string) {
  return title === 'New conversation' ? 'Hội thoại mới' : title;
}

interface StreamState {
  active: boolean;
  text: string;
  citations: Citation[];
  mode?: 'rag' | 'ai';
  error?: string;
}

interface ChatWindowProps {
  standalone?: boolean;
  sessionScope?: 'user' | 'admin';
  adminMode?: boolean;
  hideHistory?: boolean;
  embedded?: boolean;
}

export function ChatWindow({
  standalone = false,
  sessionScope = 'user',
  adminMode = false,
  hideHistory = false,
  embedded = false,
}: ChatWindowProps) {
  const isUserUi = !adminMode;
  const showHistory = !hideHistory && !standalone;
  const sessionStorageKey = `chatbot_sk_${sessionScope}`;
  const pinnedStorageKey = `chatbot_pinned_conversations_${sessionScope}`;
  const sessionKey = useMemo(() => ensureSessionKey(sessionStorageKey, sessionScope), [sessionScope, sessionStorageKey]);

  const [messages, setMessages] = useState<Message[]>([]);
  const [pendingUserText, setPendingUserText] = useState('');
  const [pendingUserTime, setPendingUserTime] = useState<string | null>(null);
  const [stream, setStream] = useState<StreamState>(STREAM_IDLE);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeConvId, setActiveConvId] = useState<string | null>(null);
  const [input, setInput] = useState('');
  const [historySearch, setHistorySearch] = useState('');
  const [sidebarOpen, setSidebarOpen] = useState(showHistory);
  const [selectedImage, setSelectedImage] = useState<File | null>(null);
  const [selectedImagePreview, setSelectedImagePreview] = useState<string | null>(null);
  const [isListening, setIsListening] = useState(false);
  const [voiceStatus, setVoiceStatus] = useState('');
  const [dragOver, setDragOver] = useState(false);
  const [pinnedIds, setPinnedIds] = useState<string[]>(() => {
    try {
      return JSON.parse(localStorage.getItem(pinnedStorageKey) || '[]');
    } catch {
      return [];
    }
  });

  const abortRef = useRef<AbortController | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const recognitionRef = useRef<any>(null);
  const speechStopRequestedRef = useRef(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const latestAssistantRef = useRef<HTMLDivElement>(null);
  const latestReplyAnchorRef = useRef<HTMLDivElement>(null);
  const scrollViewportRef = useRef<HTMLDivElement>(null);
  const headerRef = useRef<HTMLDivElement>(null);
  const pendingAssistantTopRef = useRef(false);
  const autoScrolledForCurrentReplyRef = useRef(false);

  const botAvatar = USER_BOT_AVATAR;
  const appTitle = 'Trợ lý hỗ trợ công dân';
  const compactUi = embedded && isUserUi;

  const framedStandalone = !embedded && typeof window !== 'undefined' && window.parent !== window;

  const requestHostAction = useCallback((action: 'chatbot_close' | 'chatbot_toggle_fullscreen') => {
    if (typeof window === 'undefined' || window.parent === window) return;
    window.parent.postMessage({ type: action }, '*');
  }, []);

  const scrollToBottom = useCallback((behavior: ScrollBehavior = 'smooth') => {
    requestAnimationFrame(() => {
      messagesEndRef.current?.scrollIntoView({ behavior, block: 'end' });
    });
  }, []);

  const scrollLatestReplyIntoView = useCallback((behavior: ScrollBehavior = 'smooth') => {
    requestAnimationFrame(() => {
      latestReplyAnchorRef.current?.scrollIntoView({ behavior, block: 'start' });
    });
  }, []);

  const loadMessages = useCallback(async (convId: string) => {
    try {
      const list = await getMessages(convId);
      setMessages(list);
      requestAnimationFrame(() => scrollToBottom('auto'));
    } catch {
      // ignore
    }
  }, [scrollToBottom]);

  const loadConversations = useCallback(async () => {
    try {
      const list = await getConversations(sessionKey);
      setConversations(list);
      if (hideHistory && !activeConvId && list[0]) {
        setActiveConvId(list[0].id);
        await loadMessages(list[0].id);
      }
    } catch {
      // ignore
    }
  }, [activeConvId, hideHistory, loadMessages, sessionKey]);

  useEffect(() => {
    if (!standalone) {
      loadConversations();
    }
  }, [loadConversations, standalone]);

  useEffect(() => {
    localStorage.setItem(pinnedStorageKey, JSON.stringify(pinnedIds));
  }, [pinnedIds, pinnedStorageKey]);

  useEffect(() => {
    const ta = textareaRef.current;
    if (!ta) return;
    ta.style.height = '0px';
    ta.style.height = `${Math.min(ta.scrollHeight, 180)}px`;
  }, [input]);

  useEffect(() => {
    if (!selectedImage) {
      setSelectedImagePreview(null);
      return;
    }
    const url = URL.createObjectURL(selectedImage);
    setSelectedImagePreview(url);
    return () => URL.revokeObjectURL(url);
  }, [selectedImage]);

  useEffect(() => {
    return () => {
      abortRef.current?.abort();
      stopVoiceInput(true);
    };
  }, []);

  const lastAssistantMessageId = useMemo(() => {
    for (let i = messages.length - 1; i >= 0; i -= 1) {
      if (messages[i].role === 'assistant') return messages[i].id;
    }
    return null;
  }, [messages]);

  useEffect(() => {
    if (pendingAssistantTopRef.current) return;
    scrollToBottom(stream.active ? 'smooth' : 'auto');
  }, [messages, pendingUserText, stream, scrollToBottom]);

  useEffect(() => {
    if (!stream.active || autoScrolledForCurrentReplyRef.current) return;
    autoScrolledForCurrentReplyRef.current = true;
    scrollLatestReplyIntoView('smooth');
  }, [scrollLatestReplyIntoView, stream.active]);

  useEffect(() => {
    if (!pendingAssistantTopRef.current || !lastAssistantMessageId) return;
    requestAnimationFrame(() => {
      const viewport = scrollViewportRef.current;
      const assistantEl = latestAssistantRef.current;
      if (!viewport || !assistantEl) {
        pendingAssistantTopRef.current = false;
        return;
      }
      const headerHeight = headerRef.current?.offsetHeight ?? 0;
      const gap = embedded ? 10 : 12;
      const targetTop = Math.max(0, assistantEl.offsetTop - headerHeight - gap);
      viewport.scrollTo({ top: targetTop, behavior: 'smooth' });
      pendingAssistantTopRef.current = false;
    });
  }, [embedded, lastAssistantMessageId, messages]);

  const selectConversation = useCallback(
    (convId: string) => {
      abortRef.current?.abort();
      setStream(STREAM_IDLE);
      setPendingUserText('');
      setPendingUserTime(null);
      setActiveConvId(convId);
      autoScrolledForCurrentReplyRef.current = false;
      loadMessages(convId);
    },
    [loadMessages],
  );

  const newConversation = useCallback(() => {
    abortRef.current?.abort();
    setStream(STREAM_IDLE);
    setPendingUserText('');
    setPendingUserTime(null);
    setActiveConvId(null);
    setMessages([]);
    setInput('');
    setSelectedImage(null);
    setVoiceStatus('');
    autoScrolledForCurrentReplyRef.current = false;
    requestAnimationFrame(() => textareaRef.current?.focus());
  }, []);

  const handleDeleteConv = async (e: React.MouseEvent, id: string) => {
    e.stopPropagation();
    try {
      await deleteConversation(id);
      const nextConversations = conversations.filter((c) => c.id !== id);
      setConversations(nextConversations);
      setPinnedIds((prev) => prev.filter((item) => item !== id));
      if (activeConvId === id) {
        if (hideHistory && nextConversations[0]) {
          setActiveConvId(nextConversations[0].id);
          await loadMessages(nextConversations[0].id);
        } else {
          newConversation();
        }
      }
    } catch {
      // ignore
    }
  };

  const togglePinned = (e: React.MouseEvent, id: string) => {
    e.stopPropagation();
    setPinnedIds((prev) => (prev.includes(id) ? prev.filter((item) => item !== id) : [id, ...prev]));
  };

  const handleImageFile = (file: File) => {
    if (!file.type.startsWith('image/')) return;
    setSelectedImage(file);
  };

  const stopVoiceInput = useCallback((silent = false) => {
    speechStopRequestedRef.current = true;
    const recognition = recognitionRef.current;
    if (recognition) {
      try {
        recognition.stop();
      } catch {
        // ignore
      }
    }
    recognitionRef.current = null;
    setIsListening(false);
    if (!silent) setVoiceStatus('Đã dừng ghi âm.');
  }, []);

  const startVoiceInput = () => {
    const SpeechRecognition = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
    if (!SpeechRecognition) {
      setVoiceStatus('Trình duyệt hiện tại chưa hỗ trợ ghi âm.');
      return;
    }

    if (isListening) {
      stopVoiceInput();
      return;
    }

    const recognition = new SpeechRecognition();
    recognitionRef.current = recognition;
    speechStopRequestedRef.current = false;
    recognition.lang = 'vi-VN';
    recognition.interimResults = true;
    recognition.continuous = false;
    let finalTranscript = '';

    recognition.onstart = () => {
      setIsListening(true);
      setVoiceStatus('Đang nghe...');
    };

    recognition.onresult = (event: any) => {
      let interim = '';
      for (let i = event.resultIndex; i < event.results.length; i += 1) {
        const transcript = event.results[i][0].transcript;
        if (event.results[i].isFinal) {
          finalTranscript += transcript;
        } else {
          interim += transcript;
        }
      }
      setInput((finalTranscript + interim).trimStart());
    };

    recognition.onerror = () => {
      setIsListening(false);
      recognitionRef.current = null;
      setVoiceStatus('Không thể ghi âm. Vui lòng thử lại.');
    };

    recognition.onend = () => {
      const shouldSubmit = !speechStopRequestedRef.current && finalTranscript.trim();
      setIsListening(false);
      recognitionRef.current = null;
      if (shouldSubmit) {
        setVoiceStatus('');
        setTimeout(() => {
          submitMessage(finalTranscript.trim());
        }, 0);
      } else if (!speechStopRequestedRef.current) {
        setVoiceStatus(finalTranscript.trim() ? 'Đã ghi âm xong.' : 'Không ghi nhận được nội dung giọng nói.');
      }
    };

    recognition.start();
  };

  const submitMessage = useCallback(
    async (forcedText?: string, forcedImage?: File | null) => {
      const image = forcedImage === undefined ? selectedImage : forcedImage;
      const query = (forcedText ?? input).trim();
      if ((!query && !image) || stream.active) return;

      const optimisticUserText = image
        ? `${query || 'Hãy phân tích hình ảnh này.'}\n\n[Hình ảnh đính kèm: ${image.name}]`
        : query;

      setInput('');
      setSelectedImage(null);
      setPendingUserText(optimisticUserText);
      setPendingUserTime(new Date().toISOString());
      setStream({ active: true, text: '', citations: [] });
      setVoiceStatus('');
      autoScrolledForCurrentReplyRef.current = false;

      const abort = new AbortController();
      abortRef.current = abort;
      let resolvedConvId = activeConvId;

      try {
        const callbacks = {
          onConversationId: (id: string) => {
            resolvedConvId = id;
            setActiveConvId(id);
          },
          onMode: (mode: 'rag' | 'ai') => {
            setStream((s) => ({ ...s, mode }));
          },
          onToken: (token: string) => {
            setStream((s) => ({ ...s, text: s.text + token }));
          },
          onCitations: (citations: Citation[]) => {
            setStream((s) => ({ ...s, citations: mergeCitations(s.citations, citations) }));
          },
          onDone: async () => {
            setStream(STREAM_IDLE);
            setPendingUserText('');
            setPendingUserTime(null);
            setVoiceStatus('');
            pendingAssistantTopRef.current = true;
            autoScrolledForCurrentReplyRef.current = false;
            if (resolvedConvId) {
              await loadMessages(resolvedConvId);
            }
            await loadConversations();
          },
          onError: (err: string) => {
            const normalized = (err || '').toLowerCase();
            const shouldHideTechnical =
              !adminMode &&
              (normalized.includes('quota') ||
                normalized.includes('resource_exhausted') ||
                normalized.includes('token') ||
                normalized.includes('429') ||
                normalized.includes('finish_reason') ||
                normalized.includes('model') ||
                normalized.includes('generatecontent'));
            setStream({
              active: false,
              text: '',
              citations: [],
              error: shouldHideTechnical ? 'Xin lỗi! Hiện tại tôi đang gặp một số sự cố.' : err,
            });
            setPendingUserText('');
            setPendingUserTime(null);
          },
        };

        if (image) {
          await streamChatWithImage(
            {
              query,
              conversation_id: activeConvId || undefined,
              session_key: sessionKey,
              image,
            },
            callbacks,
            abort.signal,
          );
        } else {
          await streamChat(
            {
              query,
              conversation_id: activeConvId || undefined,
              session_key: sessionKey,
            },
            callbacks,
            abort.signal,
          );
        }
      } catch (err: any) {
        if (abort.signal.aborted) return;
        setStream({ active: false, text: '', citations: [], error: err?.message || 'Không thể gửi câu hỏi.' });
        setPendingUserText('');
        setPendingUserTime(null);
      }
    },
    [activeConvId, adminMode, input, loadConversations, loadMessages, selectedImage, sessionKey, stream.active],
  );

  const filteredConversations = useMemo(() => {
    const q = historySearch.trim().toLowerCase();
    const items = q
      ? conversations.filter((item) => normalizeConversationTitle(item.title).toLowerCase().includes(q))
      : conversations;

    const pinned = items.filter((item) => pinnedIds.includes(item.id));
    const normal = items.filter((item) => !pinnedIds.includes(item.id));
    return [...pinned, ...normal];
  }, [conversations, historySearch, pinnedIds]);

  const canSend = Boolean(input.trim() || selectedImage) && !stream.active;
  const pendingDeliveryStatus = stream.active && !stream.text && !stream.mode ? 'sending' : 'sent';

  const handlePaste = (e: React.ClipboardEvent<HTMLTextAreaElement>) => {
    const items = Array.from(e.clipboardData.items || []);
    const imageItem = items.find((item) => item.type.startsWith('image/'));
    if (!imageItem) return;
    const file = imageItem.getAsFile();
    if (file) handleImageFile(file);
  };

  const onDropImage = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setDragOver(false);
    const file = e.dataTransfer.files?.[0];
    if (file) handleImageFile(file);
  };

  const submitFeedback = async (
    messageId: string,
    rating: 'like' | 'dislike',
    payload?: { issue_type?: string; description?: string; toggle?: boolean },
  ) => {
    const response = await submitMessageFeedback(messageId, {
      rating,
      issue_type: payload?.issue_type,
      description: payload?.description,
      toggle: payload?.toggle,
    });

    const nextFeedback = payload?.toggle ? null : rating;
    setMessages((prev) => prev.map((msg) => (msg.id === messageId ? { ...msg, feedback: nextFeedback } : msg)));
    return response.message;
  };

  const emptyTitle = isUserUi ? 'Xin chào! Tôi có thể giúp gì?' : 'Xin chào! Tôi có thể hỗ trợ gì cho quản trị?';
  const emptyDesc = isUserUi
    ? 'Đặt câu hỏi, gửi hình ảnh, dùng giọng nói để được hỗ trợ thủ tục hành chính trực tuyến.'
    : 'Theo dõi hội thoại, tài liệu và phản hồi của người dùng.';

  return (
    <div
      className={`h-full w-full max-w-full flex overflow-hidden ${
        isUserUi ? 'bg-[linear-gradient(180deg,#f8f1ed_0%,#f3f4fa_100%)]' : 'bg-[linear-gradient(180deg,#f8f1ed_0%,#f3f4fa_100%)]'
      }`}
    >
      {showHistory && sidebarOpen && (
        <aside className="w-[290px] border-r border-[#d9b7a7] bg-[#7d4f3b] text-white shrink-0 flex flex-col">
          <div className="p-4 border-b border-white/15">
            <div className="flex items-center justify-between gap-3">
              <div>
                <div className="text-xl font-semibold">Lịch sử hội thoại</div>
                <div className="text-sm text-[#f0ddd4] mt-1">Các phiên chat gần đây</div>
              </div>
              <button
                onClick={newConversation}
                className="w-9 h-9 rounded-xl border border-white/15 bg-white/10 hover:bg-white/15 flex items-center justify-center"
                title="Tạo hội thoại mới"
              >
                <Plus size={17} />
              </button>
            </div>
            <div className="mt-4 relative">
              <Search size={16} className="absolute left-4 top-1/2 -translate-y-1/2 text-[#e8d4ca]" />
              <input
                value={historySearch}
                onChange={(e) => setHistorySearch(e.target.value)}
                placeholder="Tìm trong lịch sử hội thoại"
                className="w-full rounded-2xl bg-[#956653] border border-white/15 pl-11 pr-4 py-3 text-sm text-white placeholder:text-[#eedfd7] focus:outline-none focus:border-[#f3ddd3]"
              />
            </div>
          </div>

          <div className="flex-1 overflow-y-auto py-3">
            {filteredConversations.length === 0 ? (
              <div className="px-4 py-6 text-sm text-[#f0ddd4]">Chưa có hội thoại nào.</div>
            ) : (
              <div className="space-y-2 px-3">
                {filteredConversations.map((conversation) => {
                  const active = conversation.id === activeConvId;
                  const pinned = pinnedIds.includes(conversation.id);
                  return (
                    <button
                      key={conversation.id}
                      onClick={() => selectConversation(conversation.id)}
                      className={`w-full text-left rounded-2xl px-4 py-3 border transition ${
                        active
                          ? 'bg-white/14 border-white/20 text-white'
                          : 'bg-transparent border-transparent text-[#f0ddd4] hover:bg-white/8 hover:border-white/12'
                      }`}
                    >
                      <div className="flex items-start gap-3">
                        <div className="min-w-0 flex-1">
                          <div className="truncate text-sm font-medium">{normalizeConversationTitle(conversation.title)}</div>
                          <div className="text-[11px] text-[#f0ddd4] mt-1">
                            {new Date(conversation.updated_at).toLocaleString('vi-VN')}
                          </div>
                        </div>
                        <div className="flex items-center gap-1">
                          <span
                            onClick={(e) => togglePinned(e, conversation.id)}
                            className="w-8 h-8 rounded-xl flex items-center justify-center hover:bg-white/12"
                            role="button"
                            tabIndex={0}
                          >
                            {pinned ? <Pin size={14} /> : <PinOff size={14} />}
                          </span>
                          <span
                            onClick={(e) => handleDeleteConv(e, conversation.id)}
                            className="w-8 h-8 rounded-xl flex items-center justify-center hover:bg-white/12"
                            role="button"
                            tabIndex={0}
                          >
                            <Trash2 size={14} />
                          </span>
                        </div>
                      </div>
                    </button>
                  );
                })}
              </div>
            )}
          </div>
        </aside>
      )}

      <div className="flex-1 min-w-0 flex flex-col overflow-hidden">
        {!embedded && (
          <div
            ref={headerRef}
            className={`shrink-0 border-b px-5 py-4 flex items-center justify-between gap-4 ${
              isUserUi ? 'bg-[linear-gradient(180deg,rgba(246,245,247,0)_0%,rgba(246,245,247,0.94)_20%,rgba(246,245,247,0.98)_100%)] border-[#ead8cf]' : 'bg-[linear-gradient(180deg,rgba(246,245,247,0)_0%,rgba(246,245,247,0.94)_20%,rgba(246,245,247,0.98)_100%)] border-[#ead8cf]'
            }`}
          >
            <div className="flex items-center gap-3 min-w-0">
              {showHistory && (
                <button
                  onClick={() => setSidebarOpen((prev) => !prev)}
                  className="w-10 h-10 rounded-2xl border border-[#dab9aa] bg-white text-[#8c533f] hover:bg-[#fff6f2] flex items-center justify-center"
                  title={sidebarOpen ? 'Ẩn lịch sử' : 'Hiện lịch sử'}
                >
                  <Menu size={18} />
                </button>
              )}
              <img
                src={botAvatar}
                alt={appTitle}
                className={`w-10 h-10 rounded-full object-cover bg-white ${
                  isUserUi ? 'border border-[#ead8cf]' : 'border border-[#ead8cf]'
                }`}
              />
              <div className="min-w-0">
                <div className={`font-semibold truncate text-[15px] ${isUserUi ? 'text-[#7d4f3b]' : 'text-[#7d4f3b]'}`}>{appTitle}</div>
                <div className={`text-[13px] truncate ${isUserUi ? 'text-[#9d705a]' : 'text-[#9d705a]'}`}>
                  {isUserUi ? 'Hỗ trợ tra cứu và hướng dẫn thủ tục trực tuyến' : 'Hỗ trợ quản trị và giám sát hội thoại'}
                </div>
              </div>
            </div>

            <div className="flex items-center gap-2 shrink-0">
              {framedStandalone && isUserUi && (
                <>
                  <button
                    onClick={() => requestHostAction('chatbot_toggle_fullscreen')}
                    className="w-10 h-10 rounded-2xl border border-[#dab9aa] bg-white text-[#8c533f] hover:bg-[#fff6f2] flex items-center justify-center"
                    title="Thu nhỏ khung chat"
                    aria-label="Thu nhỏ khung chat"
                  >
                    <Minimize2 size={18} />
                  </button>
                  <button
                    onClick={() => requestHostAction('chatbot_close')}
                    className="w-10 h-10 rounded-2xl border border-[#dab9aa] bg-white text-[#8c533f] hover:bg-[#fff6f2] flex items-center justify-center"
                    title="Đóng khung chat"
                    aria-label="Đóng khung chat"
                  >
                    <X size={18} />
                  </button>
                </>
              )}
              {!hideHistory && (
                <button
                  onClick={newConversation}
                  className={`rounded-2xl px-4 py-2 text-sm border transition ${
                    isUserUi
                      ? 'border-[#dab9aa] text-[#8c533f] bg-white hover:bg-[#fff6f2]'
                      : 'border-[#dab9aa] text-[#8c533f] bg-white hover:bg-[#fff6f2]'
                  }`}
                >
                  Hội thoại mới
                </button>
              )}
            </div>
          </div>
        )}

        <div
          ref={scrollViewportRef}
          className={`flex-1 min-h-0 overflow-y-auto overflow-x-hidden ${compactUi ? 'px-2.5 py-3' : 'px-4 md:px-6 py-4'} ${dragOver ? 'ring-2 ring-inset ring-[#c89279]' : ''}`}
          onDragOver={(e) => {
            e.preventDefault();
            setDragOver(true);
          }}
          onDragLeave={() => setDragOver(false)}
          onDrop={onDropImage}
        >
          {messages.length === 0 && !pendingUserText && !stream.active && !stream.error ? (
            <div className="h-full flex items-center justify-center">
              <div className={`text-center ${compactUi ? 'max-w-[320px] px-5' : 'max-w-2xl px-6'}`}>
                <div className={`mx-auto mb-4 ${compactUi ? 'w-16 h-16' : 'w-[64px] h-[64px]'}`}>
                  <img src={botAvatar} alt={appTitle} className="w-full h-full object-cover" />
                </div>
                <h1 className={`${compactUi ? 'text-[18px] leading-[1.25]' : 'text-[24px] leading-[1.18]'} font-semibold mb-2 ${isUserUi ? 'text-[#5e4338]' : 'text-[#5e4338]'}`}>{emptyTitle}</h1>
                <p className={`${compactUi ? 'text-[14px] leading-7' : 'text-[17px] leading-7'} ${isUserUi ? 'text-[#8b7b74]' : 'text-[#8b7b74]'}`}>{emptyDesc}</p>
              </div>
            </div>
          ) : (
            <div className={`mx-auto ${compactUi ? 'max-w-full' : 'max-w-[920px]'}`}>
              {messages.map((message) => (
                <React.Fragment key={message.id}>
                  {message.role === 'assistant' && message.id === lastAssistantMessageId && !stream.active && (
                    <div ref={latestReplyAnchorRef} className="h-px" />
                  )}
                  <div
                    ref={message.role === 'assistant' && message.id === lastAssistantMessageId ? latestAssistantRef : undefined}
                    className={`mx-auto ${compactUi ? 'max-w-full' : 'max-w-[920px]'}`}
                  >
                    <MessageBubble
                      message={message}
                      botAvatar={botAvatar}
                      variant={isUserUi ? 'user' : 'admin'}
                      onFeedback={message.role === 'assistant' ? submitFeedback : undefined}
                      compact={compactUi}
                    />
                  </div>
                </React.Fragment>
              ))}

              {pendingUserText && (
                <MessageBubble
                  message={{
                    id: 'pending-user',
                    role: 'user',
                    content: pendingUserText,
                    citations: [],
                    created_at: pendingUserTime || new Date().toISOString(),
                  }}
                  deliveryStatus={pendingDeliveryStatus}
                  variant={isUserUi ? 'user' : 'admin'}
                  compact={compactUi}
                />
              )}

              {(stream.active || stream.error) && (
                <>
                  {stream.active && <div ref={latestReplyAnchorRef} className="h-px" />}
                  <MessageBubble
                  message={{
                    id: 'streaming-assistant',
                    role: 'assistant',
                    content: stream.error || stream.text,
                    citations: stream.citations,
                    answer_mode: stream.mode,
                    created_at: new Date().toISOString(),
                  }}
                  botAvatar={botAvatar}
                  isStreaming={stream.active}
                  streamingMode={stream.mode}
                  deliveryStatus="responding"
                  variant={isUserUi ? 'user' : 'admin'}
                  compact={compactUi}
                />
                </>
              )}
              <div ref={messagesEndRef} />
            </div>
          )}
        </div>

        <div
          className={`shrink-0 border-t ${compactUi ? 'px-2.5 py-2' : 'px-4 md:px-6 py-2.5'} ${
            isUserUi ? 'bg-[#f3f4fa] border-[#f3f4fa]' : 'bg-[#f3f4fa] border-[#f3f4fa]'
          }`}
        >
          <div className={`mx-auto ${compactUi ? 'max-w-full' : 'max-w-5xl'}`}>
            {stream.error && (
              <div
                className={`mb-3 rounded-2xl border px-4 py-3 text-sm flex items-center gap-2 ${
                  isUserUi ? 'border-[#ebcfc3] bg-[#fff3ec] text-[#8c533f]' : 'border-[#ebcfc3] bg-[#fff3ec] text-[#8c533f]'
                }`}
              >
                <AlertCircle size={16} /> {stream.error}
              </div>
            )}

            {selectedImagePreview && (
              <div className={`mb-3 inline-flex items-center gap-3 rounded-2xl border ${compactUi ? 'px-3 py-2.5' : 'px-3 py-3'} ${isUserUi ? 'border-[#ead8cf] bg-[#fff7f2]' : 'border-[#ead8cf] bg-[#fff7f2]'}`}>
                <img src={selectedImagePreview} alt="Preview" className="w-14 h-14 rounded-xl object-cover border border-slate-200" />
                <div className="text-sm min-w-0">
                  <div className={`font-medium truncate ${isUserUi ? 'text-[#734232]' : 'text-[#734232]'}`}>{selectedImage?.name}</div>
                  <div className="text-slate-400">Ảnh đính kèm</div>
                </div>
                <button onClick={() => setSelectedImage(null)} className="w-9 h-9 rounded-xl border border-[#ead8cf] bg-white flex items-center justify-center text-[#8c533f] hover:text-[#734232]">
                  <X size={16} />
                </button>
              </div>
            )}

            {voiceStatus && <div className={`mb-2 ${compactUi ? 'text-[11px]' : 'text-sm'} ${isUserUi ? 'text-[#9b6e58]' : 'text-[#9b6e58]'}`}>{voiceStatus}</div>}

            <div
              className={`rounded-[24px] border ${compactUi ? 'px-2.5 py-1.5 gap-1.5' : 'px-4 py-1 gap-2.5'} flex items-center shadow-sm ${
                isUserUi ? 'border-[#e0c5b8] bg-white/95 backdrop-blur-sm' : 'border-[#e0c5b8] bg-white/95 backdrop-blur-sm'
              }`}
            >
              <button
                onClick={() => fileRef.current?.click()}
                className={`shrink-0 flex items-center justify-center self-center transition ${compactUi ? 'w-[30px] h-[30px]' : 'w-7 h-7'} ${
                  isUserUi
                    ? 'text-[#a4684f] hover:text-[#8f5a45]'
                    : 'text-[#a4684f] hover:text-[#8f5a45]'
                }`}
                title="Đính kèm hình ảnh"
              >
                <ImagePlus size={compactUi ? 16 : 17} />
              </button>
              <input
                ref={fileRef}
                type="file"
                accept="image/*"
                className="hidden"
                onChange={(e) => {
                  const file = e.target.files?.[0];
                  if (file) handleImageFile(file);
                  e.currentTarget.value = '';
                }}
              />

              <button
                onClick={startVoiceInput}
                className={`shrink-0 flex items-center justify-center self-center transition ${compactUi ? 'w-[30px] h-[30px]' : 'w-7 h-7'} ${
                  isListening
                    ? isUserUi
                      ? 'text-[#9b563d]'
                      : 'text-[#9b563d]'
                    : isUserUi
                      ? 'text-[#a4684f] hover:text-[#8f5a45]'
                      : 'text-[#a4684f] hover:text-[#8f5a45]'
                }`}
                title="Ghi âm"
              >
                <Mic size={compactUi ? 16 : 17} />
              </button>

              <div className="flex-1 min-w-0 self-center">
                <textarea
                  ref={textareaRef}
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onPaste={handlePaste}
                  rows={1}
                  placeholder={
                    isUserUi
                      ? 'Nhập hoặc gửi ảnh vấn đề cần hỗ trợ ...'
                      : 'Nhập hoặc gửi ảnh vấn đề cần hỗ trợ ...'
                  }
                  className={`w-full resize-none border-0 bg-transparent outline-none placeholder:text-slate-400 max-h-[88px] ${compactUi ? 'text-[13px] leading-5' : 'text-[14px] leading-5'}`}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && !e.shiftKey) {
                      e.preventDefault();
                      submitMessage();
                    }
                  }}
                />
              </div>

              <button
                onClick={() => submitMessage()}
                disabled={!canSend}
                className={`shrink-0 grid place-items-center self-center text-white transition disabled:cursor-not-allowed ${compactUi ? 'w-9 h-9 rounded-[16px]' : 'w-[38px] h-[38px] rounded-[17px]'} ${
                  canSend
                    ? isUserUi
                      ? 'bg-[#b2694c] hover:bg-[#9e5d46] shadow-[0_8px_18px_rgba(178,105,76,0.22)]'
                      : 'bg-[#b2694c] hover:bg-[#9e5d46] shadow-[0_8px_18px_rgba(178,105,76,0.22)]'
                    : 'bg-[#d6b8ab] opacity-90'
                }`}
                title="Gửi"
              >
                <Send size={compactUi ? 14 : 15} className="block shrink-0" />
              </button>
            </div>

            <div className={`mt-1.5 text-center ${compactUi ? 'text-[11px]' : 'text-[11px]'} ${isUserUi ? 'text-[#967567]' : 'text-[#967567]'}`}>
              {isUserUi ? 'AI có thể mắc sai lầm.' : 'AI có thể mắc sai lầm.'}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
