import React, { useEffect, useMemo, useState } from 'react';
import {
  Activity,
  BarChart3,
  ChevronRight,
  Clock,
  MessageSquare,
  ThumbsDown,
  ThumbsUp,
  TrendingUp,
  Zap,
} from 'lucide-react';
import type { AdminAuth, Message } from '../api/client';
import {
  adminConversations,
  adminDailyStats,
  adminDashboard,
  adminFeedbackLogs,
  adminLogs,
  getMessages,
  resetAdminMonitoring,
} from '../api/client';
import { MessageBubble } from './MessageBubble';

const BOT_AVATAR = '/static/assets/img/chatbot/icon_chatbot_circle_final.png';

function modeLabel(mode?: string) {
  if (mode === 'rag') return '📚 RAG tài liệu';
  if (mode === 'ai_rag' || mode === 'ai+rag') return '🤖📚 AI + RAG';
  return '🤖 AI/Search';
}

export function AdminDashboard({ auth }: { auth: AdminAuth }) {
  const [stats, setStats] = useState<any>(null);
  const [logs, setLogs] = useState<any[]>([]);
  const [feedbackLogs, setFeedbackLogs] = useState<any[]>([]);
  const [daily, setDaily] = useState<any[]>([]);
  const [conversations, setConversations] = useState<any[]>([]);
  const [selectedConversationId, setSelectedConversationId] = useState<string | null>(null);
  const [conversationMessages, setConversationMessages] = useState<Message[]>([]);
  const [loadingConversation, setLoadingConversation] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [resetting, setResetting] = useState(false);

  const selectedFeedback = useMemo(
    () => feedbackLogs.filter((item) => item.conversation_id === selectedConversationId),
    [feedbackLogs, selectedConversationId],
  );

  const loadConversationDetail = async (conversationId: string) => {
    try {
      setLoadingConversation(true);
      const list = await getMessages(conversationId);
      setConversationMessages(list);
      setSelectedConversationId(conversationId);
    } catch (err: any) {
      setError(err.message || 'Không thể tải chi tiết hội thoại');
    } finally {
      setLoadingConversation(false);
    }
  };

  useEffect(() => {
    let mounted = true;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;

    const load = async (attempt = 1) => {
      setLoading(true);
      setError('');
      try {
        const [statsData, logsData, dailyData, feedbackData, conversationsData] = await Promise.all([
          adminDashboard(auth.user, auth.pass),
          adminLogs(auth.user, auth.pass, 30),
          adminDailyStats(auth.user, auth.pass, 14),
          adminFeedbackLogs(auth.user, auth.pass, 50),
          adminConversations(auth.user, auth.pass, 50),
        ]);
        if (!mounted) return;
        setStats(statsData);
        setLogs(logsData as any[]);
        setDaily(dailyData as any[]);
        setFeedbackLogs(feedbackData as any[]);
        setConversations(conversationsData as any[]);

        const firstId = (conversationsData as any[])[0]?.id;
        if (firstId) {
          // Defer conversation detail load — không block render dashboard chính
          setTimeout(() => { if (mounted) loadConversationDetail(firstId); }, 0);
        }
      } catch (err: any) {
        if (!mounted) return;
        const is502 = (err.status === 502) || (err.message || '').includes('502');
        // Tự động retry tối đa 5 lần khi backend đang wake up (502)
        if (is502 && attempt <= 5) {
          const delay = attempt * 8000; // 8s, 16s, 24s, 32s, 40s
          setError(`Backend đang khởi động, thử lại sau ${delay / 1000}s... (lần ${attempt}/5)`);
          setLoading(false);
          retryTimer = setTimeout(() => {
            if (mounted) load(attempt + 1);
          }, delay);
          return;
        }
        setError(err.message || 'Không thể tải dashboard');
      } finally {
        if (mounted) setLoading(false);
      }
    };

    load();
    return () => {
      mounted = false;
      if (retryTimer) clearTimeout(retryTimer);
    };
  }, [auth.pass, auth.user]);

  const handleReset = async () => {
    if (!window.confirm('Đặt lại toàn bộ log giám sát và log đánh giá hiện tại?')) return;
    try {
      setResetting(true);
      await resetAdminMonitoring(auth.user, auth.pass);
      // Thông báo ChatWindow xóa trạng thái để đồng bộ sau reset
      window.dispatchEvent(new CustomEvent('chatbot:monitoring-reset'));
      const [statsData, logsData, dailyData, feedbackData, conversationsData] = await Promise.all([
        adminDashboard(auth.user, auth.pass),
        adminLogs(auth.user, auth.pass, 30),
        adminDailyStats(auth.user, auth.pass, 14),
        adminFeedbackLogs(auth.user, auth.pass, 50),
        adminConversations(auth.user, auth.pass, 50),
      ]);
      setStats(statsData);
      setLogs(logsData as any[]);
      setDaily(dailyData as any[]);
      setFeedbackLogs(feedbackData as any[]);
      setConversations(conversationsData as any[]);
      setConversationMessages([]);
      setSelectedConversationId(null);
      setError('');
    } catch (err: any) {
      setError(err.message || 'Không thể đặt lại dữ liệu giám sát');
    } finally {
      setResetting(false);
    }
  };

  if (loading) {
    return <div className="h-full grid place-items-center text-[#8c6a5b]">Đang tải dashboard...</div>;
  }

  if (error) {
    const isRetrying = error.includes('lần ') && error.includes('/5');
    return (
      <div className="h-full grid place-items-center bg-[#f6f0ec] p-6">
        <div className="max-w-md w-full bg-white rounded-2xl border border-[#ead8cf] shadow-sm p-8 text-center">
          <div className={`w-12 h-12 rounded-full flex items-center justify-center mx-auto mb-4 ${isRetrying ? 'bg-yellow-50 border border-yellow-100' : 'bg-red-50 border border-red-100'}`}>
            <span className={`text-xl ${isRetrying ? 'animate-spin inline-block' : 'text-red-400'}`}>{isRetrying ? '⟳' : '⚠'}</span>
          </div>
          <h3 className="text-base font-semibold text-[#6b4637] mb-2">
            {isRetrying ? 'Backend đang khởi động...' : 'Không thể tải dữ liệu'}
          </h3>
          <p className="text-sm text-[#9d7867] mb-6 leading-relaxed">{error}</p>
          {!isRetrying && (
            <button
              onClick={() => window.location.reload()}
              className="inline-flex items-center gap-2 px-5 py-2.5 rounded-xl bg-[#a86a4f] text-white text-sm font-medium hover:bg-[#945843] transition-colors"
            >
              Thử lại ngay
            </button>
          )}
          {isRetrying && (
            <p className="text-xs text-[#b89080]">Render free tier cần 30–60s để wake up. Vui lòng chờ...</p>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="h-full overflow-y-auto bg-[#f6f0ec]">
      <div className="max-w-7xl mx-auto p-6 space-y-6">
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <BarChart3 size={22} className="text-[#a86a4f]" />
            <h1 className="text-xl font-bold text-[#6b4637]">Giám sát hệ thống</h1>
          </div>
          <button
            onClick={handleReset}
            disabled={resetting}
            className="rounded-2xl border border-[#dfc6ba] bg-white px-4 py-2 text-sm text-[#8c533f] hover:bg-[#fff7f2] disabled:opacity-60"
          >
            {resetting ? 'Đang đặt lại...' : 'Đặt lại giám sát'}
          </button>
        </div>

        {stats && (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <KpiCard icon={<MessageSquare size={18} className="text-[#a86a4f]" />} label="Tổng conversations" value={stats.conversations?.total || 0} sub={`+${stats.conversations?.last_24h || 0} hôm nay`} />
            <KpiCard icon={<Activity size={18} className="text-[#9f6f60]" />} label="Tổng messages" value={stats.messages?.total || 0} sub={`+${stats.messages?.last_24h || 0} hôm nay`} />
            <KpiCard icon={<Clock size={18} className="text-[#c38b56]" />} label="Latency TB (7n)" value={`${stats.avg_latency_ms_7d || 0}ms`} />
            <KpiCard icon={<Zap size={18} className="text-[#8f5d49]" />} label="Tokens dùng (7n)" value={(stats.total_tokens_7d || 0).toLocaleString()} />
          </div>
        )}

        {stats && (
          <div className="grid md:grid-cols-3 gap-4">
            <div className="bg-white rounded-2xl border border-[#ead8cf] p-5 md:col-span-1">
              <h3 className="font-semibold text-[#6b4637] text-sm mb-4">Chế độ trả lời (7 ngày)</h3>
              <div className="space-y-3">
                {Object.entries(stats.answer_modes_7d || {}).map(([mode, count]: any) => (
                  <div key={mode}>
                    <div className="flex justify-between text-xs text-[#8c6a5b] mb-1">
                      <span>{modeLabel(mode)}</span>
                      <span>{count} lượt</span>
                    </div>
                    <div className="h-2 bg-[#f3e8e1] rounded-full">
                      <div
                        className="h-2 rounded-full bg-[#b2694c]"
                        style={{ width: `${(count / (Object.values(stats.answer_modes_7d).reduce((a: any, b: any) => a + b, 0) as number || 1)) * 100}%` }}
                      />
                    </div>
                  </div>
                ))}
              </div>
            </div>

            <div className="bg-white rounded-2xl border border-[#ead8cf] p-5 md:col-span-1">
              <h3 className="font-semibold text-[#6b4637] text-sm mb-4">Trạng thái tài liệu</h3>
              <div className="space-y-2">
                {Object.entries(stats.documents || {}).map(([status, count]: any) => (
                  <div key={status} className="flex justify-between items-center px-3 py-2 rounded-xl border text-xs text-[#734232] bg-[#fff9f6] border-[#ead8cf]">
                    <span className="capitalize font-medium">{status}</span>
                    <span className="font-semibold">{count} tài liệu</span>
                  </div>
                ))}
              </div>
            </div>

            <div className="bg-white rounded-2xl border border-[#ead8cf] p-5 md:col-span-1">
              <h3 className="font-semibold text-[#6b4637] text-sm mb-4">Đánh giá phản hồi (7 ngày)</h3>
              <div className="space-y-3">
                <div className="flex items-center justify-between rounded-xl border border-[#e6d5c7] bg-[#fff7f2] px-3 py-3 text-sm">
                  <span className="inline-flex items-center gap-2 text-[#8c533f]"><ThumbsUp size={15} /> Hữu ích</span>
                  <strong className="text-[#734232]">{stats.feedback_7d?.like || 0}</strong>
                </div>
                <div className="flex items-center justify-between rounded-xl border border-[#e6d5c7] bg-[#fff7f2] px-3 py-3 text-sm">
                  <span className="inline-flex items-center gap-2 text-[#8c533f]"><ThumbsDown size={15} /> Chưa ổn</span>
                  <strong className="text-[#734232]">{stats.feedback_7d?.dislike || 0}</strong>
                </div>
              </div>
            </div>
          </div>
        )}

        <div className="grid xl:grid-cols-[360px_minmax(0,1fr)] gap-4">
          <div className="bg-white rounded-2xl border border-[#ead8cf] overflow-hidden min-h-[560px]">
            <div className="px-5 py-4 border-b border-[#f1e4db]">
              <h3 className="font-semibold text-[#6b4637] text-sm">Nhật ký hội thoại</h3>
              <p className="text-xs text-[#9a7868] mt-1">Bấm vào từng cuộc hội thoại để xem chi tiết và đánh giá của người dùng</p>
            </div>
            <div className="max-h-[620px] overflow-y-auto p-3 space-y-2">
              {conversations.length === 0 ? (
                <div className="px-3 py-6 text-sm text-[#9a7868]">Chưa có hội thoại nào.</div>
              ) : (
                conversations.map((conversation) => {
                  const active = selectedConversationId === conversation.id;
                  const feedbackCount = feedbackLogs.filter((item) => item.conversation_id === conversation.id).length;
                  return (
                    <button
                      key={conversation.id}
                      onClick={() => loadConversationDetail(conversation.id)}
                      className={`w-full text-left rounded-2xl border px-4 py-3 transition ${
                        active
                          ? 'bg-[#fff4ee] border-[#d9b7a7]'
                          : 'bg-white border-[#f1e4db] hover:bg-[#fff9f6]'
                      }`}
                    >
                      <div className="flex items-start gap-3">
                        <div className="flex-1 min-w-0">
                          <div className="truncate text-sm font-medium text-[#734232]">{conversation.title || 'Hội thoại'}</div>
                          <div className="text-[11px] text-[#9a7868] mt-1">{new Date(conversation.updated_at).toLocaleString('vi-VN')}</div>
                          <div className="text-[11px] text-[#a18274] mt-1 truncate">Session: {conversation.session_key || '—'}</div>
                        </div>
                        <div className="shrink-0 text-right">
                          <div className="inline-flex items-center gap-1 rounded-full border border-[#ead8cf] bg-white px-2 py-1 text-[10px] text-[#8c533f]">
                            {feedbackCount} đánh giá
                          </div>
                          <ChevronRight size={14} className="ml-auto mt-2 text-[#b9856b]" />
                        </div>
                      </div>
                    </button>
                  );
                })
              )}
            </div>
          </div>

          <div className="space-y-4">
            <div className="bg-white rounded-2xl border border-[#ead8cf] overflow-hidden min-h-[360px]">
              <div className="px-5 py-4 border-b border-[#f1e4db] flex items-center justify-between gap-3">
                <div>
                  <h3 className="font-semibold text-[#6b4637] text-sm">Chi tiết hội thoại</h3>
                  <p className="text-xs text-[#9a7868] mt-1">Xem đầy đủ nội dung cuộc trò chuyện đã ghi nhận</p>
                </div>
                {loadingConversation && <span className="text-xs text-[#9a7868]">Đang tải...</span>}
              </div>
              <div className="max-h-[520px] overflow-y-auto px-4 py-4 bg-[linear-gradient(180deg,#f8f1ed_0%,#f3f4fa_100%)]">
                {selectedConversationId && conversationMessages.length > 0 ? (
                  <div className="max-w-4xl mx-auto">
                    {conversationMessages.map((message) => (
                      <MessageBubble
                        key={message.id}
                        message={message}
                        botAvatar={BOT_AVATAR}
                        variant="user"
                        compact={false}
                      />
                    ))}
                  </div>
                ) : (
                  <div className="h-[260px] grid place-items-center text-sm text-[#9a7868]">Chọn một cuộc hội thoại để xem chi tiết.</div>
                )}
              </div>
            </div>

            <div className="bg-white rounded-2xl border border-[#ead8cf] overflow-hidden">
              <div className="px-5 py-4 border-b border-[#f1e4db]">
                <h3 className="font-semibold text-[#6b4637] text-sm">Đánh giá của người dùng theo hội thoại</h3>
              </div>
              {selectedFeedback.length > 0 ? (
                <div className="overflow-x-auto">
                  <table className="w-full text-xs">
                    <thead className="bg-[#fff8f4] text-[#8c6a5b]">
                      <tr>
                        {['Thời gian', 'Đánh giá', 'Vấn đề', 'Mô tả', 'Trích phản hồi'].map((h) => (
                          <th key={h} className="text-left px-4 py-2.5 font-medium">{h}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-[#f1e4db]">
                      {selectedFeedback.map((log: any) => (
                        <tr key={log.id} className="hover:bg-[#fffaf7] align-top">
                          <td className="px-4 py-3 text-[#9a7868] whitespace-nowrap">{log.created_at ? new Date(log.created_at).toLocaleString('vi-VN') : 'N/A'}</td>
                          <td className="px-4 py-3">
                            <span className="inline-flex items-center gap-1 rounded-full px-2 py-1 border border-[#e6d5c7] bg-[#fff7f2] text-[#8c533f]">
                              {log.rating === 'like' ? <ThumbsUp size={11} /> : <ThumbsDown size={11} />}
                              {log.rating === 'like' ? 'Hữu ích' : 'Chưa ổn'}
                            </span>
                          </td>
                          <td className="px-4 py-3 text-[#734232] max-w-[180px]">{log.issue_type || '—'}</td>
                          <td className="px-4 py-3 text-[#734232] max-w-[260px] whitespace-pre-wrap">{log.description || '—'}</td>
                          <td className="px-4 py-3 text-[#8c6a5b] max-w-[320px] whitespace-pre-wrap">{log.answer_excerpt || '—'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <div className="px-5 py-6 text-sm text-[#9a7868]">Hội thoại này chưa có đánh giá nào.</div>
              )}
            </div>
          </div>
        </div>

        {daily.length > 0 && (
          <div className="bg-white rounded-2xl border border-[#ead8cf] p-5">
            <h3 className="font-semibold text-[#6b4637] text-sm mb-4 flex items-center gap-2">
              <TrendingUp size={16} className="text-[#a86a4f]" /> Messages theo ngày
            </h3>
            <div className="flex items-end gap-1.5 h-28">
              {daily.map((day: any) => {
                const maxVal = Math.max(...daily.map((d: any) => d.total), 1);
                const h = Math.max((day.total / maxVal) * 100, 4);
                return (
                  <div key={day.day} className="flex-1 flex flex-col items-center gap-1 group">
                    <div className="w-full bg-[#b2694c] rounded-t-sm transition-all group-hover:bg-[#9e5d46]" style={{ height: `${h}%` }} title={`${day.day}: ${day.total} messages`} />
                    <span className="text-[9px] text-[#9a7868] rotate-45 origin-left whitespace-nowrap">{(day.day || '').slice(5)}</span>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {logs.length > 0 && (
          <div className="bg-white rounded-2xl border border-[#ead8cf] overflow-hidden">
            <div className="px-5 py-4 border-b border-[#f1e4db]">
              <h3 className="font-semibold text-[#6b4637] text-sm">Query log gần đây</h3>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead className="bg-[#fff8f4] text-[#8c6a5b]">
                  <tr>
                    {['Thời gian', 'Query', 'Chế độ', 'Chunks', 'Latency', 'Tokens'].map((h) => (
                      <th key={h} className="text-left px-4 py-2.5 font-medium">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-[#f1e4db]">
                  {logs.map((log: any) => (
                    <tr key={log.id} className="hover:bg-[#fffaf7]">
                      <td className="px-4 py-2.5 text-[#9a7868] whitespace-nowrap">{log.created_at ? new Date(log.created_at).toLocaleString('vi-VN') : 'N/A'}</td>
                      <td className="px-4 py-2.5 text-[#734232] max-w-xs truncate">{log.query}</td>
                      <td className="px-4 py-2.5 text-[#8c6a5b]">{modeLabel(log.mode)}</td>
                      <td className="px-4 py-2.5 text-[#8c6a5b]">{log.retrieved_chunks}</td>
                      <td className="px-4 py-2.5 text-[#8c6a5b]">{log.latency_ms}ms</td>
                      <td className="px-4 py-2.5 text-[#8c6a5b]">{log.tokens}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function KpiCard({ icon, label, value, sub }: any) {
  return (
    <div className="bg-white rounded-2xl border border-[#ead8cf] p-5">
      <div className="w-9 h-9 bg-[#fff8f4] rounded-xl flex items-center justify-center mb-3">{icon}</div>
      <p className="text-2xl font-bold text-[#6b4637]">{value}</p>
      <p className="text-xs text-[#8c6a5b] mt-1">{label}</p>
      {sub && <p className="text-xs text-[#a18274] mt-0.5">{sub}</p>}
    </div>
  );
}
